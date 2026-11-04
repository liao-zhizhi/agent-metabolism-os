"""The emergence mapping: what is arithmetic, what is empirical, what is neither.

The point of this file is that three of the paper's claims in this area are
*identities* and one is a *refutation*, and both facts have to be pinned:

* The power-law ``R**2 = 1`` is not evidence. It follows from Reach and the
  narrowing coordinate both being affine in ``ln N``. The test that matters is
  not "does it fit" but "would it fit for a different scaling law too" -- and it
  does, so the fit discriminates nothing.
* The exponential-in-``D`` ``R**2`` is *also* not evidence, and is identical
  under two laws whose exponents differ by 4.5x. It measures the curvature of
  the saturation map ``D(N)`` and nothing else.
* Composing boundaries does not sharpen a transition. It translates one. The
  exact collapse ``A_m(N) = A_1(N/m**(1/alpha))`` is tested to machine
  precision, because the report makes a strong claim (a four-decade ramp, a
  one-decade sampling cadence) that would be wrong if the collapse were only
  approximate.
* The brief's headline number is false by a wide margin: ``p = 2`` at 20B. The
  test asserts the *disagreement*, with a bracket, so that a future change to
  the bridge or the scaling law has to move the number through the test rather
  than past it silently.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import emergence_experiment as E  # noqa: E402
from experiments import parameters as P  # noqa: E402


class TestScalingLaws(unittest.TestCase):
    """The laws are cited constants, so they are pinned, not fitted."""

    def test_chinchilla_coefficients_are_the_published_ones(self):
        self.assertAlmostEqual(E.CHINCHILLA.irreducible, 1.69)
        self.assertAlmostEqual(E.CHINCHILLA.coefficient, 406.4)
        self.assertAlmostEqual(E.CHINCHILLA.exponent, 0.34)

    def test_kaplan_coefficient_is_the_floor_of_the_power_law(self):
        """``L = (N_c/N)**a = N_c**a * N**-a``, so the coefficient is N_c**a."""
        self.assertAlmostEqual(
            E.KAPLAN.coefficient, 8.8e13 ** 0.076, places=12)
        self.assertEqual(E.KAPLAN.irreducible, 0.0)

    def test_a_loss_law_must_fall(self):
        for law in E.LAWS:
            self.assertGreater(law.loss(1.0e8), law.loss(1.0e10))
            self.assertGreater(law.loss(1.0e10), law.loss(1.0e12))

    def test_chinchilla_approaches_but_never_crosses_its_floor(self):
        law = E.CHINCHILLA
        self.assertGreater(law.loss(1.0e15), law.irreducible)
        self.assertAlmostEqual(law.loss(1.0e15), law.irreducible, places=2)

    def test_a_non_scaling_exponent_is_refused(self):
        with self.assertRaises(ValueError):
            E.ScalingLaw("bad", "nowhere", 0.0, 1.0, 1.5)
        with self.assertRaises(ValueError):
            E.ScalingLaw("bad", "nowhere", 0.0, 1.0, 0.0)

    def test_a_non_positive_parameter_count_is_refused(self):
        with self.assertRaises(ValueError):
            E.CHINCHILLA.loss(0.0)
        with self.assertRaises(ValueError):
            E.CHINCHILLA.loss(-1.0)


class TestTheBridge(unittest.TestCase):
    """``F = exp(-(L - E))`` -- the softest joint, so its properties are pinned."""

    def test_fidelity_is_one_exactly_at_the_floor(self):
        self.assertEqual(E.per_boundary_fidelity(1.69, 1.69), 1.0)

    def test_fidelity_lies_in_the_unit_interval(self):
        for probe in (1e8, 1e9, 1e11, 1e14, 1e18):
            f = E.per_boundary_fidelity(E.CHINCHILLA.loss(probe),
                                        E.CHINCHILLA.irreducible)
            self.assertGreater(f, 0.0)
            self.assertLessEqual(f, 1.0)

    def test_fidelity_rises_with_scale(self):
        previous = -1.0
        for probe in (1e8, 1e9, 1e10, 1e11, 1e12, 1e13):
            f = E.per_boundary_fidelity(E.CHINCHILLA.loss(probe),
                                        E.CHINCHILLA.irreducible)
            self.assertGreater(f, previous)
            previous = f

    def test_fidelity_is_monotone_in_the_loss(self):
        """Higher loss, lower fidelity: the bridge is not accidentally inverted."""
        for loss in (1.7, 1.8, 2.0, 2.5):
            self.assertLess(E.per_boundary_fidelity(loss, 1.69),
                            E.per_boundary_fidelity(loss - 0.1, 1.69))

    def test_resistance_is_the_complement(self):
        for f in (0.1, 0.5, 0.88, 0.999):
            self.assertAlmostEqual(E.resistance(f) + f, 1.0, places=15)

    def test_nines_are_the_decades_of_resistance(self):
        """``p = -log10 R``, so ``R = 10**-p`` round-trips."""
        for p in (0.5, 1.0, 2.0, 3.0):
            self.assertAlmostEqual(E.nines(10.0 ** -p), p, places=12)


class TestReach(unittest.TestCase):
    """``Reach = ln(criterion)/ln(F)``: the definition, evaluated."""

    def test_reach_is_one_at_the_criterion(self):
        self.assertAlmostEqual(E.depth_reach(E.CRITERION), 1.0, places=12)

    def test_reach_rises_with_fidelity(self):
        previous = -1.0
        for f in (0.5, 0.7, 0.9, 0.99):
            here = E.depth_reach(f)
            self.assertGreater(here, previous)
            previous = here

    def test_a_transparent_interface_has_no_depth_limit(self):
        self.assertEqual(E.depth_reach(1.0), float("inf"))

    def test_reach_is_the_depth_at_which_accuracy_hits_the_criterion(self):
        """Inverse relation, checked by round-tripping rather than by eyeball."""
        for f in (0.6, 0.8, 0.95):
            depth = E.depth_reach(f)
            # floor() of the continuous depth is the last integer that passes
            integer = int(math.floor(depth))
            self.assertGreaterEqual(E.task_accuracy(f, integer), E.CRITERION)
            self.assertLess(E.task_accuracy(f, integer + 1), E.CRITERION)

    def test_task_accuracy_is_the_composed_product(self):
        f = 0.9
        self.assertAlmostEqual(E.task_accuracy(f, 3), f * f * f, places=15)

    def test_a_zero_depth_is_refused(self):
        with self.assertRaises(ValueError):
            E.task_accuracy(0.9, 0)


class TestDifficulty(unittest.TestCase):
    """``D`` reuses the paper's saturation map, and must agree with it."""

    def test_difficulty_matches_the_model_map(self):
        for probe in (1e8, 1e10, 1e13):
            self.assertAlmostEqual(
                E.semantic_difficulty(probe),
                E.model.difficulty_from_potential(
                    E.effort_decades(probe), P.P0),
                places=15,
            )

    def test_difficulty_is_an_increasing_map_into_the_unit_interval(self):
        previous = -1.0
        for probe in (1e8, 1e9, 1e10, 1e12, 1e14):
            d = E.semantic_difficulty(probe)
            self.assertGreater(d, previous)
            self.assertLess(d, 1.0)
            previous = d

    def test_at_the_reference_scale_there_is_no_narrowing(self):
        self.assertEqual(E.semantic_difficulty(E.SCALE_FLOOR), 0.0)

    def test_below_the_reference_scale_is_still_zero_not_an_error(self):
        self.assertEqual(E.semantic_difficulty(E.SCALE_FLOOR / 10.0), 0.0)


class TestEmergenceScale(unittest.TestCase):
    """The critical scale is solved, so it must land exactly on the criterion."""

    def test_the_crossing_actually_crosses(self):
        for depth in (1, 2, 5, 12):
            n = E.emergence_scale(E.CHINCHILLA, depth)
            f = E.per_boundary_fidelity(E.CHINCHILLA.loss(n),
                                        E.CHINCHILLA.irreducible)
            self.assertAlmostEqual(E.task_accuracy(f, depth), E.CRITERION,
                                   places=10)

    def test_deeper_tasks_need_more_parameters(self):
        scales = [E.emergence_scale(E.CHINCHILLA, m) for m in range(1, 13)]
        for lower, upper in zip(scales, scales[1:]):
            self.assertLess(lower, upper)

    def test_without_a_floor_the_same_closed_form_applies(self):
        n = E.emergence_scale(E.KAPLAN, 5)
        f = E.per_boundary_fidelity(E.KAPLAN.loss(n), 0.0)
        self.assertAlmostEqual(E.task_accuracy(f, 5), E.CRITERION, places=10)

    def test_the_nines_solver_lands_on_the_target(self):
        for target in (1.0, 2.0, 3.0):
            n = E.scale_for_nines(E.CHINCHILLA, target)
            f = E.per_boundary_fidelity(E.CHINCHILLA.loss(n),
                                        E.CHINCHILLA.irreducible)
            self.assertAlmostEqual(E.nines(E.resistance(f)), target, places=9)

    def test_nines_crossings_follow_the_exact_closed_form(self):
        """``N_p = (A / -ln(1 - 10**-p))**(1/alpha)``, with no approximation."""
        law = E.CHINCHILLA
        for target in (1.0, 2.0, 3.0):
            n = E.scale_for_nines(law, target)
            x = -math.log(1.0 - 10.0 ** -target)
            expected = (law.coefficient / x) ** (1.0 / law.exponent)
            self.assertAlmostEqual(n / expected, 1.0, places=12)

    def test_nines_crossings_are_only_asymptotically_geometric(self):
        """Each nine costs ``1/alpha`` decades of parameters, in the limit.

        Not exactly, and the exception is worth pinning: at ``p = 1`` the
        residual is 10% and ``-ln(0.9)`` differs from 0.1 by 5%, so the first
        gap is 3.00 decades against the asymptotic 2.94. Claiming exact
        geometric spacing would be claiming ``-ln(1-x) = x``.
        """
        law = E.CHINCHILLA
        gaps = []
        for p in range(2, 7):
            lower = E.scale_for_nines(law, float(p))
            upper = E.scale_for_nines(law, float(p + 1))
            gaps.append(math.log10(upper / lower))
        # monotonically shrinking towards the asymptote
        for left, right in zip(gaps, gaps[1:]):
            self.assertLess(right, left)
        self.assertAlmostEqual(gaps[-1], 1.0 / law.exponent, places=2)
        self.assertGreater(gaps[0], 1.0 / law.exponent)


class TestTransitionShape(unittest.TestCase):
    """The result that composition translates rather than steepens."""

    def test_the_collapse_is_exact(self):
        """``A_m(N) == A_1(N / m**(1/alpha))`` to machine precision."""
        alpha = E.CHINCHILLA.exponent
        for depth in (2, 3, 5, 8, 12):
            for probe in (1e9, 1e10, 1e11, 1e12):
                direct = E.task_accuracy(
                    E.per_boundary_fidelity(
                        E.CHINCHILLA.loss(probe), E.CHINCHILLA.irreducible),
                    depth)
                shifted = E.task_accuracy(
                    E.per_boundary_fidelity(
                        E.CHINCHILLA.loss(probe / depth ** (1.0 / alpha)),
                        E.CHINCHILLA.irreducible),
                    1)
                self.assertAlmostEqual(direct, shifted, places=14)

    def test_the_width_does_not_depend_on_depth(self):
        """The point of the collapse: no narrowing with task depth."""
        width = E.transition_width(E.CHINCHILLA)
        for depth in (2, 5, 12):
            ends = []
            for level in E.WIDTH_LEVELS:
                n = E.emergence_scale(E.CHINCHILLA, depth, criterion=level)
                ends.append(math.log10(n))
            self.assertAlmostEqual(ends[1] - ends[0], width, places=10)

    def test_the_width_matches_its_closed_form(self):
        alpha = E.CHINCHILLA.exponent
        expected = math.log(math.log(10.0) / math.log(1.0 / 0.9)) / (
            alpha * math.log(10.0))
        self.assertAlmostEqual(E.transition_width(E.CHINCHILLA), expected,
                               places=12)

    def test_the_width_is_law_dependent_but_not_depth_dependent(self):
        self.assertGreater(E.transition_width(E.KAPLAN),
                           E.transition_width(E.CHINCHILLA))

    def test_the_slope_at_the_criterion_matches_its_formula(self):
        """Numerical derivative against ``criterion * ln10 * alpha * ln(1/c)``."""
        law = E.CHINCHILLA
        depth = 5
        n = E.emergence_scale(law, depth)
        step = 1.0e-3  # in decades

        def accuracy(decades: float) -> float:
            return E.task_accuracy(
                E.per_boundary_fidelity(
                    law.loss(n * 10.0 ** decades), law.irreducible),
                depth)

        numerical = (accuracy(step) - accuracy(-step)) / (2.0 * step)
        self.assertAlmostEqual(numerical, E.slope_at_criterion(law), places=4)

    def test_the_slope_does_not_depend_on_depth(self):
        """The same invariance as the width, checked on a second observable."""
        law = E.CHINCHILLA
        for depth in (1, 4, 12):
            n = E.emergence_scale(law, depth)
            step = 1.0e-3

            def accuracy(decades: float) -> float:
                return E.task_accuracy(
                    E.per_boundary_fidelity(
                        law.loss(n * 10.0 ** decades), law.irreducible),
                    depth)

            numerical = (accuracy(step) - accuracy(-step)) / (2.0 * step)
            self.assertAlmostEqual(numerical, E.slope_at_criterion(law),
                                   places=4)

    def test_the_slope_is_a_gentle_ramp_not_a_step(self):
        slope = E.slope_at_criterion(E.CHINCHILLA)
        self.assertGreater(slope, 0.0)
        self.assertLess(slope, 1.0)

    def test_points_in_transition_is_the_width_over_the_spacing(self):
        width = E.transition_width(E.CHINCHILLA)
        self.assertAlmostEqual(E.points_in_transition(width, 1.0), width,
                               places=12)
        self.assertAlmostEqual(E.points_in_transition(width, 0.5), 2.0 * width,
                               places=12)
        self.assertLess(E.points_in_transition(width, 1.0), 4.5)


class TestFits(unittest.TestCase):
    """What the regressions do, and what they do not establish."""

    @classmethod
    def setUpClass(cls):
        cls.result = E.analyse()

    def test_the_power_law_fits_to_machine_precision(self):
        self.assertGreater(self.result["power_law"]["r2"], 0.9999999999)
        self.assertLess(self.result["power_law"]["max_residual"], 1e-10)

    def test_the_exponential_in_d_fits_worse(self):
        self.assertLess(self.result["exponential_in_d"]["r2"],
                        self.result["power_law"]["r2"])
        self.assertLess(self.result["exponential_in_d"]["r2"], 0.95)

    def test_the_fitted_k_is_the_predicted_k(self):
        """``k = alpha * ln10 * P0``, with no free parameter left over."""
        predicted = (E.CHINCHILLA.exponent * math.log(10.0) * P.P0)
        self.assertAlmostEqual(self.result["power_law"]["slope"], predicted,
                               places=10)

    def test_the_invariant_is_alpha_ln_ten(self):
        invariant = self.result["invariant"]
        self.assertAlmostEqual(invariant["k_over_p0"], invariant["alpha_ln10"],
                               places=14)
        self.assertLess(invariant["relative_difference"], 1e-14)

    def test_the_implied_selectivity_is_where_the_mapping_can_die(self):
        invariant = self.result["invariant"]
        self.assertAlmostEqual(
            invariant["implied_selectivity"],
            math.exp(-invariant["k_over_p0"]), places=15)
        self.assertGreater(invariant["implied_selectivity"], 0.4)
        self.assertLess(invariant["implied_selectivity"], 0.5)

    def test_the_power_law_fit_discriminates_nothing(self):
        """A 4.5x change of exponent leaves ``R**2`` at 1 and changes ``k``."""
        rob = E.robustness()
        self.assertAlmostEqual(rob["Chinchilla"]["r2_power"], 1.0, places=10)
        self.assertAlmostEqual(rob["Kaplan"]["r2_power"], 1.0, places=10)
        self.assertNotAlmostEqual(rob["Chinchilla"]["k"], rob["Kaplan"]["k"],
                                  places=2)

    def test_the_exponential_r2_is_law_invariant(self):
        """It measures the curvature of ``D(N)``, not the scaling law."""
        rob = E.robustness()
        self.assertAlmostEqual(rob["Chinchilla"]["r2_exponential"],
                               rob["Kaplan"]["r2_exponential"], places=10)

    def test_the_sweep_is_deterministic(self):
        again = E.analyse()
        self.assertAlmostEqual(again["power_law"]["slope"],
                               self.result["power_law"]["slope"], places=15)
        self.assertEqual(len(again["rows"]), len(self.result["rows"]))


class TestTheClaimUnderTest(unittest.TestCase):
    """Where the brief's numbers are right, and where they are not."""

    @classmethod
    def setUpClass(cls):
        cls.result = E.analyse()

    def test_at_20B_fidelity_is_88_percent_not_99(self):
        at = self.result["at_nominal"]
        self.assertAlmostEqual(at["fidelity"], 0.8800, places=3)
        self.assertGreater(at["nines"], 0.85)
        self.assertLess(at["nines"], 1.0)

    def test_the_two_nine_point_is_far_beyond_20B(self):
        factor = self.result["crossings"]["p=2"]["factor_above_nominal"]
        self.assertGreater(factor, 1000.0)
        self.assertLess(factor, 3000.0)

    def test_the_one_nine_point_is_near_20B(self):
        """The charitable reading: the brief rounded up by one nine."""
        factor = self.result["crossings"]["p=1"]["factor_above_nominal"]
        self.assertGreater(factor, 1.0)
        self.assertLess(factor, 2.5)

    def test_the_depth_that_crosses_at_20B_is_five_or_six(self):
        nearest = self.result["nearest_depth_at_nominal"]
        self.assertIn(nearest["depth"], (5, 6))

    def test_every_depth_shows_the_same_width(self):
        widths = {round(row["transition_width_decades"], 9)
                  for row in self.result["family"]}
        self.assertEqual(len(widths), 1)

    def test_the_collapse_flag_is_true(self):
        transition = self.result["transition"]
        self.assertTrue(transition["depth_invariant"])
        self.assertLess(transition["worst_collapse_error"], 1e-12)

    def test_the_width_is_four_decades_not_one(self):
        """If it were one decade, the sampling artefact would not be needed."""
        width = self.result["transition"]["width_decades"]
        self.assertGreater(width, 3.5)
        self.assertLess(width, 4.5)

    def test_reach_grows_with_scale(self):
        reaches = [row["reach"] for row in self.result["rows"]]
        for lower, upper in zip(reaches, reaches[1:]):
            self.assertLess(lower, upper)

    def test_the_rows_are_ordered_and_complete(self):
        rows = self.result["rows"]
        self.assertEqual(len(rows), self.result["sweep"]["n"])
        params = [row["params"] for row in rows]
        self.assertEqual(params, sorted(params))
        self.assertAlmostEqual(params[0], self.result["sweep"]["low"], places=0)
        self.assertAlmostEqual(params[-1], self.result["sweep"]["high"], places=0)


if __name__ == "__main__":
    unittest.main()
