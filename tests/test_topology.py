"""The five-stratum topology, the seamless criterion, and the limit map.

Three bodies of algebra are tested here, and each has one test that would fail if
the claim were merely asserted rather than true:

* **Fidelity is per boundary *and* per function.** The test that matters is that
  the same physical boundary has a routing fidelity and a phase fidelity five
  orders of magnitude apart. A model with one ``F`` per boundary cannot represent
  that, so the test is a structural one, not a numerical one.
* **"Seamless" is quantised.** ``R < eps`` with ``R = 10**-p`` means the verdict
  flips at whole numbers of nines. The test pins the ceiling, because a
  ``floor`` would be an off-by-one that only shows up at exact powers of ten.
* **The power law is exponential in three senses and affine in none.** Two of the
  three are exact identities and are tested to machine precision; the third is a
  local fit whose rate is predicted analytically (``1 + f**2/5``) and is
  compared against a least-squares measurement, so the check is theory against
  numerics rather than numerics against itself.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import model  # noqa: E402
from experiments import parameters as P  # noqa: E402
from experiments import topology  # noqa: E402


class TestStrata(unittest.TestCase):
    """The stack is five strata and four boundaries, in that order."""

    def test_there_are_five_strata(self):
        self.assertEqual(len(topology.STRATA), 5)
        self.assertEqual([s.index for s in topology.STRATA], [0, 1, 2, 3, 4])

    def test_there_are_four_boundaries(self):
        self.assertEqual(len(topology.BOUNDARIES), 4)

    def test_the_first_four_strata_are_ordered_by_scale(self):
        for lower, upper in zip(topology.STRATA[:4], topology.STRATA[1:4]):
            self.assertLess(lower.length_m, upper.length_m)

    def test_the_information_stratum_has_no_scale_of_its_own(self):
        """It breaks the ordering, and that is a fact about it, not a bug.

        A symbol is addressable at whatever length its substrate is; the
        stratum's ``length_m`` is the size of a token, chosen so the ratio has
        some meaning, and it is smaller than the biological cell it is carried
        by. Asserting monotonicity over all five would be asserting something
        false about the information stratum.
        """
        info = topology.STRATA[-1]
        self.assertLess(info.length_m, topology.STRATA[-2].length_m)

    def test_an_unknown_boundary_is_refused(self):
        with self.assertRaises(IndexError):
            topology.boundary(4)


class TestFidelity(unittest.TestCase):
    """F is in (0, 1]; R = 1-F; and the same boundary has more than one F."""

    def test_resistance_is_the_complement(self):
        for name in topology.BOUNDARIES:
            self.assertAlmostEqual(
                topology.resistance(name) + topology.fidelity(name), 1.0, places=15
            )

    def test_every_characterised_fidelity_is_in_range(self):
        for name, table in topology.FIDELITY.items():
            for function, value in table.items():
                self.assertGreater(value, 0.0, (name, function))
                self.assertLessEqual(value, 1.0, (name, function))

    def test_a_function_the_boundary_never_served_is_refused(self):
        with self.assertRaises(KeyError):
            topology.fidelity(topology.boundary(2), topology.PHASE)

    def test_the_same_boundary_routes_far_better_than_it_preserves_phase(self):
        """The structural claim: one scalar F per boundary is not enough."""
        name = topology.boundary(0)
        route = topology.fidelity(name, topology.ROUTE)
        phase = topology.fidelity(name, topology.PHASE)
        self.assertGreater(route / phase, 1.0e4)

    def test_nines_counts_the_room_left(self):
        self.assertAlmostEqual(topology.nines(0.9), 1.0, places=12)
        self.assertAlmostEqual(topology.nines(0.99), 2.0, places=12)
        self.assertAlmostEqual(topology.nines(0.999), 3.0, places=12)

    def test_nines_of_perfect_fidelity_is_infinite(self):
        self.assertEqual(topology.nines(1.0), float("inf"))


class TestSeamlessCriterion(unittest.TestCase):
    """R < eps, and the threshold is counted in whole nines."""

    def test_the_comparison_is_strict(self):
        self.assertFalse(topology.is_seamless(0.99, 1.0e-2))
        self.assertTrue(topology.is_seamless(0.9901, 1.0e-2))

    def test_required_nines_rounds_up(self):
        self.assertEqual(topology.required_nines(1.0e-2), 2)
        self.assertEqual(topology.required_nines(1.0e-3), 3)
        self.assertEqual(topology.required_nines(2.0e-2), 2)
        self.assertEqual(topology.required_nines(9.9e-2), 2)

    def test_required_nines_refuses_a_degenerate_threshold(self):
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                topology.required_nines(bad)

    def test_the_verdict_flips_at_a_whole_number_of_nines(self):
        eps = 1.0e-2
        just_under = 1.0 - 0.0101
        just_over = 1.0 - 0.0099
        self.assertFalse(topology.is_seamless(just_under, eps))
        self.assertTrue(topology.is_seamless(just_over, eps))

    def test_a_resistance_outside_the_unit_interval_is_refused(self):
        for bad in (-0.01, 1.01):
            with self.assertRaises(ValueError):
                topology.resistance_ok(bad, 1.0e-2)


class TestPaths(unittest.TestCase):
    """Depth is paid for in nines, logarithmically."""

    def test_transmittance_is_a_product(self):
        values = [0.9, 0.8, 0.5]
        self.assertAlmostEqual(
            topology.path_transmittance(values), 0.36, places=15
        )

    def test_a_path_is_at_least_as_resistive_as_its_worst_boundary(self):
        values = [0.98, 0.95, 0.90, 0.70]
        worst = 1.0 - min(values)
        self.assertGreaterEqual(topology.path_resistance(values), worst)

    def test_depth_raises_the_per_boundary_requirement(self):
        single = topology.resistance_budget(1, 1.0e-2)
        deep = topology.resistance_budget(4, 1.0e-2)
        self.assertAlmostEqual(single, 1.0e-2, places=15)
        self.assertLess(deep, single)

    def test_the_linearised_budget_is_optimistic(self):
        """``eps/n`` is the number people quote, and it is too generous."""
        for n in (2, 4, 10):
            exact = topology.resistance_budget(n, 1.0e-2)
            linear = 1.0e-2 / n
            self.assertGreater(exact, linear, n)

    def test_the_depth_penalty_approaches_log10_n(self):
        """``log10(n)`` is the linearisation; the exact value is ``O(eps)`` above it."""
        for n in (2, 4, 100):
            penalty = topology.depth_penalty_nines(n, 1.0e-2)
            self.assertAlmostEqual(
                penalty, math.log10(n), delta=5.0e-3,
                msg="depth {} should cost about log10(n) nines".format(n),
            )

    def test_the_depth_penalty_converges_to_log10_n_as_eps_shrinks(self):
        for n in (2, 10):
            coarse = topology.depth_penalty_nines(n, 1.0e-2)
            fine = topology.depth_penalty_nines(n, 1.0e-8)
            self.assertLess(
                abs(fine - math.log10(n)), abs(coarse - math.log10(n))
            )
            self.assertAlmostEqual(fine, math.log10(n), delta=1.0e-7)

    def test_the_depth_penalty_is_positive_beyond_one_boundary(self):
        self.assertAlmostEqual(topology.depth_penalty_nines(1, 1.0e-2), 0.0, places=12)
        self.assertGreater(topology.depth_penalty_nines(4, 1.0e-2), 0.5)

    def test_max_depth_is_zero_when_a_single_boundary_fails(self):
        self.assertEqual(topology.max_depth(0.70, 1.0e-2), 0)

    def test_max_depth_grows_with_fidelity(self):
        self.assertGreater(
            topology.max_depth(0.999, 1.0e-2),
            topology.max_depth(0.99, 1.0e-2),
        )


class TestStackReport(unittest.TestCase):
    """The stack's verdicts, and the difference between per-boundary and path."""

    def test_the_report_covers_every_characterised_boundary(self):
        report = topology.stack_report(topology.ROUTE)
        self.assertEqual(report["n_boundaries"], 4)

    def test_the_path_transmittance_is_the_product_of_the_rows(self):
        report = topology.stack_report(topology.ROUTE)
        product = 1.0
        for entry in report["rows"]:
            product *= entry["fidelity"]
        self.assertAlmostEqual(report["path_transmittance"], product, places=15)

    def test_a_boundary_with_no_number_is_reported_as_missing(self):
        report = topology.stack_report(topology.PHASE)
        missing = [r for r in report["rows"] if r["fidelity"] is None]
        self.assertEqual(len(missing), 3)
        self.assertIsNone(missing[0]["seamless"])

    def test_the_bottleneck_is_the_least_faithful_boundary(self):
        report = topology.stack_report(topology.ROUTE)
        self.assertEqual(
            report["bottleneck"], "Cognitive|Information"
        )

    def test_at_the_policy_threshold_nothing_is_seamless(self):
        """The result the paper reports, pinned so it cannot drift silently."""
        report = topology.stack_report(topology.ROUTE, topology.SEAMLESS_EPS)
        self.assertFalse(any(r["seamless"] for r in report["rows"]))
        self.assertFalse(report["path_seamless"])

    def test_the_threshold_is_a_dial_not_a_conclusion(self):
        """A looser threshold makes the same stack seamless, which is the point."""
        report = topology.stack_report(topology.ROUTE, 0.35)
        self.assertTrue(any(r["seamless"] for r in report["rows"]))


class TestNoise(unittest.TestCase):
    """Coarser strata disturb the quantum one, attenuated on the way down."""

    def test_leakage_falls_with_distance(self):
        leaks = [topology.downward_leakage(i) for i in range(1, 5)]
        for nearer, farther in zip(leaks, leaks[1:]):
            self.assertGreater(nearer, farther)

    def test_a_source_below_the_bottom_is_refused(self):
        with self.assertRaises(ValueError):
            topology.downward_leakage(0)

    def test_the_budget_covers_every_upper_stratum(self):
        self.assertEqual(len(topology.noise_budget()), 4)

    def test_the_adjacent_stratum_binds(self):
        """Nearest neighbour couples hardest, which is not the naive answer."""
        rows = topology.noise_budget()
        binding = [r for r in rows if r["binds_first"]]
        self.assertEqual(len(binding), 1)
        self.assertEqual(binding[0]["source"], "Physico-chemical")

    def test_over_budget_falls_monotonically_with_distance(self):
        rows = topology.noise_budget()
        factors = [r["over_budget"] for r in rows]
        for nearer, farther in zip(factors, factors[1:]):
            self.assertGreater(nearer, farther)

    def test_every_row_records_its_shortfall_consistently(self):
        for row in topology.noise_budget():
            self.assertAlmostEqual(
                row["shortfall_decades"],
                row["required_decades"] - row["attenuation_decades"],
                places=12,
            )


class TestExponentialIdentities(unittest.TestCase):
    """Two exact identities, and the refutation of the third form."""

    def test_the_narrowing_coordinate_is_the_log_of_the_room(self):
        for d in (0.5, 0.9, 0.99, 0.9999):
            self.assertAlmostEqual(
                model.narrowing_coordinate(d), -math.log(1.0 - d), places=15
            )

    def test_a_wall_has_no_coordinate(self):
        with self.assertRaises(ValueError):
            model.narrowing_coordinate(1.0)

    def test_the_exponential_rate_is_the_power_law_exponent(self):
        for slack in (0.5, 0.9, 0.98, 0.999):
            self.assertAlmostEqual(
                model.exp_form_rate(slack), model.law_exponent(slack), places=15
            )

    def test_reach_is_exactly_exponential_in_the_narrowing_coordinate(self):
        """Form (i): ``Reach = A0 exp(k u)`` -- exact, not a limit."""
        slack = 0.98
        k = model.law_exponent(slack)
        for d in (0.9, 0.99, 0.999, 0.999999):
            power_law = math.exp(k * model.narrowing_coordinate(d))
            exponential = math.exp(k * -math.log(1.0 - d))
            self.assertAlmostEqual(power_law / exponential, 1.0, places=12)

    def test_reach_is_exactly_exponential_in_generation(self):
        """Form (ii): each rung multiplies the room by ``ratio``, so log is affine.

        ``eps_n = eps_0 ratio**n`` gives ``Reach_n = eps_n**-k``, and the claim is
        that this equals ``Reach_0 exp(rate n)`` to machine precision -- not
        approximately, and not only in a limit.
        """
        slack, ratio, eps0 = 0.98, 0.5, 1.0e-2
        k = model.law_exponent(slack)
        rate = model.generation_rate(slack, ratio)
        self.assertAlmostEqual(rate, k * math.log(1.0 / ratio), places=15)
        self.assertAlmostEqual(rate, k * math.log(2.0), places=12)

        reach0 = eps0 ** -k
        for n in range(1, 20):
            from_rungs = (eps0 * ratio ** n) ** -k
            from_exponential = reach0 * math.exp(rate * n)
            self.assertAlmostEqual(
                from_rungs / from_exponential, 1.0, places=12,
                msg="generation {}".format(n),
            )

    def test_generation_rate_refuses_a_degenerate_ratio(self):
        for bad in (0.0, 1.0, 1.5):
            with self.assertRaises(ValueError):
                model.generation_rate(0.98, bad)

    def test_the_tangent_rate_is_k_over_the_room(self):
        slack = 0.98
        k = model.law_exponent(slack)
        for d in (0.5, 0.99, 0.999):
            self.assertAlmostEqual(
                model.local_rate(d, slack), k / (1.0 - d), places=12
            )

    def test_the_tangent_rate_diverges_at_the_wall(self):
        self.assertGreater(
            model.local_rate(0.999999, 0.98), model.local_rate(0.999, 0.98)
        )

    def test_the_log_reach_is_strictly_convex(self):
        """Form (iv): no constant-rate exponential exists on any interval."""
        slack = 0.98
        for d in (0.1, 0.5, 0.9, 0.99, 0.999):
            self.assertGreater(model.curvature(d, slack), 0.0)

    def test_log_reach_is_not_affine_in_difficulty(self):
        self.assertFalse(model.is_affine_in_difficulty(0.98))
        self.assertFalse(model.is_affine_in_difficulty(0.5))


class TestWindows(unittest.TestCase):
    """The geometric window is the stretching coordinate; the absolute one is not."""

    def test_the_geometric_window_stays_inside_the_relative_width(self):
        points = model.geometric_window(0.999, 0.1, samples=401)
        eps = [1.0 - d for d, _ in points]
        eps0 = 1.0e-3
        self.assertGreaterEqual(min(eps), eps0 * 0.9 - 1e-18)
        self.assertLessEqual(max(eps), eps0 * 1.1 + 1e-18)
        # Cell-centred, so the outermost samples sit half a cell inside ±f.
        self.assertAlmostEqual(max(eps) / min(eps), 1.1 / 0.9, delta=0.005)

    def test_the_geometric_window_is_clipped_at_the_wall(self):
        points = model.geometric_window(1.0 - 1.0e-9, 0.5, samples=21)
        self.assertTrue(all(d < 1.0 for d, _ in points))

    def test_the_absolute_window_cannot_exceed_the_wall(self):
        points = model.absolute_window(0.9999, 1.0e-4, samples=21)
        self.assertTrue(all(d < 1.0 for d, _ in points))

    def test_a_degenerate_window_is_refused(self):
        with self.assertRaises(ValueError):
            model.geometric_window(0.5, 0.0)
        with self.assertRaises(ValueError):
            model.absolute_window(0.5, 0.0)


class TestTheLimitMapNumber(unittest.TestCase):
    """The analytic prediction ``1 + f**2/5``, checked against least squares."""

    def _fit_ratio(self, d0, f):
        k = model.law_exponent(0.98)
        points = model.geometric_window(d0, f, samples=401)
        xs = [p[0] for p in points]
        ys = [p[1] * k for p in points]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        var = sum((x - mean_x) ** 2 for x in xs)
        slope = cov / var
        return slope * (1.0 - d0) / k

    def test_the_measured_ratio_matches_the_analytic_one(self):
        """Agreement to ``O(f**4)``, which is the order the derivation drops.

        Asserting a fixed decimal place would hide the interesting part: the
        residual is not noise, it is the next term in the expansion, and it must
        shrink as ``f**4`` rather than merely being small.
        """
        for f in (0.05, 0.1, 0.2):
            measured = self._fit_ratio(0.999, f)
            predicted = model.exponential_window_ratio(f)
            self.assertLess(
                abs(measured - predicted), f ** 4,
                msg="f={} residual is not fourth order".format(f),
            )

    def test_the_residual_matches_the_fourth_order_coefficient(self):
        """``3/35`` is derived, not fitted; the measurement confirms it."""
        for f in (0.05, 0.1):
            measured = self._fit_ratio(0.999, f)
            leading = 1.0 + f * f / 5.0
            self.assertAlmostEqual(
                (measured - leading) / (3.0 * f ** 4 / 35.0), 1.0, delta=0.05,
                msg="f={}".format(f),
            )

    def test_the_residual_really_is_fourth_order(self):
        residuals = []
        for f in (0.1, 0.05, 0.025):
            residuals.append(
                abs(self._fit_ratio(0.999, f) - model.exponential_window_ratio(f))
            )
        # Halving f should shrink the residual by about 16. Demand at least 8.
        for coarse, fine in zip(residuals, residuals[1:]):
            self.assertGreater(coarse / fine, 8.0, residuals)

    def test_the_ratio_tends_to_one_as_the_window_shrinks(self):
        ratios = [self._fit_ratio(0.999, f) for f in (0.5, 0.2, 0.1, 0.05, 0.01)]
        for coarser, finer in zip(ratios, ratios[1:]):
            self.assertGreater(coarser, finer)
        self.assertLess(ratios[-1], 1.0001)

    def test_the_ratio_is_independent_of_the_anchor(self):
        """Scale-free: the same window shape at any D0 gives the same rate."""
        values = [self._fit_ratio(d0, 0.1) for d0 in (0.99, 0.999, 0.9999)]
        for value in values:
            self.assertAlmostEqual(value / values[0], 1.0, places=10)

    def test_the_analytic_ratio_is_at_least_one(self):
        for f in (0.01, 0.1, 0.5):
            self.assertGreaterEqual(model.exponential_window_ratio(f), 1.0)

    def test_a_degenerate_relative_width_is_refused(self):
        for bad in (0.0, 1.0, -0.1):
            with self.assertRaises(ValueError):
                model.exponential_window_ratio(bad)


class TestSelectivityAnchors(unittest.TestCase):
    """Selectivity and boundary fidelity move in opposite directions."""

    def test_the_protein_has_the_larger_exponent(self):
        k_bio = model.law_exponent(P.SELECTIVITY_BIO.value)
        k_art = model.law_exponent(P.SELECTIVITY_ART.value)
        self.assertGreater(k_bio, k_art)

    def test_the_exponent_ratio_is_the_one_the_paper_quotes(self):
        k_bio = model.law_exponent(P.SELECTIVITY_BIO.value)
        k_art = model.law_exponent(P.SELECTIVITY_ART.value)
        self.assertAlmostEqual(k_bio / k_art, 20.2, delta=0.1)

    def test_selectivity_is_not_the_same_number_as_boundary_fidelity(self):
        """They coincide for the protein by construction, and must not be aliased."""
        self.assertIsNot(
            P.SELECTIVITY_BIO.value,
            topology.fidelity(topology.boundary(0), topology.ROUTE),
        )
        self.assertAlmostEqual(
            P.SELECTIVITY_BIO.value,
            topology.fidelity(topology.boundary(0), topology.ROUTE),
            places=15,
        )


if __name__ == "__main__":
    unittest.main()
