"""The first beat after waking must be fast (D3.2, enforced).

The law being pinned: a hysteresis band narrower than one cycle manufactures a
wake it cannot pay for. The controller therefore has to be told, by the account
itself, that it has just woken -- and the scheduler has to refuse the expensive
beat on that one cycle.

Why these tests exist in this shape
-----------------------------------
*The fact* lives on ``EnergyAccount``, because it is the only object that sees
every ``restore`` and every ``consume``. *The policy* lives on
``DualBeatScheduler``. Neither can be tested by reading the other, so the account
tests check the transition and the scheduler tests check the refusal.

The interesting case is not the one where the charge is too low to afford the
slow beat -- price already refuses that, by accident. It is the case where the
slow beat is *affordable* and still must not run, because running it would spend
the reserve that keeps the account awake. At the shipped numbers: wake at 40%,
slow costs 35, and 40 - 35 = 5 is below the 10% sleep floor. The account would buy
one expensive answer and drown, which is the chatter the band exists to prevent.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    DEGRADED_WAKE,
    EXHAUSTED,
    FAST,
    SLOW,
    DualBeatScheduler,
    EnergyAccount,
    EnergyError,
    EnergyExhausted,
    WakeWindowError,
)
from agent_metabolism.llm_proxy import LLMProxy  # noqa: E402


def woken_account(restore_to: float = 40.0) -> EnergyAccount:
    """An account that went to sleep and has just been woken to ``restore_to``."""
    account = EnergyAccount(100.0)
    account.consume(account.current_energy - 3.0)   # down to 3%, which is EXHAUSTED
    assert account.state == EXHAUSTED
    account.restore(restore_to - account.current_energy)
    assert account.state != EXHAUSTED
    return account


class TheAccountRemembersTheTransition(unittest.TestCase):
    """A level cannot say which side of the band you came from; the epoch can."""

    def test_a_fresh_account_is_not_awaiting_a_first_beat(self) -> None:
        # A cold start has never been dormant and must not pay for a dormancy it
        # did not have.
        account = EnergyAccount(100.0)
        self.assertFalse(account.awaiting_first_beat)
        self.assertEqual(account.wake_epoch, 0)

    def test_a_wake_is_recorded_once(self) -> None:
        account = woken_account(40.0)
        self.assertTrue(account.awaiting_first_beat)
        self.assertEqual(account.wake_epoch, 1)

    def test_a_restore_that_stays_exhausted_is_not_a_wake(self) -> None:
        account = EnergyAccount(100.0)
        account.consume(95.0)
        self.assertEqual(account.state, EXHAUSTED)
        account.restore(1.0)                      # 6%, still EXHAUSTED
        self.assertEqual(account.state, EXHAUSTED)
        self.assertFalse(account.awaiting_first_beat)
        self.assertEqual(account.wake_epoch, 0)

    def test_the_first_paid_beat_clears_the_window(self) -> None:
        account = woken_account(40.0)
        account.consume(7.0)
        self.assertFalse(account.awaiting_first_beat)
        # The epoch does not reset: "woke once, now working" is a different
        # history from "never woke", and the count is what keeps them apart.
        self.assertEqual(account.wake_epoch, 1)

    def test_a_zero_cost_spend_is_not_a_beat(self) -> None:
        # consume(0.0) writes no ledger entry, so it cannot buy the wake off.
        account = woken_account(40.0)
        account.consume(0.0)
        self.assertTrue(account.awaiting_first_beat)

    def test_a_refused_spend_leaves_the_window_open(self) -> None:
        account = woken_account(40.0)
        with self.assertRaises(EnergyError):
            account.consume(1000.0)
        self.assertTrue(account.awaiting_first_beat, "nothing was paid for")

    def test_the_wake_epoch_counts_each_transition(self) -> None:
        account = woken_account(40.0)
        account.consume(39.0)                     # 1% -> EXHAUSTED again
        self.assertEqual(account.state, EXHAUSTED)
        account.restore(50.0)
        self.assertEqual(account.wake_epoch, 2)

    def test_the_reserve_is_the_sleep_floor(self) -> None:
        account = EnergyAccount(100.0, exhausted_threshold=0.10)
        self.assertAlmostEqual(account.wake_reserve, 10.0)

    def test_the_band_predicate_is_about_the_aftermath(self) -> None:
        account = woken_account(17.0)
        # 17 - 7 = 10, exactly on the floor: still awake, so the beat is legal.
        self.assertTrue(account.first_beat_clears_band(7.0))
        # 17 - 8 = 9: back below the floor, so it is not.
        self.assertFalse(account.first_beat_clears_band(8.0))


class TheSchedulerForcesFastAfterWaking(unittest.TestCase):
    """The enforcement: a demanded slow beat becomes fast, and says why."""

    def test_a_hard_task_after_waking_runs_fast(self) -> None:
        account = woken_account(40.0)
        scheduler = DualBeatScheduler(account)
        # complexity 1.0 wants the slow beat, and 35 of 40 is affordable. The
        # refusal is therefore a *policy*, not a price: without it the account
        # would spend 35 and land on 5, below the 10% floor.
        response = scheduler.execute("prove the invariant", 1.0)
        self.assertEqual(response.mode, FAST)
        self.assertTrue(response.degraded)
        self.assertEqual(response.degraded_reason, DEGRADED_WAKE)
        self.assertNotEqual(account.state, EXHAUSTED)

    def test_the_expensive_beat_would_have_drowned_the_account(self) -> None:
        """The counterfactual, computed rather than asserted: 40 - 35 = 5 < 10."""
        account = woken_account(40.0)
        self.assertGreaterEqual(account.current_energy, DualBeatScheduler(account).slow_cost)
        self.assertLess(
            account.current_energy - DualBeatScheduler(account).slow_cost,
            account.wake_reserve,
        )

    def test_the_next_beat_may_run_slow(self) -> None:
        account = woken_account(80.0)
        scheduler = DualBeatScheduler(account)
        first = scheduler.execute("prove the invariant", 1.0)
        self.assertEqual(first.degraded_reason, DEGRADED_WAKE)
        second = scheduler.execute("prove the invariant", 1.0)
        self.assertEqual(second.mode, SLOW)
        self.assertFalse(second.degraded)

    def test_an_unwoken_account_keeps_its_slow_beat(self) -> None:
        # The guard must not tax ordinary operation: a rich, never-dormant
        # account still thinks slowly on a hard task.
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        response = scheduler.execute("prove the invariant", 1.0)
        self.assertEqual(response.mode, SLOW)
        self.assertFalse(response.degraded)
        self.assertIsNone(response.degraded_reason)

    def test_an_easy_task_after_waking_is_not_labelled_degraded(self) -> None:
        # Nothing was withheld, so nothing is claimed to have been withheld.
        scheduler = DualBeatScheduler(woken_account(40.0))
        response = scheduler.execute("sum the list", 0.1)
        self.assertEqual(response.mode, FAST)
        self.assertFalse(response.degraded)
        self.assertIsNone(response.degraded_reason)

    def test_strict_mode_raises_instead_of_degrading(self) -> None:
        account = woken_account(40.0)
        scheduler = DualBeatScheduler(account, strict_first_beat=True)
        before = account.current_energy
        with self.assertRaises(WakeWindowError):
            scheduler.execute("prove the invariant", 1.0)
        # Nothing was billed for the refusal, so the next call can still run.
        self.assertEqual(account.current_energy, before)
        self.assertTrue(account.awaiting_first_beat)
        self.assertEqual(scheduler.execute("sum the list", 0.1).mode, FAST)

    def test_strict_mode_does_not_affect_easy_tasks(self) -> None:
        scheduler = DualBeatScheduler(woken_account(40.0), strict_first_beat=True)
        self.assertEqual(scheduler.execute("sum the list", 0.1).mode, FAST)

    def test_a_premature_wake_is_refused_rather_than_run(self) -> None:
        # 11% cannot fund a 7 beat and stay above 10%. Running it would buy one
        # answer with the charge that keeps the account running.
        account = woken_account(11.0)
        scheduler = DualBeatScheduler(account)
        with self.assertRaises(EnergyExhausted) as caught:
            scheduler.execute("sum the list", 0.1)
        message = str(caught.exception)
        self.assertIn("premature wake", message)
        self.assertIn("17.00", message)   # the charge that would clear the band
        self.assertEqual(account.current_energy, 11.0)

    def test_a_sufficient_wake_runs(self) -> None:
        # 17 is the smallest charge that funds a 7 beat and clears the floor.
        account = woken_account(17.0)
        response = DualBeatScheduler(account).execute("sum the list", 0.1)
        self.assertEqual(response.mode, FAST)
        self.assertEqual(response.state_after, "CONSERVE")

    def test_trust_is_reported_before_the_wake_window(self) -> None:
        """A durable cause outranks a transient one, or bankruptcy goes unnamed."""
        from agent_metabolism.penalty import PenaltyLedger

        ledger = PenaltyLedger()
        ledger.record_falsification("hash_mismatch")   # trust -> 0, CRITICAL
        account = woken_account(80.0)
        scheduler = DualBeatScheduler(account, penalty_ledger=ledger)
        response = scheduler.execute("prove the invariant", 1.0)
        self.assertEqual(response.degraded_reason, "penalty_critical")
        self.assertTrue(response.trust_blocked)

    def test_the_reason_travels_into_the_audit_record(self) -> None:
        """The distinction is only worth making if it reaches the ledger."""
        account = woken_account(40.0)
        scheduler = DualBeatScheduler(account)
        proxy = LLMProxy(scheduler)
        result = proxy.generate("prove the invariant", 1.0)
        self.assertEqual(result.mode, FAST)
        self.assertEqual(result.degraded_reason, DEGRADED_WAKE)


class TheGuardAgreesWithTheLaw(unittest.TestCase):
    """The scheduler's refusal is the same inequality as ``min_exit_ratio``."""

    def test_the_forced_fast_beat_satisfies_the_band_inequality(self) -> None:
        from agent_metabolism.dynamics import min_exit_ratio

        account = woken_account(40.0)
        scheduler = DualBeatScheduler(account)
        # The law's requirement is on the *exit* ratio: the band must fund the
        # beat it admits. Woken at 0.40 with a 7 beat, the required exit is 0.17,
        # so the forced fast beat clears it with room to spare.
        needed = min_exit_ratio(0.10, scheduler.fast_cost, budget=account.budget)
        self.assertGreaterEqual(account.ratio + 1e-12, needed)

    def test_the_shipped_band_cannot_admit_a_slow_first_beat(self) -> None:
        from agent_metabolism.dynamics import min_exit_ratio

        # This is why the rule exists rather than being optional: at the shipped
        # 0.30 exit the slow beat needs 0.45, so a slow first beat is always
        # either unaffordable or drowning.
        self.assertGreater(min_exit_ratio(0.10, 35.0), 0.30)


if __name__ == "__main__":
    unittest.main()
