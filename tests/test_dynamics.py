"""Tests for D1--D6, the dynamic control laws.

Each test pins the *shape* of a law rather than a number: that trust and witness
combine by ``min`` and not by sum (D2), that dormancy and death are different
answers to different questions (D3), that fitness multiplies in provenance so
speed cannot buy it off (D4), that clones collapse to one witness (D5), and that
spillover is bounded exactly up to ``R0 = 1`` (D6).

The numbers are policy and would move in a real deployment. The shapes are the
claim, so they are what is tested. Where a claim can be checked against the
shipped ledger (``PenaltyLedger``) rather than against a second copy of the same
arithmetic, it is checked there instead -- two spellings of one rule that can
drift apart is the bug the package exists to prevent.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism.dynamics import (  # noqa: E402
    ACTIVE,
    COLLATERAL_PER_WITNESS,
    DEAD,
    DORMANT,
    AdoptionGate,
    DormancyController,
    DynamicsError,
    Ecosystem,
    GenomeAudit,
    State,
    Trait,
    adoption_horizon,
    adoption_is_decidable,
    audit_policy,
    classify,
    derivative,
    effective_witnesses,
    external_witness_fraction,
    lineage_correlation,
    liquidate,
    min_exit_ratio,
    net_assets,
    optimal_sample_convex,
    removal_for_containment,
    reproduction_number,
    required_independence,
    solvency_margin,
    spillover,
)
from agent_metabolism.penalty import MAX_TRUST, PenaltyLedger  # noqa: E402


class SolvencyIsNotAddition(unittest.TestCase):
    """D2: trust and witness are complements, so the book takes the smaller leg.

    If this test were written against a balance sheet it would assert
    ``net == trust + witness``. It asserts the opposite, and the difference is
    the whole system: an agent at maximum trust with no witness is not worth
    maximum trust plus nothing, it is worth whatever the witness leg allows --
    zero.
    """

    def test_trust_without_witness_backs_nothing(self) -> None:
        self.assertEqual(net_assets(MAX_TRUST, 0.0), 0.0)

    def test_the_witness_leg_caps_the_trust_leg(self) -> None:
        # Half a witness at parity collateralises half the reputation, no matter
        # how large the reputation is.
        for trust in (10.0, 50.0, MAX_TRUST):
            with self.subTest(trust=trust):
                self.assertEqual(
                    net_assets(trust, 0.5), min(trust, COLLATERAL_PER_WITNESS * 0.5)
                )

    def test_net_assets_never_exceeds_the_witness_leg(self) -> None:
        for trust in (0.0, 25.0, 100.0, 1000.0):
            for witness in (0.0, 0.1, 1.0, 5.0):
                with self.subTest(trust=trust, witness=witness):
                    self.assertLessEqual(
                        net_assets(trust, witness),
                        COLLATERAL_PER_WITNESS * witness + 1e-9,
                    )

    def test_liability_is_subtracted_after_the_min(self) -> None:
        # Two unwitnessed claims make a full-trust book insolvent. This is the
        # false-completion state, and a summed model would show +98.
        self.assertLess(net_assets(MAX_TRUST, 0.0, 2.0), 0.0)

    def test_solvency_margin_is_scale_free(self) -> None:
        self.assertAlmostEqual(solvency_margin(100.0, 1.0), 1.0)
        self.assertAlmostEqual(solvency_margin(100.0, 0.5), 0.5)
        self.assertEqual(solvency_margin(0.0, 0.0), 0.0)

    def test_required_independence_refuses_an_impossible_book(self) -> None:
        self.assertEqual(required_independence(100.0), 0.0)
        self.assertAlmostEqual(required_independence(100.0, 20.0), 0.2)
        with self.assertRaises(DynamicsError):
            # Trust below the liability: no witness mass can fix it, and a
            # finite number here would be a promise the min() cannot keep.
            required_independence(1.0, 20.0)


class TheFourFlows(unittest.TestCase):
    """D1: the paper's recurrences written as rates, and R2 preserved."""

    def test_a_violation_lowers_trust_at_the_ledgers_rate(self) -> None:
        flow = derivative(State(), success_rate=0.0, violation_rate=1.0, severity=1.0)
        # One unit of severity is a fine of PENALTY_BASE (10), and trust falls by
        # half the fine. Written as a rate, that is -5 per cycle, not -50: the
        # factor of ten belongs to the base and would be counted twice if the
        # recurrence were copied rather than derived.
        self.assertLess(flow["trust"], 0.0)
        self.assertAlmostEqual(flow["trust"], -5.0)

    def test_a_verified_pass_mints_a_witness_and_buys_trust(self) -> None:
        flow = derivative(State(witness=0.0), success_rate=1.0, violation_rate=0.0)
        self.assertAlmostEqual(flow["trust"], 2.0)
        self.assertAlmostEqual(flow["witness"], 1.0)
        self.assertGreater(flow["net"], 0.0)

    def test_a_refuted_book_cannot_flow_upward(self) -> None:
        # R2 in rate form: however many passes arrive, trust does not climb.
        flow = derivative(
            State(trust=0.0, falsified=True), success_rate=100.0, violation_rate=0.0
        )
        self.assertLessEqual(flow["trust"], 0.0)

    def test_the_rate_form_agrees_with_the_shipped_ledger(self) -> None:
        # Not a second spelling: the same event, booked twice, must agree in
        # sign and in magnitude per cycle.
        ledger = PenaltyLedger()
        before = ledger.trust_score
        ledger.record_violation("FALSE_COMPLETION")  # weight 5
        drop = before - ledger.trust_score
        flow = derivative(State(), success_rate=0.0, violation_rate=1.0, severity=5.0)
        self.assertAlmostEqual(-flow["trust"], drop)

    def test_falsification_is_terminal_in_both_spellings(self) -> None:
        ledger = PenaltyLedger()
        ledger.record_falsification("hash_mismatch")
        self.assertEqual(ledger.trust_score, 0.0)
        self.assertTrue(ledger.is_falsified)
        self.assertEqual(
            classify(80.0, ledger.trust_score, falsified=ledger.is_falsified), DEAD
        )


class DormancyIsNotDeath(unittest.TestCase):
    """D3: the two answers differ by whether an exit edge exists."""

    def test_a_poor_book_is_dormant_and_a_cut_off_one_is_dead(self) -> None:
        self.assertEqual(classify(50.0, 100.0), ACTIVE)
        self.assertEqual(classify(3.0, 100.0), DORMANT)
        # Same charge, no substrate: waiting cannot help, so it is not dormancy.
        self.assertEqual(classify(3.0, 100.0, recovery_rate=0.0), DEAD)

    def test_a_refuted_book_is_dead_while_rich(self) -> None:
        # A liar's advantage is that its account is usually full. R2 makes the
        # fullness irrelevant, which is why falsification is checked first.
        self.assertEqual(classify(100.0, 0.0, falsified=True), DEAD)

    def test_the_band_must_fund_one_cycle(self) -> None:
        # The design law: a band narrower than a cycle manufactures a wake it
        # cannot pay for.
        with self.assertRaises(DynamicsError):
            DormancyController(enter_ratio=0.10, exit_ratio=0.12, cycle_cost=7.0)

    def test_the_shipped_band_clears_a_fast_beat(self) -> None:
        controller = DormancyController()  # 0.10 / 0.30, cost 7 of 100
        self.assertLessEqual(min_exit_ratio(0.10, 7.0), controller.exit_ratio)

    def test_the_shipped_band_does_not_clear_a_slow_beat(self) -> None:
        # A finding, not a failure: SLOW_ENERGY_MULTIPLIER is 5, so the first
        # beat after waking cannot be slow at exit=0.30.
        self.assertGreater(min_exit_ratio(0.10, 35.0), DormancyController().exit_ratio)

    def test_equal_thresholds_are_refused(self) -> None:
        with self.assertRaises(DynamicsError):
            DormancyController(enter_ratio=0.20, exit_ratio=0.20)

    def test_hysteresis_chatters_less_than_a_bare_comparator(self) -> None:
        """The measured claim: a band buys fewer transitions at the same work."""
        budget, cost, recovery = 100.0, 7.0, 0.05
        controller = DormancyController(
            enter_ratio=0.10, exit_ratio=0.30, budget=budget, cycle_cost=cost,
            recovery_rate=recovery,
        )
        banded = controller.simulate(9.0, 200)

        # The naive policy: sleep below 0.10, wake the moment you are above it.
        energy, dormant, flips = 9.0, False, 0
        for _ in range(200):
            ratio = energy / budget
            if dormant and ratio >= 0.10:
                dormant, flips = False, flips + 1
            elif not dormant and ratio < 0.10:
                dormant, flips = True, flips + 1
            energy = (
                min(budget, energy + recovery * (budget - energy))
                if dormant
                else max(0.0, energy - cost)
            )

        self.assertGreater(flips, banded["transitions"])
        self.assertEqual(banded["transitions"] % 1.0, 0.0, "transitions are whole")

    def test_liquidation_keeps_the_evidence_and_writes_off_the_debt(self) -> None:
        state = State(trust=0.0, witness=2.0, hallucination=5.0, falsified=True)
        result = liquidate(state, cause="refuted commitment")
        # Witnesses were external checks; their value does not depend on the
        # agent that held them, so the ecosystem keeps them.
        self.assertEqual(result.witness_survives, 2.0)
        self.assertEqual(result.liability_written_off, 5.0)
        self.assertFalse(result.restitutable)

    def test_a_death_needs_a_cause(self) -> None:
        with self.assertRaises(DynamicsError):
            liquidate(State(), cause="  ")

    def test_an_honest_death_can_restitute(self) -> None:
        self.assertTrue(liquidate(State(trust=0.0, hallucination=1.0)).restitutable)


class FitnessIsAProduct(unittest.TestCase):
    """D4: provenance multiplies, so speed cannot compensate for a forged chain."""

    def test_a_fast_forged_cache_has_zero_fitness(self) -> None:
        audit = GenomeAudit(min_external_fraction=0.5)
        forged = [Trait("cache%d" % i, True, False) for i in range(20)]
        # The speed-up is real and large; the validity gate is zero, and the
        # product is zero. An additive model would have paid 100 - penalty.
        self.assertGreater(audit.gain(forged), 50.0)
        self.assertEqual(audit.witness_gate(forged), 0.0)
        self.assertEqual(audit.fitness(forged), 0.0)

    def test_one_invalid_witness_zeroes_the_genome(self) -> None:
        audit = GenomeAudit()
        traits = [Trait("ok%d" % i, True, True) for i in range(9)]
        traits.append(Trait("bad", False, True))
        self.assertEqual(audit.fitness(traits), 0.0)

    def test_an_earned_genome_scores_its_gain(self) -> None:
        audit = GenomeAudit()
        traits = [Trait("rung%d" % i, True, True) for i in range(4)]
        self.assertAlmostEqual(audit.fitness(traits), 100.0)

    def test_external_witness_fraction_is_the_forgery_defence(self) -> None:
        traits = [Trait("a", True, True), Trait("b", True, False)]
        self.assertAlmostEqual(external_witness_fraction(traits), 0.5)
        self.assertEqual(external_witness_fraction([]), 0.0)

    def test_replay_cannot_cost_more_than_verification(self) -> None:
        with self.assertRaises(DynamicsError):
            Trait("impossible", True, True, verify_cost=1.0, replay_cost=2.0)


class AdoptionHasAHysteresisBand(unittest.TestCase):
    """D4 continued: one lucky generation is not evidence."""

    def test_a_single_spike_is_not_adopted(self) -> None:
        gate = AdoptionGate(margin=0.10, horizon=3)
        self.assertEqual(gate.observe(10.0), "PROBATION")
        self.assertEqual(gate.observe(1.0), "PROBATION")

    def test_a_sustained_advantage_is_adopted(self) -> None:
        gate = AdoptionGate(margin=0.10, horizon=3)
        verdicts = [gate.observe(2.0) for _ in range(3)]
        self.assertEqual(verdicts, ["PROBATION", "PROBATION", "ADOPT"])

    def test_a_margin_below_noise_never_adopts(self) -> None:
        gate = AdoptionGate(margin=0.10, horizon=1, incumbent=1.0)
        self.assertEqual(gate.observe(1.05), "PROBATION")

    def test_a_refutation_latches_the_rejection(self) -> None:
        gate = AdoptionGate(margin=0.0, horizon=1)
        gate.observe(5.0)
        gate.refute()
        # Latched, not scored: it must not accumulate its way back in.
        for _ in range(100):
            self.assertEqual(gate.observe(1000.0), "REJECT")

    def test_the_horizon_is_the_refutation_latency(self) -> None:
        self.assertEqual(adoption_horizon(90.0, 30.0), 3)
        self.assertEqual(adoption_horizon(0.0, 30.0), 0)
        self.assertTrue(adoption_is_decidable(90.0))
        self.assertFalse(adoption_is_decidable(float("inf")))

    def test_sampling_is_not_a_middle_ground(self) -> None:
        # Linear audit cost has no interior optimum: 0 or 1, which makes
        # "we spot-check" a way of silently choosing 0.
        cheap = audit_policy(consequence=1.0, base_rate=0.5, sample_cost=1.0)
        fatal = audit_policy(consequence=1000.0, base_rate=0.5, sample_cost=1.0)
        self.assertEqual(cheap["p"], 0.0)
        self.assertEqual(fatal["p"], 1.0)
        self.assertFalse(cheap["rational"])
        self.assertTrue(fatal["rational"])

    def test_a_middle_ground_needs_bounded_consequence(self) -> None:
        bounded = optimal_sample_convex(1000.0, 0.5, 1.0)
        catastrophic = optimal_sample_convex(1.0, 0.5, 1000.0)
        self.assertGreater(bounded, 0.0)
        self.assertLess(bounded, 1.0)
        self.assertEqual(catastrophic, 1.0)


class AQuorumOfClonesIsOneWitness(unittest.TestCase):
    """D5: independence is the asset, and correlation destroys it."""

    def test_independent_witnesses_are_worth_their_count(self) -> None:
        self.assertAlmostEqual(effective_witnesses(6, 0.0), 6.0)

    def test_clones_collapse_to_one_record_regardless_of_headcount(self) -> None:
        for n in (2, 6, 100):
            with self.subTest(n=n):
                self.assertAlmostEqual(effective_witnesses(n, 1.0), 1.0)

    def test_the_effective_count_is_monotone_in_correlation(self) -> None:
        values = [effective_witnesses(6, r) for r in (0.0, 0.25, 0.5, 0.9)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_lineage_overlap_is_the_rho_proxy(self) -> None:
        self.assertEqual(lineage_correlation(10, 10), 1.0)
        self.assertEqual(lineage_correlation(0, 10), 0.0)

    def test_a_cloned_quorum_overstates_its_solvency_by_its_size(self) -> None:
        eco = Ecosystem()
        names = ["clone%d" % i for i in range(6)]
        for name in names:
            eco.add(name, State())
            for other in names:
                if other != name:
                    eco.lineage[(name, other)] = 1.0
        report = eco.collusion_report(names)
        self.assertEqual(report["rho"], 1.0)
        self.assertAlmostEqual(report["effective"], 1.0)
        self.assertAlmostEqual(report["overstatement"], 6.0)

    def test_unknown_relations_are_treated_as_collusion(self) -> None:
        # Conservative by design: assuming independence is what turns clones
        # into a quorum.
        eco = Ecosystem()
        for i in range(4):
            eco.add("a%d" % i, State())
        self.assertAlmostEqual(eco.quorum_witness(["a0", "a1", "a2", "a3"]), 1.0)


class SpilloverIsBoundedUpToCriticality(unittest.TestCase):
    """D6: one inflamed unit poisons the pool iff R0 >= 1."""

    def test_subcritical_spillover_is_finite(self) -> None:
        self.assertAlmostEqual(spillover(10.0, 0.5), 20.0)
        self.assertAlmostEqual(spillover(10.0, 0.0), 10.0)

    def test_critical_and_supercritical_spillover_diverges(self) -> None:
        self.assertEqual(spillover(10.0, 1.0), float("inf"))
        self.assertEqual(spillover(10.0, 1.5), float("inf"))

    def test_the_reproduction_number_is_the_ratio_of_gain_to_removal(self) -> None:
        self.assertAlmostEqual(reproduction_number(0.1, 3.0, 1.0), 0.3)
        self.assertGreater(reproduction_number(0.5, 3.0, 1.0), 1.0)

    def test_containment_is_required_exactly_above_threshold(self) -> None:
        from agent_metabolism.dynamics import containment_required

        self.assertFalse(containment_required(0.99))
        self.assertTrue(containment_required(1.0))

    def test_removal_can_be_computed_rather_than_advised(self) -> None:
        gamma = removal_for_containment(0.5, 3.0, target=0.9)
        self.assertAlmostEqual(reproduction_number(0.5, 3.0, gamma), 0.9)
        with self.assertRaises(DynamicsError):
            removal_for_containment(0.5, 3.0, target=1.0)


class TheLawsAgreeWithTheLayersBelow(unittest.TestCase):
    """The constants are imported, not restated, so the laws cannot drift."""

    def test_collateral_and_trust_share_a_scale(self) -> None:
        self.assertEqual(COLLATERAL_PER_WITNESS, MAX_TRUST)

    def test_a_full_ledger_sits_on_the_solvency_boundary(self) -> None:
        ledger = PenaltyLedger()
        state = State(trust=ledger.trust_score, witness=1.0)
        self.assertAlmostEqual(state.net(), MAX_TRUST)

    def test_dynamics_is_importable_without_the_package_init(self) -> None:
        # Guards the direction of the dependency: dynamics reads the layers, and
        # nothing in the layers reads dynamics.
        import agent_metabolism.dynamics as dynamics

        self.assertFalse(hasattr(dynamics, "penalty_ledger"))
        self.assertTrue(math.isfinite(dynamics.LYSIS_THRESHOLD))


if __name__ == "__main__":
    unittest.main()
