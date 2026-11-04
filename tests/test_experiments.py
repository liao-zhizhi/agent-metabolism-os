"""The experiment's claims, pinned so the paper cannot drift away from its data.

The paper quotes roughly thirty numbers. Every one of them is produced by
``run_experiments.py``, but a test suite that re-ran the whole study would take
minutes and would still pass if the *interpretation* rotted. So these tests pin
the things a silent change would break:

* the calibration (one real gap at one difficulty, no free scale),
* the closed form (does the algebra still follow from the definitions?),
* the boundaries (are they where the paper says they are?),
* the shapes of the exported rows (does the CSV still have the columns the brief
  asked for?).

What is deliberately *not* pinned is any fitted coefficient. Those are outputs;
asserting them here would make the study unfalsifiable, which is the failure mode
this repository exists to avoid.
"""

from __future__ import annotations

import csv
import math
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from experiments import model  # noqa: E402
from experiments import parameters as P  # noqa: E402
from experiments import run_experiments as run  # noqa: E402
from agent_metabolism.interface_engine import (  # noqa: E402
    Interface,
    NarrowCondition,
)


class TestCalibration(unittest.TestCase):
    def test_one_real_gap_fixes_the_scale(self):
        """The 12-decade gap must land at 0.99, or the calibration is not one."""
        self.assertAlmostEqual(model.difficulty_from_potential(12.0), 0.99, places=12)

    def test_p0_is_derived_not_chosen(self):
        self.assertAlmostEqual(P.P0, 12.0 / math.log(100.0), places=12)

    def test_difficulty_stays_below_one(self):
        for potential in (5.0, 12.0, 40.0, 80.0):
            self.assertLess(model.difficulty_from_potential(potential), 1.0)

    def test_the_inverse_refuses_a_wall(self):
        with self.assertRaises(ValueError):
            model.potential_from_difficulty(1.0)


class TestTheLaw(unittest.TestCase):
    def test_power_law_follows_from_the_definitions(self):
        """No ceiling, so n = gamma*P exactly and the law is an identity."""
        slack, potential, span0 = 0.7, 9.0, 10.0
        n = model.constraint_count(potential)
        reach = span0 * model.amplification(slack, n)
        difficulty = model.difficulty_from_potential(potential)
        predicted = span0 * (1.0 - difficulty) ** -model.law_exponent(slack)
        # the ceiling makes n at most one above gamma*P; at P=9 it is exact
        self.assertAlmostEqual(reach, predicted, places=6)

    def test_exponent_grows_as_slack_tightens(self):
        exponents = [model.law_exponent(s) for s in (0.99, 0.9, 0.7, 0.5)]
        self.assertEqual(exponents, sorted(exponents))

    def test_amplification_rejects_impossible_slack(self):
        for slack in (0.0, 1.0, -0.5, 1.5):
            with self.assertRaises(ValueError):
                model.amplification(slack, 3)

    def test_reach_is_monotone_in_difficulty(self):
        slack = 0.8
        values = [
            model.predicted_reach(slack, p) for p in (5.0, 10.0, 20.0, 30.0)
        ]
        self.assertEqual(values, sorted(values))


class TestBoundaries(unittest.TestCase):
    def test_representation_boundary_is_the_successor_ceiling(self):
        """Bisection resolves to 1e-15, so compare with that, not to 15 places."""
        boundary = model.representation_boundary()
        self.assertAlmostEqual(boundary, 1.0 - 1.0e-9, delta=1.0e-14)

    def test_successor_stops_rising_at_the_boundary(self):
        from agent_metabolism.interface_engine import _next_difficulty

        boundary = model.representation_boundary()
        self.assertGreater(_next_difficulty(boundary - 1e-12, 0.5), boundary - 1e-12)
        self.assertLessEqual(_next_difficulty(boundary, 0.5), boundary)

    def test_ladder_depth_from_099(self):
        """Section V quotes 24 generations; the arithmetic must still give it."""
        self.assertEqual(model.ladders_before_saturation(0.99), 24)

    def test_tolerance_boundary_tracks_fidelity(self):
        """D50 should be the fidelity, not a constant."""
        for mean in P.FIDELITY_TIERS:
            probability = run._failure_probability(mean, mean)
            self.assertAlmostEqual(probability, 0.5, places=9)


class TestSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(P.SEED)
        cls.a_rows = run.experiment_a(rng, pairs=4)
        cls.b_rows = run.experiment_b(rng, chains=4, generations=3)

    def test_a_rows_carry_the_required_columns(self):
        required = {"generation", "difficulty", "narrow_condition", "reach", "status"}
        for record in self.a_rows:
            self.assertTrue(required.issubset(record), record.keys())

    def test_b_rows_carry_the_required_columns(self):
        required = {"generation", "difficulty", "narrow_condition", "reach", "status"}
        for record in self.b_rows:
            self.assertTrue(required.issubset(record), record.keys())

    def test_counterfactual_keeps_difficulty_fixed(self):
        pairs = {}
        for record in self.a_rows:
            pairs.setdefault(record["pair_id"], {})[record["mechanism"]] = record
        for pair in pairs.values():
            self.assertAlmostEqual(
                pair["cryogenic"]["difficulty_exact"],
                pair["biomimetic"]["difficulty_exact"],
                places=15,
            )

    def test_cryostat_is_narrow_and_scaffold_is_expansive(self):
        for record in self.a_rows:
            if record["mechanism"] == "cryogenic":
                self.assertEqual(record["status"], "NARROW")
            else:
                self.assertEqual(record["status"], "EXPANSIVE")

    def test_generations_rise(self):
        """Difficulty always rises; reach rises unless the span horizon caps it.

        The horizon is real: a chain that reaches 1e12 has nowhere left to grow,
        and asserting strict growth there would test the test rather than the
        model.
        """
        by_chain = {}
        for record in self.b_rows:
            by_chain.setdefault(record["chain_id"], []).append(record)
        for chain in by_chain.values():
            chain.sort(key=lambda r: r["generation"])
            for earlier, later in zip(chain, chain[1:]):
                self.assertGreater(
                    later["difficulty_exact"], earlier["difficulty_exact"]
                )
                if later["censored"]:
                    self.assertGreaterEqual(later["reach"], earlier["reach"])
                else:
                    self.assertGreater(later["reach"], earlier["reach"])

    def test_exact_difficulty_survives_the_reported_rounding(self):
        """Older rows carry both columns; the exact one must win."""
        record = {"difficulty": 1.0, "difficulty_exact": 1.0 - 1e-8}
        self.assertLess(run.exact_difficulty(record), 1.0)

    def test_the_engine_report_and_the_exact_value_now_agree(self):
        """ERR-001, from the outside.

        ``cross_interface`` used to round to six decimals, so these two columns
        disagreed for any gap with ``1 - D < 5e-7``: the report said 1.0 and the
        truth said otherwise. The engine no longer rounds, so this is an
        invariant rather than a workaround -- and it is a test that failed
        before the fix.
        """
        for record in self.b_rows:
            self.assertEqual(record["difficulty"], record["difficulty_exact"])
        for difficulty in (1.0 - 1e-7, 1.0 - 1e-9, 1.0 - 1e-12):
            evolver = run.build_evolver()
            gap = Interface("Macro", "Quantum", "c", span=10.0,
                            difficulty=difficulty)
            evolver.seed(gap)
            result = evolver.cross_interface(
                gap,
                NarrowCondition("s", "mech", "structure P-1", 2.0,
                                replicable=False, cost=0.02),
            )
            self.assertEqual(result["difficulty"], difficulty)
            self.assertLess(result["difficulty"], 1.0)

    def test_a_saturated_chain_reports_terminal_not_broken(self):
        """ERR-002, from the outside: the ladder ends out loud."""
        evolver = run.build_evolver()
        gap = Interface("Macro", "Quantum", "c", span=10.0,
                        difficulty=1.0 - 1e-9)
        evolver.seed(gap)
        result = evolver.cross_interface(
            gap,
            NarrowCondition("s", "mech", "structure P-1", 2.0,
                            replicable=False, cost=0.02),
            next_condition="inside",
        )
        self.assertTrue(result["terminal"])
        self.assertIsNone(result["sub_interface"])
        self.assertTrue(evolver.invariants()["self_similar"])


class TestExportedCsv(unittest.TestCase):
    def test_committed_csv_has_the_expected_shape(self):
        path = os.path.join(HERE, "..", "experiments", "fractal_experiment_data.csv")
        path = os.path.normpath(path)
        if not os.path.exists(path):
            self.skipTest("csv not generated yet; run experiments/run_experiments.py")
        with open(path, newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            for column in ("generation", "difficulty", "narrow_condition",
                           "reach", "status"):
                self.assertIn(column, header)
            first = next(reader)
            self.assertIn(first["experiment"], {"A", "B", "C"})
            self.assertIn(first["verdict"], {"PASS", "FAIL", "INVALID"})


if __name__ == "__main__":
    unittest.main()
