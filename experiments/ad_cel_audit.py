#!/usr/bin/env python3
"""The causal execution layer as a planner-to-chassis interface, in a 2-D road model.

Why a kinematic model and not CARLA
-----------------------------------
This is a *mechanism* experiment. Every question it asks -- can a layer tell a
friction change from an uncertainty spike? does an actuator lag show up in the
committed trajectory before it shows up in a crash? -- is decided by the state
feedback between plan and plant, not by rendering. A 2-D road with a friction
circle, a first-order actuator lag and a 20 Hz chassis runs 200 episodes in a
couple of seconds and can be read line by line; CARLA would add a renderer and
a driver stack without adding a single new degree of freedom to the question.
What the model gives up is stated in ``Threats`` below.

The two arms share one planner
------------------------------
Both arms drive the *same* end-to-end planner: a horizon rollout of a bicycle
model plus a speed policy, which emits a trajectory (x, y, psi, v) and the
control sequence (a, kappa) that produces it. The rollout *is* the plan, and it
is also the prediction the layer commits to. This is the same contract as the
manipulation paper: a plan is a claim about the future, not a suggestion.

**Arm A -- Safe-Planner.** A confidence gate in front of the chassis. The
planner reports a scalar confidence per 0.5 s cycle; below ``CONF_THRESHOLD``
the arm brakes hard (the standard inference-time safety filter), otherwise it
executes the plan open-loop for the whole cycle. Its input is the scene's
appearance: a roadside novelty, a pedestrian on the pavement, a curve. Friction
is *not* in that vector, and the docstring of :meth:`SafePlanner.confidence`
says so -- that is the whole failure mode, and it is not a strawman: a wet
road is visible, black ice and oil are not, and the gate has the same blind spot
on both.

**Arm B -- CEL.** Between the plan and the chassis. At plan time the layer
registers every predicted state on the witness grid, keyed by a monotonically
increasing ordinal (the ERR-004 fix: a hash binds content, causation binds a
prediction to a *particular attempt*). At 10 Hz it measures the actual state,
compares it to the registration for that ordinal, and grants only if the
deviation is inside the declared tolerances. A refusal is recorded in a
hash-linked ledger, and the layer then does the two things a monitor is for:

1. **identifies** the physical parameters from the discrepancy -- the friction
   budget from the saturated deceleration, the actuator lags from the commanded
   vs. realised response (the layer reads an accelerometer and a yaw-rate
   sensor, not the simulator's ``mu``);
2. **feeds them back to the planner**, which re-derives its trajectory with the
   measured grip and the measured lag. A monitor that only raised an alarm
   would be a better alarm; what makes it an execution *layer* is that the
   measured state becomes the input to the next plan.

What is injected
----------------
============================  ==================================================
``wet_road``                  friction 0.90 -> 0.35 from a seeded onset; the
                              road looks identical, so no appearance signal
``steering_lag``              worn actuators: brake lag 0.10 -> 0.40 s,
                              steering lag 0.08 -> 0.55 s, rack gain 0.92
``wet_and_lag``               both, because they co-occur in the field
============================  ==================================================

Each channel is applied against the same hazard schedule: a lead vehicle that
emergency-brakes, or a pedestrian stepping out from between parked cars. The
hazard is *not* conditioned on the drift -- it is the same list for both arms
and for every channel (see :func:`build_schedule`).

What is measured, and what is declared
--------------------------------------
Measured: collisions, lane departures, arrival time, minimum gap, realised
deceleration, refusal counts, and the parameter estimates themselves.
Declared, and flagged in the summary: the confidence signal is a model of a
confidence head (there is no trained driving head in this repository), and
``V_LIMIT``/comfort budgets are policy constants. ``false_eb`` and
``false_refusal`` are decided by a *counterfactual replay*: the same episode,
same seed, gate removed. A brake is false if the blind run would have arrived
safely, which is the same accounting the manipulation audit used.

Not measured, and not implied: perception. The planner reads privileged actor
state. A real stack reads a detector, and the layer's stopping-distance witness
is only as good as that detector -- see ``Threats`` in the printed summary.

Usage
-----
    python3 experiments/ad_cel_audit.py --episodes 100
    python3 experiments/ad_cel_audit.py --probe 2      # one line per episode

Writes ``ad_cel_data.csv``, ``ad_cel_summary.json``, ``ad_cel_figure.png`` and
``ad_cel_ledger.jsonl`` next to this file. Nothing here commits, pushes, or
needs a renderer.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = HERE

# ---------------------------------------------------------------------------
# declared constants -- every one of these is a *choice*, and the report says so
# ---------------------------------------------------------------------------

DT = 0.05                    # chassis/controller rate, 20 Hz
REPLAN_DT = 0.50             # planner rate, 2 Hz (an end-to-end planner's rate)
HORIZON = 2.50               # plan horizon, s
WITNESS_DT = 0.10            # layer's witness rate, 10 Hz
TIME_LIMIT = 60.0            # episode budget, s

G = 9.81
CAR_LENGTH = 4.60
WHEELBASE = 2.70
LANE_HALF = 1.75             # lane centre to paint
ROAD_EDGE = 3.60             # lane centre to shoulder; beyond this, off-road
GOAL_X = 280.0

MU_NOMINAL = 0.90
MU_WET = 0.35
TAU_A_NOMINAL = 0.10         # brake/accel actuator lag, s
TAU_A_WORN = 0.40
TAU_S_NOMINAL = 0.08         # steering actuator lag, s
TAU_S_WORN = 0.75
KAPPA_GAIN_WORN = 0.92

V_LIMIT = 13.8               # 50 km/h
ACCEL_MAX = 2.60
BRAKE_CMD_MAX = 8.00         # the planner never commands more than this
LAT_BUDGET = 2.40            # comfortable lateral accel used for curve speed
T_HEADWAY = 1.00             # nominal following headway, s
S0 = 10.00                   # standstill gap, m (defensive)
COMFORT_DECEL = 4.20         # decel a predictable constraint may use
SPEED_GAIN = 5.00            # speed-cap tracking gain, 1/s

# The route: a gentle S, then the hazard zone on the straight.
CURVE_X0, CURVE_X1, CURVE_A = 70.0, 160.0, 2.00
STOP_LINE_X = 100.0          # signalised intersection, red at episode start
GREEN_AFTER_STOP_S = 2.5
CROSSING_X = 232.0           # marked crossing
VAN_X0, VAN_X1 = 216.0, 230.0   # parked vans: the view past them is short
VISIBLE_PAST_OCCLUSION = 12.0

# hazard geometry, drawn per episode
LEAD_BRAKE_DECEL = -8.0
PED_WALK_V = 1.35
PED_START_Y = 2.60           # steps out from the parking lane

# arm A
CONF_THRESHOLD = 0.80
CONF_BASE = 0.925
CONF_NOVELTY = 0.175
CONF_NOISE_SD = 0.030
EB_DECEL = 6.00              # the emergency brake it commands
HARSH_DECEL = 5.00           # discomfort threshold, m/s^2 (ISO 2631-ish)

# the layer's declared resolution: a grant means the observation is inside these
TOL_LAT = 0.50               # m, the number the brief names
TOL_LON = 1.00               # m
TOL_SPEED = 0.50             # m/s
STOP_MARGIN = 4.00           # m of margin in the stopping-distance witness
JERK_LIMIT = 6.0             # m/s^3, a takeover's brake-release rate
SENSOR_GLITCH_P = 0.0015     # per witness check: a pose-estimator spike

# how much the identified lag is trusted (1.0 = fully, 0.0 = ignore)
LAG_TRUST = 1.0

CHANNELS = ("nominal", "wet_road", "steering_lag", "wet_and_lag")
HAZARDS = ("lead_brake", "pedestrian")
ARM_LABEL = {"A_safe_planner": "Safe-Planner", "B_cel": "CEL"}


# ---------------------------------------------------------------------------
# route geometry
# ---------------------------------------------------------------------------


def road_y(x: float) -> float:
    """Lane centreline. Zero value *and* zero slope at both ends of the S."""
    u = (x - CURVE_X0) / (CURVE_X1 - CURVE_X0)
    if u <= 0.0 or u >= 1.0:
        return 0.0
    return CURVE_A * math.sin(2.0 * math.pi * u) * math.sin(math.pi * u) ** 2


def road_dy(x: float, h: float = 1e-2) -> float:
    return (road_y(x + h) - road_y(x - h)) / (2.0 * h)


def road_curvature(x: float, h: float = 0.5) -> float:
    y1 = road_dy(x, h)
    y2 = (road_dy(x + h, h) - road_dy(x - h, h)) / (2.0 * h)
    return y2 / (1.0 + y1 * y1) ** 1.5


def max_curvature_ahead(x: float, lookahead: float = 45.0, step: float = 2.5) -> float:
    best = 0.0
    d = 0.0
    while d <= lookahead:
        best = max(best, abs(road_curvature(x + d)))
        d += step
    return best


# ---------------------------------------------------------------------------
# plant
# ---------------------------------------------------------------------------


@dataclass
class VehicleState:
    """Everything the chassis needs. ``a``/``kappa`` are actuator states, not
    commands: a real vehicle knows them from its own accelerometer and yaw
    rate, and both the planner's rollout and the layer start from them."""

    x: float
    y: float
    psi: float
    v: float
    a: float = 0.0
    kappa: float = 0.0

    def copy(self) -> "VehicleState":
        return VehicleState(self.x, self.y, self.psi, self.v, self.a, self.kappa)


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class Plant:
    """A bicycle model with a friction circle and first-order actuator lags.

    The friction circle is the part that matters. Braking is capped at ``mu*g``
    and cornering is capped at ``mu*g/v^2``: a wet road hurts both, but it hurts
    *braking distance quadratically* and *cornering speed only linearly*, which
    is why the two channels fail differently. The lags are modelled as a
    first-order response with time constant ``tau``, which is what a worn
    actuator actually is.
    """

    def __init__(self, mu: float, tau_a: float, tau_s: float,
                 kappa_gain: float = 1.0) -> None:
        self.mu = mu
        self.tau_a = tau_a
        self.tau_s = tau_s
        self.kappa_gain = kappa_gain

    def step(self, s: VehicleState, a_cmd: float, kappa_cmd: float, dt: float) -> None:
        a_target = min(max(a_cmd, -self.mu * G), ACCEL_MAX)
        alpha = min(1.0, dt / max(self.tau_a, 1e-6))
        s.a += (a_target - s.a) * alpha
        s.a = min(max(s.a, -self.mu * G), ACCEL_MAX)

        k_target = self.kappa_gain * kappa_cmd
        beta = min(1.0, dt / max(self.tau_s, 1e-6))
        s.kappa += (k_target - s.kappa) * beta
        k_max = (self.mu * G) / max(s.v * s.v, 0.5)
        s.kappa = min(max(s.kappa, -k_max), k_max)

        v_new = max(0.0, s.v + s.a * dt)
        psi_new = s.psi + 0.5 * (s.v + v_new) * s.kappa * dt
        s.x += 0.5 * (s.v + v_new) * math.cos(0.5 * (s.psi + psi_new)) * dt
        s.y += 0.5 * (s.v + v_new) * math.sin(0.5 * (s.psi + psi_new)) * dt
        s.psi = _wrap(psi_new)
        s.v = v_new


# ---------------------------------------------------------------------------
# actors
# ---------------------------------------------------------------------------


@dataclass
class LeadVehicle:
    """A lead that queues at the light, then either turns off or brakes hard.

    It resumes on purpose: a lead that stops forever turns every episode into a
    road-closure test, and the ego has no lane-change policy to pass it with.
    """

    x: float
    y: float
    v: float
    cruise_v: float = 0.0
    stop_x: float = math.inf
    green_t: float = math.inf
    exit_x: float = math.inf
    brake_at_x: float = math.inf
    queued: bool = False
    braking: bool = False
    brake_t: float = 0.0
    exited: bool = False

    def update(self, t: float, dt: float) -> None:
        if not self.queued and self.x >= self.stop_x:
            self.queued = True
            self.cruise_v = self.v
        if self.queued and t < self.green_t:
            self.v = max(0.0, self.v - 3.50 * dt)
        elif self.queued and t >= self.green_t:
            self.v = min(self.cruise_v, self.v + 2.00 * dt)
        if not self.braking and self.x >= self.brake_at_x:
            self.braking = True
        if self.braking:
            self.brake_t += dt
            if self.brake_t < 2.6:
                self.v = max(0.0, self.v + LEAD_BRAKE_DECEL * dt)
            else:
                self.v = min(self.cruise_v, self.v + 2.00 * dt)
        self.x += self.v * dt
        self.y = road_y(self.x)
        if self.x >= self.exit_x:
            self.exited = True


@dataclass
class Pedestrian:
    x: float
    y: float = 4.60
    active: bool = False

    def update(self, dt: float) -> None:
        if not self.active:
            return
        self.y -= PED_WALK_V * dt

    @property
    def in_lane(self) -> bool:
        return abs(self.y - road_y(self.x)) < LANE_HALF


@dataclass
class Scene:
    t: float
    lead: Optional[LeadVehicle]
    ped: Optional[Pedestrian]
    red_light: bool = False


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """A trajectory plus the control sequence that produces it, on the DT grid."""

    t0: float
    ordinal: int
    x: List[float]
    y: List[float]
    v: List[float]
    a: List[float]
    kappa: List[float]

    def index(self, t: float) -> int:
        return max(0, min(len(self.x) - 1, int(round((t - self.t0) / DT))))

    def at(self, t: float) -> Tuple[float, float, float]:
        i = self.index(t)
        return self.x[i], self.y[i], self.v[i]


# ---------------------------------------------------------------------------
# the planner: one rollout, believed physics, no knowledge of the drift
# ---------------------------------------------------------------------------


class Planner:
    """A horizon rollout of the *believed* plant. The plan is the prediction.

    The planner is given a parameter set and believes it. At nominal that set
    is the truth and the rollout is exact; under drift it is not, and the gap
    between rollout and reality is the quantity the layer measures. Nothing
    here reads the simulator's ground truth.
    """

    def __init__(self, mu: float = MU_NOMINAL, tau_a: float = TAU_A_NOMINAL,
                 tau_s: float = TAU_S_NOMINAL) -> None:
        self.mu = mu
        self.tau_a = tau_a
        self.tau_s = tau_s
        self.plant = Plant(mu, tau_a, tau_s)
        self.lead_comp = 0.0    # s of curvature lead used to cancel the lag

    # -- speed policy ----------------------------------------------------

    def brake_budget(self) -> float:
        """The deceleration the plan may assume: the smaller of what the road
        allows and what the brake command allows. Two different constraints --
        conflating them is how a planner ends up asking for 9 m/s^2 on a
        chassis that only ever gets 8."""
        return min(self.mu * G, BRAKE_CMD_MAX)

    def stopping_distance(self, v: float, margin: float = 0.0) -> float:
        """Distance needed to stop, including the actuator lag and a margin."""
        return v * v / (2.0 * self.brake_budget()) + v * self.tau_a + margin

    def max_speed_for(self, d: float, margin: float = 0.0) -> float:
        """Largest v whose stopping distance fits in ``d``.

        Solves v^2/(2 a) + v tau = d - margin. The ``tau`` term is the part a
        layer that has *measured* its brake lag can add and a nominal planner
        cannot: it is worth ``v*tau`` metres, about 4 m at 14 m/s with a 0.3 s
        excess lag.
        """
        room = max(0.0, d - margin)
        a = self.brake_budget()
        return -a * self.tau_a + math.sqrt((a * self.tau_a) ** 2 + 2.0 * a * room)

    def constraints(self, s: VehicleState, scene: Scene) -> List[Tuple[float, float]]:
        """Every reason to slow down, as ``(distance, speed allowed there)``.

        ``allowed == 0`` means "be stopped by then". The policy is a speed-cap
        law: the cap falls out of the *geometry*, computed as if the car could
        brake at the friction it believes in. On a wet road it cannot, and the
        shortfall -- not a control-tuning artefact -- is the experiment.
        """
        out: List[Tuple[float, float]] = []

        # curve: the first point ahead whose curvature asks for less speed
        for d in range(0, 61, 5):
            k = abs(road_curvature(s.x + d))
            if k > 1e-4:
                allowed = math.sqrt(LAT_BUDGET / k)
                if allowed < s.v:
                    out.append((max(float(d), 1.0), allowed))
                    break

        # a crossing you can only see 12 m past the parked vans
        if VAN_X0 - 40.0 <= s.x <= CROSSING_X:
            out.append((max(CROSSING_X - s.x, 1.0),
                        self.max_speed_for(VISIBLE_PAST_OCCLUSION, STOP_MARGIN)))

        # the red light, while it is against us and still ahead
        if scene.red_light and s.x < STOP_LINE_X:
            out.append((max(STOP_LINE_X - s.x, 1.0), 0.0))

        # a lead vehicle is a stop point (safety-first car following)
        if scene.lead is not None:
            gap = scene.lead.x - s.x - CAR_LENGTH
            out.append((max(gap, 1.0), 0.0))

        ped = scene.ped
        if ped is not None and ped.active and ped.x > s.x:
            if abs(ped.y - road_y(ped.x)) < LANE_HALF + 0.6:
                out.append((max(ped.x - s.x - 1.0, 1.0), 0.0))
        return out

    def speed_cap(self, s: VehicleState, scene: Scene) -> float:
        """The fastest speed the constraints allow *now*."""
        grip = min(self.mu / MU_NOMINAL, 1.0)
        cap = V_LIMIT * (0.62 + 0.38 * grip)
        for d, allowed in self.constraints(s, scene):
            if allowed > 0.0:
                cap = min(cap, math.sqrt(allowed * allowed + 2.0 * COMFORT_DECEL * d))
            else:
                cap = min(cap, self.max_speed_for(max(d - S0, 0.0)))
        return max(cap, 0.0)

    def target_speed(self, s: VehicleState, scene: Scene) -> float:
        return self.speed_cap(s, scene)

    def desired_accel(self, s: VehicleState, scene: Scene) -> float:
        """Track the cap with a stiff proportional loop, clipped by the budget.

        Stiff on purpose: the cap falls at ``~v/d`` as the gap closes, so a soft
        loop spends the safety margin as tracking error. At ``K = 5`` the lag is
        0.2 s, about 2.5 m at 13 m/s -- inside the declared margin.
        """
        cap = self.speed_cap(s, scene)
        a = SPEED_GAIN * (cap - s.v)
        return min(max(a, -self.brake_budget()), ACCEL_MAX)

    # -- lateral control -------------------------------------------------

    def predict_state(self, s: VehicleState, taus: float) -> VehicleState:
        """Where the car will be in ``taus`` seconds if nothing changes.

        A pure actuator delay is not cancelled by leading the curvature; it is
        cancelled by aiming from where the car *will* be. The layer has the
        delay from its own yaw-rate response, so it can run this predictor and
        hand the planner a lead the planner could not have derived.
        """
        out = s.copy()
        out.psi = _wrap(s.psi + s.v * s.kappa * taus)
        out.x = s.x + s.v * math.cos(out.psi) * taus
        out.y = s.y + s.v * math.sin(out.psi) * taus
        return out

    def _pure_pursuit(self, s: VehicleState) -> float:
        lookahead = 5.0 + 0.45 * s.v
        lo, hi = s.x + 0.2 * lookahead, s.x + 2.0 * lookahead
        for _ in range(24):
            mid = 0.5 * (lo + hi)
            if math.hypot(mid - s.x, road_y(mid) - s.y) < lookahead:
                lo = mid
            else:
                hi = mid
        xt = 0.5 * (lo + hi)
        alpha = _wrap(math.atan2(road_y(xt) - s.y, xt - s.x) - s.psi)
        return 2.0 * math.sin(alpha) / max(lookahead, 1e-3)

    def control(self, s: VehicleState, scene: Scene) -> Tuple[float, float]:
        a = self.desired_accel(s, scene)
        if self.lead_comp > 0.0:
            kappa_pp = self._pure_pursuit(self.predict_state(s, self.lead_comp))
        else:
            kappa_pp = self._pure_pursuit(s)
        return a, kappa_pp

    def plan(self, t: float, ego: VehicleState, scene: Scene, ordinal: int,
             force_stop: bool = False, a_start: Optional[float] = None) -> Plan:
        """One rollout. ``a_start``/``ramp`` make a takeover *part of the plan*:
        the rate-limited command is what the rollout predicts, so the plan and
        the execution cannot disagree by construction. Blending the command
        outside the plan (the obvious implementation) makes every takeover
        invalidate its own plan and cascades into a refusal loop -- the same
        class of defect as ERR-004, one layer down."""
        s = ego.copy()
        n = int(round(HORIZON / DT)) + 1
        xs, ys, vs, accs, kaps = [], [], [], [], []
        a_prev = ego.a if a_start is None else a_start
        jerk_step = JERK_LIMIT * DT
        for i in range(n):
            xs.append(s.x)
            ys.append(s.y)
            vs.append(s.v)
            if force_stop:
                a, k = -BRAKE_CMD_MAX, self.control(s, scene)[1]
            else:
                a, k = self.control(s, scene)
            if a_start is not None:
                # a jerk limit on the *release* only: a takeover may always
                # brake harder, because limiting the build-up of a safety
                # action is how a monitor turns a near miss into a hit. Held for
                # the whole rollout: stopping the limit at the end of a ramp
                # leaves a step exactly where the ramp meets the plan.
                a = min(a, a_prev + jerk_step)
            a_prev = a
            accs.append(a)
            kaps.append(k)
            self.plant.step(s, a, k, DT)
        return Plan(t, ordinal, xs, ys, vs, accs, kaps)


# ---------------------------------------------------------------------------
# the membrane, with the ERR-004 fix built in from the first line
# ---------------------------------------------------------------------------


class WitnessLedger:
    """One-shot, ordinal-bound registrations in a hash-linked chain.

    ``register`` binds a digest to ``(capability, ordinal)``; ``redeem`` accepts
    it exactly once, for that ordinal and no other. The first version of this
    gate in the manipulation audit was a set of digests, and a scene that
    regressed to an earlier state was accepted as evidence that the action had
    worked. A hash binds content; causation binds a prediction to a particular
    attempt. The ordinal is the attempt.
    """

    ZERO = "0" * 64

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._seq = 0
        self._prev = self.ZERO
        self._open: Dict[Tuple[str, int, int], str] = {}
        self._used: set = set()
        self.registrations = 0
        self.grants = 0
        self.refusals = 0
        self.replays = 0
        if path:
            with open(path, "w") as handle:
                handle.write("")

    @staticmethod
    def digest(capability: str, ordinal: int, slot: int, cell: Tuple[int, int, int]) -> str:
        payload = "{}:{}:{}:{},{},{}".format(capability, ordinal, slot, *cell)
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def _append(self, record: Dict[str, Any]) -> None:
        self._seq += 1
        record["seq"] = self._seq
        record["prev_hash"] = self._prev
        body = json.dumps(record, sort_keys=True).encode()
        self._prev = hashlib.sha256(body).hexdigest()
        record["entry_hash"] = self._prev
        if self.path:
            with open(self.path, "a") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    def register(self, capability: str, ordinal: int, slot: int) -> str:
        key = (capability, ordinal, slot)
        digest = self.digest(capability, ordinal, slot, (0, 0, 0))
        self._open[key] = digest
        self.registrations += 1
        self._append({"event": "registration", "capability": capability,
                      "ordinal": ordinal, "slot": slot, "digest": digest})
        return digest

    def redeem(self, capability: str, ordinal: int, slot: int,
               cell: Tuple[int, int, int],
               evidence: Optional[Dict[str, Any]] = None) -> bool:
        key = (capability, ordinal, slot)
        if key in self._used:
            self.replays += 1
            self.refusals += 1
            self._append({"event": "replay", "capability": capability,
                          "ordinal": ordinal, "slot": slot})
            return False
        expected = self._open.get(key)
        observed = self.digest(capability, ordinal, slot, cell)
        ok = expected is not None and observed == expected
        if ok:
            self._used.add(key)
            self.grants += 1
        else:
            self.refusals += 1
            self._used.add(key)
        record = {"event": "warrant" if ok else "refusal",
                  "capability": capability, "ordinal": ordinal, "slot": slot,
                  "expected": expected, "observed": observed}
        if evidence:
            record.update(evidence)
        self._append(record)
        return ok

    def chain_ok(self) -> bool:
        return self._prev != self.ZERO or self._seq == 0

    @staticmethod
    def verify(path: str) -> Tuple[bool, int, str]:
        """Re-walk the file from the genesis hash.

        ``chain_ok`` only says the process appended something. This says the
        bytes on disk are the bytes the chain claims: every entry hash is the
        hash of its own body, and every ``prev_hash`` is the previous entry's.
        A record edited after the fact breaks both. Returns
        ``(ok, entries, reason)``.
        """
        prev = WitnessLedger.ZERO
        seq = 0
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    return False, seq, "line {} is not JSON".format(seq + 1)
                claimed = record.pop("entry_hash", None)
                if claimed is None:
                    return False, seq, "line {} has no entry hash".format(seq + 1)
                if record.get("prev_hash") != prev:
                    return False, seq, "line {} does not link to its predecessor".format(seq + 1)
                body = json.dumps(record, sort_keys=True).encode()
                if hashlib.sha256(body).hexdigest() != claimed:
                    return False, seq, "line {} body does not hash to its entry".format(seq + 1)
                prev = claimed
                seq += 1
        return True, seq, "ok"


def cell_of(value: float, tolerance: float) -> int:
    """Quantise a deviation so that cell 0 means *inside* the tolerance."""
    return int(math.floor(value / (2.0 * tolerance) + 0.5))


# ---------------------------------------------------------------------------
# episode specification
# ---------------------------------------------------------------------------


@dataclass
class EpisodeSpec:
    index: int
    channel: str
    hazard: str
    ego_v0: float
    lead_gap0: float
    lead_v: float
    hazard_x: float
    ped_appear_dist: float
    ped_x: float
    lead_exit_x: float
    green_t: float
    novelty_t: Optional[float]
    conf_noise: List[float]
    seed: str

    @property
    def wet(self) -> bool:
        return self.channel in ("wet_road", "wet_and_lag")

    @property
    def lagged(self) -> bool:
        return self.channel in ("steering_lag", "wet_and_lag")

    def scenario(self) -> Tuple[Plant, Plant]:
        """(the plant the driver is in, the plan the planner believes)."""
        mu = MU_WET if self.wet else MU_NOMINAL
        tau_a = TAU_A_WORN if self.lagged else TAU_A_NOMINAL
        tau_s = TAU_S_WORN if self.lagged else TAU_S_NOMINAL
        gain = KAPPA_GAIN_WORN if self.lagged else 1.0
        return Plant(mu, tau_a, tau_s, gain), Plant(MU_NOMINAL, TAU_A_NOMINAL, TAU_S_NOMINAL)


def build_schedule(n_episodes: int, seed: int) -> List[EpisodeSpec]:
    """A declared list, identical for both arms. Never sampled per arm."""
    weights = {"nominal": 0.34, "wet_road": 0.30, "steering_lag": 0.26,
               "wet_and_lag": 0.10}
    counts = {k: int(round(w * n_episodes)) for k, w in weights.items()}
    drift = n_episodes - sum(counts.values())
    counts["wet_road"] += drift
    specs: List[EpisodeSpec] = []
    index = 0
    for channel in CHANNELS:
        for k in range(counts[channel]):
            rng = random.Random("{}:{}:{}".format(seed, channel, k))
            hazard = HAZARDS[k % len(HAZARDS)]
            ego_v0 = rng.uniform(12.6, 13.6)
            lead_gap0 = rng.uniform(27.0, 34.0)
            lead_v = ego_v0 - rng.uniform(0.6, 1.2)
            if hazard == "lead_brake":
                hazard_x = rng.uniform(196.0, 210.0)
                ped_x = 1e9
                ped_appear = 1e9
                lead_exit = math.inf
            else:
                hazard_x = 1e9
                ped_x = rng.uniform(228.0, 238.0)
                ped_appear = rng.uniform(30.0, 36.0)
                lead_exit = rng.uniform(158.0, 172.0)
            green_t = rng.uniform(9.0, 12.0)
            novelty_t = None
            if rng.random() < 0.30:
                # a roadside event that changes what the camera sees and
                # nothing about the physics: cones, a bus, a parked lorry.
                novelty_t = rng.uniform(3.0, 24.0)
            conf_noise = [CONF_BASE + rng.gauss(0.0, CONF_NOISE_SD)
                          for _ in range(200)]
            specs.append(EpisodeSpec(
                index=index, channel=channel, hazard=hazard, ego_v0=ego_v0,
                lead_gap0=lead_gap0, lead_v=lead_v, hazard_x=hazard_x,
                ped_appear_dist=ped_appear, ped_x=ped_x,
                lead_exit_x=lead_exit, green_t=green_t,
                novelty_t=novelty_t, conf_noise=conf_noise,
                seed="{}:{}:{}".format(seed, channel, k)))
            index += 1
    return specs


# ---------------------------------------------------------------------------
# controllers
# ---------------------------------------------------------------------------


class BaseController:
    """Shared plumbing: replan cadence, plan execution, bookkeeping."""

    def __init__(self, spec: EpisodeSpec) -> None:
        self.spec = spec
        self.plan: Optional[Plan] = None
        self.ordinal = 0
        self.next_replan = 0.0
        self.counters: Dict[str, int] = {
            "replans": 0, "vetoes": 0, "refusals": 0, "harsh_brakes": 0,
            "eb_events": 0, "witness_checks": 0, "stop_refusals": 0,
        }
        self.cruise_tick = 0
        self.takeover_until = -1.0
        self.last_cmd = (0.0, 0.0)

    def _confidence(self, t: float, s: VehicleState) -> float:
        raise NotImplementedError

    def _replan(self, t: float, s: VehicleState, scene: Scene) -> None:
        raise NotImplementedError

    def command(self, t: float, s: VehicleState, scene: Scene, dt: float) -> Tuple[float, float]:
        raise NotImplementedError

    def finish(self) -> Dict[str, Any]:
        return {}


class BlindPlanner(BaseController):
    """The counterfactual arm: the same planner, no gate at all.

    Used only to decide whether a brake or a refusal was *false* -- if this
    run arrives safely, then the intervention was not needed. Keeping it a
    separate episode, rather than a branch inside the main loop, means the
    counterfactual can never contaminate the run it scores.
    """

    def __init__(self, spec: EpisodeSpec) -> None:
        super().__init__(spec)
        self.planner = Planner()

    def _confidence(self, t: float, s: VehicleState) -> float:
        return 1.0

    def command(self, t: float, s: VehicleState, scene: Scene, dt: float) -> Tuple[float, float]:
        if self.plan is None or t >= self.next_replan - 1e-9:
            self._replan(t, s, scene)
        i = self.plan.index(t)
        return self.plan.a[i], self.plan.kappa[i]

    def _replan(self, t: float, s: VehicleState, scene: Scene) -> None:
        self.ordinal += 1
        self.plan = self.planner.plan(t, s, scene, self.ordinal)
        self.next_replan = t + REPLAN_DT
        self.counters["replans"] += 1


class SafePlanner(BaseController):
    """Arm A: a confidence gate. Confidence is a function of appearance only."""

    def __init__(self, spec: EpisodeSpec) -> None:
        super().__init__(spec)
        self.planner = Planner()
        self.eb = False
        self._was_eb = False

    def confidence(self, t: float, s: VehicleState) -> float:
        """A model of a confidence head, and its blind spot, stated plainly.

        It sees what the camera sees: whether there is a roadside novelty in
        view, how fast the scene is changing, and its own noise. It does **not**
        see the friction coefficient, the brake lag, or the rack gain, because
        none of those is in the image. So the gate is high and wrong exactly
        when the world changed and the picture did not.
        """
        cycle = min(len(self.spec.conf_noise) - 1, int(t / REPLAN_DT))
        conf = self.spec.conf_noise[cycle]
        nt = self.spec.novelty_t
        if nt is not None and 0.0 <= t - nt <= 2.5:
            conf -= CONF_NOVELTY
        return conf

    def command(self, t: float, s: VehicleState, scene: Scene, dt: float) -> Tuple[float, float]:
        if self.plan is None or t >= self.next_replan - 1e-9:
            self._replan(t, s, scene)
        if self.eb:
            # The standard filter: below threshold, brake hard. It cannot skip
            # its own plan (there is no other plan), so a wrong veto becomes
            # latency and discomfort, not safety.
            k = self.plan.kappa[self.plan.index(t)]
            return -EB_DECEL, k
        i = self.plan.index(t)
        return self.plan.a[i], self.plan.kappa[i]

    def _replan(self, t: float, s: VehicleState, scene: Scene) -> None:
        self.ordinal += 1
        self.plan = self.planner.plan(t, s, scene, self.ordinal)
        self.next_replan = t + REPLAN_DT
        self.counters["replans"] += 1
        conf = self.confidence(t, s)
        self.eb = conf < CONF_THRESHOLD
        if self.eb:
            self.counters["vetoes"] += 1
            if not self._was_eb:
                self.counters["eb_events"] += 1
        self._was_eb = self.eb


class CELController(BaseController):
    """Arm B: the layer. Register the plan, measure the deviation, identify,
    feed back, re-plan. Refusal is a re-estimate, not a retry."""

    def __init__(self, spec: EpisodeSpec, ledger: Optional[WitnessLedger] = None) -> None:
        super().__init__(spec)
        self.mu_hat = MU_NOMINAL
        self.tau_a_hat = TAU_A_NOMINAL
        self.tau_s_hat = TAU_S_NOMINAL
        self.planner = Planner(self.mu_hat, self.tau_a_hat, self.tau_s_hat)
        self.ledger = ledger
        self.prev_a = 0.0
        self.prev_kappa = 0.0
        self._tau_a_samples: List[float] = []
        self._tau_s_samples: List[float] = []
        self._max_decel = 0.0
        self._saturated = False
        self._cmd_hist: List[float] = []
        self._a_hist: List[float] = []
        self._blend_a = 0.0
        self._rng = random.Random(spec.seed + ":sensor")
        self.refusal_log: List[Tuple[float, float, float, float]] = []

    # -- estimates -------------------------------------------------------

    def _observe(self, s: VehicleState, a_cmd: float, kappa_cmd: float, dt: float) -> None:
        """Identify the plant from its response. Sensors only: the layer never
        reads ``mu`` or ``tau`` from the simulator.

        Two different experiments, deliberately separated:

        * **Grip** from *saturation*. When the commanded deceleration is large
          and the realised one is both large and below the command, the realised
          value is the friction limit: ``mu_hat = |a|/g``. Only the *max*
          observed value is kept, so a transient cannot depress the estimate.
        * **Lag** from *small steps*. The first-order response gives
          ``tau = dt*(cmd - a_prev)/(a_now - a_prev)``; that inversion is only
          valid while unsaturated, which is why the grip estimator and the lag
          estimator are fed disjoint samples.
        """
        # Grip is observed only at *steady state*: a first-order lag also makes
        # |a| < |cmd| for a few steps, and mistaking that transient for the
        # friction limit is how a layer talks itself into a wrong plan. The
        # command must have been flat for the whole window before the deficit
        # counts as saturation.
        self._cmd_hist.append(a_cmd)
        self._a_hist.append(s.a)
        if len(self._cmd_hist) > 12:
            self._cmd_hist.pop(0)
            self._a_hist.pop(0)
        stable = (len(self._cmd_hist) == 12
                  and (max(self._cmd_hist) - min(self._cmd_hist)) < 0.20
                  and (max(self._a_hist) - min(self._a_hist)) < 0.15)
        if stable and a_cmd < -3.0 and s.a > a_cmd + 0.8 and abs(s.a) > self._max_decel:
            self._max_decel = abs(s.a)
            self.mu_hat = min(MU_NOMINAL, max(0.15, self._max_decel / G))
            self._saturated = True
        if abs(a_cmd) < 2.5 and abs(a_cmd - self.prev_a) > 0.25:
            da = s.a - self.prev_a
            if abs(da) > 0.02 and abs(da) < abs(a_cmd - self.prev_a):
                tau = dt * (a_cmd - self.prev_a) / da
                if 0.02 < tau < 1.0:
                    self._tau_a_samples.append(tau)
                    if len(self._tau_a_samples) > 40:
                        self._tau_a_samples.pop(0)
                    self.tau_a_hat = statistics.median(self._tau_a_samples)
        k_limit = (self.mu_hat * G) / max(s.v * s.v, 1.0)
        if abs(kappa_cmd) < 0.85 * k_limit and abs(kappa_cmd - self.prev_kappa) > 1e-4:
            dk = s.kappa - self.prev_kappa
            if abs(dk) > 1e-6 and abs(dk) < abs(kappa_cmd - self.prev_kappa):
                tau = dt * (kappa_cmd - self.prev_kappa) / dk
                if 0.02 < tau < 1.0:
                    self._tau_s_samples.append(tau)
                    if len(self._tau_s_samples) > 40:
                        self._tau_s_samples.pop(0)
                    self.tau_s_hat = statistics.median(self._tau_s_samples)
        self.prev_a = s.a
        self.prev_kappa = s.kappa
        self._apply_beliefs()

    def _apply_beliefs(self) -> None:
        if self.mu_hat <= MU_NOMINAL + 1e-9:
            self.planner.mu = self.mu_hat
        self.planner.tau_a = self.tau_a_hat
        self.planner.tau_s = self.tau_s_hat
        self.planner.lead_comp = LAG_TRUST * max(0.0, self.tau_s_hat - TAU_S_NOMINAL)
        self.planner.plant = Plant(self.planner.mu, self.planner.tau_a,
                                   self.planner.tau_s)

    # -- witness ---------------------------------------------------------

    def _cap(self) -> str:
        return "execute:ep{}".format(self.spec.index)

    def _check(self, t: float, s: VehicleState, scene: Scene) -> bool:
        """One witness check. Returns True if the plan is still valid."""
        self.counters["witness_checks"] += 1
        slot = int(round((t - self.plan.t0) / WITNESS_DT))
        px, py, pv = self.plan.at(t)
        # measurement: a pose estimator, with a rare multipath/occlusion spike
        obs_x, obs_y, obs_v = s.x, s.y, s.v
        if self._rng.random() < SENSOR_GLITCH_P:
            obs_x += self._rng.choice((-1.0, 1.0)) * self._rng.uniform(0.6, 1.6)
            obs_y += self._rng.choice((-1.0, 1.0)) * self._rng.uniform(0.6, 1.6)
        dlon = obs_x - px
        dlat = obs_y - py
        dv = obs_v - pv
        cell = (cell_of(dlon, TOL_LON), cell_of(dlat, TOL_LAT), cell_of(dv, TOL_SPEED))
        ok = True
        if self.ledger is not None:
            ok = self.ledger.redeem(self._cap(), self.plan.ordinal, slot, cell,
                                    {"dlon": round(dlon, 3), "dlat": round(dlat, 3),
                                     "dv": round(dv, 3), "t": round(t, 3)})
        else:
            ok = cell == (0, 0, 0)
        if not ok:
            self.counters["refusals"] += 1
            self.refusal_log.append((t, dlon, dlat, dv))
        return ok

    def _stopping_witness(self, t: float, s: VehicleState, scene: Scene) -> bool:
        """The longitudinal check the brief names: is there enough road left to
        stop in, *given the grip this vehicle has demonstrated*?

        This is the check a confidence gate structurally cannot make, because
        it needs a measurement of the road, not an opinion about the picture.
        """
        need = self.planner.stopping_distance(s.v, STOP_MARGIN)
        target: Optional[float] = None
        if scene.lead is not None:
            target = scene.lead.x - CAR_LENGTH
        if scene.ped is not None and scene.ped.active and scene.ped.in_lane:
            if scene.ped.x > s.x:
                target = min(target, scene.ped.x) if target else scene.ped.x
        if target is None:
            return True
        return (target - s.x) >= need

    # -- control ---------------------------------------------------------

    def command(self, t: float, s: VehicleState, scene: Scene, dt: float) -> Tuple[float, float]:
        if self.plan is None or t >= self.next_replan - 1e-9:
            self._replan(t, s, scene)

        # witness at 10 Hz
        if abs((t / WITNESS_DT) - round(t / WITNESS_DT)) < 1e-6:
            if not self._check(t, s, scene):
                self._on_refusal(t, s, scene)

        i = self.plan.index(t)
        return self.plan.a[i], self.plan.kappa[i]

    def _on_refusal(self, t: float, s: VehicleState, scene: Scene) -> None:
        """A refusal is a measurement, not a retry: identify, feed back,
        re-derive. The takeover is *inside* the new plan as a jerk-limited
        ramp, so the smooth slow-down is predicted rather than improvised."""
        self._replan(t, s, scene, takeover=True)

    def _replan(self, t: float, s: VehicleState, scene: Scene,
                takeover: bool = False) -> None:
        self.ordinal += 1
        force_stop = False
        if not takeover and self._stopping_witness(t, s, scene) is False:
            self.counters["stop_refusals"] += 1
            force_stop = True
        self.plan = self.planner.plan(
            t, s, scene, self.ordinal, force_stop=force_stop,
            a_start=self.last_cmd[0] if takeover else None)
        self.next_replan = t + REPLAN_DT
        self.counters["replans"] += 1
        if self.ledger is not None:
            for slot in range(int(round(REPLAN_DT / WITNESS_DT)) + 1):
                if self.plan.t0 + slot * WITNESS_DT <= t + REPLAN_DT + 1e-9:
                    self.ledger.register(self._cap(), self.plan.ordinal, slot)


# ---------------------------------------------------------------------------
# one episode
# ---------------------------------------------------------------------------


def run_episode(spec: EpisodeSpec, arm: str = "B_cel",
                ledger: Optional[WitnessLedger] = None,
                capture: bool = False) -> Dict[str, Any]:
    """Run one episode. ``arm`` is 'A_safe_planner', 'B_cel' or 'blind'."""
    plant, believed = spec.scenario()
    if arm == "A_safe_planner":
        ctrl: BaseController = SafePlanner(spec)
    elif arm == "B_cel":
        ctrl = CELController(spec, ledger)
    elif arm == "blind":
        ctrl = BlindPlanner(spec)
    else:
        raise ValueError(arm)

    ego = VehicleState(x=0.0, y=road_y(0.0), psi=math.atan(road_dy(0.0)),
                       v=spec.ego_v0)
    lead: Optional[LeadVehicle] = None
    if spec.hazard == "lead_brake":
        lead = LeadVehicle(x=spec.lead_gap0 + CAR_LENGTH, y=road_y(spec.lead_gap0),
                           v=spec.lead_v, stop_x=110.6, green_t=spec.green_t,
                           brake_at_x=spec.hazard_x)
    else:
        # the pedestrian episodes still queue behind a lead, which then turns off
        lead = LeadVehicle(x=spec.lead_gap0 + CAR_LENGTH, y=road_y(spec.lead_gap0),
                           v=spec.lead_v, stop_x=110.6, green_t=spec.green_t,
                           exit_x=spec.lead_exit_x)
    ped = None
    if spec.hazard == "pedestrian":
        ped = Pedestrian(x=spec.ped_x, y=PED_START_Y, active=False)
    scene = Scene(0.0, lead, ped, red_light=True)

    t = 0.0
    collision = False
    collision_kind = ""
    impact_speed = 0.0
    min_gap = math.inf
    lane_departure = False
    stop_line_violation = False
    max_lat_err = 0.0
    ped_deficit = None
    ped_v = None
    ped_t = None
    harsh = 0
    trace: List[Tuple[float, float, float, float, float]] = []
    ped_trigger_x = spec.ped_x - spec.ped_appear_dist
    arrived = False
    stalled_since = None
    harsh_run = 0
    in_harsh = False

    while t < TIME_LIMIT:
        scene.t = t
        scene.red_light = t < spec.green_t
        if lead is not None:
            lead.update(t, DT)
            if lead.exited:
                lead = None
                scene.lead = None
        if lead is not None:
            gap = lead.x - ego.x - CAR_LENGTH
            min_gap = min(min_gap, gap)
            if gap < 0.30 and abs(ego.y - lead.y) < LANE_HALF + 0.15:
                collision, collision_kind, impact_speed = True, "lead", ego.v
                break
        if ped is not None:
            if not ped.active and ego.x >= ped_trigger_x:
                ped.active = True
                ped_t = t
                ped_v = ego.v
                # what the car needs at this instant vs what it has. This is a
                # measured deficit, not a scripted outcome: on a dry road it is
                # negative, and the drift is what makes it positive.
                ped_deficit = (
                    ego.v * ego.v / (2.0 * min(plant.mu * G, BRAKE_CMD_MAX))
                    + ego.v * plant.tau_a + STOP_MARGIN
                ) - (ped.x - ego.x)
            ped.update(DT)
            if ped.in_lane and abs(ped.x - ego.x) < 2.30 + 0.30 \
                    and abs(ped.y - ego.y) < 1.00 + 0.35:
                collision, collision_kind, impact_speed = True, "pedestrian", ego.v
                break

        a_cmd, k_cmd = ctrl.command(t, ego, scene, DT)
        if a_cmd <= -HARSH_DECEL:
            harsh_run += 1
        else:
            harsh_run = 0
        if harsh_run == 6 and not in_harsh:
            harsh += 1
            in_harsh = True
        elif harsh_run == 0 and in_harsh:
            in_harsh = False
        if capture:
            trace.append((t, ego.x, ego.v, a_cmd, ego.y))

        plant.step(ego, a_cmd, k_cmd, DT)
        if isinstance(ctrl, CELController):
            ctrl.last_cmd = (a_cmd, k_cmd)
            ctrl._observe(ego, a_cmd, k_cmd, DT)
        prev_a_cmd = a_cmd
        t += DT

        max_lat_err = max(max_lat_err, abs(ego.y - road_y(ego.x)))
        if scene.red_light and ego.x > STOP_LINE_X:
            stop_line_violation = True
        if abs(ego.y - road_y(ego.x)) > ROAD_EDGE:
            lane_departure = True
            break
        if ego.x >= GOAL_X:
            arrived = True
            break
        if ego.v < 0.15:
            if stalled_since is None:
                stalled_since = t
            elif t - stalled_since > 8.0:
                break
        else:
            stalled_since = None

    result = {
        "arm": arm,
        "episode": spec.index,
        "channel": spec.channel,
        "hazard": spec.hazard,
        "seed": spec.seed,
        "arrived": arrived,
        "collision": collision,
        "collision_kind": collision_kind,
        "lane_departure": lane_departure,
        "lane_paint_crossing": max_lat_err > LANE_HALF,
        "max_lat_err_m": round(max_lat_err, 3),
        "hazard_deficit_m": "" if ped_deficit is None else round(ped_deficit, 2),
        "hazard_speed_mps": "" if ped_v is None else round(ped_v, 2),
        "stop_line_violation": stop_line_violation,
        "success": bool(arrived and not collision and not lane_departure),
        "arrival_time_s": round(t, 2),
        "impact_speed": round(impact_speed, 2),
        "min_gap_m": round(min_gap, 2) if min_gap != math.inf else "",
        "harsh_brakes": harsh,
        "replans": ctrl.counters["replans"],
        "vetoes": ctrl.counters.get("vetoes", 0),
        "eb_events": ctrl.counters.get("eb_events", 0),
        "refusals": ctrl.counters.get("refusals", 0),
        "stop_refusals": ctrl.counters.get("stop_refusals", 0),
        "witness_checks": ctrl.counters.get("witness_checks", 0),
        "replays": getattr(ctrl, "ledger", None).replays if isinstance(ctrl, CELController) and ctrl.ledger else 0,
        "mu_true": round(plant.mu, 3),
        "tau_a_true": round(plant.tau_a, 3),
        "tau_s_true": round(plant.tau_s, 3),
        "mu_hat": round(getattr(ctrl, "mu_hat", MU_NOMINAL), 3),
        "tau_a_hat": round(getattr(ctrl, "tau_a_hat", TAU_A_NOMINAL), 3),
        "tau_s_hat": round(getattr(ctrl, "tau_s_hat", TAU_S_NOMINAL), 3),
        "ped_t": ped_t,
        "trace": trace if capture else None,
    }
    return result


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.959963985) -> Tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarise(results: Sequence[Dict[str, Any]],
              counterfactual: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate, plus the two counterfactual attributions.

    ``false_eb`` / ``false_refusal``: an episode counted here had the
    intervention *and* the blind replay arrived safely. That is the only
    definition of "false" that is not a guess about intent.
    """
    out: Dict[str, Any] = {"arms": {}, "by_channel": {}}
    for arm in ("A_safe_planner", "B_cel"):
        rows = [r for r in results if r["arm"] == arm]
        out["arms"][arm] = _aggregate(rows, counterfactual)
        out["by_channel"][arm] = {}
        for channel in CHANNELS:
            sub = [r for r in rows if r["channel"] == channel]
            out["by_channel"][arm][channel] = _aggregate(sub, counterfactual)
    return out


def _aggregate(rows: Sequence[Dict[str, Any]],
               counterfactual: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"episodes": 0}
    successes = sum(1 for r in rows if r["success"])
    collisions = sum(1 for r in rows if r["collision"])
    departures = sum(1 for r in rows if r["lane_departure"])
    lo, hi = wilson(successes, n)
    false_eb = sum(1 for r in rows
                   if r["eb_events"] > 0
                   and counterfactual.get(r["episode"], {}).get("success"))
    false_ref = sum(1 for r in rows
                    if r["refusals"] > 0
                    and counterfactual.get(r["episode"], {}).get("success"))
    times = [r["arrival_time_s"] for r in rows if r["arrived"]]
    gaps = [r["min_gap_m"] for r in rows if isinstance(r["min_gap_m"], (int, float))]
    impacts = [r["impact_speed"] for r in rows if r["collision"]]
    deficits = [r["hazard_deficit_m"] for r in rows
                if isinstance(r.get("hazard_deficit_m"), (int, float))]
    ped_deficits = [r["hazard_deficit_m"] for r in rows
                    if r["hazard"] == "pedestrian"
                    and isinstance(r.get("hazard_deficit_m"), (int, float))]
    return {
        "episodes": n,
        "success": successes,
        "success_rate": round(100.0 * successes / n, 1),
        "success_lo": round(100.0 * lo, 1),
        "success_hi": round(100.0 * hi, 1),
        "collisions": collisions,
        "collision_rate": round(100.0 * collisions / n, 1),
        "collision_kinds": {
            kind: sum(1 for r in rows if r["collision_kind"] == kind)
            for kind in ("lead", "pedestrian")
        },
        "lane_departures": departures,
        "lane_paint_crossings": sum(1 for r in rows if r.get("lane_paint_crossing")),
        "max_lat_err_mean_m": round(statistics.mean(r["max_lat_err_m"] for r in rows), 3),
        "max_lat_err_p90_m": round(sorted(r["max_lat_err_m"] for r in rows)[int(0.9 * (n - 1))], 3),
        "stop_line_violations": sum(1 for r in rows if r.get("stop_line_violation")),
        "arrival_time_mean_s": round(statistics.mean(times), 2) if times else None,
        "arrival_time_median_s": round(statistics.median(times), 2) if times else None,
        "episode_time_mean_s": round(statistics.mean(r["arrival_time_s"] for r in rows), 2),
        "min_gap_mean_m": round(statistics.mean(gaps), 2) if gaps else None,
        "min_gap_min_m": round(min(gaps), 2) if gaps else None,
        "impact_speed_mean": round(statistics.mean(impacts), 2) if impacts else None,
        "hazard_deficit_mean_m": round(statistics.mean(deficits), 2) if deficits else None,
        "pedestrian_deficit_mean_m": (round(statistics.mean(ped_deficits), 2)
                                      if ped_deficits else None),
        "harsh_brake_events": sum(r["harsh_brakes"] for r in rows),
        "harsh_brake_rate": round(100.0 * sum(1 for r in rows if r["harsh_brakes"] > 0) / n, 1),
        "eb_events": sum(r["eb_events"] for r in rows),
        "false_eb_episodes": false_eb,
        "false_eb_rate": round(100.0 * false_eb / n, 1),
        "refusals": sum(r["refusals"] for r in rows),
        "stop_refusals": sum(r["stop_refusals"] for r in rows),
        "false_refusal_episodes": false_ref,
        "false_refusal_rate": round(100.0 * false_ref / n, 1),
        "witness_checks": sum(r["witness_checks"] for r in rows),
        "replans": sum(r["replans"] for r in rows),
        "mu_hat_mean": round(statistics.mean(r["mu_hat"] for r in rows), 3),
        "tau_a_hat_mean": round(statistics.mean(r["tau_a_hat"] for r in rows), 3),
        "tau_s_hat_mean": round(statistics.mean(r["tau_s_hat"] for r in rows), 3),
        "replays": sum(r["replays"] for r in rows),
    }


# ---------------------------------------------------------------------------
# console
# ---------------------------------------------------------------------------


def _row(label: str, a: Dict[str, Any], b: Dict[str, Any], n: int) -> str:
    return ("{:<22} {:>7} {:>8} {:>10} {:>9} {:>7} {:>7}".format(
        label, n, "{:.1f}".format(a["success_rate"]),
        "{:.1f}".format(a["collision_rate"]), "{:.1f}".format(b["success_rate"]),
        "{:.1f}".format(b["collision_rate"]), "{:.0f}".format(a["harsh_brake_events"])))


def print_report(summary: Dict[str, Any]) -> None:
    print()
    print("=" * 78)
    print("CEL vs. Safe-Planner, 2-D road model -- per channel")
    print("=" * 78)
    print("{:<22} {:>7} {:>8} {:>10} {:>9} {:>7} {:>7}".format(
        "", "n", "A succ%", "A coll%", "B succ%", "B coll%", "A harsh"))
    print("-" * 78)
    for channel in CHANNELS:
        a = summary["by_channel"]["A_safe_planner"][channel]
        b = summary["by_channel"]["B_cel"][channel]
        print(_row(channel, a, b, a["episodes"]))
    a = summary["arms"]["A_safe_planner"]
    b = summary["arms"]["B_cel"]
    print("-" * 78)
    print(_row("all", a, b, a["episodes"]))
    print()
    print("{:<22} {:>10} {:>10} {:>10} {:>10}".format(
        "", "A time(s)", "B time(s)", "A minGap", "B minGap"))
    print("-" * 78)
    for channel in CHANNELS:
        a = summary["by_channel"]["A_safe_planner"][channel]
        b = summary["by_channel"]["B_cel"][channel]
        print("{:<22} {:>10} {:>10} {:>10} {:>10}".format(
            channel,
            "{:.1f}".format(a["arrival_time_mean_s"] or 0.0),
            "{:.1f}".format(b["arrival_time_mean_s"] or 0.0),
            "{:.1f}".format(a["min_gap_mean_m"] or -1.0),
            "{:.1f}".format(b["min_gap_mean_m"] or -1.0)))

    print()
    print("{:<22} {:>9} {:>9} {:>9} {:>9}".format(
        "", "A latMax", "B latMax", "A paint%", "B paint%"))
    print("-" * 78)
    for channel in CHANNELS:
        a = summary["by_channel"]["A_safe_planner"][channel]
        b = summary["by_channel"]["B_cel"][channel]
        print("{:<22} {:>9} {:>9} {:>9} {:>9}".format(
            channel,
            "{:.2f} m".format(a["max_lat_err_p90_m"]),
            "{:.2f} m".format(b["max_lat_err_p90_m"]),
            "{:.0f}".format(100.0 * a["lane_paint_crossings"] / max(1, a["episodes"])),
            "{:.0f}".format(100.0 * b["lane_paint_crossings"] / max(1, b["episodes"]))))
    print("(p90 of the worst cross-track error; lane half-width {:.2f} m, so any".format(LANE_HALF))
    print(" paint% above zero is the car putting a wheel over the centre line)")

    print()
    print("=" * 78)
    print("STANDARD METRICS, per 100 episodes (the brief's table)")
    print("=" * 78)
    print("{:<22} {:>12} {:>12} {:>12} {:>12} {:>12}".format(
        "", "collision%", "success%", "time(s)", "harsh brakes", "falseEB/ref"))
    print("-" * 78)
    for channel in CHANNELS:
        a = summary["by_channel"]["A_safe_planner"][channel]
        b = summary["by_channel"]["B_cel"][channel]
        print("{:<22} {:>12} {:>12} {:>12} {:>12} {:>12}".format(
            channel,
            "{:.0f} / {:.0f}".format(a["collision_rate"], b["collision_rate"]),
            "{:.0f} / {:.0f}".format(a["success_rate"], b["success_rate"]),
            "{:.1f} / {:.1f}".format(a["arrival_time_mean_s"] or 0.0,
                                     b["arrival_time_mean_s"] or 0.0),
            "{:.0f} / {:.0f}".format(100.0 * a["harsh_brake_events"] / max(1, a["episodes"]),
                                     100.0 * b["harsh_brake_events"] / max(1, b["episodes"])),
            "{:.0f} / {:.0f}".format(100.0 * a["eb_events"] / max(1, a["episodes"]),
                                     100.0 * b["refusals"] / max(1, b["episodes"]))))
    print("(cells are Safe-Planner / CEL. 'falseEB/ref' counts A's emergency brakes")
    print(" and B's refusals per 100 episodes, each only where the blind replay")
    print(" arrives safely. They are different instruments: an EB is a 6 m/s^2")
    print(" stop held until replan, a refusal is one 10 Hz tick of take-over.)")
    print(" Both arms inherit the same planner, whose light-stop is already ~2")
    print(" harsh brakes per episode: read the difference, not the absolute rate.")

    print()
    print("=" * 78)
    print("WHY THE PEDESTRIAN COLLISIONS HAPPEN (measured, not scripted)")
    print("=" * 78)
    print("  braking distance the car needs (at the friction limit, lag included)")
    print("  minus the distance it has, at the instant the pedestrian appears:")
    for channel in CHANNELS:
        a = summary["by_channel"]["A_safe_planner"][channel]
        b = summary["by_channel"]["B_cel"][channel]
        if a["pedestrian_deficit_mean_m"] is None:
            continue
        print("    {:<12} Safe-Planner {:>6.1f} m      CEL {:>6.1f} m".format(
            channel, a["pedestrian_deficit_mean_m"],
            b["pedestrian_deficit_mean_m"]))
    print("  negative = room to stop. On dry tarmac the car has >12 m spare, so no")
    print("  collision. Wet puts the same appearance at ~0 m, and the planner's own")
    print("  0.2 s of decision latency is ~2.5 m: the deficit turns into an impact.")
    print("  CEL is negative everywhere because it already spent the light stop")

    print()
    print("=" * 78)
    print("WET ROAD (the channel the brief asks about)")
    print("=" * 78)
    for channel in ("wet_road", "wet_and_lag"):
        a = summary["by_channel"]["A_safe_planner"][channel]
        b = summary["by_channel"]["B_cel"][channel]
        print("channel {}  (n={} per arm)".format(channel, a["episodes"]))
        print("  collisions      Safe-Planner {:>5.1f}%  ({}/{})    CEL {:>5.1f}%  ({}/{})".format(
            a["collision_rate"], a["collisions"], a["episodes"],
            b["collision_rate"], b["collisions"], b["episodes"]))
        print("  harsh brakes    Safe-Planner {:>5d} events ({:.1f}% of eps)   "
              "CEL {:>5d} events ({:.1f}%)".format(
                  a["harsh_brake_events"], a["harsh_brake_rate"],
                  b["harsh_brake_events"], b["harsh_brake_rate"]))
        print("  emergency brks  Safe-Planner {:>5d} events, {} false ({:.1f}%)".format(
            a["eb_events"], a["false_eb_episodes"], a["false_eb_rate"]))
        print("  witness refusals CEL {:>5d}  ({} episodes; {} of them where the "
              "blind run would have succeeded)".format(
                  b["refusals"], b["false_refusal_episodes"], b["false_refusal_episodes"]))
        print("  mean min gap    Safe-Planner {:>5.1f} m   CEL {:>5.1f} m".format(
            a["min_gap_mean_m"] or -1.0, b["min_gap_mean_m"] or -1.0))
        if a["impact_speed_mean"] is not None:
            print("  mean impact speed when it crashed: {:.1f} m/s".format(
                a["impact_speed_mean"]))
        print("  identified mu   CEL {:>5.2f}  (true {:.2f})".format(
            b["mu_hat_mean"], MU_WET))
        if a["pedestrian_deficit_mean_m"] is not None:
            print("  stopping deficit at the moment the pedestrian appears "
                  "(needed - available):")
            print("                  Safe-Planner {:>6.1f} m   CEL {:>6.1f} m".format(
                a["pedestrian_deficit_mean_m"], b["pedestrian_deficit_mean_m"]))
        print()

    print("=" * 78)
    print("THE GATE'S COST")
    print("=" * 78)
    a = summary["arms"]["A_safe_planner"]
    b = summary["arms"]["B_cel"]
    print("  emergency-brake events   A={:>4d}  B={:>4d}".format(a["eb_events"], b["eb_events"]))
    print("  false brakes / refusals  A={:>4d} eps ({:.1f}%)  B={:>4d} eps ({:.1f}%)".format(
        a["false_eb_episodes"], a["false_eb_rate"],
        b["false_refusal_episodes"], b["false_refusal_rate"]))
    print("  harsh-brake events       A={:>4d}  B={:>4d}".format(
        a["harsh_brake_events"], b["harsh_brake_events"]))
    print("  witness checks           A={:>4d}  B={:>6d}".format(
        a["witness_checks"], b["witness_checks"]))
    print("  replays refused          B={:>4d}  (ordinal binding, ERR-004)".format(b["replays"]))
    print("  mean episode time        A={:>5.1f}s  B={:>5.1f}s".format(
        a["episode_time_mean_s"], b["episode_time_mean_s"]))
    print()
    print("Threats: the confidence signal is a model of a head, not a trained")
    print("one; the planner reads privileged actor state, not a detector; and")
    print("the route is one geometry with a seeded hazard, so the rates are")
    print("estimates of a mechanism, not of a fleet.")
    print()


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------


COLUMNS = ["arm", "episode", "channel", "hazard", "seed", "success", "arrived",
           "collision", "collision_kind", "lane_departure", "lane_paint_crossing",
           "max_lat_err_m", "hazard_deficit_m", "hazard_speed_mps",
           "stop_line_violation", "arrival_time_s",
           "impact_speed", "min_gap_m", "harsh_brakes", "replans", "vetoes",
           "eb_events", "refusals", "stop_refusals", "witness_checks", "replays",
           "mu_true", "mu_hat", "tau_a_true", "tau_a_hat", "tau_s_true", "tau_s_hat"]


def write_csv(results: Sequence[Dict[str, Any]], path: str) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in results:
            writer.writerow({k: row[k] for k in COLUMNS})


def _figure(summary: Dict[str, Any], results: Sequence[Dict[str, Any]],
            traces: Dict[str, Optional[Dict[str, Any]]], path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    safe_c, cel_c = "#c0392b", "#1f6fbb"
    channels = list(CHANNELS)
    labels = ["nominal", "wet", "lag", "wet+lag"]
    xs = range(len(channels))

    # Drawn close to its printed width (a two-column IEEE figure is ~7 in), so
    # the labels land at 7-8 pt instead of being shrunk to 4 pt by the export.
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.3))
    fig.subplots_adjust(hspace=0.42, wspace=0.26)

    # 1. success, split by channel
    ax = axes[0, 0]
    a = [summary["by_channel"]["A_safe_planner"][c]["success_rate"] for c in channels]
    b = [summary["by_channel"]["B_cel"][c]["success_rate"] for c in channels]
    w = 0.38
    ax.bar([i - w / 2 for i in xs], a, w, color=safe_c, label="Safe-Planner")
    ax.bar([i + w / 2 for i in xs], b, w, color=cel_c, label="CEL")
    for i, (av, bv) in enumerate(zip(a, b)):
        ax.text(i - w / 2, av + 2, "{:.0f}".format(av), ha="center", fontsize=9)
        ax.text(i + w / 2, bv + 2, "{:.0f}".format(bv), ha="center", fontsize=9)
    ax.set_ylim(0, 112)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_ylabel("task success (%)")
    ax.set_title("(a) Success rate by world", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    ax.grid(axis="y", alpha=0.25)

    # 2. collisions, with the kinds stacked
    ax = axes[0, 1]
    for j, (arm, colour, name) in enumerate((("A_safe_planner", safe_c, "Safe"),
                                             ("B_cel", cel_c, "CEL"))):
        lead = [summary["by_channel"][arm][c]["collision_kinds"]["lead"] for c in channels]
        ped = [summary["by_channel"][arm][c]["collision_kinds"]["pedestrian"] for c in channels]
        off = -w / 2 if j == 0 else w / 2
        ax.bar([i + off for i in xs], lead, w, color=colour, label="{} / lead".format(name))
        ax.bar([i + off for i in xs], ped, w, bottom=lead, color=colour,
               alpha=0.45, hatch="//", label="{} / pedestrian".format(name))
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_ylabel("collisions per 100 episodes")
    ax.set_title("(b) Collisions, by cause", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=7, ncol=2)
    ax.grid(axis="y", alpha=0.25)

    # 3. what the gate did: emergency brakes and refusals
    ax = axes[1, 0]
    a_eb = [summary["by_channel"]["A_safe_planner"][c]["eb_events"] for c in channels]
    a_harsh = [summary["by_channel"]["A_safe_planner"][c]["harsh_brake_events"] for c in channels]
    b_ref = [summary["by_channel"]["B_cel"][c]["refusals"] for c in channels]
    b_stop = [summary["by_channel"]["B_cel"][c]["stop_refusals"] for c in channels]
    ax.bar([i - w / 2 for i in xs], a_harsh, w, color=safe_c,
           label="A: harsh brakes")
    ax.bar([i - w / 2 for i in xs], a_eb, w, color="black", alpha=0.55,
           label="A: of which emergency")
    ax.bar([i + w / 2 for i in xs], b_ref, w, color=cel_c, label="B: witness refusals")
    ax.bar([i + w / 2 for i in xs], b_stop, w, bottom=b_ref, color=cel_c, alpha=0.45,
           hatch="//", label="B: stopping-distance")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_ylabel("events")
    ax.set_title("(c) Interventions: the gate's output vs the layer's", fontsize=10,
                 loc="left")
    ax.legend(frameon=False, fontsize=7)
    ax.grid(axis="y", alpha=0.25)

    # 4. a trace: the mechanism, not just the rate
    ax = axes[1, 1]
    plotted = False
    ax_cmd = ax.twinx()
    seen = {}
    for arm, colour, name in (("A_safe_planner", safe_c, "Safe-Planner"),
                              ("B_cel", cel_c, "CEL")):
        tr = traces.get(arm)
        if not tr or not tr["trace"]:
            continue
        ts = [row[0] for row in tr["trace"]]
        vs = [row[2] for row in tr["trace"]]
        cmds = [row[3] for row in tr["trace"]]
        ax.plot(ts, vs, color=colour, lw=1.9, label="{} speed".format(name))
        ax_cmd.plot(ts, cmds, color=colour, lw=1.0, ls="--", alpha=0.65,
                    label="{} command".format(name))
        plotted = True
        if tr["collision"]:
            ax.plot([ts[-1]], [vs[-1]], "x", color=colour, ms=10, mew=2)
            ax.annotate("impact at {:.1f} m/s".format(tr["impact_speed"]),
                        (ts[-1], vs[-1]), textcoords="offset points",
                        xytext=(-10, 10), fontsize=8, color=colour, ha="right")
        seen[arm] = tr
    if plotted:
        tr = list(seen.values())[0]
        if tr.get("ped_t"):
            ax.axvline(tr["ped_t"], color="black", lw=0.9, ls=":")
            ax.annotate("pedestrian appears", xy=(tr["ped_t"], 0.965),
                        xycoords=ax.get_xaxis_transform(), fontsize=7.5,
                        ha="left", va="top", textcoords="offset points",
                        xytext=(3, 0))
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xlabel("time since episode start (s)")
        ax.set_ylabel("ego speed (m/s)")
        ax_cmd.set_ylabel("commanded accel (m/s$^2$)", color="#555555")
        ax_cmd.axhline(-BRAKE_CMD_MAX, color="#555555", lw=0.5, ls=":")
        ax_cmd.tick_params(axis="y", colors="#555555", labelsize=8)
        ax.set_title("(d) Same wet episode: speed (solid), command (dashed)",
                     fontsize=10, loc="left")
        lines = ax.get_legend_handles_labels()
        lines2 = ax_cmd.get_legend_handles_labels()
        ax.legend(lines[0] + lines2[0], lines[1] + lines2[1], frameon=False,
                  fontsize=7, loc="lower left", ncol=2)
        ax.grid(alpha=0.25)
    else:
        ax.axis("off")
        ax_cmd.axis("off")

    fig.suptitle("CEL vs. confidence gating, 2-D urban driving "
                 "(100 episodes per arm, identical planner and route)",
                 fontsize=11)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _pick_trace_index(specs: Sequence[EpisodeSpec]) -> Optional[int]:
    for spec in specs:
        if spec.channel == "wet_road" and spec.hazard == "pedestrian":
            return spec.index
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CEL vs. a confidence-gated planner in a 2-D road model")
    parser.add_argument("--episodes", type=int, default=100,
                        help="episodes per arm (default 100)")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--probe", type=int, default=0,
                        help="print one diagnostic line per episode, first N only")
    parser.add_argument("--no-ledger", action="store_true",
                        help="do not write the hash-linked ledger")
    parser.add_argument("--no-figure", action="store_true")
    parser.add_argument("--trace-json", default=None,
                        help="also write the traced wet-pedestrian episode as "
                             "JSON, so figures can be redrawn from the archive")
    args = parser.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    specs = build_schedule(args.episodes, args.seed)

    ledger_path = None
    if not args.no_ledger:
        ledger_path = os.path.join(args.out_dir, "ad_cel_ledger.jsonl")
    ledger = WitnessLedger(ledger_path)

    # The counterfactual is a separate pass with the gate removed: the same
    # episode and the same seed, so it cannot leak into the run it scores.
    blind: Dict[int, Dict[str, Any]] = {}
    for spec in specs:
        blind[spec.index] = run_episode(spec, "blind")

    results: List[Dict[str, Any]] = []
    for arm, label in (("A_safe_planner", "Safe-Planner"), ("B_cel", "CEL")):
        for spec in specs:
            row = run_episode(spec, arm, ledger=ledger if arm == "B_cel" else None)
            results.append(row)
            if args.probe and spec.index < args.probe:
                print("[{:>3}] {:<12} {:<10} {:<11} succ={:<5} coll={:<5} "
                      "t={:>5.1f}s gap={:>6} harsh={} eb={} ref={} "
                      "mu_hat={:.2f} tau_a_hat={:.2f} tau_s_hat={:.2f}".format(
                          spec.index, spec.channel, spec.hazard, label,
                          str(row["success"]), str(row["collision"]),
                          row["arrival_time_s"], row["min_gap_m"],
                          row["harsh_brakes"], row["eb_events"],
                          row["refusals"], row["mu_hat"], row["tau_a_hat"],
                          row["tau_s_hat"]))

    summary = summarise(results, blind)
    summary["config"] = {
        "episodes_per_arm": args.episodes,
        "seed": args.seed,
        "dt_s": DT,
        "replan_s": REPLAN_DT,
        "witness_rate_hz": round(1.0 / WITNESS_DT, 1),
        "tolerances": {"lateral_m": TOL_LAT, "longitudinal_m": TOL_LON,
                       "speed_mps": TOL_SPEED},
        "friction": {"nominal": MU_NOMINAL, "wet": MU_WET},
        "actuator_lag": {"nominal_s": TAU_A_NOMINAL, "worn_s": TAU_A_WORN,
                         "steering_nominal_s": TAU_S_NOMINAL,
                         "steering_worn_s": TAU_S_WORN},
        "confidence_threshold": CONF_THRESHOLD,
        # Everything the paper quotes in its protocol paragraph lives here, so
        # the prose inputs a measurement of the run rather than a retyped
        # constant that can drift away from the code.
        "route": {"length_m": GOAL_X, "stop_line_m": STOP_LINE_X,
                  "crossing_m": CROSSING_X, "lane_half_width_m": LANE_HALF,
                  "planner_horizon_s": HORIZON,
                  "planner_rate_hz": round(1.0 / REPLAN_DT, 1),
                  "ped_appear_dist_m": (30.0, 36.0),
                  "sight_past_occlusion_m": VISIBLE_PAST_OCCLUSION},
        "control": {"emergency_brake_mps2": EB_DECEL,
                    "harsh_brake_mps2": HARSH_DECEL,
                    "comfort_decel_mps2": COMFORT_DECEL,
                    "brake_command_max_mps2": BRAKE_CMD_MAX,
                    "lead_brake_mps2": LEAD_BRAKE_DECEL,
                    "jerk_limit_mps3": JERK_LIMIT,
                    "speed_gain_per_s": SPEED_GAIN,
                    "stop_margin_m": STOP_MARGIN},
        "model": "2-D bicycle, friction circle, first-order actuator lags",
        "not_measured": ["perception", "a trained confidence head",
                         "rendering / appearance"],
    }
    summary["ledger"] = {
        "path": ledger_path,
        "registrations": ledger.registrations,
        "grants": ledger.grants,
        "refusals": ledger.refusals,
        "replays_refused": ledger.replays,
        "chain_head": ledger._prev,
    }

    data_path = os.path.join(args.out_dir, "ad_cel_data.csv")
    json_path = os.path.join(args.out_dir, "ad_cel_summary.json")
    fig_path = os.path.join(args.out_dir, "ad_cel_figure.png")
    write_csv(results, data_path)
    with open(json_path, "w") as handle:
        json.dump(summary, handle, indent=2)

    traces: Dict[str, Optional[Dict[str, Any]]] = {}
    # Traces are needed either to draw the figure or to be *shipped* so that a
    # figure can be redrawn later without re-running the audit. The latter
    # matters because a figure whose numbers cannot be traced back to a file in
    # the archive is a claim with no witness -- the failure this paper is about.
    if (not args.no_figure) or args.trace_json:
        idx = _pick_trace_index(specs)
        if idx is not None:
            spec = next(s for s in specs if s.index == idx)
            traces["A_safe_planner"] = run_episode(spec, "A_safe_planner", capture=True)
            traces["B_cel"] = run_episode(spec, "B_cel", capture=True)
    if args.trace_json:
        payload = {
            "episode": idx,
            "channel": "wet_road",
            "hazard": "pedestrian",
            "seed": "{seed}:wet_road:{index}".format(seed=args.seed, index=idx),
            "ped_t": (traces.get("A_safe_planner") or {}).get("ped_t"),
            "columns": ["t", "x", "v", "a_cmd", "y"],
            "arms": {
                arm: (tr or {}).get("trace") or []
                for arm, tr in traces.items()
            },
        }
        with open(args.trace_json, "w") as handle:
            json.dump(payload, handle, indent=1)
        print("wrote {}".format(args.trace_json))
    if not args.no_figure:
        _figure(summary, results, traces, fig_path)

    print_report(summary)
    print("wrote {}".format(data_path))
    print("wrote {}".format(json_path))
    if not args.no_figure:
        print("wrote {}".format(fig_path))
    if ledger_path:
        print("wrote {}  ({} registrations, {} grants, {} refusals, {} replays "
              "refused)".format(ledger_path, ledger.registrations, ledger.grants,
                                ledger.refusals, ledger.replays))
    print("ledger chain head: {}".format(ledger._prev))
    if ledger_path:
        ok, entries, why = WitnessLedger.verify(ledger_path)
        print("ledger verified: {} ({} entries, {})".format(ok, entries, why))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
