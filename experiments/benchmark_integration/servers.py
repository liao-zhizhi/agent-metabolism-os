"""Three arms as ``vla_eval`` model servers: the wire contract is the integration.

Each class subclasses ``vla_eval.model_servers.PredictModelServer``, so it is
served by the harness's own WebSocket server (``vla_eval.api.serve_background``)
and driven by the harness's own ``SyncEpisodeRunner``. Nothing about the
episode loop is ours: we implement ``predict(obs, row)`` and return an action,
which is exactly what a real VLA server does. That is the difference between
"we wrote a loop that looks like a benchmark" and "the benchmark drove us".

The three arms differ only in what they do with the observation:

``PassthroughServer``      plans under its belief, executes, never checks. This is
                           the unfiltered policy, the thing a benchmark score
                           normally measures.
``SafeVLAServer``          plans, asks its own confidence, and refuses below 0.80
                           -- the confidence-gate baseline that safety wrappers
                           deploy around OpenVLA/pi0-style policies. ``vla-eval``
                           ships the model server for those policies but no
                           filter of its own, so the gate is here, with its
                           threshold, its noise and the price of a takeover
                           written down rather than tuned to the result.
                           A refusal returns the proposed chunk, which is what a
                           real filter does (it gates an action; it does not
                           decline to produce one) and what makes a false-veto
                           count possible.
``CausalInterfaceServer``  plans, executes, and reads the consequence each
                           control step. A deceleration that disagrees with its
                           belief is a FAIL, and one difference quotient gives
                           the friction that explains it. It then replans,
                           mid-chunk, before the block reaches the wall.

Every arm's friction belief persists across episodes, which is the whole point:
metabolism is the thing that is supposed to survive the distribution shift.
Nothing here reads the episode's success flag, because a deployed model server
does not get one.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from vla_eval.model_servers.base import Row, Rows
from vla_eval.model_servers.predict import PredictModelServer
from vla_eval.specs import DimSpec

from .policies import (
    CHUNK_SIZE, CONF_THRESHOLD, CONTROL_DT, ConfidenceModel,
    FrictionRegistry, LOOKUP_MS, MAX_DELTA, MU_CEILING, MU_FLOOR, Obs, VLA_MS,
    WITNESS_MS, brake_and_grab, estimate_friction, plan_chunk,
)

#: A mismatch between the observed speed and the predicted speed, in m/s, that
#: counts as evidence rather than noise. The simulator has no noise; a real one
#: would set this from the encoder's resolution. 0.04 m/s is about one control
#: step of friction at the lowest mu in the suite, so a policy cannot flag
#: itself on a single rounding.
MISMATCH_TOLERANCE = 0.04

NOMINAL_BELIEF = 0.35


@dataclass
class EpisodeState:
    """Per-episode bookkeeping. Dropped when the episode ends."""

    buffer: List[np.ndarray] = field(default_factory=list)
    prev_speed: Optional[float] = None
    prev_vx: Optional[float] = None
    last_delta_x: float = 0.0
    replans: int = 0
    inferences: int = 0
    lookups: int = 0
    witnesses: int = 0
    inference_ms: float = 0.0
    compute_ms: float = 0.0
    vetoed: bool = False


class _ArmServer(PredictModelServer):
    """Shared plumbing: per-episode buffers, a persistent belief, timing."""

    arm_name = "arm"

    def __init__(self, *, seed: int = 0, belief: float = NOMINAL_BELIEF) -> None:
        # chunk_size=None: this server emits one action per call and owns its
        # own chunk buffer, which is what lets the causal arm abandon a chunk
        # mid-flight. Letting the harness buffer would make that impossible.
        super().__init__(chunk_size=None)
        self.rng = np.random.default_rng(seed)
        self.belief = float(belief)
        self.episodes: Dict[str, EpisodeState] = {}
        self.registry = FrictionRegistry()
        self.total_inference_ms = 0.0
        self.total_compute_ms = 0.0
        self.inference_calls = 0
        self.lookup_calls = 0
        self.witness_calls = 0
        self.takeovers = 0

    # -- specs ------------------------------------------------------------

    def get_action_spec(self) -> Dict[str, DimSpec]:
        return {"actions": DimSpec(name="actions", dims=2, format="delta_xy",
                                   range=(-MAX_DELTA, MAX_DELTA))}

    def get_observation_spec(self) -> Dict[str, DimSpec]:
        return {"state": DimSpec(name="state", dims=10, format="raw")}

    @property
    def counters(self) -> Dict[str, Any]:
        """What this arm spent, for the report. Counted, not inferred."""
        return {
            "inference_calls": self.inference_calls,
            "lookup_calls": self.lookup_calls,
            "witness_calls": self.witness_calls,
            "takeovers": self.takeovers,
            "modelled_inference_ms": self.total_inference_ms,
            "measured_compute_ms": self.total_compute_ms,
        }

    # -- episode lifecycle -------------------------------------------------

    def _state(self, row: Row) -> EpisodeState:
        key = row.episode.episode_id
        state = self.episodes.get(key)
        if state is None:
            # A new episode: the belief and the registry are *not* reset here.
            # That persistence is the mechanism under test.
            state = EpisodeState()
            self.episodes[key] = state
        return state

    def reset_episodes(self, rows: Rows) -> Optional[Dict[int, Exception]]:
        for row in rows:
            self.episodes.pop(row.episode.episode_id, None)
        if len(self.episodes) > 256:
            # Bounded memory for long runs: keep the most recent states only.
            for key in list(self.episodes)[:-64]:
                self.episodes.pop(key, None)
        return None

    # -- the harness calls this -------------------------------------------

    def predict(self, obs: Dict[str, Any], row: Row) -> Dict[str, Any]:
        state = self._state(row)
        o = Obs.from_state(obs["state"])
        before_ms, before_replans = state.inference_ms, state.replans
        before_compute = state.compute_ms
        started = time.perf_counter()
        action = self._act(o, state, row)
        state.compute_ms += (time.perf_counter() - started) * 1000.0
        self.total_compute_ms += state.compute_ms - before_compute
        # The benchmark is told what this call cost, the way a real server
        # reports its own inference time: the environment cannot know it.
        action["inference_ms"] = state.inference_ms - before_ms
        action["replans"] = state.replans - before_replans
        action["measured_compute_ms"] = state.compute_ms - before_compute
        return action

    # -- hooks -------------------------------------------------------------

    def _act(self, o: Obs, state: EpisodeState, row: Row) -> Dict[str, Any]:
        raise NotImplementedError

    def _bill(self, state: EpisodeState, inference_ms: float) -> float:
        state.inference_ms += inference_ms
        self.total_inference_ms += inference_ms
        return inference_ms

    def _plan(self, o: Obs, state: EpisodeState) -> tuple:
        """Derive a chunk with a forward pass. Only the causal arm overrides this.

        A compiled rule is part of the mechanism under test, so it is not in the
        shared path: an arm that got lookups for free would be a different arm.
        """
        chunk = plan_chunk(o, self.belief)
        self.inference_calls += 1
        state.inferences += 1
        return chunk, self._bill(state, VLA_MS)


class PassthroughServer(_ArmServer):
    """A: the policy, with nothing between it and the actuator."""

    arm_name = "A_passthrough"

    def _act(self, o, state, row):
        if not state.buffer:
            chunk, _ = self._plan(o, state)
            state.buffer = [np.asarray(r, dtype=float) for r in chunk]
        action = state.buffer.pop(0) if state.buffer else np.zeros(2)
        state.prev_speed = float(o.speed)
        out = {"actions": action.astype(np.float32)}
        if state.buffer:
            out["inference_running"] = True
        return out


class SafeVLAServer(_ArmServer):
    """C: the confidence gate. Aligned with the OpenVLA-style safety filter.

    What it is *not*: a straw man with a broken threshold. It is the best
    available answer to "should I act?" when the only evidence is the model's
    own confidence, and its failure is that the evidence is about the model.
    """

    arm_name = "C_safe_vla"

    def __init__(self, *, seed: int = 0, threshold: float = CONF_THRESHOLD,
                 notice_prob: float = 0.75) -> None:
        super().__init__(seed=seed)
        self.threshold = float(threshold)
        self.confidence_model = ConfidenceModel(np.random.default_rng(seed + 1),
                                                notice_prob=notice_prob)
        self._rescue_pending = False

    def _act(self, o, state, row):
        # Observable failure from the last plan: stopped short of the frame.
        if o.speed < 0.02 and o.remaining > 0.05 and state.prev_speed is not None:
            self.confidence_model.observe_outcome(False)

        if row.step == 0 and self._rescue_pending:
            # The human handed control back after a rescue: the filter is
            # re-anchored, but the policy's physics prior is not, because a
            # confidence-only arm has no channel through which physics arrives.
            self.confidence_model.alert = 0.0
            self._rescue_pending = False

        if not state.buffer:
            confidence = self.confidence_model.sample()
            chunk = plan_chunk(o, self.belief)
            self.inference_calls += 1
            state.inferences += 1
            self._bill(state, VLA_MS)
            if confidence < self.threshold:
                self.takeovers += 1
                state.vetoed = True
                self._rescue_pending = True
                # A refusal is a request for a human, priced at the takeover.
                self._bill(state, 0.0)
                if o.speed > 0.02:
                    stop = brake_and_grab(o, self.belief)
                    return {"actions": stop.astype(np.float32),
                            "vetoed": True, "takeover": True,
                            "proposed_actions": chunk.astype(np.float32),
                            "confidence": confidence}
                return {"actions": np.zeros(2, dtype=np.float32),
                        "vetoed": True, "takeover": True,
                        "proposed_actions": chunk.astype(np.float32),
                        "confidence": confidence}
            state.buffer = [np.asarray(r, dtype=float) for r in chunk]
            state.prev_speed = float(o.speed)
            action = state.buffer.pop(0)
            return {"actions": action.astype(np.float32), "confidence": confidence}

        state.prev_speed = float(o.speed)
        action = state.buffer.pop(0)
        return {"actions": action.astype(np.float32)}


class CausalInterfaceServer(_ArmServer):
    """B: act, witness, and let the consequence correct the belief.

    The witness runs on the *trajectory*, not on the final pose, because the
    harness hands over an observation at 10 Hz. That is a real difference: a
    post-hoc check on a finished push can only count the damage, while a check
    one control step into a coasting block can prevent it. The 20 ms is spent
    per control step, which is why this arm's latency is not free -- it is
    20 ms against a 100 ms control period.
    """

    arm_name = "B_causal_interface"

    def __init__(self, *, seed: int = 0, tolerance: float = MISMATCH_TOLERANCE) -> None:
        super().__init__(seed=seed)
        self.tolerance = float(tolerance)
        self.failures = 0
        self.corrections = 0

    def _act(self, o, state, row):
        # -- the witness: is the observed deceleration the one the belief
        # predicted? The prediction includes the impulse we just commanded, so a
        # planned push is not mistaken for an unexplained one.
        if state.prev_vx is not None and state.buffer:
            self.witness_calls += 1
            state.witnesses += 1
            self._bill(state, WITNESS_MS)
            launched = max(0.0, state.prev_vx + state.last_delta_x)
            predicted = launched - self.belief * 9.81 * CONTROL_DT
            observed = float(o.vx)
            if abs(observed - max(0.0, predicted)) > self.tolerance:
                self.failures += 1
                estimate = estimate_friction(launched, observed, CONTROL_DT)
                if estimate is None and observed > launched + 1e-6:
                    # Something pushed the block: a contact, not the table. The
                    # witness cannot name a friction from that, and saying so is
                    # better than clamping a number.
                    self._replan(o, state)
                else:
                    if estimate is not None:
                        self.belief = float(np.clip(estimate, MU_FLOOR, MU_CEILING))
                        self.registry.clear()
                        self.corrections += 1
                    self._replan(o, state)

        if not state.buffer:
            if state.prev_vx is not None and o.speed < 0.02 and o.remaining > 0.05:
                # Settled short of the frame. The witness above fires on the
                # deceleration; this catches the case where the block stopped
                # inside a single control step and there was no coast to read.
                self._replan(o, state)
            else:
                chunk, _ = self._plan(o, state)
                state.buffer = [np.asarray(r, dtype=float) for r in chunk]

        action = state.buffer.pop(0) if state.buffer else np.zeros(2)
        state.prev_vx = float(o.vx)
        state.prev_speed = float(o.speed)
        state.last_delta_x = float(action[0])
        return {"actions": action.astype(np.float32)}

    def _replan(self, o, state) -> None:
        """Abandon the rest of the chunk; it was planned under other physics."""
        chunk, _ = self._plan(o, state)
        state.replans += 1
        state.buffer = [np.asarray(r, dtype=float) for r in chunk]

    def _plan(self, o, state):
        """Compiled rules: a friction bin the witness has confirmed is reused.

        The rule stored is the braking coefficient ``sqrt(2 mu g)``; applying it
        to a distance is arithmetic, so the replay costs a lookup rather than a
        forward pass. This is the solidification discount, and it is why the
        causal arm's steady-state cost is below the passthrough arm's.
        """
        cached = self.registry.lookup(self.belief)
        if cached is not None:
            self.lookup_calls += 1
            state.lookups += 1
            coefficient = float(cached[0])
            chunk = np.zeros((CHUNK_SIZE, 2), dtype=float)
            if o.remaining > 0.005:
                need = coefficient * math.sqrt(max(o.remaining, 0.0)) - o.vx
                chunk[0, 0] = float(np.clip(need, -MAX_DELTA, MAX_DELTA))
            return chunk, self._bill(state, LOOKUP_MS)
        chunk = plan_chunk(o, self.belief)
        self.inference_calls += 1
        state.inferences += 1
        self.registry.store(self.belief, np.array(
            [math.sqrt(2.0 * self.belief * 9.81)]))
        return chunk, self._bill(state, VLA_MS)

    @property
    def counters(self) -> Dict[str, Any]:
        base = super().counters
        base.update({"failures": self.failures, "corrections": self.corrections,
                     "belief": self.belief})
        return base


def build_arm(name: str, seed: int = 0) -> _ArmServer:
    if name in ("A", "passthrough", "A_passthrough"):
        return PassthroughServer(seed=seed)
    if name in ("C", "safe", "C_safe_vla", "safe_vla"):
        return SafeVLAServer(seed=seed)
    if name in ("B", "cel", "B_causal_interface", "causal"):
        return CausalInterfaceServer(seed=seed)
    raise ValueError("unknown arm {!r}".format(name))
