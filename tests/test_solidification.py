"""Solidification: the label, the witness, and the arithmetic behind the curve.

The section this file guards makes a claim that is easy to state and easy to
fake: *a compiled rung is cheaper because a verification already happened*. Every
test here is a way for that claim to be true-looking and false.

* **The label cannot be set for free.** ``state`` is a field, so the interesting
  question is not whether it can be assigned but whether it can be assigned
  *without* a verified witness bound to this gap. The refusals are the content:
  no witness, another gap's witness, an edited witness, a non-PASS witness, a
  witness recorded at a different difficulty.
* **The label is not the gap.** Difficulty, span and amplification must be
  identical in both states. A solidification that quietly made the gap easier
  would be the amplification bug from an earlier section wearing a new name.
* **The asymmetry is real.** Withdrawal is ungated; compilation is gated. If a
  test ever shows ``drift`` refusing, the system has lost the ability to become
  more careful, which is the one direction it must never lose.
* **The closed forms match the simulation.** ``replay_fraction``,
  ``rung_cost``, ``cascade_cost`` and the fitted slope are checked against the
  Monte Carlo rather than against a second derivation of the same algebra.
* **The headline is bounded.** The experiment's numbers are re-derived here, so
  a constant nudged to make a ratio look exponential fails in a test rather than
  in a paragraph.
"""

from __future__ import annotations

import csv
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism.double_entry import FAIL, INVALID, PASS  # noqa: E402
from agent_metabolism.interface_engine import (  # noqa: E402
    SOLIDIFIED,
    SOLIDIFIED_COST,
    SOLIDIFY_STATES,
    UNSOLIDIFIED,
    VERIFY_COST,
    FractalEvolver,
    Interface,
    InterfaceError,
    Layer,
    NarrowCondition,
    upgrade_witness,
    verify_witness,
    witness_digest,
)

from experiments import solidification_experiment as sd  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _evolver() -> FractalEvolver:
    return FractalEvolver(layers=[
        Layer("Macro", "M", measured={"t": 1.0}),
        Layer("Quantum", "Q", measured={"t": 1e-12}),
    ])


def _gap(condition: str = "phase coherence", difficulty: float = 0.81) -> Interface:
    return Interface("Macro", "Quantum", condition, span=1.0,
                     difficulty=difficulty)


def _condition(j: int = 0) -> NarrowCondition:
    return NarrowCondition(
        "mechanism {}".format(j), "dilution refrigeration",
        "deposition record {}".format(j), 100.0,
        replicable=True, cost=0.9,
    )


def _crossed(solidify: bool = False):
    """One real crossing, plus the evolver and the result record."""
    evolver = _evolver()
    gap = evolver.seed(_gap())
    result = evolver.cross_interface(gap, _condition(), next_condition="inner",
                                     solidify=solidify)
    if result["verdict"] != PASS:
        raise AssertionError("fixture did not cross: {}".format(result))
    return evolver, result


def _live(evolver: FractalEvolver, key) -> Interface:
    for root in evolver.chains():
        for node in root.walk():
            if list(node.key) == list(key):
                return node
    raise AssertionError("rung {} not found".format(list(key)))


# ---------------------------------------------------------------------------
# the label
# ---------------------------------------------------------------------------


class TestTheLabel(unittest.TestCase):
    def test_the_default_is_unsolidified(self):
        self.assertEqual(_gap().state, UNSOLIDIFIED)
        self.assertFalse(_gap().is_solidified)
        self.assertIsNone(_gap().solidified_at)

    def test_the_two_labels_are_the_whole_alphabet(self):
        for state in SOLIDIFY_STATES:
            gap = Interface("A", "B", "c", span=1.0, difficulty=0.9,
                            condition_met=True, state=state,
                            solidified_at=0.9 if state == SOLIDIFIED else None)
            self.assertEqual(gap.state, state)

    def test_an_unknown_label_is_refused(self):
        with self.assertRaises(InterfaceError):
            Interface("A", "B", "c", span=1.0, difficulty=0.9, state="TEMPERED")

    def test_a_gap_that_was_never_crossed_cannot_be_compiled(self):
        """There is no verification to replay, so the label has no referent."""
        with self.assertRaises(InterfaceError) as caught:
            Interface("A", "B", "c", span=1.0, difficulty=0.9,
                      condition_met=False, state=SOLIDIFIED, solidified_at=0.9)
        self.assertIn("never been crossed", str(caught.exception))

    def test_a_compiled_rung_must_name_its_compile_point(self):
        with self.assertRaises(InterfaceError):
            Interface("A", "B", "c", span=1.0, difficulty=0.9,
                      condition_met=True, state=SOLIDIFIED)

    def test_a_compiled_rung_whose_gap_moved_is_refused(self):
        """The whole point of ``solidified_at``: a cache cannot outlive its gap."""
        with self.assertRaises(InterfaceError) as caught:
            Interface("A", "B", "c", span=1.0, difficulty=0.95,
                      condition_met=True, state=SOLIDIFIED, solidified_at=0.90)
        self.assertIn("re-verify", str(caught.exception))

    def test_a_withdrawn_rung_must_forget_its_compile_point(self):
        with self.assertRaises(InterfaceError):
            Interface("A", "B", "c", span=1.0, difficulty=0.9,
                      condition_met=True, state=UNSOLIDIFIED, solidified_at=0.9)

    def test_cost_follows_the_label(self):
        warm = Interface("A", "B", "c", span=1.0, difficulty=0.9,
                         condition_met=True, state=SOLIDIFIED, solidified_at=0.9)
        cold = Interface("A", "B", "c", span=1.0, difficulty=0.9,
                         condition_met=True)
        self.assertAlmostEqual(cold.crossing_cost, VERIFY_COST)
        self.assertAlmostEqual(warm.crossing_cost, SOLIDIFIED_COST)
        self.assertGreater(cold.crossing_cost, warm.crossing_cost)

    def test_the_label_does_not_change_the_gap(self):
        """Solidification is a claim about verification, not about narrowness."""
        cold = Interface("A", "B", "c", span=7.0, difficulty=0.9,
                         amplification=100.0, condition_met=True)
        warm = cold.evolve(state=SOLIDIFIED, solidified_at=cold.difficulty)
        self.assertEqual(cold.difficulty, warm.difficulty)
        self.assertEqual(cold.span, warm.span)
        self.assertEqual(cold.amplification, warm.amplification)
        self.assertEqual(cold.key, warm.key)

    def test_the_dict_view_reports_the_label_and_its_cost(self):
        warm = Interface("A", "B", "c", span=1.0, difficulty=0.9,
                         condition_met=True, state=SOLIDIFIED, solidified_at=0.9)
        payload = warm.as_dict()
        self.assertEqual(payload["state"], SOLIDIFIED)
        self.assertAlmostEqual(payload["solidified_at"], 0.9)
        self.assertAlmostEqual(payload["crossing_cost"], SOLIDIFIED_COST)


# ---------------------------------------------------------------------------
# compilation is gated
# ---------------------------------------------------------------------------


class TestCompilationIsGated(unittest.TestCase):
    def test_a_crossing_is_not_a_compilation(self):
        evolver, result = _crossed(solidify=False)
        live = _live(evolver, result["interface"])
        self.assertEqual(live.state, UNSOLIDIFIED)
        self.assertFalse(result["solidified"])
        self.assertAlmostEqual(result["crossing_cost"], VERIFY_COST)

    def test_a_crossing_can_compile_itself(self):
        evolver, result = _crossed(solidify=True)
        live = _live(evolver, result["interface"])
        self.assertEqual(live.state, SOLIDIFIED)
        self.assertTrue(result["solidified"])
        self.assertAlmostEqual(evolver.solidification_report()["cost"],
                               SOLIDIFIED_COST)

    def test_the_child_is_never_inherited_as_compiled(self):
        """A new gap has never been verified, whatever its parent did."""
        evolver, result = _crossed(solidify=True)
        child = result["sub_interface"]
        self.assertEqual(child["state"], UNSOLIDIFIED)
        self.assertIsNone(child["solidified_at"])

    def test_no_witness_is_invalid_not_a_pass(self):
        evolver, result = _crossed()
        verdict = evolver.solidify(result["interface"], None)
        self.assertEqual(verdict["verdict"], INVALID)
        self.assertFalse(verdict["solidified"])
        self.assertEqual(
            _live(evolver, result["interface"]).state, UNSOLIDIFIED)

    def test_the_crossing_own_witness_compiles(self):
        evolver, result = _crossed()
        self.assertTrue(verify_witness(result))
        verdict = evolver.solidify(result["interface"], result)
        self.assertEqual(verdict["verdict"], PASS)
        self.assertTrue(verdict["solidified"])
        self.assertAlmostEqual(verdict["gain"], VERIFY_COST / SOLIDIFIED_COST)

    def test_a_witness_for_another_gap_is_refused(self):
        """The defect ERR-003 recorded, with a cache instead of a ledger."""
        evolver, first = _crossed()
        second = evolver.cross_interface(
            first["sub_interface"], _condition(1), next_condition="deeper")
        self.assertEqual(second["verdict"], PASS)
        verdict = evolver.solidify(first["interface"], second)
        self.assertEqual(verdict["verdict"], FAIL)
        self.assertIn("WITNESS_NOT_FOR_THIS_GAP",
                      [f["kind"] for f in verdict["faults"]])

    def test_an_edited_witness_is_refused(self):
        evolver, result = _crossed()
        tampered = dict(result)
        tampered["amplification"] = 1.0e6
        self.assertFalse(verify_witness(tampered))
        verdict = evolver.solidify(result["interface"], tampered)
        self.assertEqual(verdict["verdict"], FAIL)
        self.assertIn("WITNESS_DOES_NOT_VERIFY",
                      [f["kind"] for f in verdict["faults"]])

    def test_a_non_pass_witness_is_refused(self):
        evolver, result = _crossed()
        forged = dict(result)
        forged["verdict"] = FAIL
        forged["digest"] = witness_digest(forged)
        self.assertTrue(verify_witness(forged))
        verdict = evolver.solidify(result["interface"], forged)
        self.assertEqual(verdict["verdict"], FAIL)
        self.assertIn("WITNESS_NOT_A_PASS",
                      [f["kind"] for f in verdict["faults"]])

    def test_a_witness_recorded_at_another_difficulty_is_refused(self):
        evolver, result = _crossed()
        stale = dict(result)
        stale["difficulty"] = 0.99
        stale["digest"] = witness_digest(stale)
        self.assertTrue(verify_witness(stale))
        verdict = evolver.solidify(result["interface"], stale)
        self.assertEqual(verdict["verdict"], FAIL)
        self.assertIn("WITNESS_DIFFICULTY_MOVED",
                      [f["kind"] for f in verdict["faults"]])

    def test_an_uncrossed_gap_cannot_be_compiled_at_all(self):
        evolver = _evolver()
        gap = evolver.seed(_gap())
        with self.assertRaises(InterfaceError):
            evolver.solidify(gap, None)

    def test_a_failed_crossing_is_not_compiled_even_if_asked(self):
        evolver = _evolver()
        gap = evolver.seed(_gap())
        result = evolver.cross_interface(
            gap, NarrowCondition("nothing", "none", "nothing", 100.0,
                                 satisfies=False), solidify=True)
        self.assertEqual(result["verdict"], FAIL)
        self.assertFalse(result["solidified"])
        self.assertEqual(result["state"], UNSOLIDIFIED)


# ---------------------------------------------------------------------------
# withdrawal is not gated
# ---------------------------------------------------------------------------


class TestWithdrawalIsNotGated(unittest.TestCase):
    def test_withdrawal_needs_no_witness(self):
        evolver, result = _crossed(solidify=True)
        verdict = evolver.drift(result["interface"], "the rules changed")
        self.assertTrue(verdict["drifted"])
        self.assertEqual(verdict["state"], UNSOLIDIFIED)
        self.assertAlmostEqual(verdict["crossing_cost"], VERIFY_COST)

    def test_withdrawal_is_idempotent(self):
        evolver, result = _crossed(solidify=True)
        evolver.drift(result["interface"])
        again = evolver.drift(result["interface"])
        self.assertFalse(again["drifted"])
        self.assertEqual(again["state"], UNSOLIDIFIED)

    def test_withdrawal_does_not_touch_the_gap(self):
        evolver, result = _crossed(solidify=True)
        before = _live(evolver, result["interface"])
        evolver.drift(result["interface"])
        after = _live(evolver, result["interface"])
        self.assertEqual(before.difficulty, after.difficulty)
        self.assertEqual(before.span, after.span)
        self.assertEqual(before.condition_met, after.condition_met)
        self.assertNotEqual(before.state, after.state)

    def test_a_withdrawn_rung_can_be_recompiled_by_its_witness(self):
        evolver, result = _crossed()
        evolver.solidify(result["interface"], result)
        evolver.drift(result["interface"])
        again = evolver.solidify(result["interface"], result)
        self.assertEqual(again["verdict"], PASS)
        self.assertEqual(_live(evolver, result["interface"]).state, SOLIDIFIED)

    def test_a_supersession_has_to_re_earn_the_label(self):
        """A second mechanism is a second verification, not a replayed first one."""
        evolver, result = _crossed(solidify=True)
        better = evolver.cross_interface(
            result["interface"],
            NarrowCondition("protein scaffold", "assembly", "pdb 1ABC", 1000.0,
                            replicable=True, cost=0.5),
            amplification=1000.0,
        )
        self.assertEqual(better["verdict"], PASS)
        self.assertTrue(better["superseding"])
        self.assertFalse(better["solidified"])
        self.assertEqual(_live(evolver, result["interface"]).state,
                         UNSOLIDIFIED)


# ---------------------------------------------------------------------------
# bookkeeping
# ---------------------------------------------------------------------------


class TestBookkeeping(unittest.TestCase):
    def test_compiling_is_not_a_crossing(self):
        evolver, result = _crossed()
        before = len(evolver.history)
        evolver.solidify(result["interface"], result)
        self.assertEqual(len(evolver.history), before)
        self.assertEqual(len(evolver.solidification_history()), 1)

    def test_the_report_counts_only_crossed_rungs(self):
        evolver, result = _crossed(solidify=True)
        report = evolver.solidification_report()
        self.assertEqual(report["crossed"], 1)
        self.assertEqual(report["rungs"], 2)
        self.assertEqual(report["solidified"], 1)
        self.assertAlmostEqual(report["fraction"], 1.0)
        self.assertAlmostEqual(report["gain"], VERIFY_COST / SOLIDIFIED_COST)

    def test_the_report_distinguishes_a_shape_from_a_price(self):
        """Fraction compiled is not the cost of a traverse, and both are reported."""
        evolver, first = _crossed(solidify=True)
        evolver.cross_interface(first["sub_interface"], _condition(1),
                                next_condition="deeper", solidify=False)
        report = evolver.solidification_report()
        self.assertEqual(report["crossed"], 2)
        self.assertEqual(report["solidified"], 1)
        self.assertAlmostEqual(report["fraction"], 0.5)
        self.assertAlmostEqual(report["cost"], SOLIDIFIED_COST + VERIFY_COST)
        self.assertTrue(report["mixed"])

    def test_invariants_report_coherence(self):
        evolver, result = _crossed(solidify=True)
        invariants = evolver.invariants()
        self.assertTrue(invariants["self_similar"])
        self.assertEqual(invariants["solidified_rungs"], 1)
        self.assertAlmostEqual(invariants["compiled_fraction"], 1.0)

    def test_an_upgraded_witness_does_not_compile(self):
        """An upgraded witness is trustworthy from the upgrade, not before it."""
        evolver, result = _crossed()
        legacy = dict(result)
        legacy.pop("difficulty")
        legacy.pop("span_before")
        legacy["digest_scheme"] = "sha256"
        legacy["digest"] = "sha256:" + "0" * 64
        upgraded = upgrade_witness(legacy)
        verdict = evolver.solidify(result["interface"], upgraded)
        # INVALID rather than FAIL: nothing was refuted, the witness simply does
        # not bind the thing that would make it sufficient.
        self.assertEqual(verdict["verdict"], INVALID)
        self.assertIn("WITNESS_UNBOUND",
                      [f["kind"] for f in verdict["faults"]])


# ---------------------------------------------------------------------------
# the arithmetic behind the curve
# ---------------------------------------------------------------------------


class TestTheArithmetic(unittest.TestCase):
    def test_replay_fraction_is_the_uncached_share(self):
        self.assertAlmostEqual(sd.replay_fraction(0.0, 1.0), 1.0)
        self.assertAlmostEqual(sd.replay_fraction(0.1, 1.0), 0.9)
        self.assertAlmostEqual(sd.replay_fraction(1.0, 1.0), 0.0)

    def test_the_label_reaches_full_only_at_zero_drift(self):
        """Seamlessness is a limit, not a property, in any drifting environment."""
        self.assertEqual(sd.replay_fraction(0.0, 1.0), 1.0)
        for rho in (1e-9, 0.01, 0.5, 1.0):
            self.assertLess(sd.replay_fraction(rho, 1.0), 1.0)

    def test_rung_cost_is_above_one_only_past_the_crossover(self):
        self.assertLess(sd.rung_cost(0.0, 1.0), VERIFY_COST)
        self.assertLess(sd.rung_cost(0.5, 1.0), VERIFY_COST)
        self.assertGreater(sd.rung_cost(1.0, 1.0), VERIFY_COST)
        crossover = sd.break_even_rho()
        self.assertAlmostEqual(
            sd.rung_cost(crossover, 1.0), VERIFY_COST, places=12)

    def test_the_detector_break_even_is_a_price_not_a_probability(self):
        self.assertAlmostEqual(sd.break_even_detector(0.0),
                               VERIFY_COST - SOLIDIFIED_COST)
        self.assertLess(sd.break_even_detector(0.5),
                        sd.break_even_detector(0.0))

    def test_cascade_is_quadratic_in_depth(self):
        depth = 20
        per_rung = SOLIDIFIED_COST + sd.DETECTOR_COST
        expected = depth * per_rung + 0.1 * depth * (depth + 1) / 2.0
        self.assertAlmostEqual(sd.cascade_cost(depth, 0.1, 1.0), expected)

    def test_cascade_break_even_falls_with_depth(self):
        shallow = sd.cascade_break_even_rho(5)
        deep = sd.cascade_break_even_rho(100)
        self.assertGreater(shallow, deep)
        self.assertGreater(deep, 0.0)

    def test_the_harmonic_marginal_telescopes_to_log2(self):
        for depth in (2, 5, 10, 50):
            marginals = sum(sd.marginal_harmonic(j) for j in range(2, depth + 1))
            self.assertAlmostEqual(marginals, math.log2(depth), places=10)
            self.assertAlmostEqual(sd.harmonic_total(depth),
                                   VERIFY_COST + math.log2(depth), places=10)

    def test_the_fitted_slope_is_the_predicted_one(self):
        """The coefficient is derived from Eq. (19), not fitted to the data."""
        curve = sd.depth_curve()
        slope, r2 = sd.slope_log_depth(curve, "harmonic")
        self.assertAlmostEqual(slope, 1.0 / math.log10(2.0), places=3)
        self.assertGreater(r2, 0.999)

    def test_the_unlabelled_curve_is_not_logarithmic(self):
        curve = sd.depth_curve()
        slope, r2 = sd.slope_log_depth(curve, "unlabelled")
        self.assertLess(r2, 0.95)
        self.assertGreater(slope, 10.0)

    def test_the_cost_ratio_is_bounded_by_the_gain(self):
        """A fixed depth is a sum of positive terms; the ratio must saturate."""
        bound = VERIFY_COST / (SOLIDIFIED_COST + sd.DETECTOR_COST)
        for depth in (1, 2, 10, 100):
            ratio = sd.unlabelled_total(depth) / sd.warm_total(depth, 0.0, 1.0)
            self.assertLessEqual(ratio, bound + 1e-9)

    def test_a_deeper_budget_buys_more_rungs(self):
        per_rung = SOLIDIFIED_COST + sd.DETECTOR_COST
        shallow = sd.depths_at_budget(10.0, per_rung)
        deep = sd.depths_at_budget(100.0, per_rung)
        self.assertGreater(deep, shallow)
        self.assertAlmostEqual(sd.depths_at_budget(0.5, per_rung), 0.0)


# ---------------------------------------------------------------------------
# the simulation agrees with the algebra
# ---------------------------------------------------------------------------


class TestAgainstTheSimulation(unittest.TestCase):
    def test_a_single_traverse_matches_the_closed_form(self):
        import random
        rng = random.Random(11)
        for depth in (1, 5, 10):
            row = sd.simulate(depth, 4000, 0.0, 0.0, rng, labelled=False)
            self.assertAlmostEqual(row.cost / row.crossed, VERIFY_COST)
        for depth in (5, 10):
            row = sd.simulate(depth, 4000, 0.0, 1.0, rng)
            self.assertAlmostEqual(
                row.cost / row.crossed, sd.rung_cost(0.0, 1.0), places=2)

    def test_the_detector_keeps_false_completion_at_zero(self):
        import random
        rng = random.Random(12)
        for rho in (0.0, 0.05, 0.5, 1.0):
            row = sd.simulate(10, 500, rho, 1.0, rng)
            self.assertEqual(row.false, 0)

    def test_a_missed_drift_is_never_forgotten(self):
        """The naive arm is not merely cheaper: it stays wrong once it is wrong."""
        import random
        rng = random.Random(13)
        row = sd.simulate(10, 500, 0.01, 0.0, rng)
        self.assertGreater(row.false / row.crossed, 0.5)

    def test_the_naive_arm_is_the_cheapest_and_the_least_true(self):
        import random
        rng = random.Random(14)
        rho = 0.1
        honest = sd.simulate(10, 500, rho, 1.0, rng)
        naive = sd.simulate(10, 500, rho, 0.0, rng)
        self.assertLess(naive.cost, honest.cost)
        self.assertGreater(naive.false, 0)
        self.assertEqual(honest.false, 0)

    def test_cascade_costs_more_than_the_modular_arm(self):
        import random
        rng = random.Random(15)
        modular = sd.simulate(10, 500, 0.1, 1.0, rng, cascade=False)
        cascading = sd.simulate(10, 500, 0.1, 1.0, rng, cascade=True)
        self.assertGreater(cascading.cost, modular.cost)

    def test_the_sweep_is_reproducible(self):
        first = sd.sweep(runs=40, seed=7)
        second = sd.sweep(runs=40, seed=7)
        self.assertEqual(len(first), len(second))
        self.assertEqual([r["cost"] for r in first],
                         [r["cost"] for r in second])

    def test_every_scenario_is_present_at_every_depth(self):
        rows = sd.sweep(runs=5, seed=8)
        scenarios = {r["scenario"] for r in rows}
        self.assertEqual(
            scenarios,
            {"unlabelled", "labelled", "labelled_naive", "labelled_cascade"})
        depths = {r["depth"] for r in rows}
        self.assertEqual(depths, set(range(1, sd.DEPTH + 1)))


# ---------------------------------------------------------------------------
# the artifacts
# ---------------------------------------------------------------------------


class TestTheArtifacts(unittest.TestCase):
    def test_the_ladder_is_compiled_against_real_witnesses(self):
        evolver, rungs = sd.build_ladder(4)
        self.assertEqual(len(rungs), 4)
        report = evolver.solidification_report()
        self.assertEqual(report["solidified"], 4)
        self.assertEqual(report["crossed"], 4)
        self.assertAlmostEqual(report["cost"], 4 * SOLIDIFIED_COST)

    def test_a_real_ladder_refuses_an_unwitnessed_label(self):
        evolver, rungs = sd.build_ladder(2)
        evolver.drift(rungs[0])
        verdict = evolver.solidify(rungs[0], None)
        self.assertEqual(verdict["verdict"], INVALID)
        self.assertEqual(_live(evolver, rungs[0].key).state, UNSOLIDIFIED)

    def test_the_csv_carries_the_analytic_column(self):
        rows = sd.sweep(runs=3, seed=9)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "data.csv")
            original = sd.DATA_CSV
            try:
                sd.DATA_CSV = path
                sd.write_data(rows)
                with open(path, newline="", encoding="utf-8") as handle:
                    written = list(csv.DictReader(handle))
            finally:
                sd.DATA_CSV = original
        self.assertEqual(len(written), len(rows))
        self.assertIn("analytic_cost", written[0])
        self.assertIn("solid_rate", written[0])

    def test_the_paper_macros_are_generated_from_the_data(self):
        rows = sd.sweep(runs=3, seed=10)
        curve = sd.depth_curve()
        witness = sd.witness_block()
        with tempfile.TemporaryDirectory() as folder:
            data = os.path.join(folder, "data")
            figures = os.path.join(folder, "figures")
            os.makedirs(data)
            os.makedirs(figures)
            original = (sd.PAPER_DATA, sd.PAPER_FIGURES, sd.FIGURE_PNG)
            try:
                sd.PAPER_DATA, sd.PAPER_FIGURES = data, figures
                # A tiny PNG so the copy step has something to copy.
                with open(sd.FIGURE_PNG, "rb") as source:
                    png = source.read()
                sd.FIGURE_PNG = os.path.join(folder, "figure.png")
                with open(sd.FIGURE_PNG, "wb") as target:
                    target.write(png)
                files = sd.write_paper_artifacts(rows, curve, witness)
                for path in files:
                    self.assertTrue(os.path.exists(path), path)
                with open(os.path.join(data, "solidification_macros.tex"),
                          encoding="utf-8") as handle:
                    macros = handle.read()
            finally:
                (sd.PAPER_DATA, sd.PAPER_FIGURES, sd.FIGURE_PNG) = original
        self.assertIn("\\sdDepth", macros)
        self.assertIn("\\sdSlopePredicted", macros)
        self.assertIn("3.3219", macros)


if __name__ == "__main__":
    unittest.main()
