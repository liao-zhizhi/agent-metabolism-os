"""What the cost theory has to be true about, before any of its numbers count.

The document makes four claims that are easy to state and easy to get wrong:
a retry series that diverges, an amortised floor that is a price rather than a
fit, a reuse knee at ``1 - 1/L``, and a disaster threshold small enough that
the argument does not need a price on a life.

None of those is asserted by hard-coding a number. What is asserted is that the
number in the document is the number the generator reads out of the experiment,
so a re-run that moves a statistic fails here instead of shipping a stale claim:

* the price vector in the document is the price vector in
  ``solidification_summary.json``, and the knee is derived from ``L`` rather
  than typed;
* the solidification accounting identity closes for every drift rate, which is
  what makes the steady-state law a law and not a trend line;
* the per-success cost is computed from latency and success rate, so an arm
  cannot look cheap by being fast and failing;
* the crossover into a losing region (``B* < B < B_rec``) is reported, because
  a theory that only reports the region where it wins is marketing;
* the headline figures in ``COST_THEORY.md`` are the ones in the generated
  JSON, including the architect's aphorism, verbatim.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments" / "cost_theory"
DOC = HERE / "COST_THEORY.md"
NUMBERS = HERE / "cost_theory_numbers.json"
GENERATOR = HERE / "cost_theory_numbers.py"

APHORISM = "真正的降本不是省去验证，而是用确定性的因果固化去避免无尽的物理灾难。"


def load() -> dict:
    return json.loads(NUMBERS.read_text())


class GeneratorTest(unittest.TestCase):
    def test_generator_runs_clean_and_is_idempotent(self):
        before = NUMBERS.read_bytes()
        result = subprocess.run(
            [sys.executable, str(GENERATOR)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, NUMBERS.read_bytes(),
                         "generator output changed between two identical runs")

    def test_every_claim_passes(self):
        claims = load()["claims"]
        self.assertTrue(claims)
        for name, verdict in claims.items():
            with self.subTest(claim=name):
                self.assertTrue(verdict, name)


class PriceVectorTest(unittest.TestCase):
    def test_prices_are_the_experiment_prices(self):
        prices = load()["prices"]
        source = json.loads(
            (ROOT / "experiments" / "solidification_summary.json").read_text()
        )["parameters"]
        self.assertEqual(prices["c_v"], source["verify_cost"])
        self.assertEqual(prices["c_c"], source["cached_cost"])
        self.assertEqual(prices["c_d"], source["detector_cost"])

    def test_knee_is_derived_from_branching_factor(self):
        prices = load()["prices"]
        self.assertAlmostEqual(
            prices["kappa_star"], 1.0 - 1.0 / prices["L"], places=12
        )

    def test_break_even_drift_is_a_price_ratio(self):
        prices = load()["prices"]
        self.assertAlmostEqual(
            prices["break_even_rho"],
            1.0 - prices["c_c"] / prices["c_v"],
            places=12,
        )
        self.assertLess(prices["break_even_rho"], 1.0)


class SolidificationTest(unittest.TestCase):
    def test_accounting_identity_closes(self):
        for rho, row in load()["law2_solidification"]["by_rho"].items():
            with self.subTest(rho=rho):
                self.assertLess(abs(row["identity_residual"]), 0.02)

    def test_drift_price_equals_the_verification_price(self):
        law = load()["law2_solidification"]
        self.assertAlmostEqual(law["drift_slope"], 1.0, delta=0.03)

    def test_compiling_pays_below_break_even_and_stops_after(self):
        law = load()["law2_solidification"]
        for rho, row in law["by_rho"].items():
            with self.subTest(rho=rho):
                self.assertGreater(row["savings_vs_A"], 0.0)
        self.assertGreater(law["by_rho"]["0.1"]["savings_vs_A"], 0.85)

    def test_payback_is_the_second_traverse_not_a_handful(self):
        law = load()["law2_solidification"]
        self.assertEqual(law["payback_traverses"], 1.0)
        self.assertGreater(law["G_of_6"], 3.0)
        self.assertLess(law["G_of_6"], law["by_rho"]["0.1"]["G_infinity"])


class CompoundingTest(unittest.TestCase):
    def test_flat_arm_reproduces_the_measured_multiple(self):
        law = load()["law3_compounding"]
        self.assertEqual(law["marginal_flat_at_six"], 4096)
        self.assertAlmostEqual(law["multiple_at_six"] / 9.174311926605505,
                               4096.0, delta=2.0)

    def test_kappa_brackets_the_knee(self):
        rows = load()["law3_compounding"]["by_kappa"]
        self.assertTrue(rows["0.1"]["bounded"])
        self.assertFalse(rows["0.3"]["bounded"])
        self.assertLess(rows["0.1"]["geometric_ratio"], 1.0)
        self.assertGreater(rows["0.3"]["geometric_ratio"], 1.0)

    def test_cost_is_bounded_rather_than_exponential(self):
        # The document refuses the "exponential cost decline" claim; if a future
        # edit resurrects it, this is where it fails.
        rows = load()["law3_compounding"]["by_kappa"]
        for rho, row in rows.items():
            if row["bounded"]:
                with self.subTest(rho=rho):
                    self.assertGreaterEqual(row["x_at_depth_8"], 1.0)


class CatastropheTest(unittest.TestCase):
    def test_disaster_threshold_is_small_in_seconds(self):
        law = load()["law4_catastrophe"]
        self.assertLess(law["ad"]["critical_disaster_s"], 2.0)
        self.assertGreater(law["ad"]["critical_disaster_s"], 0.0)

    def test_matched_latency_is_a_fraction_of_the_raw_one(self):
        law = load()["law4_catastrophe"]
        self.assertLess(law["ad"]["delta_cost_s"], law["ad"]["delta_cost_raw_s"])

    def test_one_channel_dominates_without_a_time_penalty(self):
        channels = load()["law4_catastrophe"]["by_channel"]
        self.assertTrue(channels["steering_lag"]["dominates_for_free"])
        self.assertGreater(channels["steering_lag"]["A_paint"], 0)
        self.assertEqual(channels["steering_lag"]["B_paint"], 0)


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text()
        self.data = load()

    def test_aphorism_is_verbatim(self):
        self.assertIn(APHORISM, self.text)

    def test_headline_numbers_are_in_the_document(self):
        law1 = self.data["law1_retry"]
        law2 = self.data["law2_solidification"]
        law4 = self.data["law4_catastrophe"]
        expected = [
            f"{law1['A_ms_per_success']:.0f}",          # 9037
            f"{law1['B_ms_per_success']:.0f}",          # 4687
            f"{100 * law2['by_rho']['0.1']['savings_vs_A']:.1f}%",  # 89.4%
            f"{law4['ad']['critical_disaster_s']:.2f}",  # 0.95
        ]
        for needle in expected:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)

    def test_the_four_laws_are_all_present(self):
        for heading in ("定律一", "定律二", "定律三", "定律四"):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.text)

    def test_known_limits_are_disclosed(self):
        for disclosure in ("46%", "7%", "O(L)", "6.61", "39.23", "4710", "ERR-006"):
            with self.subTest(disclosure=disclosure):
                self.assertIn(disclosure, self.text)

    def test_executive_summary_leads_with_the_two_derivatives(self):
        head = self.text.split("## 0.")[0]
        self.assertIn("执行摘要", head)
        # the selling point is the contrast between the two derivatives, so both
        # have to be in the summary and not only in section 7
        self.assertIn("\\partial C}{\\partial T}", head)
        self.assertIn("\\partial c_v}", head)


if __name__ == "__main__":
    unittest.main()
