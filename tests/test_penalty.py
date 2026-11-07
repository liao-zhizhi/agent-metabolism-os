"""Tests for the penalty ledger and its grip on the regime switch.

The interesting claim is not "a number went up". It is that **damage outranks
supply**: a system at 100% charge must stop because it has lost trust, and no
amount of trust must ever make a spent account richer. Both directions are
pinned here, because a ledger that can only add severity is a ledger, while one
that can trade trust for energy is a way to argue with the audit.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    CONSERVE,
    DEGRADED_ENERGY,
    DEGRADED_TRUST,
    EMERGENCY,
    FAST,
    GROW,
    OXPHOS,
    SLOW,
    DualBeatScheduler,
    EnergyAccount,
    PenaltyError,
    PenaltyLedger,
    RegimeSwitch,
    UnknownViolationType,
    evaluate_regime,
    state_for,
)
from agent_metabolism.penalty import (  # noqa: E402
    AMORTIZATION_RATE,
    CRITICAL,
    HEALTHY,
    PENALTY_BASE,
    WARNING,
)


# ---------------------------------------------------------------------------
# The ledger on its own
# ---------------------------------------------------------------------------


class TestPenaltyLedger(unittest.TestCase):
    def test_starts_healthy_and_fully_trusted(self):
        ledger = PenaltyLedger()
        self.assertEqual(ledger.get_state(), HEALTHY)
        self.assertEqual(ledger.total_penalty, 0.0)
        self.assertEqual(ledger.trust_score, 100.0)

    def test_penalty_is_weight_times_the_base(self):
        ledger = PenaltyLedger()
        violation = ledger.record_violation("FALSE_COMPLETION", weight=5.0)
        self.assertEqual(violation.penalty, 5.0 * PENALTY_BASE)
        self.assertEqual(ledger.total_penalty, 50.0)

    def test_known_types_have_a_default_weight(self):
        ledger = PenaltyLedger()
        violation = ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(violation.weight, 5.0)
        self.assertEqual(violation.penalty, 50.0)

    def test_an_unknown_type_must_state_its_severity(self):
        # Guessing a default is how a novel failure gets under-reported.
        ledger = PenaltyLedger()
        with self.assertRaises(UnknownViolationType):
            ledger.record_violation("SOMETHING_NEW")
        # ...and is accepted once the price is stated.
        record = ledger.record_violation("SOMETHING_NEW", weight=2.0)
        self.assertEqual(record.penalty, 20.0)

    def test_trust_falls_with_the_fine_but_not_one_for_one(self):
        ledger = PenaltyLedger()
        violation = ledger.record_violation("FALSE_COMPLETION", weight=5.0)
        self.assertEqual(violation.trust_before, 100.0)
        self.assertEqual(violation.trust_after, 75.0)  # 50 penalty * 0.5
        self.assertEqual(ledger.trust_score, 75.0)

    def test_a_severe_violation_reaches_critical_and_a_mild_one_does_not(self):
        mild = PenaltyLedger()
        mild.record_violation("STALE_SUBSTRATE")           # penalty 10
        self.assertEqual(mild.get_state(), HEALTHY)

        severe = PenaltyLedger()
        severe.record_violation("FALSE_COMPLETION")        # penalty 50
        self.assertEqual(severe.get_state(), CRITICAL)

    def test_the_warning_band_between_the_two(self):
        ledger = PenaltyLedger()
        ledger.record_violation("BUDGET_OVERRUN", weight=2.0)  # penalty 20
        self.assertEqual(ledger.get_state(), WARNING)

    def test_fines_accumulate(self):
        ledger = PenaltyLedger()
        ledger.record_violation("PROTOCOL_VIOLATION")  # 15
        ledger.record_violation("PROTOCOL_VIOLATION")  # 15
        self.assertEqual(ledger.total_penalty, 30.0)
        self.assertEqual(len(ledger.violations), 2)

    def test_trust_cannot_go_below_zero(self):
        ledger = PenaltyLedger()
        for _ in range(10):
            ledger.record_violation("FALSE_COMPLETION", weight=5.0)
        self.assertEqual(ledger.trust_score, 0.0)
        self.assertEqual(ledger.get_state(), CRITICAL)

    def test_each_violation_is_recorded_with_its_own_arc(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")   # trust 100 -> 75
        second = ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(second.seq, 2)
        self.assertEqual(second.trust_before, 75.0)
        self.assertEqual(second.trust_after, 50.0)
        self.assertEqual(second.state_after, CRITICAL)

    def test_bad_weights_are_rejected(self):
        ledger = PenaltyLedger()
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(PenaltyError):
                ledger.record_violation("FALSE_COMPLETION", weight=bad)
        with self.assertRaises(PenaltyError):
            ledger.record_violation("FALSE_COMPLETION", weight="five")  # type: ignore[arg-type]
        with self.assertRaises(PenaltyError):
            ledger.record_violation("   ")

    def test_constructor_validation(self):
        with self.assertRaises(PenaltyError):
            PenaltyLedger(penalty_base=0.0)
        with self.assertRaises(PenaltyError):
            PenaltyLedger(initial_trust=-1.0)
        with self.assertRaises(PenaltyError):
            PenaltyLedger(initial_trust=101.0)

    def test_snapshot_is_a_report_not_a_handle(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        snap = ledger.snapshot()
        # ``penalty_state``, not ``state``: the immune sidecar's ``state`` is a
        # different vocabulary, and a shared key name let a CRITICAL ledger be
        # read as a calm brake.
        self.assertEqual(snap["penalty_state"], CRITICAL)
        self.assertNotIn("state", snap)
        snap["total_penalty"] = 0.0
        self.assertEqual(ledger.total_penalty, 50.0)


class TestRecovery(unittest.TestCase):
    def test_a_success_restores_trust(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")  # trust 75
        ledger.record_success()
        self.assertEqual(ledger.trust_score, 77.0)

    def test_a_success_amortises_the_fine(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")  # 50
        ledger.record_success()
        self.assertAlmostEqual(
            ledger.total_penalty, 50.0 * (1.0 - AMORTIZATION_RATE)
        )

    def test_one_success_does_not_clear_a_false_completion(self):
        # The asymmetry is the design: a fine is a number, trust is a scar.
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        ledger.record_success()
        self.assertEqual(ledger.get_state(), CRITICAL)
        self.assertGreater(ledger.total_penalty, 40.0)

    def test_consecutive_successes_walk_the_state_back(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(ledger.get_state(), CRITICAL)

        needed = ledger.next_charges_until(WARNING)
        # This is the assertion that stops the amortisation rate from being
        # quietly loosened later: a false completion must cost a real run of
        # verified passes, not three good afternoons.
        self.assertGreaterEqual(needed, 10)

        # One short of the projection, the system is still stopped -- the
        # projection must be tight, not merely optimistic.
        for _ in range(needed - 1):
            ledger.record_success()
        self.assertEqual(ledger.get_state(), CRITICAL)

        ledger.record_success()
        self.assertEqual(ledger.get_state(), WARNING)
        self.assertEqual(ledger.trust_score, min(100.0, 75.0 + 2.0 * needed))
        self.assertLess(ledger.total_penalty, 40.0)
        self.assertGreater(ledger.total_penalty, 15.0)

    def test_enough_successes_reach_healthy_again(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        needed = ledger.next_charges_until(HEALTHY)
        self.assertGreater(needed, 20)  # a real scar, not a rounding error
        for _ in range(needed):
            ledger.record_success()
        self.assertEqual(ledger.get_state(), HEALTHY)

    def test_next_charges_refuses_a_worse_target(self):
        ledger = PenaltyLedger()
        with self.assertRaises(PenaltyError):
            ledger.next_charges_until(CRITICAL)  # already HEALTHY
        with self.assertRaises(PenaltyError):
            ledger.next_charges_until("NONSENSE")

    def test_trust_never_exceeds_the_maximum(self):
        ledger = PenaltyLedger()
        for _ in range(50):
            ledger.record_success()
        self.assertEqual(ledger.trust_score, 100.0)

    def test_success_on_a_pristine_ledger_is_a_no_op(self):
        ledger = PenaltyLedger()
        ledger.record_success()
        self.assertEqual(ledger.total_penalty, 0.0)
        self.assertEqual(ledger.get_state(), HEALTHY)
        self.assertEqual(ledger.success_count, 1)


class TestStateBoundaries(unittest.TestCase):
    def test_the_boundary_belongs_to_the_worse_side(self):
        # Opposite of L1's energy thresholds, on purpose: charge is a resource
        # being consumed, damage is a fact being recorded.
        self.assertEqual(state_for(39.99, 100.0), WARNING)
        self.assertEqual(state_for(40.0, 100.0), CRITICAL)
        self.assertEqual(state_for(14.99, 100.0), HEALTHY)
        self.assertEqual(state_for(15.0, 100.0), WARNING)

    def test_low_trust_alone_is_critical(self):
        self.assertEqual(state_for(0.0, 50.0), CRITICAL)
        self.assertEqual(state_for(0.0, 50.01), WARNING)
        self.assertEqual(state_for(0.0, 100.0), HEALTHY)

    def test_state_for_validates(self):
        with self.assertRaises(PenaltyError):
            state_for(-1.0, 100.0)
        with self.assertRaises(PenaltyError):
            state_for(0.0, float("nan"))


# ---------------------------------------------------------------------------
# The grip on L3: damage outranks supply
# ---------------------------------------------------------------------------


class TestRegimePenaltyIntegration(unittest.TestCase):
    def setUp(self):
        self.account = EnergyAccount(100.0)
        self.ledger = PenaltyLedger()
        self.switch = RegimeSwitch(self.account, penalty_ledger=self.ledger)

    def test_a_full_account_grows_while_the_ledger_is_clean(self):
        self.assertEqual(self.account.ratio, 1.0)
        self.assertEqual(self.switch.evaluate(), GROW)
        self.assertTrue(self.switch.plan().allows_new_writes)

    def test_critical_ledger_forces_emergency_with_a_full_account(self):
        # The headline claim: energy unchanged, regime collapses.
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(self.account.ratio, 1.0)
        self.assertEqual(self.switch.evaluate(), EMERGENCY)
        plan = self.switch.plan()
        self.assertFalse(plan.allows_new_writes)
        self.assertTrue(plan.interrupt_long_tasks)

    def test_warning_ledger_blocks_expansion_but_allows_work(self):
        self.ledger.record_violation("BUDGET_OVERRUN", weight=2.0)  # 20 -> WARNING
        self.assertEqual(self.switch.evaluate(), CONSERVE)
        plan = self.switch.plan()
        self.assertFalse(plan.allow_divergent_thinking)
        self.assertEqual(plan.max_concurrent_agents, 1)
        self.assertTrue(plan.allows_new_writes)

    def test_the_reason_names_the_penalty_not_the_charge(self):
        # A reason saying "charge 100%: expansion is not affordable" would be
        # a lie, and the ledger's whole value is that it explains itself.
        self.ledger.record_violation("FALSE_COMPLETION")
        plan = self.switch.plan()
        self.assertIn("penalty ledger is CRITICAL", plan.reason)
        self.assertIn("100%", plan.reason)  # charge is reported, not blamed

    def test_penalty_only_tightens_never_loosens(self):
        # A worn account with a clean ledger must not be rescued by trust.
        self.account.consume(80.0)  # 20% -> EMERGENCY on supply alone
        self.assertEqual(self.switch.evaluate(), EMERGENCY)
        for _ in range(20):
            self.ledger.record_success()  # abundant trust
        self.assertEqual(self.switch.evaluate(), EMERGENCY)

    def test_critical_does_not_soften_an_existing_emergency(self):
        self.account.consume(80.0)
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(self.switch.evaluate(), EMERGENCY)

    def test_no_ledger_keeps_the_old_behaviour(self):
        plain = RegimeSwitch(EnergyAccount(100.0))
        self.assertIsNone(plain.penalty_state)
        self.assertEqual(plain.evaluate(), GROW)
        self.assertNotIn("penalty", plain.snapshot())

    def test_the_switch_reads_the_ledger_live(self):
        # Not a copied value at construction: charging after wiring must bite.
        self.assertEqual(self.switch.evaluate(), GROW)
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(self.switch.evaluate(), EMERGENCY)
        for _ in range(60):
            self.ledger.record_success()
        self.assertEqual(self.switch.evaluate(), GROW)

    def test_snapshot_carries_the_ledger(self):
        self.ledger.record_violation("FALSE_COMPLETION")
        snap = self.switch.snapshot()
        self.assertEqual(snap["regime"], EMERGENCY)
        self.assertEqual(snap["penalty"]["penalty_state"], CRITICAL)
        self.assertEqual(snap["allows_new_writes"], False)

    def test_transition_is_detected_when_the_ledger_collapses(self):
        self.switch.evaluate()
        self.assertFalse(self.switch.transitioned())
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertTrue(self.switch.transitioned())
        self.assertEqual(self.switch.evaluate(), EMERGENCY)

    def test_constructor_validation(self):
        with self.assertRaises(TypeError):
            RegimeSwitch(self.account, penalty_ledger="not a ledger")  # type: ignore[arg-type]


class TestPureEvaluateWithPenalty(unittest.TestCase):
    def test_critical_penalty_outranks_a_full_budget(self):
        self.assertEqual(evaluate_regime(1.0, 0.0, penalty_state=CRITICAL), EMERGENCY)

    def test_warning_penalty_downgrades_grow_to_conserve(self):
        self.assertEqual(evaluate_regime(1.0, 0.0, penalty_state=WARNING), CONSERVE)

    def test_healthy_penalty_changes_nothing(self):
        self.assertEqual(evaluate_regime(1.0, 0.0, penalty_state=HEALTHY), GROW)
        self.assertEqual(evaluate_regime(1.0, 0.0), GROW)

    def test_a_penalty_cannot_upgrade_an_emergency(self):
        # No amount of trust buys charge back.
        self.assertEqual(evaluate_regime(0.1, 0.0, penalty_state=HEALTHY), EMERGENCY)

    def test_bad_penalty_state_is_rejected(self):
        with self.assertRaises(Exception):
            evaluate_regime(1.0, 0.0, penalty_state="TERRIBLE")


# ---------------------------------------------------------------------------
# End to end: audit -> charge -> stop
# ---------------------------------------------------------------------------


class TestPainCycle(unittest.TestCase):
    def test_one_false_completion_stops_the_next_day(self):
        account = EnergyAccount(100.0)
        ledger = PenaltyLedger()
        switch = RegimeSwitch(account, penalty_ledger=ledger)

        # Night: the audit says FAIL, the harness charges the ledger.
        self.assertEqual(switch.evaluate(), GROW)
        ledger.record_violation("FALSE_COMPLETION", weight=5.0)

        # Morning: nothing about the charge changed, everything about the
        # regime did.
        self.assertEqual(account.ratio, 1.0)
        plan = switch.plan()
        self.assertEqual(plan.regime, EMERGENCY)
        self.assertFalse(plan.allows_new_writes)
        self.assertFalse(plan.allows(1) > 1)

        # A day of clean passes is not enough to reopen writes.
        for _ in range(5):
            ledger.record_success()
        self.assertEqual(switch.evaluate(), EMERGENCY)

        # Enough of them are.
        for _ in range(ledger.next_charges_until(WARNING)):
            ledger.record_success()
        self.assertIn(switch.plan().regime, (CONSERVE, GROW))


# ---------------------------------------------------------------------------
# L2: the ledger takes the slow beat away
# ---------------------------------------------------------------------------


class TestSlowThinkingIsWithheld(unittest.TestCase):
    """A bankrupt ledger costs the scheduler its slow beat, at any charge."""

    def setUp(self):
        self.account = EnergyAccount(100.0)
        self.ledger = PenaltyLedger()
        self.scheduler = DualBeatScheduler(
            self.account, base_cost=7.0, penalty_ledger=self.ledger
        )

    def test_a_clean_ledger_leaves_the_beat_untouched(self):
        response = self.scheduler.execute("design the membrane", 0.95)
        self.assertEqual(response.mode, SLOW)
        self.assertFalse(response.degraded)
        self.assertIsNone(response.degraded_reason)

    def test_critical_ledger_forbids_the_slow_beat_despite_full_energy(self):
        # The headline: 100% charge, a task that has always earned SLP, and
        # the fast beat anyway.
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(self.account.ratio, 1.0)
        self.assertEqual(self.account.state, GROW)
        response = self.scheduler.execute("design the membrane", 0.95)
        self.assertEqual(response.mode, FAST)
        self.assertEqual(response.beat, OXPHOS)
        self.assertTrue(response.degraded)
        self.assertTrue(response.trust_blocked)
        self.assertFalse(response.energy_blocked)

    def test_being_distrusted_is_cheaper_than_thinking(self):
        # The fast beat still costs what it costs -- trust removes the option,
        # not the price.
        self.ledger.record_violation("FALSE_COMPLETION")
        response = self.scheduler.execute("design the membrane", 0.95)
        self.assertEqual(response.energy_cost, 7.0)
        self.assertAlmostEqual(self.account.current_energy, 93.0)

    def test_the_reason_distinguishes_trust_from_poverty(self):
        # Two different causes, two different labels: the operator fixes
        # different things.
        self.ledger.record_violation("FALSE_COMPLETION")
        broke = self.scheduler.execute("hard", 0.95)
        self.assertEqual(broke.degraded_reason, DEGRADED_TRUST)

        poor_account = EnergyAccount(100.0)
        poor_account.consume(70.0)  # 30.0 left, slow costs 35.0
        poor = DualBeatScheduler(poor_account, base_cost=7.0).execute("hard", 0.95)
        self.assertEqual(poor.degraded_reason, DEGRADED_ENERGY)
        self.assertNotEqual(broke.degraded_reason, poor.degraded_reason)

    def test_when_both_apply_the_charge_is_not_blamed(self):
        # 30.0 left *and* distrusted. Reporting "could not afford it" would be
        # true and useless: the account is refillable, the trust is the story.
        self.ledger.record_violation("FALSE_COMPLETION")
        self.account.consume(70.0)
        response = self.scheduler.execute("hard", 0.95)
        self.assertEqual(response.degraded_reason, DEGRADED_TRUST)
        # And the charge really was insufficient, so the label is a choice.
        self.assertLess(self.account.current_energy, 35.0)

    def test_warning_is_not_enough_to_take_the_beat_away(self):
        # A caution is not a ban. The two thresholds keep their meaning at
        # this layer too, or "WARNING" would just be "CRITICAL" with a nicer
        # name.
        self.ledger.record_violation("BUDGET_OVERRUN", weight=2.0)  # 20 -> WARNING
        response = self.scheduler.execute("hard", 0.95)
        self.assertEqual(response.mode, SLOW)
        self.assertFalse(response.degraded)

    def test_simple_tasks_are_unaffected(self):
        self.ledger.record_violation("FALSE_COMPLETION")
        response = self.scheduler.execute("rename a variable", 0.2)
        self.assertEqual(response.mode, FAST)
        self.assertFalse(response.degraded)   # never wanted slow
        self.assertIsNone(response.degraded_reason)

    def test_a_conserving_account_is_still_labelled_energy_not_trust(self):
        # Regression guard: the pre-existing path must keep its old meaning.
        self.account.consume(80.0)  # CONSERVE
        response = self.scheduler.execute("hard", 1.0)
        self.assertEqual(response.mode, FAST)
        self.assertFalse(response.degraded)
        self.assertIsNone(response.degraded_reason)

    def test_recovery_gives_the_slow_beat_back(self):
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertFalse(self.scheduler.slow_thinking_allowed)

        needed = self.ledger.next_charges_until(WARNING)
        for _ in range(needed):
            self.ledger.record_success()
        self.assertTrue(self.scheduler.slow_thinking_allowed)

        response = self.scheduler.execute("hard", 0.95)
        self.assertEqual(response.mode, SLOW)
        self.assertFalse(response.degraded)

    def test_no_ledger_keeps_the_old_behaviour(self):
        plain = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0)
        self.assertIsNone(plain.penalty_state)
        self.assertTrue(plain.slow_thinking_allowed)
        self.assertEqual(plain.execute("hard", 0.95).mode, SLOW)

    def test_the_scheduler_reads_the_ledger_live(self):
        self.assertEqual(self.scheduler.execute("hard", 0.95).mode, SLOW)
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(self.scheduler.execute("hard", 0.95).mode, FAST)

    def test_constructor_validates_the_ledger(self):
        with self.assertRaises(TypeError):
            DualBeatScheduler(
                EnergyAccount(100.0), penalty_ledger="nope"  # type: ignore[arg-type]
            )

    def test_repr_reports_the_penalty_state(self):
        self.ledger.record_violation("FALSE_COMPLETION")
        self.assertIn("penalty=CRITICAL", repr(self.scheduler))


class TestSentencesTheSystemCannotAfford(unittest.TestCase):
    """L2 + L3 together: no concurrency *and* no depth, at full charge."""

    def test_a_bankrupt_ledger_takes_both_at_once(self):
        account = EnergyAccount(100.0)
        ledger = PenaltyLedger()
        switch = RegimeSwitch(account, penalty_ledger=ledger)
        scheduler = DualBeatScheduler(account, base_cost=7.0, penalty_ledger=ledger)

        plan = switch.plan()
        self.assertEqual(plan.regime, GROW)
        self.assertEqual(plan.allows(5), 5)
        self.assertEqual(scheduler.execute("hard", 0.95).mode, SLOW)

        charge_before = account.current_energy
        ledger.record_violation("FALSE_COMPLETION")

        plan = switch.plan()
        self.assertEqual(plan.regime, EMERGENCY)
        self.assertEqual(plan.allows(5), 1)
        self.assertFalse(plan.allows_new_writes)
        # Not one joule moved when the penalty landed, and the beat still went.
        self.assertEqual(account.current_energy, charge_before)
        self.assertEqual(account.state, GROW)
        self.assertEqual(scheduler.execute("hard", 0.95).mode, FAST)


class FalsificationTests(unittest.TestCase):
    """The a posteriori charge, and the asymmetry it is supposed to realize.

    The claim under test (R2): a false success claim is cheap at the moment it
    is made and terminal once it is refuted. If the upslope were merely steep
    rather than closed, the cost of lying would be a function of how long the
    liar keeps working afterwards -- which rewards exactly the persistence a
    ledger should punish.
    """

    def test_refutation_zeroes_trust_regardless_of_prior_standing(self) -> None:
        fresh = PenaltyLedger()
        seasoned = PenaltyLedger()
        for _ in range(3):
            seasoned.record_success()

        for ledger in (fresh, seasoned):
            with self.subTest(trust_before=ledger.trust_score):
                ledger.record_falsification("file_not_found")
                self.assertEqual(ledger.trust_score, 0.0)
                self.assertEqual(ledger.penalty_state, "CRITICAL")
                # Not pro-rated by what was already owed: proof of a false
                # record cannot be cheaper for an agent that was in good
                # standing, which is precisely who gets the benefit of doubt.
                self.assertGreaterEqual(ledger.total_penalty, 40.0)

    def test_a_refuted_book_never_rebuilds_trust(self) -> None:
        ledger = PenaltyLedger()
        ledger.record_falsification("hash_mismatch")
        for _ in range(200):
            ledger.record_success()
        self.assertEqual(ledger.trust_score, 0.0, "200 clean runs must buy nothing")
        self.assertEqual(ledger.penalty_state, "CRITICAL")
        self.assertTrue(ledger.is_falsified)

    def test_recovery_projection_refuses_to_promise_a_way_back(self) -> None:
        """A finite estimate here would be a false comfort: say so instead."""
        ledger = PenaltyLedger()
        ledger.record_falsification("file_not_found")
        with self.assertRaises(Exception) as caught:
            ledger.next_charges_until("HEALTHY")
        self.assertIn("unreachable", str(caught.exception))

    def test_the_latch_is_one_way(self) -> None:
        ledger = PenaltyLedger()
        self.assertFalse(ledger.is_falsified)
        ledger.record_falsification("file_not_found")
        self.assertTrue(ledger.is_falsified)
        ledger.record_success()
        self.assertTrue(ledger.is_falsified, "recovery must not clear the finding")
        self.assertEqual(ledger.falsifications, 1)

    def test_refutation_is_the_heaviest_charge_in_the_table(self) -> None:
        from agent_metabolism.penalty import DEFAULT_WEIGHTS

        heaviest = max(DEFAULT_WEIGHTS.items(), key=lambda kv: kv[1])
        self.assertEqual(heaviest[0], "FALSIFIED_COMMITMENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
