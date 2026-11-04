"""Nested solidification: compounding has to be distinguishable from arithmetic.

The claim under test is not "compiling is cheap". It is that *reuse across a
hierarchy* makes the marginal cost independent of the number of dimensions, and
that this independence is what turns a constant factor into an exponential. The
tests are written so that the two can be told apart:

* **``kappa = 0`` must give back the amortisation ratio exactly.** If this ever
  drifts, the experiment has started calling arithmetic compounding, which is
  the specific error the section exists to avoid.
* **``kappa = 1`` must give a marginal cost that does not move.** A single
  non-constant step would make "the slope mutation" a metaphor.
* **The knee must be a knee.** Above ``1 - 1/L`` the marginal cost is bounded;
  below it the growth must be the predicted ``(1-kappa)L``. A test that passes
  on both sides would be measuring nothing.
* **The thresholds must be roots, not fits.** ``B_rec`` is checked by evaluating
  the efficiency there, not by trusting the closed form.
* **The walk must be the recursion.** The Monte Carlo price is compared with
  ``m_d`` within four standard errors; a mismatch means the simulation and the
  algebra are pricing different systems, which is the failure that would make
  every number in the report decoration.

Run: ``python3 -m unittest tests.test_nested_solidification``.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import critical_budget_experiment as CB  # noqa: E402
from experiments import nested_solidification_experiment as N  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERIMENTS = os.path.join(HERE, "experiments")


class TestThePrices(unittest.TestCase):
    """The recursion, and its two degenerate endpoints."""

    def test_flat_cost_is_the_leaf_times_the_tree(self):
        for depth in (0, 1, 3, 6):
            self.assertAlmostEqual(N.flat_cost(depth),
                                   N.CV * N.BRANCH ** depth, places=9)

    def test_kappa_zero_is_the_flat_shape_up_to_the_lookup(self):
        for depth in (0, 2, 5, 8):
            self.assertAlmostEqual(N.marginal_cost(depth, 0.0),
                                   N.LOOKUP * N.BRANCH ** depth, places=9)

    def test_kappa_one_is_constant_in_depth(self):
        values = [N.marginal_cost(d, 1.0) for d in range(10)]
        for value in values:
            self.assertAlmostEqual(value, N.LOOKUP, places=12)

    def test_marginal_is_monotone_in_kappa(self):
        for depth in (1, 4, 7):
            values = [N.marginal_cost(depth, k) for k in (0.0, 0.25, 0.5, 0.9, 1.0)]
            self.assertTrue(all(values[i] > values[i + 1]
                                for i in range(len(values) - 1)))

    def test_ratio_sequence_agrees_with_the_marginal(self):
        for kappa in (0.0, 0.5, 0.9, 1.0):
            u = N.ratio_sequence(8, kappa)
            for depth in range(9):
                self.assertAlmostEqual(
                    N.marginal_cost(depth, kappa), N.LOOKUP * u[depth],
                    places=9)

    def test_invalid_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            N.marginal_cost(-1)
        with self.assertRaises(ValueError):
            N.marginal_cost(3, 1.2)

    def test_the_lookup_price_is_the_previous_sections(self):
        """The two experiments must not disagree about the replay price."""
        self.assertAlmostEqual(N.LOOKUP, CB.c_bar(), places=12)
        self.assertAlmostEqual(CB.gain_infinite(), N.CV / N.LOOKUP, places=12)


class TestTheCompoundBase(unittest.TestCase):
    """``min(L, 1/(1-kappa))``: one scalar separates arithmetic from compounding."""

    def test_base_is_one_without_reuse(self):
        self.assertAlmostEqual(N.compound_base(0.0), 1.0, places=12)

    def test_base_is_the_branch_above_the_knee(self):
        for kappa in (0.75, 0.8, 0.9, 0.99, 1.0):
            self.assertAlmostEqual(N.compound_base(kappa), N.BRANCH, places=12)

    def test_base_interpolates_below_the_knee(self):
        for kappa in (0.25, 0.5, 0.6):
            self.assertAlmostEqual(N.compound_base(kappa), 1.0 / (1.0 - kappa),
                                   places=12)

    def test_the_knee_is_where_the_two_branches_meet(self):
        knee = N.reuse_knee()
        self.assertAlmostEqual(knee, 1.0 - 1.0 / N.BRANCH, places=12)
        self.assertAlmostEqual(1.0 / (1.0 - knee), float(N.BRANCH), places=12)

    def test_the_knee_moves_with_the_branching_factor(self):
        for branch in (2, 3, 4, 8):
            self.assertAlmostEqual(N.reuse_knee(branch),
                                   1.0 - 1.0 / branch, places=12)

    def test_kappa_zero_multiple_is_the_amortisation_gain(self):
        for depth in (0, 3, 8):
            self.assertAlmostEqual(N.compound_multiple(depth, 0.0),
                                   CB.gain_infinite(), places=9)

    def test_kappa_one_multiple_is_the_gain_times_the_tree(self):
        for depth in (1, 4, 8):
            self.assertAlmostEqual(
                N.compound_multiple(depth, 1.0),
                CB.gain_infinite() * N.BRANCH ** depth, places=6)

    def test_the_multiple_grows_in_depth_only_with_reuse(self):
        flat_like = [N.compound_multiple(d, 0.0) for d in range(9)]
        self.assertTrue(all(abs(v - flat_like[0]) <= 1e-9 for v in flat_like))
        reused = [N.compound_multiple(d, 1.0) for d in range(9)]
        self.assertTrue(all(reused[i + 1] > reused[i]
                            for i in range(len(reused) - 1)))


class TestTheKnee(unittest.TestCase):
    """Above it the marginal is bounded; below it the growth is predicted."""

    def test_bounded_above_the_knee(self):
        for kappa in (0.8, 0.9, 0.99):
            near = N.marginal_cost(40, kappa)
            far = N.marginal_cost(60, kappa)
            self.assertLess(abs(far - near) / far, 1e-3)

    def test_geometric_below_the_knee(self):
        for kappa in (0.0, 0.25, 0.5, 0.6):
            predicted = (1.0 - kappa) * N.BRANCH
            self.assertGreater(predicted, 1.0)
            measured = (N.marginal_cost(8, kappa)
                        / N.marginal_cost(7, kappa))
            self.assertAlmostEqual(measured, predicted, delta=0.05)

    def test_the_knee_itself_grows_only_linearly(self):
        """At ``kappa*`` the drift is exactly 1, so ``m_d`` is affine in ``d``."""
        knee = N.reuse_knee()
        values = [N.marginal_cost(d, knee) for d in range(9)]
        steps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
        for step in steps[1:]:
            self.assertAlmostEqual(step, steps[0], places=9)


class TestTheBudget(unittest.TestCase):
    """Two kinks: ``B*`` for the base, ``B_rec`` for the hierarchy."""

    def test_below_the_critical_budget_the_arms_are_identical(self):
        b_star = N.critical_budget()
        for budget in (0.5, 2.0, 4.0, 6.0, b_star * 0.999):
            self.assertAlmostEqual(
                N.tasks_nested(budget, 6), N.tasks_flat(budget, 6), places=12)

    def test_the_critical_budget_is_the_previous_sections(self):
        self.assertAlmostEqual(N.critical_budget(),
                               CB.critical_budget(S=N.S_INFRA, cd=0.0),
                               places=12)
        self.assertGreater(N.critical_budget(), N.CV)

    def test_the_one_time_cost_is_linear_in_depth_above_the_base(self):
        increments = [N.one_time_cost(d + 1) - N.one_time_cost(d)
                      for d in range(8)]
        for increment in increments:
            self.assertAlmostEqual(increment,
                                   N.S_INFRA + N.BRANCH * N.LOOKUP, places=9)
        self.assertAlmostEqual(N.one_time_cost(0), N.critical_budget(),
                               places=9)

    def test_the_loss_region_is_empty_of_tasks(self):
        b_star = N.critical_budget()
        spend = N.one_time_cost(6)
        self.assertGreater(spend, b_star)
        for fraction in (0.0, 0.25, 0.5, 0.9, 0.999):
            budget = b_star + fraction * (spend - b_star)
            self.assertEqual(N.tasks_nested(budget, 6), 0.0)

    def test_efficiency_is_one_at_the_recovery_budget(self):
        recovery = N.recovery_budget(6)
        self.assertAlmostEqual(N.efficiency(recovery, 6), 1.0, places=9)

    def test_recovery_sits_just_above_the_one_time_cost(self):
        self.assertGreater(N.recovery_budget(6), N.one_time_cost(6))
        self.assertLess(N.recovery_budget(6),
                        1.01 * N.one_time_cost(6))

    def test_above_recovery_the_efficiency_approaches_the_multiple(self):
        target = N.compound_multiple(6)
        far = N.efficiency(1e7, 6)
        self.assertAlmostEqual(far / target, 1.0, places=4)

    def test_tasks_nested_is_monotone_away_from_the_investment(self):
        """It is monotone on each side of ``B*``; at ``B*`` it drops to zero.

        That drop is the model, not a bug: the arm stops being the flat arm and
        starts paying the per-level premium before anything is delivered. A
        smooth curve here would mean the loss region had been papered over.
        """
        b_star = N.critical_budget()
        last = -1.0
        for index in range(0, 400):
            budget = index * 0.5
            if budget >= b_star:
                break
            value = N.tasks_nested(budget, 6)
            self.assertGreaterEqual(value, last)
            last = value
        last = N.tasks_nested(b_star * 1.001, 6)
        for index in range(0, 400):
            budget = N.one_time_cost(6) + index * 0.5
            value = N.tasks_nested(budget, 6)
            self.assertGreaterEqual(value, last)
            last = value

    def test_the_investment_point_is_a_drop_not_a_slope(self):
        b_star = N.critical_budget()
        before = N.tasks_nested(b_star * 0.999, 6)
        after = N.tasks_nested(b_star * 1.001, 6)
        self.assertGreater(before, 0.0)
        self.assertEqual(after, 0.0)


class TestTheSimulation(unittest.TestCase):
    """The walk must price the same system the recursion does."""

    def test_the_walk_matches_the_recursion(self):
        for kappa, depth in ((0.5, 3), (0.9, 4), (1.0, 2)):
            mean, se = N.simulate_task_cost(depth, kappa, draws=2000,
                                            seed=7)
            expected = N.marginal_cost(depth, kappa)
            self.assertLessEqual(abs(mean - expected), 4.0 * se + 1e-12)

    def test_the_walk_is_deterministic_at_full_reuse(self):
        mean, se = N.simulate_task_cost(5, 1.0, draws=200)
        self.assertAlmostEqual(mean, N.LOOKUP, places=12)
        self.assertAlmostEqual(se, 0.0, places=12)


class TestTheArtifacts(unittest.TestCase):
    """The data and the summary have to be readable, and to agree with the code."""

    def setUp(self):
        self.csv_path = os.path.join(EXPERIMENTS, "nested_data.csv")
        self.json_path = os.path.join(EXPERIMENTS, "nested_summary.json")

    def test_the_csv_has_the_kinds_and_columns(self):
        if not os.path.exists(self.csv_path):
            self.skipTest("run the experiment first")
        with open(self.csv_path, encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        kinds = {row["kind"] for row in rows}
        self.assertEqual(kinds, {"depth", "budget", "knee", "branch"})
        for column in ("budget", "tasks_flat", "tasks_nested", "efficiency"):
            self.assertIn(column, rows[0])

    def test_the_summary_agrees_and_all_checks_pass(self):
        if not os.path.exists(self.json_path):
            self.skipTest("run the experiment first")
        with open(self.json_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertTrue(payload["all_pass"])
        thresholds = payload["thresholds"]
        self.assertAlmostEqual(thresholds["B_star"], N.critical_budget(),
                               places=9)
        self.assertAlmostEqual(thresholds["one_time_at_six"],
                               N.one_time_cost(6), places=9)
        self.assertAlmostEqual(thresholds["recovery_at_six"],
                               N.recovery_budget(6), places=9)

    def test_every_claim_has_a_measurement(self):
        if not os.path.exists(self.json_path):
            self.skipTest("run the experiment first")
        with open(self.json_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(len(payload["checks"]), 7)
        for check in payload["checks"]:
            self.assertIn(check["verdict"], ("pass", "FAIL"))
            self.assertIn("predicted", check)
            self.assertIn("measured", check)

    def test_the_generated_macros_are_tex_legal(self):
        """A macro name ending in a digit is read as the macro plus that digit."""
        path = os.path.join(EXPERIMENTS, "paper", "data", "nested_macros.tex")
        if not os.path.exists(path):
            self.skipTest("run the experiment first")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        names = re.findall(r"\\newcommand\{\\([A-Za-z]+)\}", text)
        self.assertTrue(names)
        for name in names:
            self.assertFalse(name[-1].isdigit(), name)
        values = dict(re.findall(
            r"\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}", text))
        self.assertAlmostEqual(float(values["nestedKnee"]), N.reuse_knee(),
                               places=2)
        self.assertAlmostEqual(float(values["nestedBrec"]),
                               N.recovery_budget(6), places=2)
        self.assertAlmostEqual(float(values["nestedEffFar"]),
                               N.efficiency(200.0, 6), places=0)

    def test_the_generated_table_holds_both_thresholds(self):
        path = os.path.join(EXPERIMENTS, "paper", "data", "nested_table.tex")
        if not os.path.exists(path):
            self.skipTest("run the experiment first")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("{:.2f}".format(N.critical_budget()), text)
        self.assertIn("{:.2f}".format(N.recovery_budget(6)), text)
        self.assertIn("30207", text)


if __name__ == "__main__":
    unittest.main()
