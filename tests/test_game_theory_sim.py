"""What the paradox simulation has to be true about, before its verdict counts.

The script claims a ranking: a tollbooth on a certification layer beats open
source, a paper-only strategy, and a closed product, for a researcher with
roughly fifteen months of runway. That ranking is a *model output*, so a test
that re-asserted it would only be checking that nobody edited the parameters.

What is asserted here is everything that would make the output meaningless if it
were wrong:

* the energy accounting closes, so ``E`` is a stock with sources and a burn rate
  rather than a number that drifts upward on its own;
* ``psi`` is exactly ``E * D`` every month, so the headline "prestige is not a
  potential difference" is an identity and not a fitted curve;
* more copyable never helps the moat and more convertible adoption never hurts
  dependency, so the two knobs cannot be quietly reversed in sign;
* the same parameters produce the same trajectory, so a seed change is not a
  result change;
* the closed strategy's failure is caused by its lead time exceeding its runway
  -- extend the runway and the death moves, which is the mechanism the script
  says killed it;
* the recommendation is *falsifiable*: somewhere in the sensitivity grid the
  6-month winner changes, so the model is not rigged to a single answer;
* a stress run on the two load-bearing parameters really does lower the success
  probability, which is what makes "the plan is about parameter setting" a
  claim rather than a slogan.

Run: ``python3 -m pytest tests/test_game_theory_sim.py -q``.
"""

from __future__ import annotations

import math
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments import game_theory_sim as G  # noqa: E402


class TestAccounting(unittest.TestCase):
    def test_energy_is_a_stock_with_sources_and_a_burn(self) -> None:
        run = G.simulate(G.TOLL)
        expected = G.E0 + sum(run.inflow) - G.BURN * G.MONTHS
        self.assertAlmostEqual(run.E[G.MONTHS], expected, places=9)

    def test_potential_energy_is_the_product(self) -> None:
        run = G.simulate(G.HYBRID)
        for e, d, psi in zip(run.E, run.D, run.psi):
            self.assertAlmostEqual(psi, e * d, places=12)

    def test_prestige_alone_cannot_pass_the_attention_ceiling(self) -> None:
        # academic reach is real and so is its energy; it is the product with
        # dependency that stays near zero, and this pins both halves.
        run = G.simulate(G.ACADEMIC)
        self.assertGreater(run.E[G.MONTHS], 1.0)
        self.assertLessEqual(run.E[G.MONTHS], G.ATTN_CAP + 1e-9)

    def test_states_stay_inside_the_model(self) -> None:
        for p in G.STRATEGIES:
            run = G.simulate(p)
            for series in (run.E, run.M, run.D, run.psi):
                for value in series:
                    self.assertTrue(math.isfinite(value))
                    self.assertGreaterEqual(value, -1e-12)
                    self.assertLessEqual(value, G.E_CAP + 1.0)


class TestMonotonicity(unittest.TestCase):
    def test_copying_more_never_grows_the_moat(self) -> None:
        weak = G.simulate(replace(G.TOLL, copy_ease=0.05))
        strong = G.simulate(replace(G.TOLL, copy_ease=0.60))
        self.assertLessEqual(strong.M[G.MONTHS], weak.M[G.MONTHS] + 1e-12)

    def test_more_convertible_adoption_never_lowers_dependency(self) -> None:
        lo = G.simulate(replace(G.TOLL, dep_convert=0.10))
        hi = G.simulate(replace(G.TOLL, dep_convert=0.90))
        self.assertGreaterEqual(hi.D[G.MONTHS], lo.D[G.MONTHS] - 1e-12)

    def test_parity_time_is_undefined_once_the_moat_holds(self) -> None:
        run = G.simulate(replace(G.TOLL, copy_ease=0.02, dep_convert=0.90))
        parity = run.parity_months(G.MONTHS)
        self.assertTrue(parity is None or parity > 24.0)


class TestMechanism(unittest.TestCase):
    def test_deterministic(self) -> None:
        self.assertEqual(G.simulate(G.OPEN).psi, G.simulate(G.OPEN).psi)

    def test_closed_dies_of_runway_not_of_ideas(self) -> None:
        poor = G.simulate(G.CLOSED, e0=15 * G.BURN)
        rich = G.simulate(G.CLOSED, e0=48 * G.BURN)
        self.assertIsNotNone(poor.death_month)
        self.assertLess(poor.death_month, G.MONTHS)
        self.assertTrue(rich.death_month is None or rich.death_month > poor.death_month)

    def test_tollbooth_opens_dependency_before_academic(self) -> None:
        toll = G.simulate(G.TOLL)
        academic = G.simulate(G.ACADEMIC)
        self.assertIsNotNone(toll.t_entrench)
        self.assertGreater(toll.D[12], academic.D[12])

    def test_open_source_is_the_most_copyable_and_the_least_dependent(self) -> None:
        runs = {p.key: G.simulate(p) for p in G.STRATEGIES}
        self.assertEqual(max(r.copyable_share for r in runs.values()),
                         runs["open"].copyable_share)
        self.assertLess(runs["open"].M[G.MONTHS], 1e-3)


class TestFalsifiability(unittest.TestCase):
    def test_six_month_winner_moves_with_the_load_bearing_parameters(self) -> None:
        grid = G.ranking_grid((0.15, 0.45, 1.00), (2.0, 6.0, 12.0))
        winners = {cell for row in grid for cell in row}
        self.assertGreaterEqual(len(winners), 2,
                                "a model that never changes its answer is not a model")

    def test_tollbooth_wins_six_months_under_declared_parameters(self) -> None:
        psi = {p.key: G.simulate(p).at(6)[3] for p in G.STRATEGIES}
        self.assertEqual(max(psi, key=psi.get), "toll")
        self.assertLess(psi["closed"], 1e-6)

    def test_stress_on_core_copyability_reduces_success(self) -> None:
        base = G.monte_carlo(G.TOLL, 400)["p_success"]
        stressed = G.monte_carlo(replace(G.TOLL, copy_ease=0.25), 400)["p_success"]
        self.assertLessEqual(stressed, base)

    def test_stress_on_certification_conversion_reduces_success(self) -> None:
        base = G.monte_carlo(G.TOLL, 400)["p_success"]
        stressed = G.monte_carlo(replace(G.TOLL, dep_convert=0.20), 400)["p_success"]
        self.assertLessEqual(stressed, base)


class TestReport(unittest.TestCase):
    def test_report_runs_and_writes_json(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "gt.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = G.main(["--draws", "50", "--json", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text())
            self.assertEqual(set(payload["runs"]), {p.key for p in G.STRATEGIES})
            self.assertTrue(all(payload["checks"].values()))
            for entry in payload["monte_carlo"].values():
                self.assertGreaterEqual(entry["p_success"], 0.0)
                self.assertLessEqual(entry["p_success"], 1.0)


if __name__ == "__main__":
    unittest.main()
