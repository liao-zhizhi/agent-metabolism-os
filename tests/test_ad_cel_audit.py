"""What the driving audit has to be true about, before any of its numbers count.

The report claims a collision rate, a refusal count and an identified friction
coefficient. None of those are asserted here -- a unit test that hard-codes them
would only be checking that the seed did not change. What is asserted is
everything that would make those numbers meaningless if it were wrong:

* both arms are handed the same declared worlds in the same order, and the
  counterfactual replay ("what the car would have done with no gate at all")
  really has no gate in it, or the words "false brake" describe nothing;
* the planner is told the nominal road in every episode. The drift is a
  difference between the plant and the *belief*, never between two arms, so a
  win cannot come from handing one arm a better world;
* the wet road lengthens the stopping distance because the friction circle caps
  braking, not because a constant was set to make it so;
* the layer identifies grip from saturation and lag from a step response, and a
  braking *transient* is not mistaken for ice -- the defect that made an earlier
  version report a slippery road on dry tarmac;
* the ledger refuses a replay of a spent ordinal (ERR-004) and its chain
  detects a record edited after the fact;
* a lateral deviation one tolerance cell out is a refusal, which is the brief's
  "0.5 m" converted into a decided bit;
* a takeover ramps out of the command the car was already executing instead of
  stepping, because the refusal loop that follows a step is self-inflicted.

Run: ``python3 -m pytest tests/test_ad_cel_audit.py -q``.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import ad_cel_audit as A  # noqa: E402


def _first(specs, channel, hazard=None):
    for spec in specs:
        if spec.channel == channel and (hazard is None or spec.hazard == hazard):
            return spec
    raise AssertionError("no {} / {} in the schedule".format(channel, hazard))


class ScheduleTest(unittest.TestCase):
    """The worlds are declared once, not sampled per arm."""

    @classmethod
    def setUpClass(cls):
        cls.specs = A.build_schedule(40, 2026)

    def test_every_episode_has_a_unique_index_and_a_seed(self):
        indices = [s.index for s in self.specs]
        self.assertEqual(indices, list(range(40)))
        self.assertEqual(len({s.seed for s in self.specs}), 40)

    def test_channel_counts_are_the_declared_weights(self):
        counts = {c: sum(1 for s in self.specs if s.channel == c) for c in A.CHANNELS}
        self.assertEqual(sum(counts.values()), 40)
        self.assertEqual(counts["wet_and_lag"], 4)
        self.assertGreater(counts["wet_road"], 0)
        self.assertGreater(counts["steering_lag"], 0)

    def test_both_hazard_kinds_occur_in_every_channel(self):
        for channel in A.CHANNELS:
            kinds = {s.hazard for s in self.specs if s.channel == channel}
            self.assertEqual(kinds, set(A.HAZARDS), channel)

    def test_the_schedule_is_deterministic(self):
        again = A.build_schedule(40, 2026)
        self.assertEqual([s.seed for s in self.specs], [s.seed for s in again])
        self.assertEqual([s.ego_v0 for s in self.specs], [s.ego_v0 for s in again])


class WorldTest(unittest.TestCase):
    """The drift is between the plant and the belief, never between arms."""

    @classmethod
    def setUpClass(cls):
        cls.specs = A.build_schedule(40, 2026)

    def test_the_drifted_world_differs_from_the_believed_world(self):
        spec = _first(self.specs, "wet_and_lag")
        plant, believed = spec.scenario()
        self.assertAlmostEqual(plant.mu, A.MU_WET)
        self.assertAlmostEqual(plant.tau_s, A.TAU_S_WORN)
        self.assertAlmostEqual(plant.tau_a, A.TAU_A_WORN)
        self.assertAlmostEqual(believed.mu, A.MU_NOMINAL)
        self.assertAlmostEqual(believed.tau_s, A.TAU_S_NOMINAL)

    def test_a_nominal_episode_believes_the_truth(self):
        spec = _first(self.specs, "nominal")
        plant, believed = spec.scenario()
        self.assertAlmostEqual(plant.mu, believed.mu)
        self.assertAlmostEqual(plant.tau_a, believed.tau_a)
        self.assertAlmostEqual(plant.tau_s, believed.tau_s)

    def test_the_wet_road_lengthens_the_stop_through_the_friction_circle(self):
        def stop_distance(mu):
            plant = A.Plant(mu, A.TAU_A_NOMINAL, A.TAU_S_NOMINAL)
            s = A.VehicleState(0.0, 0.0, 0.0, 14.0)
            for _ in range(400):
                plant.step(s, -A.BRAKE_CMD_MAX, 0.0, A.DT)
                if s.v <= 0.0:
                    break
            return s.x

        dry = stop_distance(A.MU_NOMINAL)
        wet = stop_distance(A.MU_WET)
        # The dry stop is command-limited (8 < 0.9g); the wet one is grip-limited
        # (0.35g = 3.4 < 8). That asymmetry is the whole experiment.
        self.assertGreater(wet / dry, 1.8)

    def test_both_arms_get_the_same_world_and_the_same_start(self):
        spec = _first(self.specs, "wet_road", "pedestrian")
        a = A.run_episode(spec, "A_safe_planner", capture=True)
        b = A.run_episode(spec, "B_cel", capture=True)
        self.assertEqual(a["mu_true"], b["mu_true"])
        self.assertEqual(a["tau_s_true"], b["tau_s_true"])
        self.assertEqual(a["trace"][0][1:3], b["trace"][0][1:3])

    def test_the_counterfactual_replay_has_no_gate(self):
        spec = _first(self.specs, "wet_road", "pedestrian")
        blind = A.run_episode(spec, "blind")
        self.assertEqual(blind["eb_events"], 0)
        self.assertEqual(blind["refusals"], 0)
        self.assertEqual(blind["witness_checks"], 0)


class IdentificationTest(unittest.TestCase):
    """The layer reads the road from the response, not from the simulator."""

    @classmethod
    def setUpClass(cls):
        cls.specs = A.build_schedule(40, 2026)

    def test_grip_is_identified_from_saturation_on_a_wet_road(self):
        spec = _first(self.specs, "wet_road", "lead_brake")
        row = A.run_episode(spec, "B_cel")
        self.assertAlmostEqual(row["mu_hat"], A.MU_WET, delta=0.05)

    def test_lag_is_identified_from_the_step_response(self):
        spec = _first(self.specs, "steering_lag")
        row = A.run_episode(spec, "B_cel")
        self.assertAlmostEqual(row["tau_s_hat"], A.TAU_S_WORN, delta=0.15)
        self.assertAlmostEqual(row["tau_a_hat"], A.TAU_A_WORN, delta=0.08)

    def test_a_braking_transient_is_not_mistaken_for_low_grip(self):
        # First-order brake lag also makes |a| < |cmd| for a few steps. Reading
        # that transient as saturation reported a wet road on dry tarmac.
        for spec in [s for s in self.specs if s.channel == "nominal"][:6]:
            row = A.run_episode(spec, "B_cel")
            self.assertGreaterEqual(row["mu_hat"], 0.85,
                                    "nominal episode {} reported mu={}".format(
                                        spec.index, row["mu_hat"]))


class LedgerTest(unittest.TestCase):
    """A hash binds content; the ordinal binds the attempt."""

    def test_a_replay_of_a_spent_ordinal_is_refused(self):
        ledger = A.WitnessLedger()
        ledger.register("execute:ep0", 3, 0)
        self.assertTrue(ledger.redeem("execute:ep0", 3, 0, (0, 0, 0)))
        self.assertFalse(ledger.redeem("execute:ep0", 3, 0, (0, 0, 0)))
        self.assertEqual(ledger.replays, 1)

    def test_a_deviation_outside_the_tolerance_is_a_refusal(self):
        ledger = A.WitnessLedger()
        ledger.register("execute:ep0", 1, 0)
        # one cell out laterally == at least TOL_LAT of cross-track error
        self.assertNotEqual(A.cell_of(A.TOL_LAT * 1.2, A.TOL_LAT), 0)
        self.assertFalse(ledger.redeem("execute:ep0", 1, 0, (0, 1, 0)))

    def test_the_chain_verifies_and_detects_an_edited_record(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "ledger.jsonl")
        ledger = A.WitnessLedger(path)
        for ordinal in range(3):
            ledger.register("execute:ep0", ordinal, 0)
            ledger.redeem("execute:ep0", ordinal, 0, (0, 0, 0))
        ok, entries, why = A.WitnessLedger.verify(path)
        self.assertTrue(ok, why)
        self.assertEqual(entries, 6)

        with open(path) as handle:
            lines = handle.readlines()
        record = json.loads(lines[1])
        record["dlat"] = 0.9          # a retouched observation
        lines[1] = json.dumps(record, sort_keys=True) + "\n"
        with open(path, "w") as handle:
            handle.writelines(lines)

        ok, _, why = A.WitnessLedger.verify(path)
        self.assertFalse(ok)
        self.assertIn("line 2", why)


class ControlTest(unittest.TestCase):
    """The takeover has to be a plan, not an improvisation."""

    @classmethod
    def setUpClass(cls):
        cls.specs = A.build_schedule(40, 2026)

    def test_a_takeover_releases_the_brake_at_a_bounded_jerk(self):
        planner = A.Planner()
        # clear road, past the light: the planner wants to accelerate again
        ego = A.VehicleState(160.0, A.road_y(160.0), 0.0, 12.0, a=-4.0)
        scene = A.Scene(0.0, None, None, red_light=False)
        plan = planner.plan(0.0, ego, scene, 1, a_start=-4.0)
        steps = [plan.a[i + 1] - plan.a[i] for i in range(len(plan.a) - 1)]
        self.assertLessEqual(max(steps), A.JERK_LIMIT * A.DT + 1e-9)
        self.assertGreater(plan.a[-1], -1.5)   # it does let go of the brake

    def test_success_means_arrived_without_a_collision(self):
        for spec in self.specs[:4]:
            for arm in ("A_safe_planner", "B_cel"):
                row = A.run_episode(spec, arm)
                if row["success"]:
                    self.assertTrue(row["arrived"])
                    self.assertFalse(row["collision"])
                    self.assertFalse(row["lane_departure"])


class ExportTest(unittest.TestCase):
    def test_the_csv_carries_the_declared_columns(self):
        specs = A.build_schedule(20, 2026)
        rows = [A.run_episode(specs[0], "A_safe_planner"),
                A.run_episode(specs[0], "B_cel")]
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "data.csv")
        A.write_csv(rows, path)
        with open(path) as handle:
            header = next(csv.reader(handle))
        self.assertEqual(header, A.COLUMNS)
        self.assertIn("mu_hat", header)
        self.assertIn("hazard_deficit_m", header)


if __name__ == "__main__":
    unittest.main()
