"""Tests for double-entry validation.

The cases worth writing are not "does True/True return PASS". They are the ones
where a looser design would have given a confidently wrong answer:

* ``check=False, witness=False`` is a **balanced** entry, not a failure. An agent
  that accurately reports a failure must not be punished for it, or the ledger
  trains agents to hide failures -- the exact behaviour it exists to catch.
* ``witness=None`` is not ``witness=False``. One is "nobody looked", the other is
  "somebody looked and found nothing"; conflating them turns every unexamined
  claim into an accusation.
* A locked agent must not be able to clear its own lock by filing a PASS.
* Per-agent trust must be *unknown*, not shared, when the ledgers are pooled --
  a per-agent column filled with a company number is a lie of presentation.
* An energy account that moves without a journal entry must fail reconciliation.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    FAIL,
    INVALID,
    PASS,
    AgentLocked,
    DoubleEntryError,
    DoubleEntryLedger,
    EnergyAccount,
    PenaltyLedger,
    Transaction,
    digest_file,
    verdict_for,
)


def _books(**kwargs):
    """A ledger with one penalty account per agent, which is the useful mode."""
    return DoubleEntryLedger(penalty_factory=lambda agent: PenaltyLedger(), **kwargs)


def _tx(agent="coder", intent="do the thing", check=True, witness=None, **kwargs):
    return Transaction(agent, intent, check, witness, **kwargs)


class TestTheRule(unittest.TestCase):
    """`verdict_for` is the whole design in one function; test it directly."""

    def test_verified_work_is_pass(self):
        self.assertEqual(verdict_for(True, True), PASS)

    def test_an_accurate_failure_report_is_pass(self):
        """Both sides agree, so the books balance.

        This is the case a naive implementation gets wrong by testing
        ``check and witness``, which would charge an agent for honesty.
        """
        self.assertEqual(verdict_for(False, False), PASS)

    def test_claiming_success_without_evidence_is_fail(self):
        self.assertEqual(verdict_for(True, False), FAIL)

    def test_claiming_failure_when_work_landed_is_fail(self):
        self.assertEqual(verdict_for(False, True), FAIL)

    def test_no_witness_is_invalid_in_either_direction(self):
        self.assertEqual(verdict_for(True, None), INVALID)
        self.assertEqual(verdict_for(False, None), INVALID)

    def test_the_rule_is_not_conjunction(self):
        # A guard against "simplifying" the implementation back to `check and
        # witness`, which would silently make honest failures into offences.
        self.assertNotEqual(verdict_for(False, False), FAIL)


class TestTransactionShape(unittest.TestCase):
    def test_check_must_be_a_real_bool(self):
        for bogus in (1, 0, "yes", "ok", [], None):
            with self.assertRaises(DoubleEntryError):
                Transaction("coder", "x", bogus)  # type: ignore[arg-type]

    def test_witness_must_be_bool_or_none(self):
        for bogus in ("true", 1, 0, [], {}):
            with self.assertRaises(DoubleEntryError):
                Transaction("coder", "x", True, bogus)  # type: ignore[arg-type]

    def test_an_entry_without_an_agent_is_refused(self):
        with self.assertRaises(DoubleEntryError):
            Transaction("", "do it", True)

    def test_an_entry_without_an_intent_is_refused(self):
        with self.assertRaises(DoubleEntryError):
            Transaction("coder", "   ", True)

    def test_a_negative_cost_is_refused(self):
        with self.assertRaises(DoubleEntryError):
            Transaction("coder", "x", True, None, cost=-1.0)

    def test_a_non_finite_cost_is_refused(self):
        with self.assertRaises(DoubleEntryError):
            Transaction("coder", "x", True, None, cost=float("nan"))

    def test_recording_something_that_is_not_a_transaction_is_a_type_error(self):
        books = _books()
        with self.assertRaises(TypeError):
            books.record_transaction({"agent": "coder"})  # type: ignore[arg-type]


class TestBooking(unittest.TestCase):
    def test_a_pass_is_booked_with_both_sides(self):
        books = _books()
        receipt = books.record_transaction(_tx(witness=True, artefact="app.py"))
        self.assertEqual(receipt["verdict"], PASS)
        self.assertTrue(receipt["check"])
        self.assertTrue(receipt["witness"])
        self.assertEqual(receipt["artefact"], "app.py")
        self.assertEqual(receipt["seq"], 1)

    def test_the_receipt_keeps_the_intent_verbatim(self):
        books = _books()
        receipt = books.record_transaction(_tx(intent="write app.py"))
        self.assertEqual(receipt["intent"], "write app.py")

    def test_an_honest_failure_is_booked_and_charged_nothing(self):
        books = _books()
        receipt = books.record_transaction(_tx(check=False, witness=False))
        self.assertEqual(receipt["verdict"], PASS)
        self.assertEqual(receipt["penalty"], 0.0)
        self.assertEqual(receipt["charged_as"], "")

    def test_an_honest_failure_does_not_buy_trust_back(self):
        """Paying for accurate bad news would make failing cheap."""
        books = _books()
        books.record_transaction(_tx(check=True, witness=False))  # lose trust
        before = books.trust_of("coder")
        books.record_transaction(_tx(check=False, witness=False, intent="failed"))
        self.assertEqual(books.trust_of("coder"), before)

    def test_verified_work_buys_trust_back(self):
        books = _books()
        books.record_transaction(_tx(check=True, witness=False))
        before = books.trust_of("coder")
        books.record_transaction(_tx(intent="retry", witness=True))
        self.assertGreater(books.trust_of("coder"), before)

    def test_the_journal_grows_one_receipt_per_judged_entry(self):
        books = _books()
        for i in range(5):
            books.record_transaction(_tx(intent="step {}".format(i), witness=True))
        self.assertEqual(len(books.receipts), 5)
        self.assertEqual([r.seq for r in books.receipts], [1, 2, 3, 4, 5])


class TestCharging(unittest.TestCase):
    def test_a_false_completion_is_charged(self):
        books = _books()
        receipt = books.record_transaction(_tx(check=True, witness=False))
        self.assertEqual(receipt["verdict"], FAIL)
        self.assertEqual(receipt["charged_as"], "FALSE_COMPLETION")
        self.assertGreater(receipt["penalty"], 0.0)
        self.assertLess(books.trust_of("coder"), 100.0)

    def test_under_claiming_is_charged_less_than_lying(self):
        liar = _books()
        liar_receipt = liar.record_transaction(_tx(check=True, witness=False))

        sandbagger = _books()
        sandbag_receipt = sandbagger.record_transaction(_tx(check=False, witness=True))

        self.assertEqual(sandbag_receipt["verdict"], FAIL)
        self.assertLess(sandbag_receipt["penalty"], liar_receipt["penalty"])
        self.assertEqual(sandbag_receipt["charged_as"], "FALSE_REPORT")

    def test_an_unwitnessed_entry_is_not_charged(self):
        """INVALID means nobody looked, and a fine for that would be a fine for
        the auditor's own gap. The consequence is the lock, not money."""
        books = _books()
        receipt = books.record_transaction(_tx(check=True, witness=None))
        self.assertEqual(receipt["verdict"], INVALID)
        self.assertEqual(receipt["penalty"], 0.0)
        self.assertEqual(books.trust_of("coder"), 100.0)

    def test_one_agents_lie_does_not_charge_another(self):
        books = _books()
        books.record_transaction(_tx(agent="coder", check=True, witness=False))
        books.record_transaction(_tx(agent="planner", witness=True))
        self.assertLess(books.trust_of("coder"), books.trust_of("planner"))
        self.assertEqual(books.trust_of("planner"), 100.0)


class TestLocking(unittest.TestCase):
    def test_a_missing_witness_locks_the_agent(self):
        books = _books()
        books.record_transaction(_tx(check=True, witness=None))
        self.assertTrue(books.is_locked("coder"))
        self.assertIn("no witness", books.lock_reason("coder"))

    def test_a_missing_witness_locks_an_honest_failure_too(self):
        """An open entry cannot be closed, whichever way the claim points."""
        books = _books()
        books.record_transaction(_tx(check=False, witness=None))
        self.assertTrue(books.is_locked("coder"))

    def test_only_the_offender_is_locked(self):
        books = _books()
        books.record_transaction(_tx(agent="coder", witness=None))
        self.assertTrue(books.is_locked("coder"))
        self.assertFalse(books.is_locked("planner"))

    def test_the_next_operation_is_refused(self):
        books = _books()
        books.record_transaction(_tx(witness=None))
        with self.assertRaises(AgentLocked) as caught:
            books.record_transaction(_tx(intent="try again", witness=True))
        self.assertEqual(caught.exception.agent, "coder")

    def test_a_refusal_is_counted_but_not_judged(self):
        """A refusal is not a failure: no work was witnessed because none ran."""
        books = _books()
        books.record_transaction(_tx(witness=None))
        with self.assertRaises(AgentLocked):
            books.record_transaction(_tx(witness=True))
        sheet = books.get_balance_sheet()
        self.assertEqual(sheet["totals"]["refusals"], 1)
        self.assertEqual(sheet["totals"]["judged"], 1, "the refusal added no verdict")
        self.assertEqual(len(books.receipts), 1)

    def test_a_passed_entry_does_not_unlock(self):
        """The agent cannot buy its way out with a PASS.

        It cannot even reach the door: the lock is checked before the verdict is
        computed, so there is no PATH here that clears it.
        """
        books = _books()
        books.record_transaction(_tx(witness=None))
        for _ in range(3):
            with self.assertRaises(AgentLocked):
                books.record_transaction(_tx(witness=True, artefact="app.py"))
        self.assertTrue(books.is_locked("coder"))

    def test_an_operator_can_unlock(self):
        books = _books()
        books.record_transaction(_tx(witness=None))
        books.unlock("coder", reason="witnessed by hand")
        self.assertFalse(books.is_locked("coder"))
        receipt = books.record_transaction(_tx(intent="try again", witness=True))
        self.assertEqual(receipt["verdict"], PASS)

    def test_unlocking_does_not_erase_the_open_entry(self):
        """History is not a draft: the INVALID stays on the books."""
        books = _books()
        books.record_transaction(_tx(witness=None))
        books.unlock("coder", reason="witnessed by hand")
        sheet = books.get_balance_sheet()
        self.assertEqual(sheet["totals"]["invalid"], 1)
        self.assertEqual(sheet["books"][0]["unwitnessed"], 1)
        self.assertEqual(sheet["totals"]["unlocks"], 1)

    def test_unlocking_a_free_agent_is_an_error(self):
        books = _books()
        books.record_transaction(_tx(witness=True))
        with self.assertRaises(DoubleEntryError):
            books.unlock("coder")

    def test_unlocking_an_unknown_agent_is_an_error(self):
        with self.assertRaises(DoubleEntryError):
            _books().unlock("nobody")


class TestTwoConsecutiveFailures(unittest.TestCase):
    """The ruling: one FAIL is a charge, two in a row stops the agent.

    Underneath this rule is a measured inversion. With the lock on INVALID only,
    a liar survived five attempts (trust to 0, penalty CRITICAL) while an agent
    that merely omitted its witness was stopped on its second -- so the cheapest
    path to staying operational was to lie. These tests exist to keep that from
    coming back.
    """

    def test_one_failure_is_only_a_charge(self):
        books = _books()
        receipt = books.record_transaction(_tx(check=True, witness=False))
        self.assertEqual(receipt["verdict"], FAIL)
        self.assertFalse(receipt["book_locked"])
        self.assertFalse(books.is_locked("coder"))
        self.assertEqual(books.get_balance_sheet()["books"][0]["fail_streak"], 1)

    def test_the_second_failure_closes_the_book(self):
        books = _books()
        books.record_transaction(_tx(intent="first", check=True, witness=False))
        second = books.record_transaction(
            _tx(intent="second", check=True, witness=False)
        )
        self.assertEqual(second["verdict"], FAIL)
        self.assertTrue(second["book_locked"], "the caller must be told")
        self.assertTrue(books.is_locked("coder"))
        self.assertEqual(books.get_balance_sheet()["books"][0]["lock_kind"], "repeat_fail")

    def test_the_proof_is_still_booked_before_the_lock_bites(self):
        """The second FAIL must return a receipt, not raise.

        Raising on the entry being judged would lose the record of the very
        thing that closed the book -- and a gate whose rejection is the first
        the caller hears of it wastes a round trip.
        """
        books = _books()
        books.record_transaction(_tx(intent="first", check=True, witness=False))
        receipt = books.record_transaction(
            _tx(intent="second", check=True, witness=False)
        )
        self.assertEqual(receipt["seq"], 2)
        self.assertEqual(receipt["verdict"], FAIL)
        self.assertEqual(len(books.receipts), 2)
        self.assertGreater(receipt["penalty"], 0.0)

    def test_every_later_operation_is_refused(self):
        books = _books()
        books.record_transaction(_tx(check=True, witness=False))
        books.record_transaction(_tx(check=True, witness=False))
        with self.assertRaises(AgentLocked):
            books.record_transaction(_tx(intent="third", witness=True, artefact="a"))
        with self.assertRaises(AgentLocked):
            books.record_transaction(_tx(intent="honest", check=False, witness=False))

    def test_a_pass_between_failures_resets_the_streak(self):
        """The required positive case: fail, pass, fail is not a stop."""
        books = _books()
        books.record_transaction(_tx(intent="fail one", check=True, witness=False))
        books.record_transaction(_tx(intent="honest work", witness=True, artefact="a"))
        self.assertEqual(books.get_balance_sheet()["books"][0]["fail_streak"], 0)
        receipt = books.record_transaction(
            _tx(intent="fail two", check=True, witness=False)
        )
        self.assertFalse(receipt["book_locked"])
        self.assertFalse(books.is_locked("coder"))

    def test_an_honest_failure_also_resets_the_streak(self):
        """An accurate bad report must not creep toward a stop.

        An honest failure is a PASS, so it clears the counter. If it counted
        toward the limit, the ledger would punish the one behaviour it wants.
        """
        books = _books()
        books.record_transaction(_tx(intent="fail", check=True, witness=False))
        books.record_transaction(_tx(intent="accurately failed", check=False, witness=False))
        self.assertEqual(books.get_balance_sheet()["books"][0]["fail_streak"], 0)
        receipt = books.record_transaction(
            _tx(intent="fail again", check=True, witness=False)
        )
        self.assertFalse(books.is_locked("coder"))

    def test_under_claiming_also_counts(self):
        """Both directions of FAIL are contradictions, so both count."""
        books = _books()
        books.record_transaction(_tx(intent="f1", check=False, witness=True))
        receipt = books.record_transaction(_tx(intent="f2", check=False, witness=True))
        self.assertTrue(receipt["book_locked"])
        self.assertTrue(books.is_locked("coder"))

    def test_mixed_failure_directions_still_count(self):
        books = _books()
        books.record_transaction(_tx(intent="lie", check=True, witness=False))
        receipt = books.record_transaction(_tx(intent="under-claim", check=False, witness=True))
        self.assertTrue(receipt["book_locked"])

    def test_one_agents_streak_does_not_lock_another(self):
        books = _books()
        books.record_transaction(_tx(agent="coder", check=True, witness=False))
        books.record_transaction(_tx(agent="coder", check=True, witness=False))
        self.assertTrue(books.is_locked("coder"))
        self.assertFalse(books.is_locked("planner"))
        ok = books.record_transaction(_tx(agent="planner", witness=True, artefact="a"))
        self.assertEqual(ok["verdict"], PASS)

    def test_a_refusal_does_not_advance_the_streak(self):
        books = _books()
        books.record_transaction(_tx(check=True, witness=False))
        books.record_transaction(_tx(check=True, witness=False))
        with self.assertRaises(AgentLocked):
            books.record_transaction(_tx(witness=False))
        self.assertEqual(books.get_balance_sheet()["books"][0]["fail_streak"], 2)

    def test_the_limit_is_configurable_but_never_below_two(self):
        strict = DoubleEntryLedger(
            penalty_factory=lambda a: PenaltyLedger(), fail_limit=3
        )
        for i in range(2):
            receipt = strict.record_transaction(
                _tx(intent="f{}".format(i), check=True, witness=False)
            )
            self.assertFalse(receipt["book_locked"])
        third = strict.record_transaction(_tx(intent="f3", check=True, witness=False))
        self.assertTrue(third["book_locked"])

    def test_a_limit_of_one_is_refused(self):
        """Stopping on the first mistake is the over-accusation this avoids."""
        for bad in (1, 0, -1):
            with self.assertRaises(DoubleEntryError):
                DoubleEntryLedger(penalty_factory=lambda a: PenaltyLedger(), fail_limit=bad)

    def test_a_bool_is_not_a_limit(self):
        with self.assertRaises(DoubleEntryError):
            DoubleEntryLedger(
                penalty_factory=lambda a: PenaltyLedger(), fail_limit=True
            )

    def test_a_human_can_restart_a_stopped_agent(self):
        books = _books()
        books.record_transaction(_tx(check=True, witness=False))
        books.record_transaction(_tx(check=True, witness=False))
        books.unlock("coder", reason="spoken to; retraining scheduled")
        receipt = books.record_transaction(_tx(intent="after talk", witness=True, artefact="a"))
        self.assertEqual(receipt["verdict"], PASS)
        self.assertFalse(books.is_locked("coder"))

    def test_unlocking_does_not_forgive_the_streak(self):
        """The operator unblocked the book, not the record.

        An agent whose first act after a manual unlock is another false
        completion has shown the intervention changed nothing, so it is stopped
        at once. Honest work clears the streak on its own.
        """
        books = _books()
        books.record_transaction(_tx(check=True, witness=False))
        books.record_transaction(_tx(check=True, witness=False))
        books.unlock("coder", reason="second chance")
        receipt = books.record_transaction(_tx(intent="lies again", check=True, witness=False))
        self.assertTrue(receipt["book_locked"])
        self.assertTrue(books.is_locked("coder"))

    def test_the_two_locks_are_distinguishable(self):
        """An operator deciding whether to unlock needs to know which one it is."""
        unwitnessed = _books()
        unwitnessed.record_transaction(_tx(witness=None))
        lies = _books()
        lies.record_transaction(_tx(check=True, witness=False))
        lies.record_transaction(_tx(check=True, witness=False))

        self.assertEqual(
            unwitnessed.get_balance_sheet()["books"][0]["lock_kind"], "unwitnessed"
        )
        self.assertEqual(
            lies.get_balance_sheet()["books"][0]["lock_kind"], "repeat_fail"
        )
        self.assertIn("[unwitnessed]", unwitnessed.render_balance_sheet())
        self.assertIn("[repeat_fail]", lies.render_balance_sheet())

    def test_an_invalid_lock_is_still_a_single_entry(self):
        books = _books()
        receipt = books.record_transaction(_tx(witness=None))
        self.assertTrue(receipt["book_locked"])
        self.assertTrue(books.is_locked("coder"))

    def test_the_inversion_is_gone(self):
        """The reason the rule exists, asserted as a property.

        Lying must not be the cheaper strategy. Before the rule, a liar booked
        five entries while an unwitnessed agent was stopped after one; the
        comparison here is that neither can outlast the other by lying.
        """

        def booked_entries(source_kwargs):
            books = _books()
            for attempt in range(10):
                try:
                    books.record_transaction(
                        _tx(intent="attempt {}".format(attempt), **source_kwargs)
                    )
                except AgentLocked:
                    break
            return len(books.receipts), books.is_locked("coder")

        liar_entries, liar_locked = booked_entries({"check": True, "witness": False})
        mute_entries, mute_locked = booked_entries({"check": True, "witness": None})

        self.assertTrue(liar_locked)
        self.assertTrue(mute_locked)
        self.assertEqual(liar_entries, 2)
        self.assertEqual(mute_entries, 1)
        # A liar gets one more entry than a mute agent, because the first false
        # completion is a charge rather than a stop -- but not five more, and
        # both end up stopped.
        self.assertLessEqual(liar_entries - mute_entries, 1)


class TestBalanceSheet(unittest.TestCase):
    def test_the_shape_is_stable(self):
        books = _books()
        books.record_transaction(_tx(witness=True, artefact="app.py"))
        sheet = books.get_balance_sheet()
        for key in ("as_of", "agents", "totals", "health", "balance", "books"):
            self.assertIn(key, sheet)
        for key in ("total", "active", "locked"):
            self.assertIn(key, sheet["agents"])
        for key in ("pass", "fail", "invalid", "judged", "energy_spent"):
            self.assertIn(key, sheet["totals"])
        for key in ("score", "state", "trust", "judged"):
            self.assertIn(key, sheet["health"])

    def test_counts_add_up(self):
        books = _books()
        books.record_transaction(_tx(agent="a", witness=True))
        books.record_transaction(_tx(agent="b", check=True, witness=False))
        books.record_transaction(_tx(agent="c", witness=None))
        sheet = books.get_balance_sheet()
        self.assertEqual(sheet["totals"]["pass"], 1)
        self.assertEqual(sheet["totals"]["fail"], 1)
        self.assertEqual(sheet["totals"]["invalid"], 1)
        self.assertEqual(sheet["totals"]["judged"], 3)
        self.assertEqual(len(books.receipts), 3)

    def test_active_means_has_judged_work_and_is_not_locked(self):
        books = _books()
        books.record_transaction(_tx(agent="worker", witness=True))
        books.record_transaction(_tx(agent="mute", witness=None))
        sheet = books.get_balance_sheet()
        self.assertEqual(sheet["agents"]["total"], 2)
        self.assertEqual(sheet["agents"]["active"], 1)
        self.assertEqual(sheet["agents"]["locked"], ["mute"])

    def test_energy_is_summed_from_the_journal(self):
        books = _books()
        books.record_transaction(_tx(witness=True, cost=7.0))
        books.record_transaction(_tx(witness=True, cost=5.5))
        self.assertAlmostEqual(books.get_balance_sheet()["totals"]["energy_spent"], 12.5)

    def test_no_evidence_gives_no_score(self):
        """Not 100: an empty book is not health, it is an absence of evidence."""
        sheet = DoubleEntryLedger().get_balance_sheet()
        self.assertIsNone(sheet["health"]["score"])
        self.assertIsNone(sheet["health"]["state"])
        self.assertIn("no evidence", DoubleEntryLedger().render_balance_sheet())

    def test_the_weakest_book_sets_the_trust(self):
        books = _books()
        books.record_transaction(_tx(agent="solid", witness=True))
        books.record_transaction(_tx(agent="liar", check=True, witness=False))
        sheet = books.get_balance_sheet()
        self.assertEqual(sheet["health"]["trust"], books.trust_of("liar"))
        self.assertLess(sheet["health"]["trust"], 100.0)

    def test_an_ungrounded_pass_is_flagged_but_still_a_pass(self):
        """A pass with no named artefact is a record, not evidence.

        It is surfaced rather than downgraded: silently failing it would be an
        accusation the ledger cannot support, since it never saw the call site.
        """
        books = _books()
        books.record_transaction(_tx(witness=True, artefact=""))
        sheet = books.get_balance_sheet()
        self.assertEqual(sheet["totals"]["pass"], 1)
        self.assertEqual(sheet["totals"]["ungrounded_passes"], 1)
        self.assertIn("name no artefact", books.render_balance_sheet())

    def test_per_agent_trust_is_unknown_when_the_account_is_pooled(self):
        """One account cannot answer "which agent", so the column must say so."""
        pooled = DoubleEntryLedger(PenaltyLedger())
        pooled.record_transaction(_tx(agent="a", witness=True))
        pooled.record_transaction(_tx(agent="b", check=True, witness=False))
        self.assertFalse(pooled.per_agent_trust)
        self.assertIsNone(pooled.trust_of("a"))
        self.assertIsNone(pooled.trust_of("b"))
        self.assertIn("  -", pooled.render_balance_sheet())

    def test_the_two_ledger_modes_cannot_be_mixed(self):
        with self.assertRaises(DoubleEntryError):
            DoubleEntryLedger(PenaltyLedger(), penalty_factory=lambda a: PenaltyLedger())


class TestReconciliation(unittest.TestCase):
    def test_a_matching_account_reconciles(self):
        account = EnergyAccount(100.0)
        books = _books(energy_account=account)
        account.consume(12.0)
        books.record_transaction(_tx(witness=True, cost=12.0))
        balance = books.get_balance_sheet()["balance"]
        self.assertTrue(balance["reconciled"])
        self.assertAlmostEqual(balance["external_energy"], 12.0)

    def test_a_charge_outside_the_journal_breaks_reconciliation(self):
        """The point of the check: catch an account that moved without an entry.

        This is what double-entry is *for*. A single-entry book would report
        12.0 spent and look fine; the journal says 0.0 and the disagreement is
        the finding.
        """
        account = EnergyAccount(100.0)
        books = _books(energy_account=account)
        account.consume(12.0)  # spent, but never booked
        balance = books.get_balance_sheet()["balance"]
        self.assertFalse(balance["reconciled"])
        self.assertIn("out-of-band charge", books.render_balance_sheet())

    def test_without_an_account_reconciliation_is_vacuously_true(self):
        books = _books()
        books.record_transaction(_tx(witness=True, cost=3.0))
        balance = books.get_balance_sheet()["balance"]
        self.assertTrue(balance["reconciled"])
        self.assertIsNone(balance["external_energy"])


class TestDigest(unittest.TestCase):
    def test_digest_file_reads_the_real_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "app.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("print('hello')\n")
            first = digest_file(path)
            self.assertTrue(first.startswith("sha256:"))
            self.assertEqual(len(first), len("sha256:") + 64)

            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n")
            self.assertNotEqual(digest_file(path), first, "a one-byte change must show")

    def test_the_witness_can_be_a_digest_comparison(self):
        """The honest path, end to end: expect a digest, then check it."""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "app.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("v1\n")
            promised = digest_file(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("v2\n")

            books = _books()
            receipt = books.record_transaction(
                _tx(
                    check=True,
                    witness=digest_file(path) == promised,
                    artefact=path,
                )
            )
            self.assertEqual(receipt["verdict"], FAIL)
            self.assertEqual(receipt["charged_as"], "FALSE_COMPLETION")


class TestRendering(unittest.TestCase):
    def test_the_report_is_plain_text(self):
        books = _books()
        books.record_transaction(_tx(witness=True, artefact="app.py"))
        text = books.render_balance_sheet()
        self.assertIsInstance(text, str)
        self.assertTrue(text.isascii(), "the report must survive a log file")
        self.assertIn("DOUBLE-ENTRY BALANCE SHEET", text)
        self.assertIn("agent", text)

    def test_every_agent_appears(self):
        books = _books()
        for name in ("planner", "coder", "tester"):
            books.record_transaction(_tx(agent=name, witness=True))
        text = books.render_balance_sheet()
        for name in ("planner", "coder", "tester"):
            self.assertIn(name, text)

    def test_a_locked_agent_is_marked(self):
        books = _books()
        books.record_transaction(_tx(agent="coder", witness=None))
        text = books.render_balance_sheet()
        self.assertIn("LOCKED", text)


class TestZeroDependencies(unittest.TestCase):
    def test_the_module_imports_only_the_standard_library_and_itself(self):
        """The package-wide check parses every module; this pins the new one.

        Kept local so a failure names the file that broke the promise instead of
        a list of offenders.
        """
        import ast

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "agent_metabolism", "double_entry.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        allowed = set(sys.stdlib_module_names) | {"agent_metabolism"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            offenders.extend(n for n in names if n not in allowed)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
