"""What the 2-D sandbox has to be true about, independent of its numbers.

The experiment reports rates and milliseconds, and none of those numbers are
asserted here. What is asserted is the physics and the bookkeeping that would
make the numbers meaningless if they were wrong:

* a push planned with the friction the block actually has lands in the frame,
  and a push planned with the wrong friction does not -- otherwise "the world
  drifted" would not be a thing the arms could be wrong about;
* momentum is conserved across a block/block contact, restitution is below one,
  and blocks do not sink into each other -- otherwise the sandbox is an
  animation rather than a simulation;
* the friction inversion is refused, not clamped, when the witness cannot
  identify it, and it recovers the true value from a *censored* push (one that
  ended against a wall) using the trajectory rather than the final pose;
* drift is coherent: a regime holds for the whole window, and every arm sees
  the same one;
* the safe filter's refusal is counted as a false veto only when the action it
  refused would in fact have succeeded -- the counterfactual is evaluated on a
  clone, so the refusal never quietly changes the world;
* the latency an arm reports is the latency its operations cost, so a compiled
  push cannot be billed as a derivation.

Run: ``python3 -m pytest tests/test_physics_sandbox.py -q`` (or unittest).
"""
from __future__ import annotations

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import physics_sandbox as S  # noqa: E402


def table_with(friction: float, blocks=None) -> S.Table:
    blocks = blocks if blocks is not None else S.build_sequence(0)[0]
    table = S.Table(blocks=blocks)
    for block in table.blocks:
        block.friction = friction
    return table


class TestPhysics(unittest.TestCase):
    """The solver, such as it is, has to be a solver."""

    def test_engine_is_named(self):
        # The solver has to be the one that ran, not the one that was available:
        # a machine with pybullet installed must not relabel a numpy run.
        self.assertEqual(S.engine_name(), "numpy")
        notice = S.engine_notice()
        self.assertIn("numpy", notice)
        if not S.available_sdks():
            self.assertIn("fallback", notice)
            self.assertIn("not installed", notice)

    def test_push_lands_in_frame_when_belief_is_true(self):
        table = table_with(0.35)
        blocks, jobs = S.build_sequence(0)
        job = jobs[0]
        speed = S.speed_for_travel(job.target_x - S.BLOCK_START_X, 0.35)
        report = table.push(job.block_id, speed)
        self.assertTrue(report.settled)
        self.assertTrue(job.in_frame(report.final), report.final)
        self.assertAlmostEqual(report.travel, job.target_x - S.BLOCK_START_X,
                               places=2)

    def test_low_friction_overshoots_and_stops_where_the_equation_says(self):
        table = table_with(0.18)
        job = S.build_sequence(0)[1][0]
        speed = S.speed_for_travel(0.5, 0.35)          # plan for 0.35
        report = table.push(job.block_id, speed)
        expected = speed * speed / (2.0 * 0.18 * S.GRAVITY)
        # Overshoot, but not by a factor of two: this is a friction change, not
        # a teleport.
        self.assertGreater(report.travel, 0.70)
        self.assertLess(report.travel, expected + 0.01)
        self.assertFalse(job.in_frame(report.final))

    def test_high_friction_stops_short(self):
        table = table_with(0.54)
        job = S.build_sequence(0)[1][0]
        speed = S.speed_for_travel(0.5, 0.35)
        report = table.push(job.block_id, speed)
        self.assertLess(report.travel, 0.40)
        self.assertFalse(job.in_frame(report.final))
        self.assertFalse(report.damaged)

    def test_a_hard_overshoot_is_recorded_as_damage(self):
        table = table_with(0.18)
        job = S.build_sequence(0)[1][4]                # lane clear of the decoy
        report = table.push(job.block_id, S.speed_for_travel(0.5, 0.35))
        self.assertTrue(report.wall_contact)
        self.assertGreater(report.peak_impact, S.DAMAGE_IMPACT_SPEED)
        self.assertTrue(report.damaged)

    def test_restitution_never_adds_energy(self):
        blocks = [S.Block(0, np.array([0.85, 0.35])), ]
        table = S.Table(blocks=blocks)
        table.get(0).friction = 0.0
        table.get(0).velocity = np.array([2.0, 0.0])
        contacts = []
        after_bounce = []
        for _ in range(20):
            table._integrate(S.DT, contacts)
            if any(c.kind == "wall_x1" for c in contacts):
                after_bounce.append(float(np.linalg.norm(table.get(0).velocity)))
        self.assertTrue(after_bounce, "the block never reached the wall")
        self.assertLessEqual(max(after_bounce), 2.0 + 1e-9)
        self.assertAlmostEqual(after_bounce[0], 2.0 * S.WALL_RESTITUTION, places=1)

    def test_block_collision_conserves_momentum(self):
        a = S.Block(0, np.array([0.30, 0.35]))
        b = S.Block(1, np.array([0.358, 0.35]))
        table = S.Table(blocks=[a, b])
        for block in table.blocks:
            block.friction = 0.0               # isolate the contact from drag
        table.get(0).velocity = np.array([0.5, 0.0])
        before = sum(blk.mass * blk.velocity for blk in table.blocks)
        for _ in range(20):
            table._integrate(S.DT, [])
        after = sum(blk.mass * blk.velocity for blk in table.blocks)
        np.testing.assert_allclose(before, after, atol=1e-9)
        # Equal masses and e = 0.6 split the relative velocity predictably:
        # the struck block takes (1+e)/2 of it and the striker keeps (1-e)/2.
        self.assertAlmostEqual(float(table.get(1).velocity[0]), 0.4, places=6)
        self.assertAlmostEqual(float(table.get(0).velocity[0]), 0.1, places=6)

    def test_blocks_do_not_overlap(self):
        a = S.Block(0, np.array([0.30, 0.35]))
        b = S.Block(1, np.array([0.358, 0.35]))
        table = S.Table(blocks=[a, b])
        table.get(0).velocity = np.array([0.5, 0.0])
        for _ in range(20):
            table._integrate(S.DT, [])
        gap = float(np.linalg.norm(table.get(0).pos - table.get(1).pos))
        self.assertGreaterEqual(gap, 2 * S.BLOCK_RADIUS - 1e-9)

    def test_sequence_is_five_blocks_and_a_decoy(self):
        blocks, jobs = S.build_sequence(3)
        self.assertEqual(len(jobs), 5)
        self.assertEqual(len(blocks), 6)
        self.assertEqual([b.role for b in blocks].count("decoy"), 1)
        self.assertEqual([j.block_id for j in jobs], [0, 1, 2, 3, 4])


class TestFrictionIdentification(unittest.TestCase):
    """The witness has to be able to name a number, or to say that it cannot."""

    def test_roundtrip_for_an_uncensored_push(self):
        for mu in (0.18, 0.35, 0.54):
            speed = S.speed_for_travel(0.5, mu)
            travel = speed * speed / (2.0 * mu * S.GRAVITY)
            self.assertAlmostEqual(S.friction_from_travel(speed, travel), mu,
                                   places=6)

    def test_refuses_when_the_block_did_not_move(self):
        self.assertIsNone(S.friction_from_travel(1.0, 0.0))
        self.assertIsNone(S.friction_from_travel(0.0, 0.3))

    def test_recovers_friction_from_a_censored_push(self):
        # The case that used to be unrecoverable: the block hit the wall, so its
        # final pose is not evidence. Its trajectory still is.
        table = table_with(0.18)
        job = S.build_sequence(0)[1][0]
        speed = S.speed_for_travel(0.5, 0.35)
        report = table.push(job.block_id, speed)
        self.assertTrue(report.wall_contact)
        self.assertIsNotNone(report.contact_pos)
        estimate = S.friction_from_report(report, speed)
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual(estimate, 0.18, delta=0.01)

    def test_contact_inversion_refuses_when_contact_speed_exceeds_launch(self):
        self.assertIsNone(S.friction_from_contact(1.0, 1.2, 0.3))
        self.assertIsNone(S.friction_from_contact(1.0, 0.5, 0.0))


class TestCoherentDrift(unittest.TestCase):
    """Non-i.i.d. means the world keeps its new friction, not that it flipped."""

    def test_drift_window_has_constant_friction_and_full_length(self):
        schedule = S.DriftSchedule.build(400, 1.0, np.random.default_rng(0))
        self.assertTrue(schedule.events)
        start = schedule.events[0][0]
        mu = schedule.at(start)
        for offset in range(S.DRIFT_WINDOW):
            self.assertAlmostEqual(schedule.at(start + offset), mu)
        self.assertNotAlmostEqual(schedule.at(start + S.DRIFT_WINDOW),
                                  mu, places=3)

    def test_drift_share_is_reported_and_matches_the_schedule(self):
        schedule = S.DriftSchedule.build(1000, 0.01, np.random.default_rng(7))
        flagged = sum(1 for f in schedule.frictions
                      if abs(f - S.NOMINAL_FRICTION) > 1e-9)
        self.assertEqual(flagged,
                         sum(1 for i in range(1000) if schedule.in_drift(i)))

    def test_every_arm_sees_the_same_world(self):
        schedule = S.DriftSchedule.build(60, 0.05, np.random.default_rng(3))
        results = [S.run_arm(arm, schedule, 12)
                   for arm in (S.BlindVLA(0), S.SafeVLAFilter(1),
                               S.CausalInterface(2))]
        truths = [np.array([r.mu_true for r in res.records]) for res in results]
        for other in truths[1:]:
            np.testing.assert_allclose(truths[0], other)


class TestArms(unittest.TestCase):
    """Each arm's cost and diagnosis, at the level of one push."""

    def _job(self):
        blocks, jobs = S.build_sequence(0)
        return table_with(S.NOMINAL_FRICTION, blocks), jobs[0]

    def test_blind_arm_never_revises_its_belief(self):
        arm = S.BlindVLA(0)
        table, job = self._job()
        for block in table.blocks:
            block.friction = S.SLIPPERY
        out = arm.execute(table, job, S.SLIPPERY, 0)
        self.assertFalse(out.success)
        self.assertEqual(out.mu_used, S.NOMINAL_FRICTION)
        self.assertEqual(out.latency_ms, S.VLA_MS)

    def test_causal_interface_bills_a_compiled_push_at_lookup_plus_witness(self):
        arm = S.CausalInterface(0)
        table, job = self._job()
        arm.registry[round(S.NOMINAL_FRICTION, 2)] = True
        out = arm.execute(table, job, S.NOMINAL_FRICTION, 0)
        self.assertTrue(out.success)
        self.assertAlmostEqual(out.latency_ms, S.LOOKUP_MS + S.WITNESS_MS)
        self.assertEqual(out.attempts, 1)

    def test_causal_interface_pays_the_derivation_once_then_amortises(self):
        arm = S.CausalInterface(0)
        blocks, jobs = S.build_sequence(0)
        table = table_with(S.NOMINAL_FRICTION, blocks)
        for block in table.blocks:
            block.friction = S.NOMINAL_FRICTION
        first = arm.execute(table, jobs[0], S.NOMINAL_FRICTION, 0)
        second = arm.execute(table, jobs[1], S.NOMINAL_FRICTION, 1)
        self.assertAlmostEqual(first.latency_ms, S.VLA_MS + S.WITNESS_MS)
        self.assertAlmostEqual(second.latency_ms, S.LOOKUP_MS + S.WITNESS_MS)
        self.assertLess(second.latency_ms, first.latency_ms / 4.0)

    def test_causal_interface_recovers_friction_from_one_failure(self):
        arm = S.CausalInterface(0)
        blocks, jobs = S.build_sequence(0)
        table = table_with(S.SLIPPERY, blocks)
        for block in table.blocks:
            block.friction = S.SLIPPERY
        first = arm.execute(table, jobs[0], S.SLIPPERY, 0)
        self.assertFalse(first.success)          # the first drifted push is lost
        self.assertAlmostEqual(arm.belief, S.SLIPPERY, delta=0.03)
        second = arm.execute(table, jobs[1], S.SLIPPERY, 1)
        self.assertTrue(second.success)
        self.assertEqual(second.damaged, False)

    def test_safe_filter_refusal_costs_a_takeover_and_flags_false_vetoes(self):
        arm = S.SafeVLAFilter(0)
        arm._confidence = lambda: 0.5            # force the refusal branch
        table, job = self._job()
        out = arm.execute(table, job, S.NOMINAL_FRICTION, 0)
        self.assertTrue(out.refused)
        self.assertEqual(out.latency_ms, S.HUMAN_MS)
        # The action would have succeeded under the nominal friction, so the
        # refusal is a false veto, and the block still arrives (the human is
        # competent, which is the expensive part).
        self.assertTrue(out.false_veto)
        self.assertTrue(out.success)
        self.assertGreater(S.HUMAN_MS, S.VLA_MS)

    def test_safe_filter_does_not_refuse_when_confident(self):
        arm = S.SafeVLAFilter(0)
        arm._confidence = lambda: 0.95
        table, job = self._job()
        out = arm.execute(table, job, S.NOMINAL_FRICTION, 0)
        self.assertFalse(out.refused)
        self.assertFalse(out.false_veto)
        self.assertEqual(out.latency_human_ms, 0.0)
        self.assertAlmostEqual(out.latency_ms, S.VLA_MS)

    def test_latency_calibration_is_ordered_like_the_operations(self):
        # A compiled lookup must be cheaper than a witness, which must be far
        # cheaper than a forward pass, which must be far cheaper than a human.
        self.assertLess(S.LOOKUP_MS, S.WITNESS_MS)
        self.assertLess(S.WITNESS_MS * 5, S.VLA_MS)
        self.assertLess(S.VLA_MS * 10, S.HUMAN_MS)


class TestEndToEnd(unittest.TestCase):
    """A small run, with the numbers the report is built from."""

    @classmethod
    def setUpClass(cls):
        cls.schedule = S.DriftSchedule.build(150, 0.02, np.random.default_rng(5))
        cls.results = [S.run_arm(arm, cls.schedule, 30)
                       for arm in (S.BlindVLA(0), S.SafeVLAFilter(1),
                                   S.CausalInterface(2))]
        cls.summary = S.summarise(cls.results)

    def test_one_record_per_job_per_arm(self):
        for result in self.results:
            self.assertEqual(result.pushes, 150)
            self.assertEqual(len(result.sequence_success), 30)

    def test_compiled_arm_is_cheaper_than_re_derivation_every_push(self):
        blind = self.summary["A_blind_vla"]["total_latency_ms"]
        causal = self.summary["B_causal_interface"]["total_latency_ms"]
        self.assertLess(causal, blind)

    def test_causal_arm_damages_less_than_the_blind_arm(self):
        self.assertLessEqual(
            self.summary["B_causal_interface"]["damage_events"],
            self.summary["A_blind_vla"]["damage_events"])

    def test_summary_accounts_for_every_component(self):
        for name, row in self.summary.items():
            components = sum(row["latency_components_ms"].values())
            self.assertAlmostEqual(components, row["total_latency_ms"], places=6)

    def test_drift_section_only_covers_drifted_pushes(self):
        for result in self.results:
            drifted = [r for r in result.records if r.in_drift]
            self.assertEqual(len(drifted), result.drift_summary()["pushes"])


if __name__ == "__main__":
    unittest.main()
