"""What the LIBERO audit has to be true about, before any of its numbers count.

The experiment reports success rates, milliseconds and a false-veto rate. None of
those are asserted here. What is asserted is everything that would make the
numbers meaningless if it were wrong:

* the schedule is a declared list, so every arm is handed the same worlds in the
  same order and a "recovery" is measured against the same onset twice;
* the pose-drift displacements are inside the arm's precise workspace and clear
  of the neighbouring bowl's footprint -- a bump that parks the plate somewhere
  unreachable would measure the arm's reach, not the layer;
* a re-derivation resumes from what the *observed* world is, not from what the
  plan was doing: a bowl that slipped has to be picked up again even if the plan
  thought it was placing it;
* the rebuilt tail replaces the rest of the program in place, and the loop that
  consumes it sees the replacement (this is the defect that made every repair a
  no-op: ``for index, step in enumerate(steps)`` over a *rebound* list);
* the plan aims the hand where the grasped object has to land, so the declared
  grasp offset cancels instead of accumulating into a 40 mm miss;
* the witness digest is stable under sub-grid jitter and changes on a real move,
  which is what lets a grant mean "the world did what the model said";
* the repair budget is finite, so a plan facing an unsatisfiable world stops
  paying forward passes instead of looping.

Run: ``python3 -m pytest tests/test_benchmark_libero_audit.py -q``.
Nothing here creates a MuJoCo environment, so it runs without the harness venv.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.benchmark_libero_audit import (  # noqa: E402
    BOWL_LIFT_EPS, BREAKER_TRIPS, GRASP_LATERAL, GRID_M, MAX_REPLANS,
    NOMINAL, POSE_DRIFT, POSE_DELTAS, POSE_REGIMES, PLATE_RADIUS_M,
    ChunkedPickPlace, DriftRegime, PhysState, SceneBelief, Step, WitnessInputs,
    WorldModel, _resume_label, _tail_from, classify_bowl, episode_schedule,
    onsets, witness_digest, witness_text,
)


class ScheduleTest(unittest.TestCase):
    """Every arm sees the same worlds in the same order."""

    def test_schedule_is_exactly_as_long_as_the_episode_count(self):
        for n in (0, 1, 7, 100, 250):
            self.assertEqual(len(episode_schedule(n)), n)

    def test_schedule_is_deterministic(self):
        left = [r.name for r in episode_schedule(100)]
        right = [r.name for r in episode_schedule(100)]
        self.assertEqual(left, right)

    def test_schedule_is_not_a_prefix_artefact(self):
        """A 100-episode run must still contain every channel, not just nominal."""
        names = {r.name for r in episode_schedule(100)}
        for required in (NOMINAL, POSE_DRIFT):
            self.assertIn(required, names)
        self.assertGreaterEqual(len(names), 5)

    def test_pose_episodes_cycle_the_three_displacements(self):
        rows = [r for r in episode_schedule(100) if r.name == POSE_DRIFT]
        seen = {tuple(np.round(r.pose_delta, 6)) for r in rows if r.pose_delta}
        self.assertEqual(seen, {tuple(np.round(d, 6)) for d in POSE_DELTAS})

    def test_onsets_mark_only_transitions_into_a_drifted_world(self):
        schedule = [
            DriftRegime(NOMINAL), DriftRegime(NOMINAL),
            DriftRegime(POSE_DRIFT, pose_delta=(0.1, 0.0)),
            DriftRegime(POSE_DRIFT, pose_delta=(0.1, 0.0)),
            DriftRegime(NOMINAL),
            DriftRegime(POSE_DRIFT, pose_delta=(-0.1, 0.0)),
        ]
        self.assertEqual(onsets(schedule), [(2, POSE_DRIFT), (5, POSE_DRIFT)])


class DriftGeometryTest(unittest.TestCase):
    """The displacements are measured constraints, not taste."""

    def test_every_pose_drift_moves_the_plate_past_its_own_radius(self):
        for delta in POSE_DELTAS:
            moved = float(np.hypot(*delta))
            self.assertGreater(moved, 0.08,
                               "a bump the plate survives is noise, not drift")

    def test_every_pose_drift_stays_out_of_the_neighbouring_bowl(self):
        """The plate is spawned at (0.076, 0.214); bowl 2 sits at (0.009, 0.306).

        A displacement that parks the plate inside bowl 2's footprint leaves a
        gap too narrow for a 94 mm bowl, so the goal is unsatisfiable for any
        policy -- the run would report the reach of the arm, not the layer.
        """
        plate = np.array([0.076, 0.214])
        neighbour = np.array([0.009, 0.306])
        for delta in POSE_DELTAS:
            landed = plate + np.asarray(delta)
            self.assertGreater(float(np.linalg.norm(landed - neighbour)), 0.09,
                               "the drifted plate is inside bowl 2's footprint")

    def test_pose_regimes_carry_their_own_displacement(self):
        self.assertEqual(len(POSE_REGIMES), len(POSE_DELTAS))
        for regime, delta in zip(POSE_REGIMES, POSE_DELTAS):
            self.assertEqual(regime.name, POSE_DRIFT)
            self.assertEqual(tuple(regime.pose_delta), tuple(delta))


class ReparameterisationTest(unittest.TestCase):
    """What "re-derive instead of retry" means, as an assertion."""

    def test_a_bowl_that_slipped_has_to_be_picked_up_again(self):
        for label in ("place", "transport", "lift"):
            self.assertEqual(_resume_label("table", label), "approach")

    def test_a_bowl_still_in_the_gripper_resumes_where_the_plan_was(self):
        for label in ("transport", "place", "lift"):
            self.assertEqual(_resume_label("gripped", label), label)

    def test_a_bowl_already_on_the_plate_only_has_to_be_let_go(self):
        self.assertEqual(_resume_label("placed", "place"), "retreat")
        self.assertEqual(_resume_label("placed", "transport"), "retreat")

    def test_an_unexpected_world_restarts_the_grasp(self):
        self.assertEqual(_resume_label("table", "lift"), "approach")
        self.assertEqual(_resume_label("gripped", "close"), "lift")


class TailTest(unittest.TestCase):
    """The rebuilt tail is what the executor actually runs."""

    @staticmethod
    def program():
        steps = []
        for name in ("approach", "descend", "close", "lift",
                     "transport", "place", "release", "retreat"):
            steps.append(Step(index=len(steps), label=name,
                              target=np.zeros(3), umax=1.0, gripper=0.0,
                              steps=6, carry=False))
        return steps

    def test_tail_starts_at_the_named_stage_and_is_reindexed(self):
        tail = _tail_from(self.program(), "place")
        self.assertEqual([s.label for s in tail], ["place", "release", "retreat"])
        self.assertEqual([s.index for s in tail], [0, 1, 2])

    def test_a_missing_label_falls_back_to_the_termination(self):
        tail = _tail_from(self.program(), "not_a_stage")
        self.assertEqual([s.label for s in tail], ["release", "retreat"])

    def test_replacing_the_tail_in_place_is_visible_to_an_active_loop(self):
        """The defect this pins: rebinding ``steps`` left the repair unexecuted."""
        steps = self.program()
        seen = []
        repair_at = 4
        for index, step in enumerate(steps):
            seen.append(step.label)
            if index == repair_at and step.label == "transport":
                tail = _tail_from(self.program(), "approach")
                for offset, item in enumerate(tail):
                    item.index = index + 1 + offset
                steps[:] = list(steps[: index + 1]) + tail
        # the loop must have executed the spliced re-grasp, not the stale tail
        self.assertEqual(seen[repair_at + 1], "approach")
        self.assertIn("close", seen[repair_at:])
        self.assertGreater(len(seen), 8)

    def test_the_repair_budget_is_finite(self):
        self.assertGreater(MAX_REPLANS, 0)
        self.assertLess(MAX_REPLANS, 1000)
        self.assertGreater(BREAKER_TRIPS, 0)


class PlanGeometryTest(unittest.TestCase):
    """The hand goes where the *held object* has to land."""

    def test_the_declared_grasp_offset_cancels_at_the_plate(self):
        model = WorldModel()
        policy = ChunkedPickPlace(model)
        state = PhysState(
            eef=np.array([-0.208, 0.0, 1.173]),
            bowl=np.array([-0.071, -0.003, 0.898]),
            plate=np.array([0.076, 0.214, 0.902]),
            gripper=np.zeros(2), held=False, steps=0)
        steps, belief = policy.program(state)
        place = next(s for s in steps if s.label == "place" and s.carry)
        # the bowl hangs GRASP_LATERAL behind the hand, so the hand must be that
        # far past the plate for the bowl to land on it
        self.assertAlmostEqual(float(place.target[0]), float(belief.plate[0]), places=6)
        self.assertAlmostEqual(float(place.target[1]),
                               float(belief.plate[1]) + GRASP_LATERAL, places=6)
        order = [s.label for s in steps]
        deduped = [label for i, label in enumerate(order) if i == 0 or order[i - 1] != label]
        self.assertEqual(deduped,
                         ["approach", "descend", "close", "lift", "transport",
                          "place", "release", "retreat"])
        self.assertTrue(all(s.carry for s in steps if s.label in ("lift", "transport", "place")))
        self.assertFalse(any(s.carry for s in steps if s.label in ("approach", "descend", "close")))

    def test_the_program_ends_by_letting_go_and_backing_off(self):
        steps, _ = ChunkedPickPlace(WorldModel()).program(PhysState(
            eef=np.array([-0.208, 0.0, 1.173]),
            bowl=np.array([-0.071, -0.003, 0.898]),
            plate=np.array([0.076, 0.214, 0.902]),
            gripper=np.zeros(2), held=False, steps=0))
        self.assertEqual([s.label for s in steps[-2:]], ["release", "retreat"])

    def test_the_bowl_states_are_the_three_the_plan_can_assert(self):
        plate = np.array([0.076, 0.214, 0.902])
        rest = 0.898
        self.assertEqual(classify_bowl(np.array([-0.071, -0.003, rest]), plate, rest), "table")
        self.assertEqual(classify_bowl(np.array([-0.071, -0.003, rest + BOWL_LIFT_EPS + 0.01]),
                                       plate, rest), "gripped")
        self.assertEqual(classify_bowl(np.array([0.080, 0.214, rest]), plate, rest), "placed")
        # just outside the plate's footprint is still on the table
        self.assertEqual(classify_bowl(
            np.array([0.080 + PLATE_RADIUS_M + 0.01, 0.214, rest]), plate, rest), "table")


class GateBindingTest(unittest.TestCase):
    """One prediction, one redemption -- the defect the first full run exposed.

    The gateway holds one set of acceptable digests per capability and never
    drains it, so a scene-only digest made every chunk comparable with every
    earlier chunk. A bowl that slipped back onto the table during transport
    matched the ``table`` digest registered at ``close`` ten chunks before and
    the gate granted a claim of ``gripped``. These tests pin the ordinal that
    closes it.
    """

    def setUp(self):
        import tempfile
        from experiments.benchmark_libero_audit import MembraneCEL
        self._tmp = tempfile.TemporaryDirectory()
        self.cel = MembraneCEL(os.path.join(self._tmp.name, "ledger.jsonl"))
        self.plate = np.array([0.076, 0.214, 0.902])

    def tearDown(self):
        self._tmp.cleanup()

    def _inputs(self, claim):
        return WitnessInputs(plate=self.plate.copy(), claim=claim)

    def test_the_scene_the_plan_predicted_is_granted(self):
        expected = self._inputs("gripped")
        self.cel.expect(expected)
        granted, _ = self.cel.check(expected, self._inputs("gripped"))
        self.assertTrue(granted)
        self.assertEqual(len(self.cel.refusals), 0)

    def test_a_scene_that_diverges_is_refused(self):
        expected = self._inputs("gripped")
        self.cel.expect(expected)
        granted, _ = self.cel.check(expected, self._inputs("table"))
        self.assertFalse(granted)
        self.assertEqual(self.cel.refusals[-1]["reason"], "NOT_REGISTERED")

    def test_a_regression_cannot_reuse_an_earlier_prediction(self):
        """``table`` was a legitimate prediction once; later it is a failure."""
        table = self._inputs("table")
        self.cel.expect(table)
        self.assertTrue(self.cel.check(table, self._inputs("table"))[0])
        # the plan now claims the bowl is in the gripper and it is not
        self.cel.expect(self._inputs("gripped"))
        granted, _ = self.cel.check(self._inputs("gripped"), self._inputs("table"))
        self.assertFalse(granted,
                         "a stale registration granted a claim it did not predict")

    def test_an_ordinal_is_never_reused_across_episodes(self):
        table = self._inputs("table")
        for _ in range(3):  # three episodes' worth of predictions
            self.cel.expect(table)
            self.assertTrue(self.cel.check(table, self._inputs("table"))[0])
        # a fresh prediction of the same scene is still its own key
        self.cel.expect(self._inputs("gripped"))
        self.assertFalse(self.cel.check(self._inputs("gripped"), table)[0])
        self.assertEqual(len(self.cel.refusals), 1)

    def test_each_check_is_recorded_in_a_verifiable_ledger(self):
        table = self._inputs("table")
        self.cel.expect(table)
        self.cel.check(table, table)
        self.assertEqual(self.cel.gateway.verify()["status"], "PASS")
        self.assertEqual(self.cel.grants, 1)


class WitnessTest(unittest.TestCase):
    """A grant has to mean the world matched, not that the noise was small."""

    def test_sub_grid_jitter_does_not_change_the_digest(self):
        plate = np.array([0.076, 0.214, 0.902])
        left = WitnessInputs(plate=plate.copy(), claim="gripped",
                             bowl=np.array([0.07, 0.02, 0.99]))
        right = WitnessInputs(plate=plate.copy(), claim="gripped",
                              bowl=np.array([0.0704, 0.0196, 0.9903]))
        self.assertEqual(left.digest(GRID_M), right.digest(GRID_M))

    def test_a_real_move_changes_the_digest(self):
        plate = np.array([0.076, 0.214, 0.902])
        left = WitnessInputs(plate=plate.copy(), claim="gripped")
        moved = WitnessInputs(plate=plate + np.array([0.105, 0.08, 0.0]), claim="gripped")
        self.assertNotEqual(left.digest(GRID_M), moved.digest(GRID_M))

    def test_a_different_claim_changes_the_digest(self):
        plate = np.array([0.076, 0.214, 0.902])
        self.assertNotEqual(
            WitnessInputs(plate=plate.copy(), claim="gripped").digest(GRID_M),
            WitnessInputs(plate=plate.copy(), claim="placed").digest(GRID_M))

    def test_the_digest_is_expressible_in_the_plans_frame(self):
        """Both sides must quantise the same reference, or boundaries flap."""
        belief = WitnessInputs(plate=np.array([0.076, 0.214, 0.902]), claim="table",
                               bowl=np.array([-0.071, -0.003, 0.898]))
        observed = WitnessInputs(plate=np.array([0.0764, 0.2136, 0.9022]), claim="table",
                                 bowl=np.array([-0.0706, -0.0033, 0.8981]))
        self.assertEqual(observed.digest(GRID_M, origin=belief),
                         belief.digest(GRID_M))
        self.assertEqual(witness_digest(witness_text(observed.plate, observed.claim,
                                                     observed.bowl, belief, GRID_M)),
                         belief.digest(GRID_M))


class BeliefTest(unittest.TestCase):
    """A re-derivation may not quietly edit the scene it is scored against."""

    def test_cloning_a_belief_does_not_alias_the_scene(self):
        belief = SceneBelief(bowl=np.array([0.0, 0.0, 0.9]),
                             plate=np.array([0.1, 0.1, 0.9]))
        copy = belief.clone()
        copy.bowl += 1.0
        self.assertFalse(np.allclose(belief.bowl, copy.bowl))


if __name__ == "__main__":
    unittest.main()
