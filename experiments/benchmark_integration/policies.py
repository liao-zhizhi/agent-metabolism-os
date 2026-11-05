"""The policies, with no harness in them: planning, confidence, friction recovery.

Kept free of ``vla_eval`` imports so that the mechanisms can be unit-tested on a
machine where the harness is not installed, and so that the servers in
``servers.py`` are thin: a server is a policy plus a wire contract, and if the
policy lived inside the server the wire contract would be untestable on its own.

The action is a delta velocity, the same convention LIBERO's OSC policy uses: the
policy adds ``(dvx, dvy)`` to the block and the environment integrates the
consequence. Planning under a friction belief ``mu`` is a braking-distance
inversion,

    v_required = sqrt(2 * mu * g * d),        delta = v_required - v

which is exact in continuous time. That is what makes a wrong belief legible:
the block ends at ``v_required**2 / (2 * mu_true * g)``, so the error in the
final position is the *ratio* ``mu_belief / mu_true``, and a belief that is 2x
too high overshoots by 2x rather than by a little.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

GRAVITY = 9.81
CONTROL_DT = 0.10
CHUNK_SIZE = 10
MAX_DELTA = 3.0
MAX_SPEED = 3.0
HORIZON = 30

#: Latency constants, the same three the sandbox prices, plus the human.
VLA_MS = 200.0
WITNESS_MS = 20.0
LOOKUP_MS = 5.0
HUMAN_MS = 3000.0

#: The confidence threshold of the baseline, as published practice.
CONF_THRESHOLD = 0.80

MU_FLOOR = 0.10
MU_CEILING = 0.90


@dataclass
class Obs:
    """The ten numbers the benchmark sends, parsed."""

    x: float
    y: float
    vx: float
    vy: float
    target_x: float
    target_y: float
    frame_x0: float
    frame_x1: float
    steps_remaining: int
    elapsed: float

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    @property
    def remaining(self) -> float:
        return self.target_x - self.x

    @classmethod
    def from_state(cls, state: Sequence[float]) -> "Obs":
        values = [float(v) for v in state]
        return cls(values[0], values[1], values[2], values[3],
                   values[4], values[5], values[6], values[7],
                   int(values[8]), values[9])


def required_speed(distance: float, mu: float) -> float:
    return math.sqrt(max(0.0, 2.0 * mu * GRAVITY * max(distance, 0.0)))


def plan_chunk(obs: Obs, mu: float, chunk_size: int = CHUNK_SIZE) -> np.ndarray:
    """One impulse to the braking speed, then coast -- executed open-loop.

    The rest of the chunk is zeros on purpose: a VLA chunk is a commitment, and
    the failure mode under drift is that the commitment was made under the old
    physics. A policy that re-planned every control step would be reading the
    world continuously, which is the capability the comparison is about, not a
    detail to give every arm for free.
    """
    chunk = np.zeros((chunk_size, 2), dtype=float)
    distance = obs.remaining
    if distance <= 0.005:
        return chunk
    need = required_speed(distance, mu) - obs.vx
    chunk[0, 0] = float(np.clip(need, -MAX_DELTA, MAX_DELTA))
    return chunk


def brake_and_grab(obs: Obs, mu: float) -> np.ndarray:
    """The action that stops the block where it is, as well as it can."""
    stop = -obs.vx if obs.speed > 1e-6 else 0.0
    return np.array([float(np.clip(stop, -MAX_DELTA, MAX_DELTA)), 0.0])


def estimate_friction(v_before: float, v_after: float, dt: float) -> Optional[float]:
    """Coulomb friction from one observed deceleration. ``None`` if unreadable.

    ``v_after < v_before`` and the difference is ``mu * g * dt``. There is no
    model in this estimate: it is the trajectory's own second difference, which
    is why it costs a comparison rather than a forward pass. It returns ``None``
    when the block sped up (it was pushed by something else, e.g. the decoy) or
    was already stopped, because in both cases the number would describe the
    contact rather than the table.
    """
    if dt <= 0.0:
        return None
    if v_before <= 1e-4:
        return None
    if v_after >= v_before - 1e-6:
        return None
    mu = (v_before - v_after) / (GRAVITY * dt)
    if not (0.02 <= mu <= 2.0):
        return None
    return float(np.clip(mu, MU_FLOOR, MU_CEILING))


@dataclass
class ConfidenceModel:
    """The policy's self-reported confidence, and why it is the wrong signal.

    Three ingredients, none of which observes friction:

    * a base rate around 0.93, which is where a competent policy sits on its
      training distribution;
    * ``alert``, raised when the policy *sees* a push fail and decayed every
      step after. It cannot name the cause, so its only available response is to
      become uncertain -- which is right after a failure and wrong before one;
    * Gaussian noise of six points, which is what puts a 0.8 threshold in the
      region where it fires on tasks that would have worked.
    """

    rng: np.random.Generator
    notice_prob: float = 0.75
    alert: float = 0.0
    decay: float = 0.15

    def sample(self) -> float:
        noise = float(self.rng.normal(0.0, 0.06))
        return float(np.clip(0.93 - 0.55 * self.alert + noise, 0.0, 0.999))

    def observe_outcome(self, succeeded: bool) -> None:
        if not succeeded and self.rng.random() < self.notice_prob:
            self.alert = 1.0
        self.alert = max(0.0, self.alert - self.decay)


@dataclass
class FrictionRegistry:
    """The compiled rules: friction bin -> the chunk profile that worked there.

    Keyed by the belief, not by the task, which is what lets one rule serve every
    block in the same regime and what lets a lookup replace a forward pass.
    """

    rules: dict = field(default_factory=dict)

    def key(self, mu: float) -> int:
        return int(round(mu * 100.0))

    def has(self, mu: float) -> bool:
        return self.key(mu) in self.rules

    def store(self, mu: float, chunk: np.ndarray) -> None:
        self.rules[self.key(mu)] = np.asarray(chunk, dtype=float).copy()

    def lookup(self, mu: float) -> Optional[np.ndarray]:
        chunk = self.rules.get(self.key(mu))
        return None if chunk is None else chunk.copy()

    def clear(self) -> None:
        self.rules.clear()
