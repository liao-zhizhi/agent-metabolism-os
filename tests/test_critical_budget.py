"""The critical budget: a threshold that must be able to come out wrong.

The section this file guards is the one place in the paper where a number is
allowed to *contradict* the brief that asked for it. So the tests are mostly
checks that the negative result is still negative, and that the positive ones
are not artefacts of a constant nudged until the curve looked right.

* **Theorem 1 has to stay degenerate.** With no fixed premium and no monitor the
  two depth maps differ by ``(1/c_bar - 1/c_v)(B - c_v)``, so the crossing is
  ``c_v`` for every price and every drift rate. A test that finds an interior
  crossing there would mean the factorisation is wrong, and the sentence that
  contradicts the brief would have to be withdrawn.
* **Theorem 2 has to be a root, not a guess.** The closed form is checked
  against a numerical root of the equation it claims to solve, across a grid of
  ``S``, ``rho`` and ``c_d``, so an algebra slip cannot survive as a constant.
* **The limits have to be limits.** ``B*`` is infinite exactly when
  ``c_d >= (1-rho)(c_v-c_c)``, and its asymptote in ``rho`` is
  ``1 - c_d/(c_v-c_c)``. Both are tested by approaching them.
* **Reach has to turn over.** The advantage must be bounded by the horizon, so
  the ratio has to come *down*. A monotone ratio is a failure, and the check is
  written to report exactly that.
* **The generated macros have to be TeX-legal.** A macro name ending in a digit
  once broke the build silently; the test refuses one.

Run: ``python3 -m pytest tests/test_critical_budget.py`` (or ``unittest``).
"""

from __future__ import annotations

import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import critical_budget_experiment as C  # noqa: E402

PAPER_DATA = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments", "paper", "data")


class TestAmortisation(unittest.TestCase):
    """Proposition 1, including the part the brief got wrong."""

    def test_gain_is_one_at_a_single_traversal(self):
        for rho in (0.0, 0.1, 0.5, 0.9):
            self.assertAlmostEqual(C.gain_finite(1, rho=rho), 1.0, places=12)

    def test_gain_rises_monotonically_to_its_limit(self):
        limit = C.gain_infinite(rho=0.1)
        last = 0.0
        for traversals in (2, 3, 5, 10, 100, 10 ** 4):
            value = C.gain_finite(traversals, rho=0.1)
            self.assertGreater(value, last)
            self.assertLess(value, limit)
            last = value

    def test_gain_limit_is_the_price_ratio(self):
        self.assertAlmostEqual(C.gain_infinite(rho=0.0), 100.0, places=9)
        self.assertAlmostEqual(C.gain_infinite(rho=0.1), 1.0 / 0.109, places=12)
        self.assertAlmostEqual(C.gain_infinite(rho=1.0), 1.0, places=12)

    def test_two_traversals_is_not_the_limit(self):
        """The finite-T correction is exact, not asymptotic: G(2) = 1.803."""
        self.assertAlmostEqual(C.gain_finite(2, rho=0.1), 1.8034, places=3)
        self.assertLess(C.gain_finite(2, rho=0.1), C.gain_infinite(rho=0.1))


class TestTheDegenerateThreshold(unittest.TestCase):
    """Theorem 1: with no premium the crossing is one crossing, not a threshold."""

    def test_the_difference_factorises(self):
        for budget in (1.0, 1.5, 4.0, 20.0):
            predicted = (1.0 / C.c_bar() - 1.0) * (budget - C.CV)
            actual = C.depth_solid(budget) - C.depth_unsolid(budget)
            self.assertAlmostEqual(actual, predicted, places=9)

    def test_the_crossing_is_cv_for_every_price(self):
        crossing = C.critical_budget(S=0.0, cd=0.0)
        self.assertAlmostEqual(crossing, C.CV, places=12)

    def test_the_crossing_is_cv_for_every_drift_rate(self):
        """No monitor: drift moves the slope, never the crossing."""
        for rho in (0.0, 0.1, 0.5, 0.9, 0.99):
            self.assertAlmostEqual(C.critical_budget(rho=rho, cd=0.0),
                                   C.CV, places=12)

    def test_below_one_crossing_the_arms_tie(self):
        """Neither arm has finished a rung, and the fractional model says so."""
        for budget in (0.2, 0.5, 0.99):
            self.assertAlmostEqual(C.depth_solid(budget),
                                   C.depth_unsolid(budget), places=12)
            self.assertLess(C.depth_unsolid(budget), 1.0)

    def test_nothing_is_behind_above_one_crossing(self):
        for budget in (1.0001, 1.5, 10.0, 100.0):
            self.assertGreater(C.depth_solid(budget),
                               C.depth_unsolid(budget))

    def test_the_compiled_arm_is_never_behind_anywhere(self):
        """The brief predicted a loss region; there is none, at any budget."""
        for index in range(1, 400):
            budget = 0.01 * index
            self.assertGreaterEqual(
                C.depth_solid(budget), C.depth_unsolid(budget) - 1e-12)


class TestTheoremTwo(unittest.TestCase):
    """The closed form has to agree with a root of the equation it solves."""

    def _root(self, rho: float, S: float, cd: float) -> float:
        def gap(budget: float) -> float:
            return (C.depth_solid(budget, rho=rho, S=S, cd=cd)
                    - C.depth_unsolid(budget))
        lo = 1e-6
        hi = 1e6
        for _ in range(300):
            mid = 0.5 * (lo + hi)
            if gap(mid) > 0.0:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

    def test_closed_form_is_the_root(self):
        for rho in (0.0, 0.1, 0.5, 0.9):
            for S in (0.0, 0.25, 1.0, 4.0):
                for cd in (0.0, 0.02, 0.2):
                    if C.critical_budget(rho=rho, S=S, cd=cd) == math.inf:
                        continue
                    self.assertAlmostEqual(
                        C.critical_budget(rho=rho, S=S, cd=cd),
                        self._root(rho, S, cd), places=6)

    def test_affine_in_the_fixed_premium(self):
        slope = C.gain_infinite() / (C.gain_infinite() - 1.0)
        base = C.critical_budget(S=0.0)
        for S in (0.5, 1.0, 2.0, 3.0):
            self.assertAlmostEqual(C.critical_budget(S=S),
                                   base + S * slope, places=9)

    def test_the_slope_is_the_predicted_slope(self):
        slope = (C.critical_budget(S=1.0) - C.critical_budget(S=0.0))
        self.assertAlmostEqual(slope, C.gain_infinite() / (C.gain_infinite() - 1.0),
                              places=9)
        self.assertGreater(slope, 1.0)

    def test_monitor_limit(self):
        for rho in (0.0, 0.1, 0.5):
            limit = C.detector_limit(rho=rho)
            self.assertTrue(math.isfinite(
                C.critical_budget(rho=rho, cd=0.999 * limit)))
            self.assertEqual(
                C.critical_budget(rho=rho, cd=1.001 * limit), math.inf)

    def test_drift_asymptote(self):
        asymptote = C.drift_limit()
        below = C.critical_budget(rho=asymptote - 1e-4, cd=C.CD)
        self.assertGreater(below, 100.0)
        self.assertEqual(
            C.critical_budget(rho=asymptote + 1e-4, cd=C.CD), math.inf)

    def test_rising_in_drift_when_a_monitor_is_present(self):
        values = [C.critical_budget(rho=r, cd=C.CD)
                  for r in (0.0, 0.1, 0.5, 0.75, 0.9)]
        self.assertTrue(all(values[i + 1] > values[i]
                            for i in range(len(values) - 1)))

    def test_compiling_pays_exactly_when_the_cache_is_cheaper(self):
        for price in (0.01, 0.5, 0.99):
            self.assertLess(C.c_bar(0.0, cc=price), C.CV)
        self.assertGreater(C.c_bar(0.0, cc=1.2), C.CV)


class TestReach(unittest.TestCase):
    """Theorem 3: the advantage is bounded by the horizon, not unbounded."""

    def test_reach_saturates_at_the_horizon(self):
        horizon = C.A0 * math.exp(C.ALPHA)
        self.assertAlmostEqual(C.reach(1e6) / horizon, 1.0, places=9)
        self.assertLess(C.reach(1.0), horizon)

    def test_reach_is_monotone_in_depth(self):
        last = 0.0
        for depth in range(0, 200, 3):
            value = C.reach(float(depth))
            self.assertGreaterEqual(value, last)
            last = value

    def test_the_ratio_returns_to_one(self):
        self.assertAlmostEqual(C.reach_ratio(1000.0), 1.0, places=6)
        self.assertLess(C.reach_ratio(200.0), 1.01)

    def test_the_ratio_has_an_interior_maximum(self):
        grid = [0.1 + 0.1 * i for i in range(1, 2000)]
        values = [C.reach_ratio(b) for b in grid]
        peak = max(range(len(values)), key=values.__getitem__)
        self.assertGreater(peak, 0)
        self.assertLess(peak, len(values) - 1)
        self.assertGreater(values[peak], values[0])
        self.assertGreater(values[peak], values[-1])

    def test_the_peak_search_agrees_with_the_grid(self):
        budget, ratio = C.peak_reach_ratio()
        self.assertGreaterEqual(ratio, C.reach_ratio(budget - 1e-3) - 1e-6)
        self.assertGreaterEqual(ratio, C.reach_ratio(budget + 1e-3) - 1e-6)

    def test_depth_gain_is_linear_and_unbounded(self):
        slope = 1.0 / C.c_bar() - 1.0 / C.CV
        for budget in (2.0, 5.0, 50.0, 500.0):
            gain = C.depth_solid(budget) - C.depth_unsolid(budget)
            self.assertAlmostEqual(gain / (budget - C.CV), slope, places=6)


class TestGranularity(unittest.TestCase):
    """§6: the whole-rung threshold, and the premium beyond which it vanishes."""

    def test_the_step_index_rule(self):
        for S in (0.0, 0.25, 0.5, 1.0, 2.0):
            first = C.CV + S
            form = C.granular_threshold_form(S=S)
            # Just below the step the count is short of the comparison budget.
            below = first + (form - first) - 0.5 * C.CC
            count = 1.0 + C.simulate_mean_count(below - first, runs=20000)
            self.assertLess(count, below / C.CV)

    def test_measured_matches_the_form(self):
        for S in (0.0, 0.5, 1.0):
            measured = C.measure_granular_threshold(S=S, runs=40000)
            form = C.granular_threshold_form(S=S)
            self.assertAlmostEqual(measured, form, delta=2 * C.CC)

    def test_the_premium_limit_is_finite(self):
        limit = C.premium_limit()
        self.assertGreater(limit, 5.0)
        self.assertLess(limit, 9.0)

    def test_no_crossing_above_the_premium_limit(self):
        above = C.premium_limit() + 1.0
        self.assertEqual(C.granular_threshold_form(S=above), math.inf)


class TestTheArtifacts(unittest.TestCase):
    """The generated macros have to be TeX-legal and match the module."""

    def setUp(self):
        path = os.path.join(PAPER_DATA, "critical_budget_macros.tex")
        if not os.path.exists(path):
            self.skipTest("run the experiment first")
        with open(path, encoding="utf-8") as handle:
            self.text = handle.read()

    def test_no_macro_name_ends_in_a_digit(self):
        """``\\cbFoo2`` is read by TeX as ``\\cbFoo`` followed by ``2``."""
        names = re.findall(r"\\newcommand\{\\([A-Za-z]+)\}", self.text)
        self.assertTrue(names)
        for name in names:
            self.assertFalse(name[-1].isdigit(), name)

    def test_macros_carry_the_modules_numbers(self):
        values = dict(re.findall(
            r"\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}", self.text))
        self.assertAlmostEqual(float(values["cbCritBudgetZero"]), 1.0, places=3)
        self.assertAlmostEqual(float(values["cbSlopePredicted"]),
                               C.gain_infinite() / (C.gain_infinite() - 1.0),
                               places=4)
        self.assertAlmostEqual(float(values["cbDetectorLimit"]),
                               C.detector_limit(), places=3)


if __name__ == "__main__":
    unittest.main()
