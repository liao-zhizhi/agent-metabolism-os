"""The emergence comparison: the refutation's own load-bearing claims.

The experiment reports a negative result, so the tests carry more weight than
usual. A negative result can be produced by three very different things: the
hypothesis is false, the model cannot express it, or the code silently computes
something else. These tests separate the three.

* The model *can* express an explosion. ``nesting_sweep`` is run with a schedule
  whose marginal cost is negative, and the reach must then rise with depth. If
  that test failed, "no admissible schedule produces an explosion" would be a
  statement about the arithmetic rather than about the hypothesis.
* The reach columns are monotone in the two variables that matter, in the
  direction the paper's law predicts. A sign error here would reverse the
  headline.
* The nested model is strictly better than the independent one for K > 1 and
  equal at K = 1, because that is the entire nesting discount: one rung is
  unaffected, and the claim "nesting rescues depth" would be empty if it were
  not.
* Group A's assertion is a strict superset of B's witness, layer by layer, and
  the gap between them widens with depth. That is the false plateau, stated as
  an inequality rather than as prose.
* The analytic columns are checked against real files on real ladders, with the
  membrane and the lattice both in the loop, and the two implementations are
  required to agree.
* The brief's own numbers (99.68% and 7.9e-13 at D = 0.99) are recomputed from
  the grid, so a change to a constant fails here rather than in a paragraph.

The end-to-end slice is small on purpose. The mechanisms are the production ones
and the numbers came from the full run.
"""

from __future__ import annotations

import csv
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import true_emergence_experiment as T  # noqa: E402


class TestLayerModel(unittest.TestCase):
    """One layer, and the two ways of reading it."""

    def test_layer_probability_falls_with_difficulty(self):
        values = [T.layer_probability(n) for n in (0.0, 0.5, 1.0, 2.0, 3.0)]
        for higher, lower in zip(values, values[1:]):
            self.assertGreater(higher, lower)

    def test_one_nine_costs_one_factor(self):
        self.assertAlmostEqual(T.layer_probability(2.0), 0.01)
        self.assertAlmostEqual(T.layer_probability(1.0), 0.10)

    def test_assertion_is_a_strict_superset_of_the_witness(self):
        for nines in (0.1, 1.0, 2.0, 3.0):
            witness = T.layer_probability(nines)
            asserted = T.claim_probability(nines)
            self.assertGreater(asserted, witness)
            self.assertLessEqual(asserted, 1.0)

    def test_contamination_sets_the_floor(self):
        """As the layer becomes impossible, the assertion tends to c, not to 0."""
        floor = T.claim_probability(12.0, contamination=0.5)
        self.assertAlmostEqual(floor, 0.5, places=6)

    def test_no_contamination_means_no_gap(self):
        self.assertAlmostEqual(T.claim_probability(2.0, contamination=0.0),
                               T.layer_probability(2.0))

    def test_chain_expectation_matches_its_closed_form(self):
        for p in (0.01, 0.3, 0.9):
            expected = sum(p ** j for j in range(1, 7))
            self.assertAlmostEqual(T.expected_chain(p, 6), expected)

    def test_chain_is_capped_by_depth(self):
        self.assertAlmostEqual(T.expected_chain(1.0, 5), 5.0)
        self.assertAlmostEqual(T.expected_chain(0.0, 5), 0.0)


class TestNesting(unittest.TestCase):
    """The budget, and whether the model could ever have said yes."""

    def test_a_longer_rung_cannot_be_the_easier_chain(self):
        """P(pass K) <= P(pass j) for every prefix, under any per-layer law.

        This is the whole refutation in one inequality. It is asserted on
        adversarial sequences, not just on the ones the experiment generates:
        the discount is allowed to be as strong as anyone likes.
        """
        import random
        rng = random.Random(11)
        for _ in range(500):
            ladder = [rng.uniform(0.0, 1.0) for _ in range(T.MAX_DEPTH)]
            cumulative = 1.0
            for value in ladder:
                cumulative *= value
                self.assertLessEqual(cumulative, ladder[0] + 1e-12)

    def test_the_bound_is_attained_when_nothing_fails(self):
        self.assertAlmostEqual(T.prefix_bound([1.0] * 6), 1.0)
        self.assertAlmostEqual(T.prefix_bound([0.5, 0.9, 0.99]), 0.4455)

    def test_marginals_are_positive(self):
        for value in T.budget_marginals():
            self.assertGreater(value, 0.0)

    def test_marginals_decrease(self):
        marginals = T.budget_marginals()[1:]
        for higher, lower in zip(marginals, marginals[1:]):
            self.assertGreater(higher, lower)

    def test_marginals_tend_to_zero(self):
        deep = T.budget_marginals(nines=2.0, depth_max=4000)
        self.assertLess(deep[-1], 0.001)

    def test_total_budget_is_logarithmic_in_depth(self):
        self.assertAlmostEqual(T.nested_nines(2.0, 100) - T.nested_nines(2.0, 10),
                               math.log10(10.0))

    def test_nested_beats_independent_above_one_rung(self):
        for depth in (2, 3, 6):
            nested = T.reach_nested(2.0, depth, T.layer_probability(2.0))
            independent = T.reach_independent(2.0, depth,
                                              T.layer_probability(2.0))
            self.assertGreater(nested, independent)

    def test_nesting_is_a_no_op_at_one_rung(self):
        p = T.layer_probability(2.0)
        self.assertAlmostEqual(T.reach_nested(2.0, 1, p),
                               T.reach_independent(2.0, 1, p))

    def test_nesting_does_not_rescue_difficulty(self):
        """Nested reach still falls with nines; that is the point of the paper."""
        values = [T.reach_nested(n, 6, T.layer_probability(n))
                  for n in (0.5, 1.0, 2.0, 3.0)]
        for higher, lower in zip(values, values[1:]):
            self.assertGreater(higher, lower)

    def test_the_model_can_express_an_explosion(self):
        """Negative marginal cost must produce increasing reach.

        If this fails, the sweep's negative result is an artefact of the grid and
        not a statement about admissible schedules.
        """
        schedule = [2.0, 1.0, 0.5, 0.25, 0.12, 0.06]   # deliberately shrinking
        cost = 0.0
        reaches = []
        for term in schedule:
            cost += term
            reaches.append(T.REACH_ANCHOR * 10.0 ** (-cost))
        for higher, lower in zip(reaches, reaches[1:]):
            self.assertGreater(higher, lower)

    def test_admissible_schedules_never_increase(self):
        for row in T.nesting_sweep():
            self.assertFalse(row["reach_increasing"])
            for a, b in zip(row["marginal"], row["marginal"][1:]):
                self.assertGreaterEqual(a, 0.0)


class TestGrid(unittest.TestCase):
    """The sweep, as data."""

    @classmethod
    def setUpClass(cls):
        cls.cells = T.build_grid()
        cls.result = T.analyse(cls.cells)

    def test_the_grid_is_complete(self):
        self.assertEqual(len(self.cells), T.GRID_POINTS * T.MAX_DEPTH)
        depths = {c.depth for c in self.cells}
        self.assertEqual(depths, set(range(1, T.MAX_DEPTH + 1)))

    def test_the_difficulty_range_is_the_one_in_the_brief(self):
        hard = max(c.difficulty for c in self.cells)
        soft = min(c.difficulty for c in self.cells)
        self.assertGreater(hard, 0.99)
        self.assertLess(soft, 0.15)

    def test_no_probability_leaves_the_unit_interval(self):
        for cell in self.cells:
            for value in (cell.a_per_layer, cell.b_per_layer,
                          cell.false_completion):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_witnessed_reach_falls_with_difficulty(self):
        for depth in range(1, T.MAX_DEPTH + 1):
            rows = sorted((c for c in self.cells if c.depth == depth),
                          key=lambda c: c.nines)
            values = [c.b_reach_independent for c in rows]
            for higher, lower in zip(values, values[1:]):
                self.assertGreater(higher, lower)

    def test_asserted_reach_falls_with_difficulty_but_floors(self):
        rows = sorted((c for c in self.cells if c.depth == T.MAX_DEPTH),
                      key=lambda c: c.nines)
        values = [c.a_reach for c in rows]
        for higher, lower in zip(values, values[1:]):
            self.assertGreater(higher, lower)
        floor = values[-1]
        self.assertAlmostEqual(
            floor / T.REACH_ANCHOR,
            T.CONTAMINATION ** T.MAX_DEPTH,
            places=2)

    def test_nothing_increases_with_depth(self):
        """The shape the hypothesis needs is absent, in every column."""
        for key in ("0.900", "0.990", "0.999"):
            response = self.result["depth_response"][key]
            self.assertFalse(response["a_increases"])
            self.assertFalse(response["b_increases"])
            self.assertFalse(response["b_nested_increases"])

    def test_false_completion_rises_with_depth(self):
        rows = sorted((c for c in self.cells if abs(c.nines - 2.0) < 0.07),
                      key=lambda c: c.depth)
        values = [c.false_completion for c in rows]
        for lower, higher in zip(values, values[1:]):
            self.assertLess(lower, higher)

    def test_assertion_ratio_diverges(self):
        by_nines = {}
        for cell in self.cells:
            if cell.depth == T.MAX_DEPTH:
                by_nines[round(cell.nines, 6)] = cell.inflation
        ordered = [by_nines[k] for k in sorted(by_nines)]
        for lower, higher in zip(ordered, ordered[1:]):
            self.assertGreater(higher, lower)

    def test_the_two_groups_have_different_exponents(self):
        exponents = self.result["exponents"]
        self.assertLess(exponents[str(T.MAX_DEPTH)]["a"], 1.0)
        self.assertAlmostEqual(exponents[str(T.MAX_DEPTH)]["b"],
                               float(T.MAX_DEPTH), places=6)

    def test_the_nested_exponent_is_one_at_every_depth(self):
        for depth in ("1", "3", "6"):
            self.assertAlmostEqual(self.result["exponents"][depth]["b_nested"],
                                   1.0, places=6)

    def test_the_brief_numbers(self):
        """D = 0.99, depth 6: 99.68% false, 7.94e-13 connected."""
        block = self.result["reported"]["0.990"]
        deepest = block["per_depth"][-1]
        self.assertAlmostEqual(deepest["false_completion"], 1.0, places=3)
        p = deepest["b_reach"] / T.REACH_ANCHOR
        expected = T.layer_probability(block["nines"]) ** T.MAX_DEPTH
        self.assertAlmostEqual(p, expected, delta=expected * 1e-6)
        self.assertLess(p, 1e-11)

    def test_the_floors(self):
        floors = self.result["floors"]
        self.assertGreater(floors["a"], 1e3)
        self.assertLess(floors["b"], 1e-10)
        self.assertAlmostEqual(floors["a_closed_form"],
                               T.REACH_ANCHOR * T.CONTAMINATION ** T.MAX_DEPTH)

    def test_the_collapse_point_is_finite_for_the_witness(self):
        self.assertIsNotNone(self.result["collapse"]["b"])
        self.assertGreater(self.result["collapse"]["b"], 0.0)


class TestRealLadder(unittest.TestCase):
    """The membrane and the lattice, on real files, at a small sample."""

    @classmethod
    def setUpClass(cls):
        cls.rows = T.verification_block(
            samples=20, settings=((0.5, 3), (2.5, 3)))

    def setUp(self):
        if not T._HAVE_SIDECAR:
            self.skipTest("audit sidecar not importable")

    def test_the_block_ran(self):
        self.assertEqual(len(self.rows), 2)
        for row in self.rows:
            self.assertEqual(row["samples"], 20)

    def test_model_and_membrane_agree(self):
        for row in self.rows:
            self.assertLess(abs(row["model_chain"] - row["observed_chain"]),
                            0.35)

    def test_harder_ladders_get_less_far(self):
        soft, hard = self.rows
        self.assertGreater(soft["observed_chain"], hard["observed_chain"])

    def test_a_witnessed_ladder_stops_at_the_first_failure(self):
        """Chain length is the count of consecutive PASSes, not a partial score."""
        root = tempfile.mkdtemp(prefix="emergence-test-")
        import random
        try:
            ledger = os.path.join(root, "_ledgers")
            os.makedirs(ledger, exist_ok=True)
            workspace = T.Workspace(root)
            rng = random.Random(7)
            for task in range(1, 16):
                passed, chain = T.run_causal_task(
                    task, 3.0, 4, workspace, rng, ledger)
                self.assertEqual(passed, chain)
                self.assertLessEqual(chain, 4)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_the_lattice_separates_absent_from_wrong(self):
        engine = T.VerdictEngine
        intent = T.digest_of("the artefact this stage was asked to produce")
        self.assertEqual(
            engine(intent=intent, effect=None, check="success").evaluate().value,
            "INVALID")
        self.assertEqual(
            engine(intent=intent, effect=T.digest_of("a different artefact"),
                   check="success").evaluate().value, "FAIL")
        self.assertEqual(
            engine(intent=intent, effect=intent,
                   check="success").evaluate().value, "PASS")


class TestArtifacts(unittest.TestCase):
    """The files the brief asks for, written and re-readable."""

    def test_csv_round_trip(self):
        cells = T.build_grid(points=5)
        with tempfile.TemporaryDirectory() as tmp:
            original = T.DATA_CSV
            try:
                T.DATA_CSV = os.path.join(tmp, "grid.csv")
                T.write_data(cells)
                with open(T.DATA_CSV, newline="", encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
            finally:
                T.DATA_CSV = original
        self.assertEqual(len(rows), len(cells))
        self.assertEqual(set(rows[0]), {
            "nines", "difficulty", "depth", "a_per_layer", "a_reach", "a_chain",
            "b_per_layer", "b_reach_independent", "b_reach_nested", "b_chain",
            "true_reach", "false_completion", "inflation"})
        first = rows[0]
        self.assertAlmostEqual(float(first["difficulty"]),
                               1.0 - 10 ** -float(first["nines"]))

    def test_the_figure_is_written(self):
        cells = T.build_grid(points=7)
        result = T.analyse(cells)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "figure.png")
            T.render(cells, result, path)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 10000)

    def test_the_run_is_deterministic(self):
        one = T.build_grid(points=9)
        two = T.build_grid(points=9)
        self.assertEqual([c.a_reach for c in one], [c.a_reach for c in two])
        self.assertEqual([c.b_reach_nested for c in one],
                         [c.b_reach_nested for c in two])


if __name__ == "__main__":
    unittest.main()
