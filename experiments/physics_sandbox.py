"""A 2-D table you can push blocks across: numpy rigid bodies, real impulses.

The solver is this module. ``pybullet`` and ``pymunk`` are *detected* and
reported, not silently swapped in: two solvers under one name means the numbers
in the CSV describe whichever one happened to be installed on the machine that
ran it. Neither is installed here, so the fallback below is what produced every
number in this directory, and the run says so on its first line:

    physics solver: numpy fallback (pybullet and pymunk are not installed)

A pybullet backend written against an SDK that cannot be imported is a backend
that cannot be tested, and shipping one would make the engine line a claim
rather than a fact.

The fallback is a top-down table: gravity acts into the page, so the only force
in the plane is Coulomb friction, and a block pushed once coasts and stops.
Circle/segment collisions, impulse resolution with restitution, and
momentum-conserving block/block contacts. What the tests pin are the properties
a solver must have rather than the values it happens to produce:

* momentum is conserved across a block/block contact (to integration error);
* a block pushed with the friction it believes in stops where it was aimed;
* friction above the belief stops it short, friction below overshoots --- and
  the overshoot is what reaches the wall, so damage is a consequence of the
  physics rather than a flag set by the caller;
* restitution is below one, so nothing gains energy from a bounce.

The table is a deliberate simplification of a pick-and-place cell, not a claim
about manipulation in general.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

GRAVITY = 9.81

#: Wall restitution and block restitution. Both below one; a contact that
#: returns more energy than it received would be a solver bug, and the test
#: suite asserts the bound rather than expecting it.
WALL_RESTITUTION = 0.35
BLOCK_RESTITUTION = 0.60

#: Impact speed above which a contact is recorded as damage. 0.8 m/s is the
#: order of the quasi-static contact limits in ISO/TS 15066: a contact a cell
#: would report, not a contact a cell would ignore.
DAMAGE_IMPACT_SPEED = 0.8

#: Integration. 5 ms with a velocity floor is enough for Coulomb friction to be
#: right to the millimetre over the 0.5 m pushes used here, and it is cheap
#: enough that a thousand pushes run in seconds.
DT = 0.005
MIN_SPEED = 5e-3
MAX_STEPS = 400          # 2 s: longer than any push in this task family


def available_sdks() -> List[str]:
    """Which 2-D rigid-body SDKs this process could import, if any."""
    found = []
    for name in ("pybullet", "pymunk"):
        try:
            __import__(name)
        except Exception:  # noqa: BLE001
            continue
        found.append(name)
    return found


def engine_name() -> str:
    """The solver that produced the numbers. Not the solver that was available."""
    return "numpy"


def engine_notice() -> str:
    """The one-line degradation notice, phrased so it cannot be missed."""
    sdks = available_sdks()
    if sdks:
        return ("physics solver: numpy ({} installed but not used -- swapping "
                "solvers would change every number under the same name)"
                .format(", ".join(sdks)))
    return ("physics solver: numpy fallback (pybullet and pymunk are not "
            "installed) -- circle/segment collisions, impulse resolution, "
            "Coulomb friction")


@dataclass
class Block:
    """A rigid disc on the table. Mass in kg, lengths in metres."""

    block_id: int
    pos: np.ndarray
    mass: float = 0.20
    radius: float = 0.030
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    friction: float = 0.35
    role: str = "target"          # "target" or "decoy"

    @property
    def x(self) -> float:
        return float(self.pos[0])

    @property
    def y(self) -> float:
        return float(self.pos[1])

    @property
    def speed(self) -> float:
        return float(np.linalg.norm(self.velocity))

    def clone(self) -> "Block":
        return Block(self.block_id, self.pos.copy(), self.mass, self.radius,
                     self.velocity.copy(), self.friction, self.role)


@dataclass(frozen=True)
class Contact:
    kind: str            # "wall_x0" | "wall_x1" | "wall_y0" | "wall_y1" | "block"
    block_id: int
    other_id: int
    speed: float

    @property
    def damage(self) -> bool:
        return self.speed > DAMAGE_IMPACT_SPEED


@dataclass
class PushReport:
    """What the rig can tell you about one push, whether or not you look."""

    block_id: int
    start: np.ndarray
    final: np.ndarray
    travel: float
    settled: bool
    duration: float
    contacts: List[Contact]
    impulse_speed: float
    contact_pos: Optional[np.ndarray] = None
    contact_speed: float = 0.0

    @property
    def peak_impact(self) -> float:
        return max((c.speed for c in self.contacts), default=0.0)

    @property
    def damaged(self) -> bool:
        return any(c.damage for c in self.contacts)

    @property
    def wall_contact(self) -> bool:
        return any(c.kind != "block" for c in self.contacts)

    @property
    def block_contact(self) -> bool:
        return any(c.kind == "block" for c in self.contacts)


class Table:
    """A rectangular table with discs on it and a pusher that applies impulses."""

    def __init__(self, width: float = 0.95, height: float = 0.70,
                 blocks: Optional[Sequence[Block]] = None) -> None:
        self.width = width
        self.height = height
        self.blocks: List[Block] = [b.clone() for b in (blocks or ())]
        self.time = 0.0
        #: Where and how fast the pushed block was when it first touched
        #: anything. This is what a trajectory log gives you and a final pose
        #: does not: a censored push still has an identifiable first contact.
        self.contact_trace: Optional[Tuple[np.ndarray, float]] = None

    # -- state ------------------------------------------------------------
    def get(self, block_id: int) -> Block:
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        raise KeyError("no block {}".format(block_id))

    def observe(self, block_id: int) -> Tuple[float, float, float]:
        """The measurement a camera would give: position and speed."""
        block = self.get(block_id)
        return float(block.pos[0]), float(block.pos[1]), float(np.linalg.norm(block.velocity))

    # -- dynamics ---------------------------------------------------------
    def push(self, block_id: int, speed: float,
             direction: Sequence[float] = (1.0, 0.0)) -> PushReport:
        """Give one block an impulsive speed, then integrate to rest."""
        block = self.get(block_id)
        unit = np.asarray(direction, dtype=float)
        norm = float(np.linalg.norm(unit))
        if norm < 1e-12:
            raise ValueError("push direction cannot be zero")
        unit = unit / norm
        block.velocity = unit * float(speed)
        start = block.pos.copy()
        self.contact_trace = None

        contacts: List[Contact] = []
        settled = False
        steps = 0
        for steps in range(1, MAX_STEPS + 1):
            self._integrate(DT, contacts)
            self.time += DT
            speed_now = float(np.linalg.norm(self.get(block_id).velocity))
            if speed_now < MIN_SPEED and not self._moving():
                settled = True
                break
        self._settle_velocities()

        final = self.get(block_id).pos.copy()
        trace_pos, trace_speed = (self.contact_trace
                                  if self.contact_trace is not None
                                  else (None, 0.0))
        return PushReport(block_id=block_id, start=start, final=final,
                          travel=float(final[0] - start[0]), settled=settled,
                          duration=steps * DT, contacts=contacts,
                          impulse_speed=float(speed),
                          contact_pos=trace_pos, contact_speed=trace_speed)

    def _moving(self) -> bool:
        return any(float(np.linalg.norm(b.velocity)) >= MIN_SPEED for b in self.blocks)

    def _settle_velocities(self) -> None:
        for block in self.blocks:
            if float(np.linalg.norm(block.velocity)) < MIN_SPEED:
                block.velocity = np.zeros(2)

    def _integrate(self, dt: float, contacts: List[Contact]) -> None:
        for block in self.blocks:
            speed = float(np.linalg.norm(block.velocity))
            if speed <= 0.0:
                continue
            # Coulomb friction: a constant deceleration opposing motion, which is
            # what stops the block at a predictable distance.
            decel = block.friction * GRAVITY * dt
            new_speed = speed - decel
            if new_speed <= 0.0:
                block.velocity = np.zeros(2)
            else:
                block.velocity = block.velocity * (new_speed / speed)
            block.pos = block.pos + block.velocity * dt
            self._walls(block, contacts)
        self._pairs(contacts)

    def _walls(self, block: Block, contacts: List[Contact]) -> None:
        r = block.radius
        for axis, low_name, high_name in ((0, "wall_x0", "wall_x1"),
                                          (1, "wall_y0", "wall_y1")):
            low, high = r, (self.width if axis == 0 else self.height) - r
            if block.pos[axis] < low:
                impact = abs(float(block.velocity[axis]))
                block.pos[axis] = low
                self._note_contact(block, impact)
                block.velocity[axis] = -block.velocity[axis] * WALL_RESTITUTION
                contacts.append(Contact(low_name, block.block_id, -1, impact))
            elif block.pos[axis] > high:
                impact = abs(float(block.velocity[axis]))
                block.pos[axis] = high
                self._note_contact(block, impact)
                block.velocity[axis] = -block.velocity[axis] * WALL_RESTITUTION
                contacts.append(Contact(high_name, block.block_id, -1, impact))

    def _note_contact(self, block: Block, speed: float) -> None:
        if self.contact_trace is None:
            self.contact_trace = (block.pos.copy(), float(speed))

    def _pairs(self, contacts: List[Contact]) -> None:
        count = len(self.blocks)
        for i in range(count):
            for j in range(i + 1, count):
                a, b = self.blocks[i], self.blocks[j]
                delta = b.pos - a.pos
                distance = float(np.linalg.norm(delta))
                overlap = a.radius + b.radius - distance
                if overlap <= 0.0 or distance < 1e-12:
                    continue
                normal = delta / distance
                # Positional correction split by inverse mass, so the lighter
                # body moves further and the pair does not sink into each other.
                total_inv = 1.0 / a.mass + 1.0 / b.mass
                a.pos = a.pos - normal * (overlap * (1.0 / a.mass) / total_inv)
                b.pos = b.pos + normal * (overlap * (1.0 / b.mass) / total_inv)

                relative = float(np.dot(b.velocity - a.velocity, normal))
                if relative >= 0.0:
                    continue       # separating already: no impulse, no damage
                impulse = -(1.0 + BLOCK_RESTITUTION) * relative / total_inv
                a.velocity = a.velocity - normal * (impulse / a.mass)
                b.velocity = b.velocity + normal * (impulse / b.mass)
                self._note_contact(a, abs(relative))
                contacts.append(Contact("block", a.block_id, b.block_id,
                                        abs(relative)))


# ---------------------------------------------------------------------------
# Task definition: five blocks, five frames, one decoy
# ---------------------------------------------------------------------------

LANES = (0.14, 0.26, 0.38, 0.50, 0.62)
BLOCK_START_X = 0.16
FRAME_X0, FRAME_X1 = 0.60, 0.72
FRAME_HALF_HEIGHT = 0.035
BLOCK_RADIUS = 0.030
DECOY_LANE = LANES[2]
DECOY_X = 0.86


@dataclass(frozen=True)
class Job:
    """One push in a sequence: which block, and where it is supposed to end."""

    sequence: int
    step: int
    block_id: int
    target_x: float
    lane: float

    @property
    def target(self) -> np.ndarray:
        return np.array([self.target_x, self.lane])

    def in_frame(self, pos: Sequence[float]) -> bool:
        x, y = float(pos[0]), float(pos[1])
        return (FRAME_X0 - 0.01 <= x <= FRAME_X1 + 0.01
                and abs(y - self.lane) <= FRAME_HALF_HEIGHT)


def build_sequence(sequence: int) -> Tuple[List[Block], List[Job]]:
    blocks = [Block(i, np.array([BLOCK_START_X, lane]), radius=BLOCK_RADIUS)
              for i, lane in enumerate(LANES)]
    blocks.append(Block(len(LANES), np.array([DECOY_X, DECOY_LANE]),
                        radius=BLOCK_RADIUS, role="decoy"))
    jobs = [Job(sequence, step, block.block_id,
                0.5 * (FRAME_X0 + FRAME_X1), lane)
            for step, (block, lane) in enumerate(zip(blocks[:len(LANES)], LANES))]
    return blocks, jobs


# ---------------------------------------------------------------------------
# The world the arms are compared on: coherent drift, shared by every arm
# ---------------------------------------------------------------------------


NOMINAL_FRICTION = 0.35
SLIPPERY = 0.18
ROUGH = 0.54
DRIFT_WINDOW = 50            # pushes a regime lasts once it starts

#: The floor of the physically plausible range. Used to size experiments so
#: that their outcome cannot be censored by a wall, not as a belief.
MU_FLOOR = 0.10


@dataclass
class DriftSchedule:
    """Which friction the world has, push by push.

    Not i.i.d. A regime, once it arrives, holds for ``DRIFT_WINDOW`` pushes: the
    world does not forget, and a system that detects the change does not thereby
    end it. Every arm sees the identical schedule, which is the only way the
    comparison is about the arms.
    """

    frictions: List[float]
    events: List[Tuple[int, float]]

    @classmethod
    def build(cls, pushes: int, hazard: float, rng) -> "DriftSchedule":
        frictions: List[float] = []
        events: List[Tuple[int, float]] = []
        current = NOMINAL_FRICTION
        remaining = 0
        alternator = 0
        for index in range(pushes):
            if remaining == 0 and rng.random() < hazard:
                alternator += 1
                current = SLIPPERY if alternator % 2 else ROUGH
                remaining = DRIFT_WINDOW
                events.append((index, current))
            elif remaining == 0 and index > 0:
                current = NOMINAL_FRICTION
            if remaining > 0:
                remaining -= 1
            frictions.append(current)
        return cls(frictions, events)

    def at(self, index: int) -> float:
        return self.frictions[min(index, len(self.frictions) - 1)]

    def in_drift(self, index: int) -> bool:
        return abs(self.at(index) - NOMINAL_FRICTION) > 1e-9


# ---------------------------------------------------------------------------
# Planning, and the two ways of being wrong about friction
# ---------------------------------------------------------------------------


def speed_for_travel(travel: float, friction: float) -> float:
    """The launch speed that coasts exactly ``travel`` under Coulomb friction."""
    return math.sqrt(max(0.0, 2.0 * friction * GRAVITY * travel))


def friction_from_travel(speed: float, travel: float) -> Optional[float]:
    """Invert a witnessed travel. ``None`` when the witness cannot identify it.

    A block that hit a wall has a travel that is a property of the table, not of
    the friction, so the inversion is refused rather than clamped: a clamped
    number would be a confident wrong answer, which is the failure mode this
    entire comparison is about.
    """
    if travel <= 1e-4 or speed <= 0.0:
        return None
    return speed * speed / (2.0 * travel * GRAVITY)


def friction_from_contact(speed: float, contact_speed: float,
                          distance: float) -> Optional[float]:
    """Invert a *censored* push: friction from the state at first contact.

    A push that ends against a wall loses its final pose as evidence, but not
    its trajectory. Deceleration is constant, so two points on the way --
    the launch and the moment of contact -- determine it:

        v_c^2 = v_0^2 - 2 * mu * g * d_contact

    This is the difference between reading a log and reading a photograph, and
    it is why the interface does not need a human to learn a number.
    """
    if distance <= 5e-3 or speed <= 0.0 or contact_speed >= speed:
        return None
    mu = (speed * speed - contact_speed * contact_speed) / (2.0 * distance * GRAVITY)
    return mu if mu > 0.0 else None


def friction_from_report(report: PushReport, speed: float) -> Optional[float]:
    """The friction a witness log identifies, or ``None`` if it identifies none."""
    if report.contact_pos is not None:
        distance = float(np.linalg.norm(report.contact_pos - report.start))
        estimate = friction_from_contact(speed, report.contact_speed, distance)
        if estimate is not None:
            return estimate
    return friction_from_travel(speed, report.travel)


# ---------------------------------------------------------------------------
# Latency calibration
# ---------------------------------------------------------------------------
#
# These are the numbers the economics is denominated in. They are *assumptions*,
# not measurements, and they are named so a reader can disagree with a constant
# instead of having to reverse-engineer a unit. The one thing they are not is
# arbitrary: each is the cost of a specific operation a deployed cell performs.
#
#   200 ms  a full vision-language-action forward pass, i.e. re-deriving the
#           action from the camera image. This is the operation every arm needs
#           and the one that dominates a 5 Hz control loop.
#    20 ms  a mechanical witness: hash the expected observation, compare the
#           observed point cloud, decide PASS/FAIL. No network, no model.
#     5 ms  a compiled-rule lookup plus emitting the warrant record.
#  3000 ms  a human takeover: detect that a human is needed, page them, hand
#           over teleoperation, hand back. The 3 s is the optimistic end -- it
#           excludes the human's own reaction time.
#
# A note on honesty: 200 ms and 20 ms are a factor of ten apart because a VLA
# forward pass and a point-cloud comparison are a factor of ten apart on the
# hardware this is aimed at. The comparison would be much less interesting at
# 30 ms and 20 ms, and a reader should treat any result that depends on the
# exact ratio as a result about the calibration rather than about the interface.

VLA_MS = 200.0
WITNESS_MS = 20.0
LOOKUP_MS = 5.0
HUMAN_MS = 3000.0

#: The safety filter's threshold, as published practice: below 0.8 confidence,
#: do not act, escalate.
CONF_THRESHOLD = 0.80


@dataclass
class PushOutcome:
    """One row of the CSV: a job, what the arm did, and what it cost."""

    arm: str
    sequence: int
    step: int
    push_index: int
    mu_true: float
    mu_used: float
    success: bool
    damaged: bool
    attempts: int
    latency_ms: float
    latency_vla_ms: float
    latency_witness_ms: float
    latency_lookup_ms: float
    latency_human_ms: float
    refused: bool
    false_veto: bool
    in_drift: bool
    peak_impact: float
    confidence: float = 1.0


@dataclass
class JobCost:
    """Latency of one job, accumulated by the arm that spent it."""

    vla: float = 0.0
    witness: float = 0.0
    lookup: float = 0.0
    human: float = 0.0

    @property
    def total(self) -> float:
        return self.vla + self.witness + self.lookup + self.human


def _blind_push(table: Table, job: Job, belief: float) -> Tuple[PushReport, float]:
    """Apply the impulse a planner with this friction belief would compute."""
    block = table.get(job.block_id)
    distance = float(job.target_x - block.pos[0])
    if distance <= 1e-3:
        distance = 0.0
    speed = speed_for_travel(distance, belief)
    report = table.push(job.block_id, speed)
    return report, speed


class Arm:
    """Base class. Every arm is handed the same jobs and the same drift schedule."""

    name = "arm"

    def __init__(self, seed: int = 0) -> None:
        self.rng = np.random.default_rng(seed)
        self.belief = NOMINAL_FRICTION
        self.human_calls = 0
        self.refusals = 0

    def begin_sequence(self, sequence: int) -> None:
        """Hook for state that is scoped to a five-block sequence."""

    def execute(self, table: Table, job: Job, mu_true: float,
                push_index: int) -> PushOutcome:
        raise NotImplementedError


class BlindVLA(Arm):
    """A: the policy acts, nothing checks it.

    The friction belief is set once and never revised, which is precisely the
    property under test: without a witness there is no signal that would revise
    it, so "the model drifts" is not a bug an operator can wait out.
    """

    name = "A_blind_vla"

    def execute(self, table, job, mu_true, push_index) -> PushOutcome:
        # Guard: a blind arm will not even notice a wall it is aiming at.
        report, _ = _blind_push(table, job, self.belief)
        success = bool(job.in_frame(report.final) and report.settled)
        return PushOutcome(
            arm=self.name, sequence=job.sequence, step=job.step,
            push_index=push_index, mu_true=mu_true, mu_used=self.belief,
            success=success, damaged=report.damaged, attempts=1,
            latency_ms=VLA_MS, latency_vla_ms=VLA_MS, latency_witness_ms=0.0,
            latency_lookup_ms=0.0, latency_human_ms=0.0,
            refused=False, false_veto=False, in_drift=False,
            peak_impact=report.peak_impact, confidence=1.0)


class SafeVLAFilter(Arm):
    """C: the industry-standard baseline. Act only if the model is confident.

    The filter is not a straw man, and it is not stupid: it is the best you can
    do when the only signal available is the policy's own confidence. Its two
    failure modes are structural rather than tuning problems --

    * the policy becomes *more* confident after a success, so a drift that
      starts right after a good run is exactly the situation where it will not
      escalate;
    * conversely, a drift that makes the policy uncertain after a *good* plan
      produces refusals on states where the action would have been fine, and
      every refusal costs a human takeover.

    Both are consequences of confidence being a property of the model rather
    than of the world, which is the claim the experiment is meant to separate
    from "our filter has better thresholds".
    """

    name = "C_safe_vla"

    def __init__(self, seed: int = 0, notice_prob: float = 0.75,
                 decay: float = 0.25) -> None:
        super().__init__(seed)
        self.alert = 0.0
        self.notice_prob = notice_prob
        self.decay = decay

    def _confidence(self) -> float:
        # A self-reported confidence with a 6-point standard deviation. The
        # noise is not decoration: it is why a 0.8 threshold fires on tasks that
        # would have succeeded, and each of those firings costs a takeover.
        noise = float(self.rng.normal(0.0, 0.06))
        return float(np.clip(0.93 - 0.55 * self.alert + noise, 0.0, 0.999))

    def execute(self, table, job, mu_true, push_index) -> PushOutcome:
        confidence = self._confidence()

        if confidence < CONF_THRESHOLD:
            self.refusals += 1
            self.human_calls += 1
            # Counterfactual, evaluated on a clone so the refusal still costs
            # what a refusal costs: would the blind action have worked?
            shadow = Table(table.width, table.height, table.blocks)
            shadow_report, _ = _blind_push(shadow, job, self.belief)
            would_work = bool(job.in_frame(shadow_report.final)
                              and shadow_report.settled)
            # The human pushes the block to its frame. Recalibration happens as
            # a side effect of the takeover, which is the expensive way to
            # learn a number the witness would have given for 20 ms.
            block = table.get(job.block_id)
            block.pos = np.array([job.target_x, job.lane])
            block.velocity = np.zeros(2)
            success = True
            self.belief = mu_true
            self.alert = 0.0
            return PushOutcome(
                arm=self.name, sequence=job.sequence, step=job.step,
                push_index=push_index, mu_true=mu_true, mu_used=mu_true,
                success=success, damaged=False, attempts=1,
                latency_ms=HUMAN_MS, latency_vla_ms=0.0,
                latency_witness_ms=0.0, latency_lookup_ms=0.0,
                latency_human_ms=HUMAN_MS, refused=True,
                false_veto=bool(would_work), in_drift=False,
                peak_impact=0.0, confidence=confidence)

        report, _ = _blind_push(table, job, self.belief)
        success = bool(job.in_frame(report.final) and report.settled)
        if not success and self.rng.random() < self.notice_prob:
            # The policy does notice gross failures sometimes -- a block that
            # stopped a hand's width short is hard to miss. It is the *cause*
            # it cannot name, so the only response available is to escalate.
            self.alert = 1.0
        self.alert = max(0.0, self.alert - self.decay)
        return PushOutcome(
            arm=self.name, sequence=job.sequence, step=job.step,
            push_index=push_index, mu_true=mu_true, mu_used=self.belief,
            success=success, damaged=report.damaged, attempts=1,
            latency_ms=VLA_MS, latency_vla_ms=VLA_MS,
            latency_witness_ms=0.0, latency_lookup_ms=0.0,
            latency_human_ms=0.0, refused=False, false_veto=False,
            in_drift=False, peak_impact=report.peak_impact,
            confidence=confidence)


class CausalInterface(Arm):
    """B: act, witness, and only then decide whether the rule may be reused.

    The registry is keyed by the friction belief, not by the task. That is what
    makes the cost collapse to a lookup: the same rule serves every block in the
    same regime. It is also what makes drift expensive in a *bounded* way -- a
    stale key fires once, the witness says FAIL, and the observed travel inverts
    to a new friction (20 ms of evidence instead of a 3 s human), which is a
    number that can be written down.

    The witness is post-hoc by nature. It cannot un-damage a block; it can stop
    the *second* wrong push and every one after it. The report says so in the
    damage column rather than hoping nobody checks.
    """

    name = "B_causal_interface"

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.registry: dict = {}
        self.max_attempts = 3

    def _probe(self, table: Table, job: Job, cost: JobCost):
        """Buy a short push whose outcome cannot be censored by a wall.

        The point of a probe is that its travel is a function of friction and
        nothing else, so the speed is chosen against the *lowest* plausible
        friction: even then the block stops short of the table edge. A probe
        that could reach a wall would be a probe that can lie.
        """
        block = table.get(job.block_id)
        available = (table.width - block.radius - 0.02) - float(block.pos[0])
        if available < 0.03:
            return None
        target = 0.6 * available
        speed = speed_for_travel(target, MU_FLOOR)
        report = table.push(job.block_id, speed)
        cost.witness += WITNESS_MS
        return report

    def execute(self, table, job, mu_true, push_index) -> PushOutcome:
        cost = JobCost()
        attempts = 0
        success = False
        damaged = False
        peak = 0.0

        while attempts < self.max_attempts:
            attempts += 1
            block = table.get(job.block_id)
            distance = float(job.target_x - block.pos[0])

            if distance <= 0.02:
                # At the target, or past it. Past it is the one failure a
                # forward push cannot undo, and pretending otherwise by
                # grinding out retries would be a worse report than a FAIL.
                success = bool(job.in_frame(block.pos))
                break

            key = round(self.belief, 2)
            compiled = key in self.registry
            if compiled:
                cost.lookup += LOOKUP_MS
            else:
                # No rule for this regime yet: derive it, at VLA prices.
                cost.vla += VLA_MS
                self.registry[key] = True

            report, speed = _blind_push(table, job, self.belief)
            peak = max(peak, report.peak_impact)
            damaged = damaged or report.damaged

            # The witness: 20 ms to read the final pose and compare it to the
            # frame the plan promised. This is the whole interface.
            cost.witness += WITNESS_MS
            success = bool(job.in_frame(report.final) and report.settled)
            if success:
                break

            # FAIL. The travel is the evidence; inverting it costs arithmetic,
            # not a forward pass. The inversion is only valid when the travel
            # was caused by friction alone -- a block that found a wall or a
            # neighbour travelled a distance that describes the table. In that
            # case the layer refuses to name a number and buys a small,
            # identifiable experiment instead of a confident wrong one.
            estimate = friction_from_report(report, speed)
            if estimate is None:
                probe = self._probe(table, job, cost)
                if probe is not None:
                    peak = max(peak, probe.peak_impact)
                    damaged = damaged or probe.damaged
                    estimate = friction_from_report(probe, probe.impulse_speed)
            if estimate is None:
                # Nothing identifiable can be bought from here. Reporting FAIL
                # is the only honest option left; a guessed friction would be
                # the exact failure this arm exists to avoid.
                break
            self.belief = float(np.clip(estimate, 0.05, 0.9))
            self.registry.clear()
            if attempts >= self.max_attempts:
                break
            cost.vla += VLA_MS          # re-derive under the new belief

        return PushOutcome(
            arm=self.name, sequence=job.sequence, step=job.step,
            push_index=push_index, mu_true=mu_true, mu_used=self.belief,
            success=success, damaged=damaged, attempts=attempts,
            latency_ms=cost.total, latency_vla_ms=cost.vla,
            latency_witness_ms=cost.witness, latency_lookup_ms=cost.lookup,
            latency_human_ms=cost.human,
            # This arm never refuses an action and never transfers control: it
            # acts, then reads the result. A refusal would be a different
            # design, and one this comparison is meant to price.
            refused=False, false_veto=False, in_drift=False,
            peak_impact=peak)


# ---------------------------------------------------------------------------
# Experiment driver
# ---------------------------------------------------------------------------


@dataclass
class ArmResult:
    name: str
    records: List[PushOutcome]
    sequence_success: List[bool]
    recovery: List[int]

    @property
    def pushes(self) -> int:
        return len(self.records)

    @property
    def successes(self) -> int:
        return sum(1 for r in self.records if r.success)

    @property
    def sequence_rate(self) -> float:
        if not self.sequence_success:
            return 0.0
        return sum(self.sequence_success) / len(self.sequence_success)

    @property
    def push_rate(self) -> float:
        return self.successes / self.pushes if self.pushes else 0.0

    @property
    def damage_events(self) -> int:
        return sum(1 for r in self.records if r.damaged)

    @property
    def human_calls(self) -> int:
        return sum(1 for r in self.records if r.refused and r.latency_human_ms > 0)

    @property
    def false_vetoes(self) -> int:
        return sum(1 for r in self.records if r.false_veto)

    @property
    def refusals(self) -> int:
        return sum(1 for r in self.records if r.refused)

    @property
    def total_ms(self) -> float:
        return float(sum(r.latency_ms for r in self.records))

    @property
    def mean_ms(self) -> float:
        return self.total_ms / self.pushes if self.pushes else 0.0

    @property
    def ms_per_success(self) -> float:
        return self.total_ms / self.successes if self.successes else float("inf")

    @property
    def ms_per_sequence(self) -> float:
        n = len(self.sequence_success)
        return self.total_ms / n if n else 0.0

    def component_ms(self) -> dict:
        keys = ("latency_vla_ms", "latency_witness_ms",
                "latency_lookup_ms", "latency_human_ms")
        return {k.replace("latency_", "").replace("_ms", ""):
                float(sum(getattr(r, k) for r in self.records)) for k in keys}

    def drift_summary(self) -> dict:
        drift_records = [r for r in self.records if r.in_drift]
        if not drift_records:
            return {"pushes": 0, "success_rate": 0.0, "mean_ms": 0.0,
                    "recovery": 0.0}
        return {
            "pushes": len(drift_records),
            "success_rate": sum(1 for r in drift_records if r.success) / len(drift_records),
            "mean_ms": float(sum(r.latency_ms for r in drift_records)) / len(drift_records),
            "recovery": float(np.mean(self.recovery)) if self.recovery else 0.0,
        }


def run_arm(arm: Arm, schedule: DriftSchedule, sequences: int) -> ArmResult:
    records: List[PushOutcome] = []
    sequence_success: List[bool] = []
    index = 0
    for seq in range(sequences):
        blocks, jobs = build_sequence(seq)
        table = Table(blocks=blocks)
        arm.begin_sequence(seq)
        ok = True
        for job in jobs:
            mu_true = schedule.at(index)
            for block in table.blocks:
                block.friction = mu_true
            outcome = arm.execute(table, job, mu_true, index)
            outcome.in_drift = schedule.in_drift(index)
            records.append(outcome)
            ok = ok and outcome.success
            index += 1
        sequence_success.append(ok)

    # Recovery: pushes from a drift onset until the arm succeeds again. This is
    # the only number that distinguishes "notices" from "recovers".
    recovery: List[int] = []
    for start, _ in schedule.events:
        window = range(start, min(start + DRIFT_WINDOW, len(records)))
        for offset, idx in enumerate(window):
            if records[idx].success:
                recovery.append(offset + 1)
                break
        else:
            recovery.append(DRIFT_WINDOW)
    return ArmResult(arm.name, records, sequence_success, recovery)


def summarise(results: Sequence[ArmResult]) -> dict:
    summary = {}
    for result in results:
        summary[result.name] = {
            "pushes": result.pushes,
            "push_success_rate": result.push_rate,
            "sequence_success_rate": result.sequence_rate,
            "damage_events": result.damage_events,
            "damage_per_1000": 1000.0 * result.damage_events / max(result.pushes, 1),
            "human_calls": result.human_calls,
            "refusals": result.refusals,
            "false_vetoes": result.false_vetoes,
            "false_veto_rate": (result.false_vetoes / result.refusals
                                if result.refusals else 0.0),
            "total_latency_ms": result.total_ms,
            "mean_latency_ms": result.mean_ms,
            "ms_per_success": result.ms_per_success,
            "ms_per_sequence": result.ms_per_sequence,
            "latency_components_ms": result.component_ms(),
            "drift": result.drift_summary(),
        }
    return summary


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

PALETTE = {
    "A_blind_vla": "#8c8c8c",
    "C_safe_vla": "#d1495b",
    "B_causal_interface": "#2a6f97",
}
LABEL = {
    "A_blind_vla": "A  blind VLA (no check)",
    "C_safe_vla": "C  Safe-VLA filter (conf >= 0.8)",
    "B_causal_interface": "B  causal interface (witness + rules)",
}


def print_report(summary: dict, schedule: DriftSchedule, sequences: int) -> None:
    order = ["A_blind_vla", "C_safe_vla", "B_causal_interface"]
    print()
    print("=" * 96)
    print("COHERENT DRIFT  (window = {} pushes, {} windows in {} pushes)"
          .format(DRIFT_WINDOW, len(schedule.events), len(schedule.frictions)))
    print("=" * 96)
    header = ("{:<34}{:>10}{:>10}{:>10}{:>10}{:>12}")
    print(header.format("arm", "push ok", "seq ok", "dmg/1k", "human", "ms/push"))
    print("-" * 96)
    for name in order:
        s = summary[name]
        print(header.format(
            LABEL[name],
            "{:.1%}".format(s["push_success_rate"]),
            "{:.1%}".format(s["sequence_success_rate"]),
            "{:.1f}".format(s["damage_per_1000"]),
            "{}".format(s["human_calls"]),
            "{:.1f}".format(s["mean_latency_ms"])))

    print()
    print("LATENCY BUDGET, ms (totals over {} pushes)".format(
        summary[order[0]]["pushes"]))
    print("-" * 96)
    header = "{:<34}{:>12}{:>12}{:>12}{:>12}{:>14}"
    print(header.format("arm", "vla", "witness", "lookup", "human", "total"))
    for name in order:
        c = summary[name]["latency_components_ms"]
        print(header.format(
            LABEL[name],
            "{:.0f}".format(c["vla"]), "{:.0f}".format(c["witness"]),
            "{:.0f}".format(c["lookup"]), "{:.0f}".format(c["human"]),
            "{:.0f}".format(summary[name]["total_latency_ms"])))

    print()
    c = summary["C_safe_vla"]
    b = summary["B_causal_interface"]
    print("SAFE FILTER vs CAUSAL INTERFACE")
    print("-" * 96)
    print("  latency per completed sequence : C {:>9.0f} ms   B {:>9.0f} ms   ({:+.1f}%)"
          .format(c["ms_per_sequence"], b["ms_per_sequence"],
                  100.0 * (b["ms_per_sequence"] - c["ms_per_sequence"])
                  / max(c["ms_per_sequence"], 1e-9)))
    print("  latency per successful push   : C {:>9.1f} ms   B {:>9.1f} ms   ({:+.1f}%)"
          .format(c["ms_per_success"], b["ms_per_success"],
                  100.0 * (b["ms_per_success"] - c["ms_per_success"])
                  / max(c["ms_per_success"], 1e-9)))
    print("  sequence success             : C {:>9.1%}      B {:>9.1%}"
          .format(c["sequence_success_rate"], b["sequence_success_rate"]))
    print("  push success                 : C {:>9.1%}      B {:>9.1%}"
          .format(c["push_success_rate"], b["push_success_rate"]))
    print("  damage per 1000 pushes       : C {:>9.1f}       B {:>9.1f}"
          .format(c["damage_per_1000"], b["damage_per_1000"]))
    print("  human takeovers              : C {:>9d}       B {:>9d}"
          .format(c["human_calls"], b["human_calls"]))
    print("  false vetoes / refusals      : C {:>4d}/{:<4d}        B {:>4d}/{:<4d}"
          .format(c["false_vetoes"], c["refusals"],
                  b["false_vetoes"], b["refusals"]))
    print("  mean pushes to recover       : C {:>9.1f}       B {:>9.1f}"
          .format(c["drift"].get("recovery", 0.0), b["drift"].get("recovery", 0.0)))
    print("  push success inside drift    : C {:>9.1%}      B {:>9.1%}"
          .format(c["drift"].get("success_rate", 0.0),
                  b["drift"].get("success_rate", 0.0)))
    print("  mean latency inside drift    : C {:>8.0f} ms   B {:>8.0f} ms"
          .format(c["drift"].get("mean_ms", 0.0), b["drift"].get("mean_ms", 0.0)))
    print("=" * 96)
    print("Caveat that the numbers cannot state for themselves: the witness is")
    print("post-hoc. Every damage count above includes the first push of each")
    print("drift window, which no interface can prevent -- B bounds the damage")
    print("to one push per window; A and C do not bound it at all.")
    print()


def write_csv(results: Sequence[ArmResult], path: str) -> None:
    fields = list(PushOutcome.__dataclass_fields__.keys())
    with open(path, "w", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for result in results:
            for record in result.records:
                row = []
                for key in fields:
                    value = getattr(record, key)
                    if isinstance(value, bool):
                        row.append("1" if value else "0")
                    elif isinstance(value, float):
                        row.append("{:.6g}".format(value))
                    else:
                        row.append(str(value))
                handle.write(",".join(row) + "\n")


def write_json(summary: dict, schedule: DriftSchedule, sequences: int,
               path: str, engine: str) -> None:
    import json
    payload = {
        "engine": engine,
        "sdks_available": available_sdks(),
        "pushes": summary["A_blind_vla"]["pushes"],
        "sequences": sequences,
        "drift": {
            "window": DRIFT_WINDOW,
            "hazard_per_push": EVENTS_HAZARD,
            "nominal_friction": NOMINAL_FRICTION,
            "regimes": sorted({round(f, 3) for f in schedule.frictions}),
            "events": [{"push_index": i, "friction": round(mu, 3)}
                       for i, mu in schedule.events],
            "fraction_of_pushes_in_drift":
                sum(1 for f in schedule.frictions
                    if abs(f - NOMINAL_FRICTION) > 1e-9) / len(schedule.frictions),
        },
        "latency_model_ms": {
            "vla_forward_pass": VLA_MS,
            "mechanical_witness": WITNESS_MS,
            "compiled_lookup": LOOKUP_MS,
            "human_takeover": HUMAN_MS,
            "confidence_threshold": CONF_THRESHOLD,
            "note": ("assumed constants, not measured on hardware; see the "
                     "module docstring for what each operation is"),
        },
        "physics": {
            "damage_impact_speed_m_s": DAMAGE_IMPACT_SPEED,
            "wall_restitution": WALL_RESTITUTION,
            "block_restitution": BLOCK_RESTITUTION,
            "dt_s": DT,
            "table_m": [0.95, 0.70],
        },
        "arms": summary,
    }
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _rolling(values: Sequence[float], window: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if len(arr) < window:
        window = max(1, len(arr))
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="valid")


def plot(results: Sequence[ArmResult], schedule: DriftSchedule, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.0))

    # (a) latency per push, with the drift windows shaded.
    ax = axes[0][0]
    for result in results:
        series = _rolling([r.latency_ms for r in result.records], 25)
        ax.plot(np.arange(len(series)), series, color=PALETTE[result.name],
                lw=1.6, label=LABEL[result.name])
    for start, mu in schedule.events:
        ax.axvspan(start, min(start + DRIFT_WINDOW, results[0].pushes),
                   color="#ffd166", alpha=0.25, lw=0)
    ax.set_yscale("log")
    ax.set_xlabel("push index")
    ax.set_ylabel("latency per push, ms (rolling mean, 25)")
    ax.set_title("(a) cost per action; shaded = coherent drift window")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.3, which="both")

    # (b) the economics: latency spent against successes bought.
    ax = axes[0][1]
    for result in results:
        spent, bought = 0.0, 0
        xs, ys = [0], [0.0]
        for record in result.records:
            spent += record.latency_ms
            if record.success:
                bought += 1
                xs.append(bought)
                ys.append(spent / 1000.0)
        ax.plot(xs, ys, color=PALETTE[result.name], lw=1.8,
                label="{}: {:.0f} ms/success".format(
                    result.name[0], result.ms_per_success))
    ax.set_xlabel("successful pushes")
    ax.set_ylabel("cumulative latency, s")
    ax.set_title("(b) cumulative cost against successes bought")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)

    # (c) success and damage.
    ax = axes[1][0]
    names = [r.name for r in results]
    xs = np.arange(len(names))
    width = 0.36
    push = [r.push_rate for r in results]
    seq = [r.sequence_rate for r in results]
    ax.bar(xs - width / 2, push, width, label="push success",
           color=[PALETTE[n] for n in names], alpha=0.95)
    ax.bar(xs + width / 2, seq, width, label="sequence success (5/5)",
           color=[PALETTE[n] for n in names], alpha=0.45)
    ax.set_xticks(xs)
    ax.set_xticklabels([LABEL[n].split("  ")[0] for n in names], fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate")
    ax.set_title("(c) task success")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3, axis="y")
    for i, r in enumerate(results):
        ax.text(i, 1.01, "{:.0f} dmg/1k".format(
            1000.0 * r.damage_events / max(r.pushes, 1)), ha="center", fontsize=8)

    # (d) where the latency actually goes.
    ax = axes[1][1]
    short = {"A_blind_vla": "A blind", "C_safe_vla": "C safe-VLA",
             "B_causal_interface": "B causal"}
    names = [short[r.name] for r in results]
    parts = [("vla", "#4c78a8", "VLA forward pass"),
             ("witness", "#59a14f", "witness"),
             ("lookup", "#bdbdbd", "compiled lookup"),
             ("human", "#d1495b", "human takeover")]
    left = np.zeros(len(results))
    for key, color, label in parts:
        values = np.array([r.component_ms()[key] / 1000.0 for r in results])
        ax.barh(names, values, left=left, color=color, label=label)
        left += values
    for i, total in enumerate(left):
        ax.text(total * 1.02, i, "{:.1f} s".format(total), va="center", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlim(1.0, max(left) * 4)
    ax.set_xlabel("total latency over the run, s (log)")
    ax.set_title("(d) where the latency goes\n"
                 "blue = VLA, green = witness, grey = lookup, red = human")
    ax.grid(alpha=0.3, axis="x", which="both")

    fig.suptitle(
        "2-D push sandbox: cost, success and damage under non-i.i.d. friction drift",
        fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=150)


#: Recorded so the JSON can state the hazard it was run at without a global.
EVENTS_HAZARD = 0.01

#: A single seed is an anecdote with a decimal point. The sweep re-runs the
#: comparison at three drift frequencies and three seeds each, so the ordering
#: the report claims can be checked for being a property of the mechanism
#: rather than of one random stream.
SWEEP_HAZARDS = (0.0, 0.005, 0.01, 0.02)
SWEEP_SEEDS = (7, 11, 13)


def run_sweep(pushes: int) -> list:
    """Latency and damage as a function of how often the world moves.

    The x-axis of the result is the *realised* share of pushes spent in a
    drifted regime, not the hazard. A hazard large enough to start a new window
    before the previous one ends saturates the drift share, and plotting the
    hazard there would read as a plateau in the mechanism.
    """
    sequences = max(1, pushes // len(LANES))
    rows = []
    for hazard in SWEEP_HAZARDS:
        for seed in SWEEP_SEEDS:
            schedule = S_drift(pushes, hazard, seed)
            share = sum(1 for f in schedule.frictions
                        if abs(f - NOMINAL_FRICTION) > 1e-9) / len(schedule.frictions)
            for arm_factory in (S_blind, S_safe, S_causal):
                arm = arm_factory(seed)
                result = run_arm(arm, schedule, sequences)
                rows.append({
                    "hazard": hazard,
                    "drift_share": share,
                    "seed": seed,
                    "arm": result.name,
                    "pushes": result.pushes,
                    "push_success_rate": result.push_rate,
                    "sequence_success_rate": result.sequence_rate,
                    "damage_per_1000": 1000.0 * result.damage_events
                                       / max(result.pushes, 1),
                    "human_calls": result.human_calls,
                    "false_vetoes": result.false_vetoes,
                    "mean_latency_ms": result.mean_ms,
                    "ms_per_success": result.ms_per_success,
                })
    return rows


def S_drift(pushes, hazard, seed):
    return DriftSchedule.build(pushes, hazard, np.random.default_rng(seed))


def S_blind(seed):
    return BlindVLA(seed)


def S_safe(seed):
    return SafeVLAFilter(seed + 1)


def S_causal(seed):
    return CausalInterface(seed + 2)


def write_sweep_csv(rows, path: str) -> None:
    fields = ["hazard", "drift_share", "seed", "arm", "pushes",
              "push_success_rate", "sequence_success_rate", "damage_per_1000",
              "human_calls", "false_vetoes", "mean_latency_ms", "ms_per_success"]
    with open(path, "w", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in rows:
            handle.write(",".join(
                "{:.6g}".format(row[k]) if isinstance(row[k], float) else str(row[k])
                for k in fields) + "\n")


def _sweep_axis(rows):
    """Group by the controlled factor (hazard), report the realised drift share."""
    out = []
    for hazard in SWEEP_HAZARDS:
        subset = [r for r in rows if r["hazard"] == hazard]
        if subset:
            out.append((hazard, float(np.mean([r["drift_share"] for r in subset]))))
    return out


def plot_sweep(rows, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    order = ["A_blind_vla", "C_safe_vla", "B_causal_interface"]
    axis = _sweep_axis(rows)
    xs = [share for _, share in axis]
    for ax, key, label, ylog in (
            (axes[0], "ms_per_success", "latency per successful push, ms", "log"),
            (axes[1], "damage_per_1000", "damage events per 1000 pushes", "symlog")):
        for name in order:
            means, lows, highs = [], [], []
            for hazard, _ in axis:
                values = [r[key] for r in rows
                          if r["arm"] == name and r["hazard"] == hazard]
                means.append(float(np.mean(values)))
                lows.append(min(values))
                highs.append(max(values))
            ax.plot(xs, means, marker="o", color=PALETTE[name],
                    label=LABEL[name])
            ax.fill_between(xs, lows, highs, color=PALETTE[name], alpha=0.15)
        if ylog == "log":
            ax.set_yscale("log")
        elif ylog == "symlog":
            # Damage is zero on a stationary table, and a log axis would hide
            # the difference between "none" and "a few" exactly where the
            # comparison is interesting.
            ax.set_yscale("symlog", linthresh=1.0)
        ax.set_xlabel("share of pushes spent in a drifted regime")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
    axes[0].set_title("(a) cost; three seeds, band = min..max")
    axes[1].set_title("(b) damage; three seeds, band = min..max")
    fig.suptitle("Sensitivity to how often the world moves", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=150)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import os

    global EVENTS_HAZARD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequences", type=int, default=200,
                        help="five-block sequences per arm (default 200)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--hazard", type=float, default=0.01,
                        help="per-push probability a drift window starts")
    parser.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--sweep", action="store_true",
                        help="also run the drift-hazard x seed sensitivity sweep")
    args = parser.parse_args(argv)

    EVENTS_HAZARD = args.hazard
    pushes = args.sequences * len(LANES)
    print(engine_notice())
    print("pushes per arm: {} ({} sequences x {} blocks), seed {}"
          .format(pushes, args.sequences, len(LANES), args.seed))

    schedule = DriftSchedule.build(pushes, args.hazard, np.random.default_rng(args.seed))
    print("drift windows: {} at pushes {}"
          .format(len(schedule.events), [i for i, _ in schedule.events]))

    arms = [BlindVLA(args.seed), SafeVLAFilter(args.seed + 1),
            CausalInterface(args.seed + 2)]
    results = [run_arm(arm, schedule, args.sequences) for arm in arms]
    summary = summarise(results)

    print_report(summary, schedule, args.sequences)

    csv_path = os.path.join(args.outdir, "physics_sandbox_data.csv")
    json_path = os.path.join(args.outdir, "physics_sandbox_summary.json")
    png_path = os.path.join(args.outdir, "physics_sandbox_figure.png")
    write_csv(results, csv_path)
    write_json(summary, schedule, args.sequences, json_path, engine_name())
    plot(results, schedule, png_path)
    print("wrote {} ({} rows)".format(csv_path,
                                      sum(r.pushes for r in results)))
    print("wrote {}".format(json_path))
    print("wrote {}".format(png_path))

    if args.sweep:
        print()
        print("sweep: hazards {} x seeds {} ...".format(
            list(SWEEP_HAZARDS), list(SWEEP_SEEDS)))
        rows = run_sweep(500)
        sweep_csv = os.path.join(args.outdir, "physics_sandbox_sweep.csv")
        sweep_png = os.path.join(args.outdir, "physics_sandbox_sweep.png")
        write_sweep_csv(rows, sweep_csv)
        plot_sweep(rows, sweep_png)
        print("  {:>6}  {:<28}{:<28}{:<28}".format(
            "drift", "A blind VLA", "C safe filter", "B causal interface"))
        for hazard, share in _sweep_axis(rows):
            cells = []
            for name in ("A_blind_vla", "C_safe_vla", "B_causal_interface"):
                subset = [r for r in rows if r["arm"] == name
                          and r["hazard"] == hazard]
                cells.append("{:.0f} ms/ok {:>5.1f}% {:>4.0f} dmg".format(
                    float(np.mean([r["ms_per_success"] for r in subset])),
                    100.0 * float(np.mean([r["push_success_rate"] for r in subset])),
                    float(np.mean([r["damage_per_1000"] for r in subset]))))
            print("  {:>6.0%}  {:<28}{:<28}{:<28}".format(share, *cells))
        print("wrote {}".format(sweep_csv))
        print("wrote {}".format(sweep_png))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
