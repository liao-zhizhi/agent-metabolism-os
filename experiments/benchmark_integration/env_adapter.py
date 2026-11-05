"""Environment adapter: the 2-D push table, expressed as a ``vla_eval`` benchmark.

This is the piece that makes the comparison a benchmark comparison rather than a
sandbox comparison. The class below subclasses ``vla_eval.benchmarks.StepBenchmark``
and therefore has to satisfy the harness's own contract -- ``get_tasks``,
``get_action_spec``, ``get_observation_spec``, ``get_metric_keys``, ``reset``,
``step``, ``make_obs``, ``get_step_result`` -- because the harness's real
``SyncEpisodeRunner`` calls them, not because we declared them.

What the environment is
-----------------------
Five push tasks, LIBERO-shaped: a natural-language instruction, a block, a
target frame, a fixed horizon. One episode is one attempt. The policy is asked
for an action at every control step, exactly as ``SyncEpisodeRunner`` is built
to do; inside a server it may answer from an action chunk, which is what a VLA
does.

The action is a *delta velocity* -- the convention LIBERO's OSC policy uses --
added to the block's velocity. The block is a rigid disc on a table with Coulomb
friction, and the physics is ``experiments.physics_sandbox.Table``: circle and
wall contacts with impulse resolution, momentum-conserving block/block contacts.

The episode ends when the block settles inside the frame (success), overshoots
past it (failure), hits the wall or the decoy hard enough to register a safety
violation, or runs out of horizon.

Why the fractions matter
------------------------
``success`` is the autonomous one. A policy that refuses to act has not completed
the task, and a benchmark that counted the human's completion as the policy's own
would report a number no deployment could reproduce. ``assisted_success`` is
recorded next to it, with the takeover priced into ``latency_ms``, so the cost of
refusing is visible instead of living in a footnote.

``safety_violation`` is a property of what the block did, not of what the policy
claimed: a contact above ``DAMAGE_IMPACT_SPEED``, a command above the velocity
limit, or a pose outside the table.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from experiments.physics_sandbox import (  # noqa: E402
    BLOCK_RADIUS, DAMAGE_IMPACT_SPEED, DT, FRAME_HALF_HEIGHT, FRAME_X0, FRAME_X1,
    LANES, Block, Table,
)

from vla_eval.benchmarks.base import StepBenchmark, StepResult  # noqa: E402
from vla_eval.specs import DimSpec  # noqa: E402

from .task_spec import (  # noqa: E402
    CHUNK_SIZE, CONTROL_DT, DECOY_X, HORIZON, MAX_COMMAND_SPEED,
    NOMINAL_FRICTION, OVERSHOOT_TOLERANCE, SETTLED_SPEED, START_X, TASK_NAMES,
    episode_task,
)


class PushTableBenchmark(StepBenchmark):
    """One block, one frame, one instruction; the world comes in with the task."""

    name = "push_table_2d"
    #: No MuJoCo and no camera: the observation is a state vector, so the CPU
    #: backend is the honest declaration (the harness fails fast on a backend a
    #: benchmark has not verified).
    render_backends = frozenset({"cpu"})
    step_dt = CONTROL_DT

    @classmethod
    def configure_render(cls, mode: str) -> Dict[str, str]:
        """There is no renderer to configure: the observation is a state vector."""
        if mode not in cls.render_backends:
            raise NotImplementedError(
                "{} only runs on {}".format(cls.__name__, sorted(cls.render_backends)))
        return {}

    def __init__(self, suite: str = "nominal", seed: int = 0,
                 regime_windows: Optional[Sequence[Sequence[float]]] = None) -> None:
        super().__init__()
        self.suite = suite
        self.seed = seed
        # A regime schedule for runs driven by the CLI, which constructs the
        # benchmark from a config and steps it episode by episode. ``[[mu, n], ...]``
        # expanded in order; ``None`` means every episode uses the task's own
        # friction, which is what the in-process driver passes explicitly.
        self.regime_windows = (list(regime_windows)
                               if regime_windows is not None else None)
        self.regime_episodes = self._expand_regimes()
        self.regime_counter = 0
        self.table: Optional[Table] = None
        self.task: Dict[str, Any] = {}
        self.frame: Sequence[float] = (FRAME_X0, FRAME_X1)
        self.target = np.zeros(2)
        self.friction = NOMINAL_FRICTION
        self.violation = False
        self.command_violation = False
        self.peak_impact = 0.0
        self.overshoot = False
        self.step_count = 0
        self.takeover = False
        self.vetoed = False
        self.shadow_success: Optional[bool] = None
        self.modelled_inference_ms = 0.0
        self.replans = 0
        self.veto_reason = ""

    # -- contract: data ---------------------------------------------------

    def get_tasks(self) -> List[Dict[str, Any]]:
        return [episode_task(i, self.suite) for i in range(len(TASK_NAMES))]

    def get_action_spec(self) -> Dict[str, DimSpec]:
        return {
            "actions": DimSpec(
                name="actions", dims=2, format="delta_xy",
                range=(-MAX_COMMAND_SPEED, MAX_COMMAND_SPEED),
                description=("delta-velocity command, m/s, added to the block's velocity "
                             "-- LIBERO's OSC delta-action convention"),
            ),
        }

    def get_observation_spec(self) -> Dict[str, DimSpec]:
        return {
            "state": DimSpec(
                name="state", dims=10, format="raw",
                description=("block x, y, vx, vy, target x, target y, frame x0, "
                             "frame x1, steps remaining, elapsed seconds"),
            ),
            "task_description": DimSpec(name="task_description", dims=0,
                                        format="language"),
            "images": DimSpec(name="images", dims=0, format="rgb",
                              description="present but blank: the state is exact"),
        }

    def get_metric_keys(self) -> Dict[str, str]:
        return {
            "success": "mean",
            "assisted_success": "mean",
            "safety_violation": "mean",
            "latency_ms": "mean",
            "takeovers": "sum",
            "false_vetoes": "sum",
            "replans": "sum",
            "steps": "mean",
        }

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "control_dt": CONTROL_DT,
            "chunk_size": CHUNK_SIZE,
            "horizon": HORIZON,
            "max_command_speed": MAX_COMMAND_SPEED,
            "damage_impact_speed": DAMAGE_IMPACT_SPEED,
            "frame": list(self.frame),
            "suite": self.suite,
        }

    # -- world -------------------------------------------------------------

    def _build_world(self) -> None:
        blocks = [Block(i, np.array([START_X, lane]), radius=BLOCK_RADIUS)
                  for i, lane in enumerate(LANES)]
        blocks.append(Block(len(LANES), np.array([DECOY_X, LANES[2]]),
                            radius=BLOCK_RADIUS, role="decoy"))
        self.table = Table(blocks=blocks)
        for block in self.table.blocks:
            block.friction = self.friction

    def _expand_regimes(self) -> List[Dict[str, float]]:
        if not self.regime_windows:
            return []
        out: List[Dict[str, float]] = []
        for entry in self.regime_windows:
            mu, count = float(entry[0]), int(entry[1])
            frame = (float(entry[2]), float(entry[3])) if len(entry) >= 4 else None
            for _ in range(count):
                out.append({"friction": mu, "frame": frame})
        return out

    def reset(self, task) -> Any:
        self.task = dict(task)
        if self.regime_episodes and self.regime_counter < len(self.regime_episodes):
            window = self.regime_episodes[self.regime_counter]
            self.regime_counter += 1
            self.task["friction"] = window["friction"]
            if window["frame"] is not None:
                self.task["frame"] = window["frame"]
                self.task["target_x"] = 0.5 * (window["frame"][0] + window["frame"][1])
        # Read from the effective task, which is where the regime schedule (when
        # one is configured) has just written this episode's world.
        self.friction = float(self.task.get("friction", NOMINAL_FRICTION))
        self.frame = tuple(self.task.get("frame", (FRAME_X0, FRAME_X1)))
        self.target = np.array([
            float(self.task.get("target_x", 0.5 * (self.frame[0] + self.frame[1]))),
            float(self.task.get("lane", LANES[0]))])
        self._build_world()
        self.violation = False
        self.command_violation = False
        self.peak_impact = 0.0
        self.overshoot = False
        self.step_count = 0
        self.takeover = False
        self.vetoed = False
        self.shadow_success = None
        self.modelled_inference_ms = 0.0
        self.replans = 0
        self.veto_reason = ""
        self._chunk: List[np.ndarray] = []
        return self._raw_obs()

    def _block(self) -> Block:
        return self.table.get(int(self.task.get("task_idx", 0)))

    def _raw_obs(self) -> Dict[str, Any]:
        block = self._block()
        return {
            "x": block.x, "y": block.y,
            "vx": float(block.velocity[0]), "vy": float(block.velocity[1]),
            "speed": block.speed,
            "target": self.target.copy(),
            "steps_remaining": HORIZON - self.step_count,
            "elapsed": self.step_count * CONTROL_DT,
        }

    def make_obs(self, raw_obs: Any, task) -> Dict[str, Any]:
        state = np.array([
            raw_obs["x"], raw_obs["y"], raw_obs["vx"], raw_obs["vy"],
            raw_obs["target"][0], raw_obs["target"][1],
            self.frame[0], self.frame[1],
            raw_obs["steps_remaining"], raw_obs["elapsed"],
        ], dtype=np.float32)
        return {
            "state": state,
            "task_description": task.get("instruction", ""),
            # An image key is present because every real VLA expects one. It is a
            # blank frame and the spec says so: pretending to render would add a
            # dependency for no information the state lacks.
            "images": {"agentview": np.zeros((8, 8, 3), dtype=np.uint8)},
        }

    # -- stepping ---------------------------------------------------------

    def step(self, action: Dict[str, Any]) -> StepResult:
        if action.get("vetoed"):
            # A refusal: the episode ends, the task was not completed
            # autonomously, and the proposed action is scored on a clone so that
            # "would it have worked?" is answered rather than assumed.
            self.vetoed = True
            self.takeover = bool(action.get("takeover", True))
            self.veto_reason = str(action.get("veto_reason", "confidence below threshold"))
            proposed = action.get("proposed_actions")
            if proposed is not None and self.shadow_success is None:
                self.shadow_success = self._rollout_shadow(np.asarray(proposed, dtype=float))
            self.modelled_inference_ms += float(action.get("inference_ms", 0.0))
            return StepResult(obs=self._raw_obs(), reward=0.0, done=True, info={})

        if "actions" not in action:
            raise ValueError("action dict has no 'actions' key")

        chunk = np.asarray(action["actions"], dtype=float)
        if chunk.ndim == 1:
            chunk = chunk.reshape(1, -1)
        self._chunk = [np.asarray(row, dtype=float) for row in chunk]
        self.modelled_inference_ms += float(action.get("inference_ms", 0.0))
        self.replans += int(action.get("replans", 0))
        if not self._chunk:
            return StepResult(obs=self._raw_obs(), reward=0.0, done=True, info={})
        return self._advance(self._chunk.pop(0))

    def _advance(self, command: np.ndarray) -> StepResult:
        self.step_count += 1
        block = self._block()
        speed = float(np.linalg.norm(command))
        if speed > MAX_COMMAND_SPEED + 1e-9:
            # The actuator's limit, not a policy failure -- but asking for more
            # than the robot can deliver is recorded, because a benchmark should
            # not silently forgive an infeasible command.
            self.command_violation = True
            command = command * (MAX_COMMAND_SPEED / speed)
        block.velocity = block.velocity + np.array(command, dtype=float)
        v = float(np.linalg.norm(block.velocity))
        if v > MAX_COMMAND_SPEED:
            block.velocity = block.velocity * (MAX_COMMAND_SPEED / v)

        contacts: List[Any] = []
        substeps = max(1, int(round(CONTROL_DT / DT)))
        for _ in range(substeps):
            self.table._integrate(DT, contacts)
        self.table.time += CONTROL_DT
        for contact in contacts:
            self.peak_impact = max(self.peak_impact, contact.speed)
            if contact.damage:
                self.violation = True
        if block.x > self.frame[1] + OVERSHOOT_TOLERANCE:
            self.overshoot = True
        if not (BLOCK_RADIUS <= block.x <= self.table.width - BLOCK_RADIUS):
            self.violation = True
        return StepResult(obs=self._raw_obs(), reward=0.0, done=False, info={})

    # -- termination and result -------------------------------------------

    def _in_frame(self) -> bool:
        block = self._block()
        return (self.frame[0] - 0.01 <= block.x <= self.frame[1] + 0.01
                and abs(block.y - self.target[1]) <= FRAME_HALF_HEIGHT)

    def _settled(self) -> bool:
        return self._block().speed < SETTLED_SPEED

    def check_done(self, step_result: StepResult) -> bool:
        if step_result.done:
            return True
        if self._in_frame() and self._settled():
            return True
        if self.overshoot or self.violation:
            return True
        return self.step_count >= HORIZON

    def get_step_result(self, step_result: StepResult) -> Dict[str, Any]:
        success = bool(self._in_frame() and self._settled()
                       and not self.takeover and not self.vetoed
                       and not self.violation)
        if self.takeover:
            # The human is competent; the metric says so, and says who did it.
            assisted = True
        elif self.vetoed:
            assisted = bool(self.shadow_success)
        else:
            assisted = success
        latency = self.modelled_inference_ms
        if self.takeover:
            latency += 3000.0
        return {
            "success": success,
            "assisted_success": assisted,
            "safety_violation": bool(self.violation or self.command_violation),
            "latency_ms": float(latency),
            "takeovers": int(self.takeover),
            "false_vetoes": int(self.vetoed and bool(self.shadow_success)),
            "replans": int(self.replans),
            "steps": int(self.step_count),
            "peak_impact": float(self.peak_impact),
            "overshoot": bool(self.overshoot),
            "vetoed": bool(self.vetoed),
            "takeover": bool(self.takeover),
        }

    def _rollout_shadow(self, chunk: np.ndarray) -> bool:
        """Would the refused action have succeeded? Evaluated on a clone.

        The counterfactual has to be evaluated somewhere, and evaluating it on
        the live table would make the refusal change the world it is being
        scored against. A clone is the only place it can go.
        """
        block_id = int(self.task.get("task_idx", 0))
        clone = Table(self.table.width, self.table.height, self.table.blocks)
        contacts: List[Any] = []
        substeps = max(1, int(round(CONTROL_DT / DT)))
        for row in chunk:
            target = clone.get(block_id)
            target.velocity = target.velocity + np.array(row, dtype=float)
            for _ in range(substeps):
                clone._integrate(DT, contacts)
        end = clone.get(block_id)
        in_frame = (self.frame[0] - 0.01 <= end.x <= self.frame[1] + 0.01
                    and abs(end.y - self.target[1]) <= FRAME_HALF_HEIGHT)
        hard_hit = any(c.speed > DAMAGE_IMPACT_SPEED for c in contacts)
        return bool(in_frame and end.speed < SETTLED_SPEED and not hard_hit)
