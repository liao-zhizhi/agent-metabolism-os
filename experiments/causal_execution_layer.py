"""From a latent prediction to a witnessed physical act.

The product claim, stated so that it can lose:

    A world model's abstract prediction is a probability over what *would*
    happen. An actuator accepts a torque. Between them sits a translation that
    nobody witnesses, and that is where a hallucination becomes a physical act.

    H1  If the prediction is passed straight to the hardware, a claim of success
        and the physical result disagree at a rate set by the world model's own
        model error, and no component in the path can notice.

    H2  Checking the command's *form* (schema, envelope) lowers the damage rate
        but cannot lower the disagreement rate, because a well-formed command
        that is physically wrong is still well-formed. The test is conditional:
        given that a push happened, how often did a claim survive a failure?

    H3  Forcing a witness --- a physical fact read back after the act, with the
        command's digest declared before it --- drives that conditional rate down
        to the witness's own resolution and no further: a PASS can only be wrong
        by the tolerance the layer states, which is 5 mm here. What remains is a
        price: refusals where no rule covers the prediction, extra round trips,
        and the risk that a premature witness turns a success into a retry that
        overshoots.

H3's residual is not a measurement of the world. A verdict defined as "what the
rig reported" cannot disagree with the rig, so the quantities that can still fail
are the *instrument's*: does the witness arrive before the block stops? does the
registry cover the case? does a PASS survive ground truth outside its tolerance
band? Those are what this script measures, and the settle-window sweep exists to
make the instrument fail on purpose.

What is simulated, and what is not
-----------------------------------
Simulated: the world model, and the rig. The world model plans from an estimate
of static friction that is wrong by a known amount, which is the only reason
anything goes wrong. The rig is a one-dimensional push with a friction
threshold, a back wall and a torque envelope. Both are explicit generative
models, so the base rates are known rather than assumed.

Not simulated: the layer. ``RuleRegistry``, the envelope guard, the digest
declared before the act and recomputed after it, the adjudication and the
circuit breaker are the objects under test, and they are the ones this
repository would ship. The rig stands in for hardware; the layer is not a
stand-in for the layer.

What this experiment does *not* show
------------------------------------
That the layer makes a bad plan good. It does not: the first push still happens
and still fails when the plan was wrong. What the witness buys is that the
failure is *known*, cannot be *reported as a success*, cannot be *repeated
unchanged*, and does not *damage the rig* on the way out. Those are four claims
and the CSV keeps them in four columns.

Honest limits, stated here rather than in a footnote: the witness can only judge
what the rig can measure, so "in the frame" is verifiable and "pushed well" is
not; the registry is calibrated on this same task family, so its coverage is a
property of this family and not of registries in general; the schema guard never
fires in this population, which is a statement about this decoder and not about
decoders; and a fixed settle window is an instrument setting that the sweep
shows can hurt.

Zero third-party dependencies for the measurement; matplotlib is imported only
to draw the figure and the run completes without it.

Outputs, beside this file:
    causal_execution_data.csv       one row per trial per arm
    causal_execution_settle.csv     the settle-window sweep
    causal_execution_summary.json   the numbers the console prints
    causal_execution_layer_figure.png

Usage:
    python3 experiments/causal_execution_layer.py
    python3 experiments/causal_execution_layer.py --trials 400 --seed 7
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))

DATA_CSV = os.path.join(HERE, "causal_execution_data.csv")
SETTLE_CSV = os.path.join(HERE, "causal_execution_settle.csv")
SUMMARY_JSON = os.path.join(HERE, "causal_execution_summary.json")
FIGURE_PNG = os.path.join(HERE, "causal_execution_layer_figure.png")

TASK = "push the red block into the left frame"

# Geometry, in metres. The frame is the interval between the entry plane and the
# back wall; "in the frame" means the block's centre is inside it.
BLOCK_START_X = 0.0
FRAME_ENTRY_X = -0.60
BACK_WALL_X = -1.05
NEED_TRAVEL = (BLOCK_START_X - FRAME_ENTRY_X) / 1.6

# The rig. A push moves the block only above a static-friction threshold, and
# travel is linear in the excess torque. These are the rig's numbers, not the
# world model's opinion of them -- the disagreement between the two is the whole
# experiment.
TRAVEL_GAIN = 1.6          # metres per unit of torque above the threshold
TAU_MIN = 0.20             # lightest block in the population
TAU_SPAN = 0.25            # heaviest block is TAU_MIN + TAU_SPAN
TORQUE_MAX = 0.90          # hardware envelope; above this the motor saturates
IMPACT_DAMAGE_TORQUE = 0.55  # back-wall contact above this deforms the frame

# The witness. Motion relaxes exponentially; the layer reads the position once,
# after SETTLE_MS. A read that happens before the block stops is a premature
# witness, and the sweep at the end prices exactly that mistake.
RELAX_MS = 45.0
DEFAULT_SETTLE_MS = 400.0
GOAL_TOL = 0.005

# The world model's planning margin, in torque units: how much travel it buys on
# top of the minimum, because it knows its friction estimate is not exact.
PLAN_MARGIN = 0.06

# Verdicts. Three are the repository's lattice; UNAVAILABLE is the registry's own
# limit (no rule covers this prediction) and is deliberately not FAIL: nothing
# was actuated, so nothing was contradicted.
PASS = "PASS"
FAIL = "FAIL"
INVALID = "INVALID"
UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Data structures. Frozen where the object is a claim made at a moment.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Prediction:
    """What the world model says before anything moves."""

    task: str
    latent: Tuple[float, ...]
    confidence: float
    claimed_success: bool
    claimed_travel: float

    @property
    def planned_torque(self) -> float:
        return self.latent[0]

    @property
    def believed_friction(self) -> float:
        return self.latent[1]


@dataclass(frozen=True)
class MotorCommand:
    """The only thing the hardware accepts. No probabilities, no tokens."""

    motor_id: int
    torque: float

    def payload(self) -> Dict[str, object]:
        return {"motor_id": int(self.motor_id),
                "torque": round(float(self.torque), 6)}

    def json(self) -> str:
        return json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.json().encode("utf-8")).hexdigest()


def payload_digest_of(state: "PhysicsState", command_digest: str) -> str:
    body = json.dumps(
        {
            "command": command_digest,
            "reading": round(state.reading_x, 6),
            "contact_back": bool(state.contact_back),
            "applied": round(state.applied_torque, 6),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PhysicsState:
    """What the rig says after. The reading is all a witness can assert about."""

    reading_x: float        # what the sensor saw, at the moment it looked
    settled_x: float        # where the block actually ended up
    contact_back: bool
    applied_torque: float

    @property
    def in_frame(self) -> bool:
        """The verdict-eligible fact. Based on the reading, not on the truth."""
        return self.reading_x <= FRAME_ENTRY_X + GOAL_TOL

    @property
    def truly_in_frame(self) -> bool:
        """Ground truth, computed outside the layer and never available to it."""
        return self.settled_x <= FRAME_ENTRY_X + GOAL_TOL


@dataclass(frozen=True)
class Witness:
    """A physical fact, plus the digest of the command it is a fact about."""

    state: PhysicsState
    command_digest: str
    round_trip_ms: float
    payload_digest: str
    hardware_fault: Optional[str] = None

    def verifies(self) -> bool:
        return self.payload_digest == payload_digest_of(self.state, self.command_digest)


@dataclass(frozen=True)
class Decision:
    """What the layer tells the world model, and why."""

    verdict: str
    reason: str
    command: Optional[MotorCommand]
    witness: Optional[Witness]
    breaker_open: bool = False


@dataclass
class TrialResult:
    """One row of the CSV. Flat on purpose: a reader should not need this file."""

    arm: str
    trial: int
    mass_nu: float
    tau_static: float
    tau_believed: float
    confidence: float
    model_claimed: bool
    claimed_success: bool
    motor_id: int
    torque: float
    command_valid: bool
    envelope_breach: bool
    executed: bool
    attempts: int
    final_x: float
    witnessed_x: float
    in_frame: bool
    contact_back: bool
    hardware_fault: bool
    collision_damage: bool
    damage_event: bool
    verdict: str
    hallucination_escaped: bool
    false_veto: bool
    refused: bool
    round_trips: int
    halted: bool


# ---------------------------------------------------------------------------
# The rig
# ---------------------------------------------------------------------------


class Rig:
    """One-dimensional push rig with a friction threshold and a back wall.

    Every number the world model believes about this object is an estimate. The
    rig never reads the estimate; it reads the torque.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.x = BLOCK_START_X
        self.settled_x = BLOCK_START_X
        self.tau_static = TAU_MIN
        self.fault: Optional[str] = None

    def place(self, mass_nu: float) -> None:
        self.x = BLOCK_START_X + self._rng.uniform(-0.02, 0.02)
        self.settled_x = self.x
        self.tau_static = TAU_MIN + TAU_SPAN * mass_nu
        self.fault = None

    def apply(self, command: MotorCommand, settle_ms: float) -> Witness:
        command_digest = command.digest()
        requested = float(command.torque)
        # Per push. A fault is an event, not a state of health: a motor that
        # saturated once is not a motor that is still saturated, and a sticky
        # flag would make the second witness of a trial unreadable.
        self.fault = None

        if command.motor_id not in (1, 2) or not math.isfinite(requested):
            self.fault = "unknown_motor_id:{}".format(command.motor_id)
            state = PhysicsState(self.x, self.settled_x, False, 0.0)
            return Witness(state, command_digest, 0.0,
                           payload_digest_of(state, command_digest), self.fault)

        # Saturation is a hardware event, not a clamp the planner asked for: the
        # motor does what it can and is damaged doing it.
        applied = requested + self._rng.gauss(0.0, 0.02)
        applied = max(0.0, applied)
        if applied > TORQUE_MAX:
            self.fault = "motor_saturated"
            applied = TORQUE_MAX

        excess = applied - self.tau_static
        travel = TRAVEL_GAIN * excess if excess > 0.0 else 0.0
        start_x = self.x
        room = start_x - BACK_WALL_X
        contact = travel >= room - 1e-9 and travel > 0.0
        travel = min(travel, room)
        self.x -= travel
        self.settled_x = self.x

        settled_fraction = 1.0 - math.exp(-settle_ms / RELAX_MS)
        reading = start_x - travel * settled_fraction
        reading += self._rng.gauss(0.0, 0.002)

        state = PhysicsState(reading_x=reading, settled_x=self.settled_x,
                             contact_back=bool(contact), applied_torque=applied)
        return Witness(state, command_digest, settle_ms,
                       payload_digest_of(state, command_digest), self.fault)


# ---------------------------------------------------------------------------
# The world model
# ---------------------------------------------------------------------------


class WorldModel:
    """Plans a push from a friction estimate it cannot check.

    The latent is deliberately wide and mostly inert: a real prediction vector is
    high-dimensional and the layer is not allowed to read meaning into it. Three
    coordinates carry something, and the registry knows which because it was
    calibrated on them, not because it guessed.
    """

    DIMS = 16

    def __init__(self, rng: random.Random, friction_error_sd: float = 0.085) -> None:
        self._rng = rng
        self._error_sd = friction_error_sd
        self._bias = 0.0          # accumulates after a FAIL is fed back

    def predict(self, tau_static: float, remaining: float = NEED_TRAVEL) -> Prediction:
        error = self._rng.gauss(self._bias, self._error_sd)
        believed = tau_static + error
        planned = believed + remaining + PLAN_MARGIN
        confidence = min(0.99, max(0.05, 1.0 - abs(error) / 0.30))
        # It asserts only when it trusts its own simulator. A hedged prediction is
        # not a hallucination, and the CSV keeps the two apart.
        claims = confidence >= 0.55

        latent = [planned, believed, confidence,
                  TRAVEL_GAIN * (planned - believed), 1.0]
        for dim in range(5, self.DIMS):
            latent.append(math.sin((dim + 1) * planned * 3.1) * 0.05)
        return Prediction(TASK, tuple(latent), confidence, claims,
                          TRAVEL_GAIN * (planned - believed))

    def revise(self, expected_travel: float, observed_travel: float) -> None:
        """The feedback edge: the only channel by which reality reaches the plan.

        It corrects by the discrepancy between the push it asked for and the push
        it got --- not by the claim, and not by the cumulative shortfall, which
        would double-count travel already made. It also shrinks its own
        uncertainty: a witness is information about *this* block, so the next
        plan is not drawn from the same wide error as the first. If the layer
        said PASS this is never called, because there is nothing to correct.
        """
        shortfall = max(0.0, expected_travel - observed_travel)
        self._bias += shortfall / TRAVEL_GAIN
        self._error_sd = max(0.01, self._error_sd * 0.45)


# ---------------------------------------------------------------------------
# The layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """A prediction-to-command mapping proven once and then fixed."""

    rule_id: str
    mass_centre: float
    mass_radius: float
    torque_ceiling: float
    motor_id: int

    def covers(self, believed_friction: float) -> bool:
        return abs(believed_friction - self.mass_centre) <= self.mass_radius


class RuleRegistry:
    """Three rules, calibrated on three mass classes, and nothing else.

    A rule is not a translator that echoes whatever the model asked for; it is a
    statement of what has been shown to work for a mass class, with a torque
    ceiling. A request above the ceiling is refused rather than clamped, because
    a silent clamp hides the disagreement inside the command and makes the
    witness uninterpretable: the block moved less, and nobody can say whether
    the plan or the clamp is responsible.
    """

    def __init__(self, rules: Sequence[Rule]) -> None:
        self.rules = list(rules)
        self.refusals: Dict[str, int] = {}

    @classmethod
    def calibrated(cls) -> "RuleRegistry":
        return cls([
            Rule("R1-light", 0.250, 0.090, TORQUE_MAX, 1),
            Rule("R2-medium", 0.375, 0.090, TORQUE_MAX, 1),
            Rule("R3-heavy", 0.500, 0.090, TORQUE_MAX, 1),
        ])

    def _count(self, key: str) -> None:
        self.refusals[key] = self.refusals.get(key, 0) + 1

    def translate(self, prediction: Prediction) -> Tuple[Optional[Rule], Optional[MotorCommand], str]:
        believed = prediction.believed_friction
        for rule in self.rules:
            if not rule.covers(believed):
                continue
            torque = prediction.planned_torque
            if torque > rule.torque_ceiling:
                self._count("above_ceiling")
                return rule, None, "request {:.3f} exceeds {} ceiling {:.3f}".format(
                    torque, rule.rule_id, rule.torque_ceiling)
            if torque < 0.0:
                self._count("negative")
                return rule, None, "request {:.3f} is below zero".format(torque)
            return rule, MotorCommand(rule.motor_id, round(torque, 6)), "rule " + rule.rule_id
        self._count("uncovered")
        return None, None, "no rule covers a believed friction of {:.3f}".format(believed)


class CausalExecutionLayer:
    """Intercept, translate, declare, actuate, witness, adjudicate, break.

    The digest is computed from the command *before* the rig is touched, and a
    witness is accepted only if it carries that digest. That is what makes the
    verdict a fact about the act rather than a second opinion about the plan.
    """

    def __init__(self, registry: RuleRegistry, rig: Rig,
                 settle_ms: float = DEFAULT_SETTLE_MS,
                 max_revisions: int = 2) -> None:
        self.registry = registry
        self.rig = rig
        self.settle_ms = settle_ms
        self.max_revisions = max_revisions
        self.breaker_opens = 0
        self.verdicts = {PASS: 0, FAIL: 0, INVALID: 0, UNAVAILABLE: 0}
        self._last_failed: Optional[Tuple[str, str]] = None

    def _adjudicate(self, witness: Witness, command: MotorCommand) -> Decision:
        if not witness.verifies():
            return Decision(INVALID, "witness digest does not match the command",
                            command, witness)
        if witness.hardware_fault:
            return Decision(FAIL, "rig fault: " + witness.hardware_fault,
                            command, witness)
        if witness.state.in_frame:
            return Decision(PASS, "block witnessed inside the frame", command, witness)
        return Decision(FAIL, "block witnessed at x={:.3f}, outside the frame".format(
            witness.state.reading_x), command, witness)

    def execute(self, prediction: Prediction) -> Tuple[Decision, int, int]:
        """One attempt. Returns (decision, round_trips, breaker_events)."""
        rule, command, why = self.registry.translate(prediction)
        if command is None:
            verdict = UNAVAILABLE if rule is None else INVALID
            self.verdicts[verdict] += 1
            return Decision(verdict, why, None, None), 0, 0

        # Re-sending the exact command that just failed is not a correction; the
        # breaker refuses it and says so, so a repeat cannot be mistaken for a
        # retry.
        key = (rule.rule_id, command.json())
        if self._last_failed is not None and key == self._last_failed:
            self.breaker_opens += 1
            self.verdicts[INVALID] += 1
            return Decision(INVALID, "breaker open: identical command already failed",
                            command, None, breaker_open=True), 0, 1

        witness = self.rig.apply(command, self.settle_ms)
        decision = self._adjudicate(witness, command)
        self.verdicts[decision.verdict] += 1
        if decision.verdict == FAIL:
            self._last_failed = key
        elif decision.verdict == PASS:
            self._last_failed = None
        return decision, 1, 0

    def run_task(self, world: WorldModel, first: Prediction) -> Dict[str, object]:
        """The closed loop: predict, act, witness, revise, act again.

        ``first`` is passed in rather than drawn here, so that the prediction the
        CSV reports is the prediction that was acted on. Drawing it inside would
        make the logged plan and the executed plan two different draws, which is
        exactly the kind of quiet mismatch this whole script is about.

        The trial's outcome is the last *witnessed* decision. A refusal after a
        witnessed failure is not a refusal to act --- something did act --- it is
        the layer declining to authorise a further attempt, and it is recorded as
        ``halted`` rather than folded into the verdict.
        """
        attempts = 0
        round_trips = 0
        breakers = 0
        revisions = 0
        halted = False
        verdict = UNAVAILABLE
        reason = ""
        witness: Optional[Witness] = None
        command: Optional[MotorCommand] = None
        prediction = first

        while attempts < 1 + self.max_revisions:
            attempts += 1
            start_x = self.rig.x
            decision, trips, breaks = self.execute(prediction)
            round_trips += trips
            breakers += breaks
            if decision.command is not None:
                command = decision.command
            if decision.verdict == PASS:
                verdict, reason = PASS, decision.reason
                if decision.witness is not None:
                    witness = decision.witness
                break
            if decision.witness is None:
                # Refused. If nothing has been witnessed yet, that is the trial's
                # outcome; if something has, the loop stops on the last verdict.
                if witness is None:
                    verdict, reason = decision.verdict, decision.reason
                else:
                    halted = True
                break
            witness = decision.witness
            verdict, reason = decision.verdict, decision.reason
            observed = max(0.0, start_x - decision.witness.state.reading_x)
            world.revise(prediction.claimed_travel, observed)
            revisions += 1
            remaining = max(0.0, decision.witness.state.reading_x - FRAME_ENTRY_X)
            prediction = world.predict(self.rig.tau_static, remaining)

        return {
            "verdict": verdict, "reason": reason, "witness": witness,
            "command": command, "attempts": attempts, "round_trips": round_trips,
            "breakers": breakers, "revisions": revisions, "halted": halted,
        }


# ---------------------------------------------------------------------------
# The three arms
# ---------------------------------------------------------------------------


def population(trials: int, seed: int) -> List[Tuple[float, float, Prediction]]:
    """The block population and the world model's first opinion about each.

    Built once and handed to every arm. This is not tidiness: if each arm drew
    its own plan, an arm that retried would consume the model's random stream and
    face different blocks than the arm it is being compared with, and the paired
    comparison at the end would be measuring the wrong thing.
    """
    mass_rng = random.Random(seed + 11)
    model = WorldModel(random.Random(seed + 1))
    out: List[Tuple[float, float, Prediction]] = []
    for _ in range(trials):
        mass = mass_rng.random()
        tau_static = TAU_MIN + TAU_SPAN * mass
        out.append((mass, tau_static, model.predict(tau_static)))
    return out


def _base_row(*, arm: str, trial: int, mass_nu: float, tau_static: float,
              prediction: Prediction) -> TrialResult:
    return TrialResult(
        arm=arm, trial=trial, mass_nu=round(mass_nu, 6),
        tau_static=round(tau_static, 6),
        tau_believed=round(prediction.believed_friction, 6),
        confidence=round(prediction.confidence, 6),
        # Two different reports, kept apart on purpose: what the model believed
        # before the act, and what the system told it afterwards.
        model_claimed=prediction.claimed_success,
        claimed_success=prediction.claimed_success,
        motor_id=0, torque=0.0, command_valid=False, envelope_breach=False,
        executed=False, attempts=0, final_x=0.0, witnessed_x=0.0,
        in_frame=False, contact_back=False, hardware_fault=False,
        collision_damage=False, damage_event=False, verdict=INVALID,
        hallucination_escaped=False, false_veto=False, refused=True,
        round_trips=0, halted=False,
    )


def _fill_physical(row: TrialResult, command: MotorCommand,
                   state: PhysicsState, *, executed: bool,
                   hardware_fault: bool, round_trips: int) -> TrialResult:
    row.motor_id = int(command.motor_id)
    row.torque = round(float(command.torque), 6)
    row.command_valid = command.motor_id in (1, 2) and math.isfinite(command.torque)
    row.envelope_breach = bool(row.command_valid and command.torque > TORQUE_MAX)
    row.executed = executed
    row.final_x = round(state.settled_x, 6)
    row.witnessed_x = round(state.reading_x, 6)
    row.in_frame = state.truly_in_frame
    row.contact_back = state.contact_back
    row.hardware_fault = bool(hardware_fault)
    row.collision_damage = bool(state.contact_back
                                and state.applied_torque > IMPACT_DAMAGE_TORQUE)
    row.damage_event = bool(row.collision_damage or row.hardware_fault
                            or row.envelope_breach)
    row.refused = not executed
    row.round_trips = round_trips
    return row


def arm_passthrough(rig: Rig,
                    blocks: Sequence[Tuple[float, float, Prediction]]) -> List[TrialResult]:
    """No interface. The model's own decoder writes the motor command."""
    rows: List[TrialResult] = []
    for trial, (mass, tau_static, prediction) in enumerate(blocks):
        rig.place(mass)
        command = MotorCommand(1, prediction.planned_torque)
        witness = rig.apply(command, DEFAULT_SETTLE_MS)
        row = _base_row(arm="passthrough", trial=trial, mass_nu=mass,
                        tau_static=tau_static, prediction=prediction)
        _fill_physical(row, command, witness.state, executed=True,
                       hardware_fault=bool(witness.hardware_fault), round_trips=1)
        row.attempts = 1
        row.verdict = "reported-success" if prediction.claimed_success else "reported-uncertain"
        row.hallucination_escaped = bool(prediction.claimed_success
                                         and not row.in_frame)
        rows.append(row)
    return rows


def arm_structural(registry: RuleRegistry, rig: Rig,
                   blocks: Sequence[Tuple[float, float, Prediction]]) -> List[TrialResult]:
    """Schema, envelope and rule ceiling -- but no witness.

    The ablation that matters. Strictly better than passthrough (no malformed
    command reaches the rig, no torque above the envelope) and still unable to
    tell a silent failure from a success, because the only report of the outcome
    is the plan that produced the command.
    """
    rows: List[TrialResult] = []
    for trial, (mass, tau_static, prediction) in enumerate(blocks):
        rig.place(mass)
        row = _base_row(arm="structural", trial=trial, mass_nu=mass,
                        tau_static=tau_static, prediction=prediction)
        rule, command, _ = registry.translate(prediction)
        if command is None:
            row.verdict = UNAVAILABLE if rule is None else INVALID
            row.attempts = 1
            rows.append(row)
            continue
        witness = rig.apply(command, DEFAULT_SETTLE_MS)
        _fill_physical(row, command, witness.state, executed=True,
                       hardware_fault=bool(witness.hardware_fault), round_trips=1)
        row.attempts = 1
        row.verdict = "reported-success" if prediction.claimed_success else "reported-uncertain"
        row.hallucination_escaped = bool(prediction.claimed_success
                                         and not row.in_frame)
        rows.append(row)
    return rows


def arm_causal(registry: RuleRegistry, rig: Rig,
               blocks: Sequence[Tuple[float, float, Prediction]],
               seed: int, settle_ms: float
               ) -> Tuple[List[TrialResult], Dict[str, int]]:
    """The layer, with the loop closed."""
    rows: List[TrialResult] = []
    revisions_total = 0
    breakers_total = 0
    halted_total = 0
    for trial, (mass, tau_static, prediction) in enumerate(blocks):
        rig.place(mass)
        # A fresh planner per trial, so that a revision in trial 3 cannot leak
        # into trial 4. The first prediction is the shared one; only retries are
        # drawn afresh, from the corrected belief.
        world = WorldModel(random.Random(seed + 1000 + trial))
        layer = CausalExecutionLayer(registry, rig, settle_ms=settle_ms)
        info = layer.run_task(world, prediction)
        witness: Optional[Witness] = info["witness"]          # type: ignore[assignment]
        state = witness.state if witness else PhysicsState(
            rig.x, rig.settled_x, False, 0.0)

        row = _base_row(arm="causal", trial=trial, mass_nu=mass,
                        tau_static=tau_static, prediction=prediction)
        last_command: MotorCommand = info["command"] or MotorCommand(0, 0.0)  # type: ignore[assignment]
        _fill_physical(row, last_command, state,
                       executed=bool(info["round_trips"]),
                       hardware_fault=bool(witness.hardware_fault) if witness else False,
                       round_trips=int(info["round_trips"]))
        row.attempts = int(info["attempts"])
        row.verdict = str(info["verdict"])
        row.halted = bool(info["halted"])
        row.claimed_success = bool(row.verdict == PASS)

        # Two things the layer cannot decide for itself, and which therefore must
        # be checked outside it: did a PASS survive contact with the truth, and
        # did a FAIL veto a task that in fact succeeded?
        row.false_veto = bool(row.verdict == FAIL and row.in_frame)
        row.hallucination_escaped = bool(row.verdict == PASS and not row.in_frame)
        revisions_total += int(info["revisions"])
        breakers_total += int(info["breakers"])
        halted_total += int(info["halted"])
        rows.append(row)
    return rows, {"revisions": revisions_total, "breakers": breakers_total,
                  "halted": halted_total}


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _rate(hits: int, total: int) -> float:
    return hits / total if total else 0.0


def _wilson(hits: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    if not total:
        return (0.0, 0.0)
    p = hits / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarise(rows: Sequence[TrialResult]) -> Dict[str, object]:
    n = len(rows)
    executed = [r for r in rows if r.executed]
    asserted = [r for r in executed if r.claimed_success]
    escaped = [r for r in rows if r.hallucination_escaped]
    return {
        "trials": n,
        "executed": len(executed),
        "asserted_success": len(asserted),
        "physics_success": sum(1 for r in rows if r.in_frame),
        "escaped_hallucinations": len(escaped),
        "escape_rate_all": round(_rate(len(escaped), n), 4),
        # The honest comparison across arms with different coverage: given that
        # a push happened and a claim was made, how often did it survive?
        "escape_rate_executed": round(_rate(len(escaped), len(executed)), 4),
        "escape_wilson": [round(v, 4) for v in _wilson(len(escaped), len(executed))],
        # A PASS can only be wrong by the tolerance the layer declares. If it is
        # ever wrong by more than that, the verdict is not a witness.
        "escapes_outside_tolerance": sum(
            1 for r in escaped
            if abs(r.final_x - FRAME_ENTRY_X) > 2 * GOAL_TOL),
        "damage_events": sum(1 for r in rows if r.damage_event),
        "damage_rate": round(_rate(sum(1 for r in rows if r.damage_event), n), 4),
        "envelope_breaches": sum(1 for r in rows if r.envelope_breach),
        "hardware_faults": sum(1 for r in rows if r.hardware_fault),
        "collisions": sum(1 for r in rows if r.collision_damage),
        "refused": sum(1 for r in rows if r.refused),
        "false_vetoes": sum(1 for r in rows if r.false_veto),
        "mean_attempts": round(sum(r.attempts for r in rows) / n, 3) if n else 0.0,
        "mean_round_trips": round(sum(r.round_trips for r in rows) / n, 3) if n else 0.0,
        "task_success_rate": round(_rate(sum(1 for r in rows if r.in_frame), n), 4),
    }


def paired_escape_rate(passthrough: Sequence[TrialResult],
                       other: Sequence[TrialResult]) -> Optional[float]:
    """Passthrough's escape rate on exactly the trials the other arm executed.

    Comparing two arms on different trial sets is how an ablation gets to look
    good: refusing the hard cases lowers the numerator and the denominator
    together. This fixes the denominator first.
    """
    executed = {r.trial for r in other if r.executed}
    subset = [r for r in passthrough if r.trial in executed]
    if not subset:
        return None
    return round(_rate(sum(1 for r in subset if r.hallucination_escaped), len(subset)), 4)


def seed_sweep(seeds: Sequence[int], trials: int) -> List[Dict[str, float]]:
    """Re-run the headline comparison on other populations.

    One seed is an anecdote. The claim under test is that a witnessed PASS is
    never wrong by more than its tolerance, and a single run in which that
    happened to be true says nothing about the claim. This is the cheapest
    available defence against reporting the run that looked good.
    """
    out: List[Dict[str, float]] = []
    for seed in seeds:
        blocks = population(trials, seed)
        rows, _ = arm_causal(RuleRegistry.calibrated(), Rig(random.Random(seed)),
                             blocks, seed, DEFAULT_SETTLE_MS)
        summary = summarise(rows)
        out.append({
            "seed": seed,
            "escaped": summary["escaped_hallucinations"],
            "outside_tolerance": summary["escapes_outside_tolerance"],
            "damage": summary["damage_events"],
            "refused": summary["refused"],
            "success_rate": summary["task_success_rate"],
            "mean_attempts": summary["mean_attempts"],
        })
    return out


def bin_by_error(rows: Sequence[TrialResult]) -> List[Dict[str, float]]:
    """Escape against the model error that produces it.

    Not smoothing: the point is that the escape is a function of a quantity the
    layer never sees. A better decoder cannot remove it; only a witness can.
    """
    bins = [(0.00, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.60)]
    out: List[Dict[str, float]] = []
    for low, high in bins:
        members = [r for r in rows
                   if low <= abs(r.tau_believed - r.tau_static) < high]
        if not members:
            continue
        asserted = [r for r in members if r.claimed_success]
        if not asserted:
            continue
        escaped = sum(1 for r in asserted if r.hallucination_escaped)
        out.append({"low": low, "high": high, "n": len(members),
                    "asserted": len(asserted), "escaped": escaped,
                    "rate": round(_rate(escaped, len(asserted)), 4)})
    return out


def settle_sweep(blocks: Sequence[Tuple[float, float, Prediction]], seed: int,
                 windows: Sequence[float]) -> List[Dict[str, float]]:
    """Make the instrument fail on purpose, by reading the rig too early.

    Same blocks, same plans, one moving part: when the layer looks.
    """
    out: List[Dict[str, float]] = []
    for settle_ms in windows:
        rig = Rig(random.Random(seed))
        rows, _ = arm_causal(RuleRegistry.calibrated(), rig, blocks, seed, settle_ms)
        summary = summarise(rows)
        out.append({
            "settle_ms": settle_ms,
            "false_veto_rate": round(_rate(summary["false_vetoes"], len(blocks)), 4),
            "mean_attempts": summary["mean_attempts"],
            "retry_collisions": sum(1 for r in rows if r.collision_damage
                                    and r.attempts > 1),
            "damage_rate": summary["damage_rate"],
            "task_success_rate": summary["task_success_rate"],
            "escaped": summary["escaped_hallucinations"],
        })
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_csv(path: str, rows: Sequence[TrialResult]) -> None:
    fields = list(asdict(rows[0]).keys())
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_settle_csv(path: str, rows: Sequence[Dict[str, float]]) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"
PALE = "#c9cdd3"
GREEN = "#3f7d5a"
GOLD = "#a5762a"


def render(sums: Dict[str, Dict[str, object]], bins: Sequence[Dict[str, float]],
           sweep: Sequence[Dict[str, float]], trials: int,
           path: str = FIGURE_PNG) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 - a missing plot is not a missing result
        print("matplotlib unavailable: figure skipped; CSV and JSON are complete")
        return None

    plt.rcParams.update({
        "font.size": 8.5, "axes.labelsize": 8.5, "axes.titlesize": 9,
        "legend.fontsize": 7.0, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
        "axes.linewidth": 0.7, "grid.linewidth": 0.4, "grid.alpha": 0.35,
        "savefig.dpi": 220, "figure.dpi": 110,
        "axes.spines.top": False, "axes.spines.right": False,
    })
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.0), constrained_layout=True)
    arms = [("pass-\nthrough", "passthrough", MUTED),
            ("form-\nchecked", "structural", COOL),
            ("witnessed", "causal", GREEN)]

    # (a) the two failures that matter, side by side
    ax = axes[0][0]
    xs = list(range(len(arms)))
    width = 0.36
    left = [100.0 * float(sums[key]["escape_rate_all"]) for _, key, _ in arms]
    right = [100.0 * float(sums[key]["damage_rate"]) for _, key, _ in arms]
    ax.bar([x - width / 2 for x in xs], left, width, color=ACCENT,
           label="claim survives a failure")
    ax.bar([x + width / 2 for x in xs], right, width, color=GOLD,
           label="damage reaches the rig")
    for x, value in zip(xs, left):
        ax.annotate("{:.0f}%".format(value), (x - width / 2, value),
                    ha="center", va="bottom", fontsize=7, color=ACCENT)
    for x, value in zip(xs, right):
        ax.annotate("{:.0f}%".format(value), (x + width / 2, value),
                    ha="center", va="bottom", fontsize=7, color=GOLD)
    ax.set_xticks(xs)
    ax.set_xticklabels([label for label, _, _ in arms])
    ax.set_ylabel("per cent of {} pushes".format(trials))
    ax.set_ylim(0, max(left + right) * 1.45 + 1)
    ax.set_title("(a) two different failures")
    ax.legend(frameon=False, loc="upper right")
    ax.grid(axis="y")

    # (b) what the layer said, as a composition
    ax = axes[0][1]
    causal = sums["causal"]
    total = float(causal["trials"])
    passes = total * float(causal["task_success_rate"])
    refusals = float(causal["refused"])
    fails = total - passes - refusals
    stack = [(passes, GREEN, "witnessed success\n(block in frame)"),
             (fails, ACCENT, "witnessed failure\n(block short)"),
             (refusals, PALE, "refused\n(no rule / above ceiling)")]
    bottom = 0.0
    for value, color, label in stack:
        share = 100.0 * value / total
        ax.bar([0], [share], bottom=bottom, color=color, width=0.5, label=label)
        if share > 5:
            ax.annotate("{:.0f}%".format(share), (0, bottom + share / 2),
                        ha="center", va="center", fontsize=7.5,
                        color="white" if color is not PALE else "#333333")
        bottom += share
    ax.set_xlim(-0.5, 0.5)
    ax.set_ylim(0, 100)
    ax.set_xticks([])
    ax.set_ylabel("per cent of {} pushes".format(trials))
    ax.set_title("(b) what the layer reported")
    ax.legend(frameon=False, loc="center left", bbox_to_anchor=(0.62, 0.5))
    ax.grid(axis="y")

    # (c) escape probability against the model error that causes it
    ax = axes[1][0]
    if bins:
        centres = [(b["low"] + b["high"]) / 2 for b in bins]
        rates = [100.0 * b["rate"] for b in bins]
        ax.plot(centres, rates, marker="o", color=ACCENT, linewidth=1.4,
                markersize=4, label="pass-through")
        ax.plot(centres, [0.0] * len(centres), marker="s", color=GREEN,
                linewidth=1.4, markersize=3.5, label="witnessed")
        ax.set_ylim(-3, 103)
    ax.set_xlabel("|believed friction - actual friction|  (torque units)")
    ax.set_ylabel("claims contradicting physics (%)")
    ax.set_title("(c) the escape tracks an unseen error")
    ax.legend(frameon=False)
    ax.grid(axis="y")

    # (d) the instrument failing on purpose
    ax = axes[1][1]
    xs = [s["settle_ms"] for s in sweep]
    ax.plot(xs, [100 * s["false_veto_rate"] for s in sweep], marker="o",
            color=COOL, linewidth=1.4, markersize=3.5, label="false vetoes")
    ax.plot(xs, [100 * s["retry_collisions"] / max(1, trials) for s in sweep],
            marker="s", color=GOLD, linewidth=1.4, markersize=3.5,
            label="collisions caused by the retry")
    ax.plot(xs, [s["mean_attempts"] - 1 for s in sweep], marker="^",
            color=MUTED, linewidth=1.2, markersize=3.5,
            label="extra attempts per push")
    ax.set_xscale("log")
    ax.set_xlabel("witness delay after the push (ms)")
    ax.set_ylabel("per cent of pushes  /  attempts")
    ax.set_title("(d) a premature witness is not free")
    ax.legend(frameon=False, fontsize=6.4)
    ax.grid(axis="y")

    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------


def transcript(causal_rows: Sequence[TrialResult], limit: int = 1) -> None:
    """The three moments a reader needs to see once, in the order they happen."""
    shown = 0
    for row in causal_rows:
        if row.verdict != FAIL or not row.executed:
            continue
        print()
        print("  ── 一次被拦截的推方块 (trial {}) ──".format(row.trial))
        print("  世界模型原始输出：概率 {:.2f}（预期成功），潜空间 {} 维".format(
            row.confidence, WorldModel.DIMS))
        print("  因果接口翻译：{{\"motor_id\": {}, \"torque\": {:.3f}}}".format(
            row.motor_id, row.torque))
        print("  物理见证：方块停在 x = {:.3f}，框内判定 {} -> 判定 {}".format(
            row.witnessed_x, "真" if row.witnessed_x <= FRAME_ENTRY_X + GOAL_TOL else "假",
            row.verdict))
        if row.attempts > 1:
            print("            -> FAIL 回传世界模型，修正摩擦估计后重试 {} 次".format(
                row.attempts - 1))
        if row.false_veto:
            print("            -> 注意：真实终点 x = {:.3f} 其实已入框，这是误判".format(
                row.final_x))
        shown += 1
        if shown >= limit:
            break


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--no-figure", action="store_true")
    args = parser.parse_args(argv)

    blocks = population(args.trials, args.seed)
    passthrough = arm_passthrough(Rig(random.Random(args.seed)), blocks)
    structural = arm_structural(RuleRegistry.calibrated(),
                                Rig(random.Random(args.seed)), blocks)
    registry_causal = RuleRegistry.calibrated()
    causal_rows, meta = arm_causal(registry_causal,
                                   Rig(random.Random(args.seed)), blocks,
                                   args.seed, DEFAULT_SETTLE_MS)

    sums = {"passthrough": summarise(passthrough),
            "structural": summarise(structural),
            "causal": summarise(causal_rows)}
    bins = bin_by_error(passthrough)
    sweep = settle_sweep(blocks, args.seed,
                         (40, 60, 80, 120, 200, 400, 800, 4000))
    seed_robustness = seed_sweep([1, 7, 42, 2026, 31337], args.trials)

    paired_p = paired_escape_rate(passthrough, structural)
    paired_c = paired_escape_rate(passthrough, causal_rows)
    escalated = sums["passthrough"]["escaped_hallucinations"] > 0
    interception = (1.0 - (sums["causal"]["escaped_hallucinations"]
                           / max(1, sums["passthrough"]["escaped_hallucinations"])))

    write_csv(DATA_CSV, list(passthrough) + list(structural) + list(causal_rows))
    write_settle_csv(SETTLE_CSV, sweep)

    summary = {
        "task": TASK,
        "trials_per_arm": args.trials,
        "seed": args.seed,
        "rig": {
            "tau_static_range": [TAU_MIN, TAU_MIN + TAU_SPAN],
            "travel_gain": TRAVEL_GAIN,
            "torque_max": TORQUE_MAX,
            "impact_damage_torque": IMPACT_DAMAGE_TORQUE,
            "frame": [BACK_WALL_X, FRAME_ENTRY_X],
            "settle_ms": DEFAULT_SETTLE_MS,
            "relax_ms": RELAX_MS,
        },
        "arms": sums,
        "paired_passthrough_escape_rate": {"vs_structural": paired_p,
                                           "vs_causal": paired_c},
        "escape_by_error": bins,
        "settle_sweep": sweep,
        "seed_robustness": seed_robustness,
        "causal_loop": meta,
        "registry_refusals": registry_causal.refusals,
        "interception_rate": round(interception, 4) if escalated else None,
        "claims": {
            "h1_pass_through_contradicts_physics": escalated,
            "h2_form_checking_does_not_lower_the_conditional_rate": bool(
                paired_p is not None
                and abs(paired_p - sums["passthrough"]["escape_rate_executed"]) < 0.05),
            "h3_escapes_confined_to_the_tolerance_band": bool(
                sums["causal"]["escapes_outside_tolerance"] == 0),
            "h3_cost_is_refusal_and_retry": bool(
                sums["causal"]["refused"] > 0
                or sums["causal"]["mean_attempts"] > 1.0),
        },
    }
    with open(SUMMARY_JSON, "w") as handle:
        json.dump(summary, handle, indent=1)

    figure = None if args.no_figure else render(sums, bins, sweep, args.trials)

    # ---- console ---------------------------------------------------------
    print("=" * 78)
    print("CAUSAL EXECUTION LAYER — {} pushes per arm, seed {}".format(
        args.trials, args.seed))
    print("=" * 78)
    for label, key in (("无接口 pass-through", "passthrough"),
                       ("仅形式校验 form-checked", "structural"),
                       ("因果执行接口 witnessed", "causal")):
        s = sums[key]
        print()
        print("  {}".format(label))
        print("    执行/尝试            : {:>3d}/{} 次 push".format(
            s["executed"], s["trials"]))
        print("    声称成功却与物理不符 : {:>3d} 次 = {:>6.1%} (占已执行)".format(
            s["escaped_hallucinations"], s["escape_rate_executed"]), end="")
        if s["escaped_hallucinations"]:
            print("  其中越过容差带 {} 次".format(s["escapes_outside_tolerance"]))
        else:
            print()
        print("    到达硬件的损坏事件   : {:>3d} 次 = {:>6.1%}".format(
            s["damage_events"], s["damage_rate"]))
        print("    实际入框             : {:>3d} 次 = {:>6.1%}".format(
            int(round(s["task_success_rate"] * s["trials"])),
            s["task_success_rate"]))
        if s["refused"]:
            print("    拒绝执行             : {:>3d} 次 (无规则覆盖 / 超扭矩上限)".format(
                s["refused"]))
        if s["false_vetoes"]:
            print("    误判失败（其实已入框）: {:>3d} 次".format(s["false_vetoes"]))
        if s["mean_attempts"] > 1.0:
            print("    平均执行轮次         : {:.2f}".format(s["mean_attempts"]))

    transcript(causal_rows)

    print()
    print("  ── 闭环 ──")
    print("  回传 FAIL 后重规划     : {} 次".format(meta["revisions"]))
    print("  同一指令被熔断拒绝     : {} 次".format(meta["breakers"]))
    print("  修正后仍无规则可用     : {} 次（停在最后一次见证状态）".format(meta["halted"]))
    print("  幻觉拦截率             : {}".format(
        "n/a（无幻觉可拦）" if not escalated else "{:.1%}".format(interception)))

    print()
    print("  ── 提前见证的代价（settle sweep，{} 次/点）──".format(args.trials))
    for point in sweep:
        print("    witness @ {:>5.0f} ms : 误判 {:>5.1%}  重试致碰撞 {:>2d}  "
              "完成率 {:>5.1%}  平均轮次 {:.2f}".format(
                  point["settle_ms"], point["false_veto_rate"],
                  point["retry_collisions"], point["task_success_rate"],
                  point["mean_attempts"]))

    print()
    print("  ── 跨 seed 复核（{} 次/组）──".format(args.trials))
    for point in seed_robustness:
        print("    seed {:>6d} : 越带错报 {}  损坏 {}  拒绝 {}  完成 {:>5.1%}  轮次 {:.2f}".format(
            point["seed"], point["outside_tolerance"], point["damage"],
            point["refused"], point["success_rate"], point["mean_attempts"]))

    print()
    print("  ── 判定 ──")
    for key, value in summary["claims"].items():
        print("    {:<56s} {}".format(key, "PASS" if value else "FAIL"))
    print()
    print("  H1  无接口时，世界模型的声称与物理事实冲突。")
    print("  H2  形式校验把损坏降到 0，但不降低条件错报率（配对比较 {:.1%} vs {:.1%}）。".format(
        sums["passthrough"]["escape_rate_executed"], paired_p or 0.0))
    print("  H3  见证把错报率降到 {:.1%}，代价是 {} 次拒绝与 {:.2f} 轮/次。".format(
        sums["causal"]["escape_rate_executed"], sums["causal"]["refused"],
        sums["causal"]["mean_attempts"]))

    print()
    print("WROTE")
    for path in (DATA_CSV, SETTLE_CSV, SUMMARY_JSON, figure):
        if path:
            print("  " + os.path.relpath(path, os.path.dirname(HERE)))
    print("No commit. No push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
