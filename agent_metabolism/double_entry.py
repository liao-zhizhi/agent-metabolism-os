"""Double-entry validation: every claim is booked twice, and the two sides meet.

A single-entry book records what the agent says happened. It cannot be audited,
because the only witness in it is the party being audited -- which is why "the
tool call succeeded" is worth exactly what the agent's incentives are worth.
Double-entry is the older and cheaper answer: book the claim, book the evidence,
and refuse to close the books until the two agree. This module is that journal.

The two sides
-------------
``check``    the agent's report. What it says it did.
``witness``  the physical evidence. What the world says it did.

The verdict is one line, and it is worth reading twice::

    if witness is None:  return INVALID      # nobody looked
    if check is witness: return PASS         # the books agree
    return FAIL                              # the books contradict

``check is witness`` is not a typo for ``check and witness``. Two ``True`` values
balance, and so do two ``False`` values: an agent that reports failure and is
confirmed to have failed has filed an *accurate* report, and accuracy is what
this ledger is for. Charging it for honesty would train agents to hide failures,
which is the behaviour the ledger exists to prevent.

Why "missing" is not "false"
----------------------------
``witness=False`` means someone looked and found nothing. ``witness=None`` means
nobody looked. Collapsing them would turn every unexamined claim into an
accusation -- the same defect as reporting a compiler that was never run as a
rejection. So they are different verdicts, and they cost different things:

===========  =========  ========  ===================================
check        witness    verdict   consequence
===========  =========  ========  ===================================
True         True       PASS      verified work; buys trust back
True         False      FAIL      false completion; charged
True         None       INVALID   no evidence; the agent is locked
False        False      PASS      honest failure; recorded, earns nothing
False        True       FAIL      under-claim; charged at a lesser weight
False        None       INVALID   no evidence; the agent is locked
===========  =========  ========  ===================================

Two ways to be locked, and they are different findings
------------------------------------------------------
``INVALID``   an open entry. Nobody looked, so the book cannot be reconciled.
              One is enough.
``FAIL``      a contradiction. The first is a charge; a second *consecutive*
              one stops the agent until a human intervenes. An honest PASS
              clears the streak, so only sustained dishonesty accumulates.

The two-failure stop exists because the alternative was measured and it selected
for fraud. With the lock on ``INVALID`` alone, an agent could lie five times
running -- trust to zero, penalty ``CRITICAL``, but still operational -- while an
agent that merely omitted its witness was stopped on its second attempt. The
cheapest way to keep working was therefore to lie. A ledger that makes fraud the
survivable strategy is worse than no ledger: it manufactures the appearance of
accountability while selecting for the opposite.

The first failure is still only a charge, because a single wrong report is a
normal day and stopping on it would be the over-accusation this design avoids.
What is not survivable is *repeating* it: if a fine changed nothing, more fines
will not either.

The lock is not a punishment, it is a bookkeeping rule: a book with a missing
entry cannot be closed, so the agent may not open the next one until a human
witnesses the gap. It is deliberately *not* clearable from inside -- an agent
able to unlock itself by filing a PASS would have exactly the power the lock
withholds. The same holds for the two-failure stop: the second FAIL is still
booked and still returns a receipt, because the caller must be told what happened;
only the entries *after* it are refused.

What this module does not do
----------------------------
* **It does not establish the witness.** ``witness=True`` is a claim by the
  caller. If the caller is the agent's own harness, this ledger records a
  self-certification with extra steps. The honest path is :func:`digest_file`
  and a comparison against what was expected *before* the work ran -- the same
  ordering constraint the membrane enforces. The ledger's guarantee is narrower
  and should be stated narrowly: both sides are recorded together, the
  contradiction is charged, and the totals are reconcilable.
* **It does not judge intent against outcome.** ``intent`` is stored for the
  audit trail; whether the work matched what was promised is a semantic question
  no field here can answer.
* **It is not tamper-evident by itself.** The receipts are an in-memory journal.
  Durable, hash-chained evidence belongs to the membrane, which already does it;
  duplicating that here would mean two chains to verify and one of them stale.
* **The lock is process-local.** It stops a cooperative harness, not a hostile
  one that imports around it.

Zero dependencies: standard library, plus this package's own penalty ledger. The
penalty ledger is injected and duck-typed, so a caller may supply its own.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "AgentLocked",
    "DoubleEntryError",
    "DoubleEntryLedger",
    "FAIL",
    "INVALID",
    "PASS",
    "Receipt",
    "Transaction",
    "VERDICTS",
    "digest_file",
    "verdict_for",
]

#: Same three strings as the verdict lattice, matched by contract so a caller can
#: hand a result to ``ImmuneState.record_verdict(Verdict(...))`` without this
#: module importing the auditor. ``NOT RUN`` is absent on purpose: a transaction
#: that was never judged is not booked at all, it is refused.
PASS = "PASS"
FAIL = "FAIL"
INVALID = "INVALID"
VERDICTS = (PASS, FAIL, INVALID)

#: Worst-wins ordering, same as everywhere else in this system.
_SEVERITY = {PASS: 0, INVALID: 1, FAIL: 2}

#: Penalty state ordering, so "the company is as healthy as its weakest book".
_PENALTY_ORDER = {"HEALTHY": 0, "WARNING": 1, "CRITICAL": 2}

#: Charged when an agent reports success and the evidence says otherwise. The
#: weight comes from ``penalty.DEFAULT_WEIGHTS`` rather than being restated here.
LIAR_VIOLATION = "FALSE_COMPLETION"

#: Charged when an agent reports failure and the evidence says the work landed.
#: Less severe than a false completion, and given an explicit weight because
#: ``DEFAULT_WEIGHTS`` has no entry for it: under-reporting is more often a
#: broken sensor than fraud, so it is billed as a protocol fault, not a lie.
UNDERCLAIM_VIOLATION = "FALSE_REPORT"
UNDERCLAIM_WEIGHT = 2.0

#: Tolerance for reconciling the journal against an external energy account.
_RECONCILE_EPSILON = 1e-9


class DoubleEntryError(ValueError):
    """The entry cannot be booked as given."""


class AgentLocked(DoubleEntryError):
    """The agent's books have an open entry, so it may not start another.

    Raised *before* anything is recorded for the refused attempt, and caught by
    nothing in this module: a refusal the caller can ignore is not a gate. The
    attempt itself is still counted, because an agent repeatedly pushing at a
    closed book is information an operator wants.
    """

    def __init__(self, agent: str, reason: str) -> None:
        super().__init__(
            "agent {!r} is locked and may not record another transaction: "
            "{}".format(agent, reason)
        )
        self.agent = agent
        self.reason = reason


def digest_file(path: str) -> str:
    """``sha256:<hex>`` of a file, in the form the rest of the system uses.

    Provided so the honest witness is also the easy one. A caller with no helper
    tends to pass ``witness=True`` from memory, which is how a journal becomes
    decoration.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def verdict_for(check: bool, witness: Optional[bool]) -> str:
    """The whole double-entry rule, as a pure function.

    ``check is witness`` covers both ``True/True`` (verified work) and
    ``False/False`` (an accurate failure report). See the module docstring for
    why the second case must not be a FAIL.
    """
    if witness is None:
        return INVALID
    return PASS if check is witness else FAIL


@dataclass(frozen=True)
class Transaction:
    """One entry, before it is judged.

    Args:
        agent: who is booking it. Required, because a journal whose lines have
            no author cannot be reconciled per author.
        intent: what the agent said it was going to do. Stored, not judged.
        check: the agent's report. Must be a real ``bool``: ``1`` and ``"ok"``
            are rejected rather than coerced, because a truthy string that
            silently means "success" is the same class of bug as a witness that
            is really a hope.
        witness: ``True`` / ``False`` from an inspection that happened outside
            this module, or ``None`` when nothing was inspected. ``None`` is a
            different statement from ``False``; see the module docstring.
        artefact: what the witness inspected -- a path, a git ref, a digest.
            Optional, and surfaced in the balance sheet as the difference
            between a grounded pass and a bare assertion.
        cost: energy this operation consumed, recorded so the journal can be
            reconciled against L1.
    """

    agent: str
    intent: str
    check: bool
    witness: Optional[bool] = None
    artefact: str = ""
    cost: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.agent, str) or not self.agent.strip():
            raise DoubleEntryError(
                "agent must be a non-empty str, got {!r}".format(self.agent)
            )
        if not isinstance(self.intent, str) or not self.intent.strip():
            raise DoubleEntryError(
                "intent must be a non-empty str: an entry with no stated purpose "
                "cannot be audited, only counted"
            )
        # `type(...) is bool` rather than isinstance: `isinstance(1, bool)` is
        # False but `isinstance(True, int)` is True, and the asymmetry is easy to
        # get backwards. Accepting 1 as "success" is exactly the coercion that
        # makes a journal lie quietly.
        if type(self.check) is not bool:
            raise DoubleEntryError(
                "check must be a bool, got {!r} ({})".format(
                    self.check, type(self.check).__name__
                )
            )
        if self.witness is not None and type(self.witness) is not bool:
            raise DoubleEntryError(
                "witness must be a bool or None, got {!r} ({}); use None to mean "
                "'not inspected', never a falsy placeholder".format(
                    self.witness, type(self.witness).__name__
                )
            )
        if not isinstance(self.artefact, str):
            raise DoubleEntryError(
                "artefact must be a str, got {!r}".format(type(self.artefact).__name__)
            )
        if type(self.cost) is bool or not isinstance(self.cost, (int, float)):
            raise DoubleEntryError(
                "cost must be a number, got {!r}".format(self.cost)
            )
        if not math.isfinite(float(self.cost)):
            raise DoubleEntryError("cost must be finite, got {!r}".format(self.cost))
        if self.cost < 0.0:
            raise DoubleEntryError(
                "cost cannot be negative, got {!r}; a refund is not a transaction"
                .format(self.cost)
            )


@dataclass(frozen=True)
class Receipt:
    """A booked entry: both sides, the verdict they compose to, and the cost.

    Immutable. The balance sheet is derived from these rather than from running
    counters, so the report and the journal cannot drift apart.
    """

    #: ``1..n`` over *judged* entries only. Two consequences a caller building an
    #: audit log on top must know, because both are silent if assumed away:
    #:
    #: * a **refusal** (``AgentLocked``) does **not** advance it -- nothing was
    #:   judged -- and neither does a manual ``unlock``. A consumer that uses this
    #:   number as an event id reuses the last one and, if it tails by "seq greater
    #:   than what I have seen", silently drops the refusal. The CEL console hit
    #:   exactly this and keeps its own counter; see ``product_mvp/cel_engine.py``.
    #: * it is per-ledger position, not a timestamp. Two receipts with the same
    #:   ``agent`` and different ``seq`` are ordered; nothing else about them is.
    seq: int
    agent: str
    intent: str
    check: bool
    witness: Optional[bool]
    verdict: str
    artefact: str = ""
    cost: float = 0.0
    trust_after: Optional[float] = None
    penalty: float = 0.0
    charged_as: str = ""
    #: True when booking this entry closed the agent's book. The caller has to be
    #: told here rather than discovering it on its next attempt: a gate whose
    #: rejection is the first a caller hears of it wastes a whole round trip.
    book_locked: bool = False

    @property
    def verified_work(self) -> bool:
        """A pass that stands for an effect that actually landed."""
        return self.verdict == PASS and self.check is True

    @property
    def honest_failure(self) -> bool:
        return self.verdict == PASS and self.check is False

    @property
    def grounded(self) -> bool:
        """A pass with something named as the evidence."""
        return self.verdict == PASS and bool(self.artefact)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "agent": self.agent,
            "intent": self.intent,
            "check": self.check,
            "witness": self.witness,
            "verdict": self.verdict,
            "artefact": self.artefact,
            "cost": self.cost,
            "trust_after": self.trust_after,
            "penalty": self.penalty,
            "charged_as": self.charged_as,
            "book_locked": self.book_locked,
        }


@dataclass
class _Book:
    """One agent's side of the ledger. Mutable, internal, never handed out."""

    agent: str
    verified: int = 0
    honest_failure: int = 0
    refuted: int = 0
    under_claimed: int = 0
    unwitnessed: int = 0
    energy: float = 0.0
    refusals: int = 0
    unlocked: int = 0
    #: Consecutive FAILs with no PASS between them. Reset by a PASS, which
    #: includes an honest failure -- an accurate bad report must not count
    #: toward the limit that stops the agent.
    fail_streak: int = 0
    locked_reason: Optional[str] = None
    #: ``"unwitnessed"`` or ``"repeat_fail"``. Two very different locks, and an
    #: operator deciding whether to unlock needs to know which one they face.
    lock_kind: Optional[str] = None

    @property
    def locked(self) -> bool:
        return self.locked_reason is not None

    @property
    def judged(self) -> int:
        return (
            self.verified
            + self.honest_failure
            + self.refuted
            + self.under_claimed
            + self.unwitnessed
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "verified": self.verified,
            "honest_failure": self.honest_failure,
            "refuted": self.refuted,
            "under_claimed": self.under_claimed,
            "unwitnessed": self.unwitnessed,
            "energy": self.energy,
            "refusals": self.refusals,
            "unlocked": self.unlocked,
            "fail_streak": self.fail_streak,
            "locked": self.locked,
            "lock_kind": self.lock_kind,
            "locked_reason": self.locked_reason,
            "judged": self.judged,
        }


class DoubleEntryLedger:
    """Books agent work twice: the claim, and the evidence, and the reconciliation.

    Args:
        penalty_ledger: a system-wide penalty ledger, or any object with
            ``record_violation(type, weight=None)``, ``record_success()``,
            ``trust_score`` and ``penalty_state``. When ``None``, one
            :class:`~agent_metabolism.penalty.PenaltyLedger` is created but is
            only charged as a single pooled account -- passing a factory is what
            gives each agent its own books.
        penalty_factory: ``callable(agent) -> ledger``. When given, each agent is
            charged in its own ledger, which is what makes "who is slacking"
            answerable. Trust is then per agent, not a company average.
        energy_account: optional L1 account used for **reconciliation only**. The
            journal never spends it; it checks that every charge in the account
            also appears in the journal. That check is the point -- an account
            that moves without an entry is exactly the kind of out-of-band write
            double-entry exists to reveal.

    Example::

        >>> books = DoubleEntryLedger()
        >>> books.record_transaction(
        ...     Transaction("coder", "write app.py", check=True, witness=True)
        ... )["verdict"]
        'PASS'
        >>> books.record_transaction(
        ...     Transaction("coder", "write tests.py", check=True, witness=False)
        ... )["verdict"]
        'FAIL'
    """

    def __init__(
        self,
        penalty_ledger: Any = None,
        *,
        penalty_factory: Optional[Callable[[str], Any]] = None,
        energy_account: Any = None,
        fail_limit: int = 2,
    ) -> None:
        if penalty_ledger is not None and penalty_factory is not None:
            raise DoubleEntryError(
                "pass either penalty_ledger (one pooled account) or "
                "penalty_factory (one per agent), not both: with both, a charge "
                "could land in either and the totals would stop reconciling"
            )
        for name, ledger in (("penalty_ledger", penalty_ledger),):
            if ledger is None:
                continue
            for method in ("record_violation", "record_success"):
                if not callable(getattr(ledger, method, None)):
                    raise TypeError(
                        "{} must expose {}()".format(name, method)
                    )
        if penalty_factory is not None and not callable(penalty_factory):
            raise TypeError(
                "penalty_factory must be callable(agent) -> ledger, got {!r}".format(
                    type(penalty_factory).__name__
                )
            )
        if type(fail_limit) is bool or not isinstance(fail_limit, int):
            raise DoubleEntryError(
                "fail_limit must be an int, got {!r}".format(fail_limit)
            )
        if fail_limit < 2:
            raise DoubleEntryError(
                "fail_limit must be at least 2, got {!r}. A limit of 1 would "
                "stop an agent on its first mistake, which is the over-accusation "
                "this whole design avoids; the first failure is a charge, the "
                "second consecutive one is a stop.".format(fail_limit)
            )
        if energy_account is not None:
            for attribute in ("total_spent",):
                if not hasattr(energy_account, attribute):
                    raise TypeError(
                        "energy_account must expose {}, got {!r}".format(
                            attribute, type(energy_account).__name__
                        )
                    )

        # Imported here rather than at module scope so the type only has to exist
        # when it is actually used, and so a caller injecting its own ledgers
        # never touches this one.
        self._pooled = penalty_ledger
        self._factory = penalty_factory
        self._energy = energy_account
        self._fail_limit = fail_limit
        self._books: Dict[str, _Book] = {}
        self._ledgers: Dict[str, Any] = {}
        self._receipts: List[Receipt] = []
        self._refusals: List[Dict[str, Any]] = []
        self._unlocks: List[Dict[str, Any]] = []

    # -- read-only view ---------------------------------------------------

    @property
    def receipts(self) -> Tuple[Receipt, ...]:
        return tuple(self._receipts)

    @property
    def locked_agents(self) -> Tuple[str, ...]:
        return tuple(name for name, book in self._books.items() if book.locked)

    def is_locked(self, agent: str) -> bool:
        book = self._books.get(agent)
        return book is not None and book.locked

    def lock_reason(self, agent: str) -> Optional[str]:
        book = self._books.get(agent)
        return book.locked_reason if book is not None else None

    @property
    def per_agent_trust(self) -> bool:
        """True only when each agent is charged in its own ledger.

        With one pooled account every agent's "trust" would be the same company
        number printed in each row -- a per-agent column that is not per agent.
        The renderer shows ``-`` instead, and this property is what makes that
        difference visible to a caller rather than an accident of formatting.
        """
        return self._factory is not None

    def trust_of(self, agent: str) -> Optional[float]:
        """This agent's trust, or ``None`` when the ledgers are pooled."""
        if not self.per_agent_trust:
            return None
        ledger = self._ledger_for(agent, create=False)
        if ledger is None:
            return None
        return float(getattr(ledger, "trust_score", 0.0))

    # -- the journal ------------------------------------------------------

    def record_transaction(self, tx: Transaction) -> Dict[str, Any]:
        """Book one entry and return the receipt as a dict.

        Raises:
            TypeError: ``tx`` is not a :class:`Transaction`. A duck-typed object
                would let a caller omit ``witness`` and get INVALID instead of an
                error, which reads like a judgement and is really a bug.
            AgentLocked: this agent has an open entry. The attempt is counted;
                nothing else about it is booked.
        """
        if not isinstance(tx, Transaction):
            raise TypeError(
                "tx must be a Transaction, got {!r}".format(type(tx).__name__)
            )

        book = self._book_for(tx.agent)
        if book.locked:
            # Counted, then refused. Recording a verdict here would be a lie: no
            # work was witnessed, because none was allowed to happen.
            book.refusals += 1
            self._refusals.append(
                {
                    "agent": tx.agent,
                    "intent": tx.intent,
                    "reason": book.locked_reason,
                }
            )
            raise AgentLocked(tx.agent, book.locked_reason or "unwitnessed entry")

        verdict = verdict_for(tx.check, tx.witness)
        penalty, charged_as, trust_after = self._settle(tx, verdict, book)
        # Computed before the receipt so the caller is told the book closed in
        # the same answer, instead of finding out on the next attempt.
        closes_book = self._closes_book(book, verdict)
        receipt = Receipt(
            seq=len(self._receipts) + 1,
            agent=tx.agent,
            intent=tx.intent,
            check=tx.check,
            witness=tx.witness,
            verdict=verdict,
            artefact=tx.artefact,
            cost=float(tx.cost),
            trust_after=trust_after,
            penalty=penalty,
            charged_as=charged_as,
            book_locked=closes_book,
        )
        self._receipts.append(receipt)
        self._apply(book, receipt)
        return receipt.as_dict()

    def unlock(self, agent: str, reason: str = "") -> None:
        """Close an open entry by hand, after a human witnessed the gap.

        Deliberately unreachable from :meth:`record_transaction`: an agent that
        could clear its own lock by filing a PASS would hold the very power the
        lock withholds. Unlocking does **not** erase the entry -- history is not
        a draft -- it only allows the next one.

        It does not reset ``fail_streak`` either, and that is a decision rather
        than an oversight. The operator unblocked the *book*, not the record: an
        agent whose first act after a manual unlock is another false completion
        has demonstrated the intervention did not change anything, and should be
        stopped immediately. An agent that does honest work clears the streak on
        its own, because a PASS resets it.
        """
        book = self._books.get(agent)
        if book is None:
            raise DoubleEntryError(
                "no book for {!r}; nothing to unlock".format(agent)
            )
        if not book.locked:
            raise DoubleEntryError("agent {!r} is not locked".format(agent))
        previous = book.locked_reason
        kind = book.lock_kind
        book.locked_reason = None
        book.lock_kind = None
        book.unlocked += 1
        self._unlocks.append(
            {
                "agent": agent,
                "reason": reason.strip(),
                "cleared": previous,
                "kind": kind,
            }
        )

    # -- the balance sheet ------------------------------------------------

    def get_balance_sheet(self) -> Dict[str, Any]:
        """Everything an operator needs to ask "who is actually working?".

        Counts and derived rates, never invented money: the figures here are
        operations and joules, and calling them currency would be the fake
        precision this package avoids elsewhere.
        """
        books = list(self._books.values())
        active = [book for book in books if not book.locked and book.judged]
        locked = [book.agent for book in books if book.locked]

        pass_count = sum(1 for r in self._receipts if r.verdict == PASS)
        fail_count = sum(1 for r in self._receipts if r.verdict == FAIL)
        invalid_count = sum(1 for r in self._receipts if r.verdict == INVALID)
        judged = pass_count + fail_count + invalid_count

        journal_energy = sum(r.cost for r in self._receipts)
        external_energy = (
            float(getattr(self._energy, "total_spent")) if self._energy is not None else None
        )
        reconciled = (
            True
            if external_energy is None
            else abs(external_energy - journal_energy) < _RECONCILE_EPSILON
        )

        trust = self._aggregate_trust(books)
        score = None if not judged else 100.0 * (pass_count / judged) * (trust / 100.0)

        return {
            "as_of": len(self._receipts),
            "agents": {
                "total": len(books),
                "active": len(active),
                "locked": locked,
            },
            "totals": {
                "pass": pass_count,
                "fail": fail_count,
                "invalid": invalid_count,
                "judged": judged,
                "claimed_success": sum(1 for r in self._receipts if r.check),
                "witnessed": sum(1 for r in self._receipts if r.witness is True),
                "ungrounded_passes": sum(
                    1 for r in self._receipts if r.verdict == PASS and not r.artefact
                ),
                "energy_spent": journal_energy,
                "refusals": len(self._refusals),
                "unlocks": len(self._unlocks),
            },
            "health": {
                "score": score,
                "state": self._aggregate_state(books),
                "trust": trust,
                "judged": judged,
            },
            "balance": {
                "verified_assets": sum(book.verified for book in books),
                "unverified_claims": sum(book.unwitnessed for book in books),
                "written_off": sum(book.refuted for book in books),
                "reconciled": reconciled,
                "journal_energy": journal_energy,
                "external_energy": external_energy,
            },
            "books": [book.as_dict() for book in self._sorted_books()],
        }

    def render_balance_sheet(self) -> str:
        """The sheet as plain text. No colour, no box drawing: it must log."""
        sheet = self.get_balance_sheet()
        totals = sheet["totals"]
        health = sheet["health"]
        balance = sheet["balance"]
        agents = sheet["agents"]
        width = 78

        lines: List[str] = []
        lines.append("=" * width)
        lines.append("DOUBLE-ENTRY BALANCE SHEET")
        lines.append("=" * width)
        lines.append(
            "  as of entry : {}".format(sheet["as_of"])
        )
        lines.append(
            "  agents      : {} total, {} active, {} locked".format(
                agents["total"], agents["active"], len(agents["locked"])
            )
        )
        if agents["locked"]:
            lines.append("  locked      : {}".format(", ".join(agents["locked"])))
        lines.append("")

        lines.append(
            "  {:<12} {:>8} {:>8} {:>11} {:>6} {:>7} {:>4}  {}".format(
                "agent", "verified", "refuted", "unwitnessed", "energy", "trust",
                "strk", "status",
            )
        )
        lines.append("  " + "-" * (width - 2))
        for book in sheet["books"]:
            trust = self.trust_of(book["agent"])
            lines.append(
                "  {:<12} {:>8} {:>8} {:>11} {:>6.1f} {:>7} {:>4}  {}".format(
                    book["agent"],
                    book["verified"],
                    book["refuted"],
                    book["unwitnessed"],
                    book["energy"],
                    "-" if trust is None else "{:.1f}".format(trust),
                    book["fail_streak"],
                    "LOCKED" if book["locked"] else (
                        "idle" if not book["judged"] else "active"
                    ),
                )
            )
        if not sheet["books"]:
            lines.append("  (no books yet)")
        lines.append("")
        # `strk` is the consecutive-FAIL counter, and it is printed because it is
        # the difference between "one bad afternoon" and "one more and I stop".
        if any(book["fail_streak"] for book in sheet["books"]):
            lines.append(
                "  strk = consecutive FAILs; a PASS clears it, {} stops the agent"
                .format(self._fail_limit)
            )
            lines.append("")
        locked = [b for b in sheet["books"] if b["locked"]]
        if locked:
            lines.append("  LOCKED BOOKS")
            lines.append("  " + "-" * (width - 2))
            for book in locked:
                lines.append("  {:<12} [{}]".format(
                    book["agent"], book["lock_kind"] or "unknown"))
                for chunk in _wrap(book["locked_reason"] or "", width - 17):
                    lines.append("  {:<15} {}".format("", chunk))
            lines.append("")

        lines.append("  BALANCE")
        lines.append("  " + "-" * (width - 2))
        lines.append(
            "  verified assets    : {:>4}   work that landed and was seen".format(
                balance["verified_assets"]
            )
        )
        lines.append(
            "  unverified claims  : {:>4}   claimed, never seen".format(
                balance["unverified_claims"]
            )
        )
        lines.append(
            "  written off        : {:>4}   claimed, and contradicted".format(
                balance["written_off"]
            )
        )
        reconciled = "yes" if balance["reconciled"] else "NO -- out-of-band charge"
        lines.append(
            "  reconciled to L1   : {:>4}   journal {:.1f} / account {}".format(
                reconciled,
                balance["journal_energy"],
                "-" if balance["external_energy"] is None
                else "{:.1f}".format(balance["external_energy"]),
            )
        )
        lines.append("")

        lines.append("  HEALTH")
        lines.append("  " + "-" * (width - 2))
        score = health["score"]
        lines.append(
            "  verdicts           : {} pass / {} fail / {} invalid".format(
                totals["pass"], totals["fail"], totals["invalid"]
            )
        )
        lines.append(
            "  trust (weakest)    : {:.1f}".format(health["trust"])
        )
        lines.append(
            "  penalty state      : {}".format(health["state"] or "-")
        )
        if score is None:
            # Deliberately not 100: with no judged entry there is no evidence of
            # health, and printing full marks for an empty book is the same lie
            # as calling an unrun check a PASS.
            lines.append("  score              : n/a (no evidence yet)")
        else:
            lines.append(
                "  score              : {:.1f} / 100  ({} judged)".format(
                    score, health["judged"]
                )
            )
        if totals["ungrounded_passes"]:
            lines.append(
                "  note               : {} pass(es) name no artefact; they are "
                "records, not evidence".format(totals["ungrounded_passes"])
            )
        lines.append("=" * width)
        return "\n".join(lines)

    # -- internals --------------------------------------------------------

    def _book_for(self, agent: str) -> _Book:
        book = self._books.get(agent)
        if book is None:
            book = _Book(agent=agent)
            self._books[agent] = book
        return book

    def _ledger_for(self, agent: str, *, create: bool) -> Any:
        if agent in self._ledgers:
            return self._ledgers[agent]
        if self._pooled is not None:
            return self._pooled
        if self._factory is not None:
            if not create:
                return None
            ledger = self._factory(agent)
            self._ledgers[agent] = ledger
            return ledger
        if not create:
            return None
        # One pooled account is still better than none, but it cannot answer
        # "which agent" -- which is why the balance sheet reports per-agent
        # trust as "-" in that mode instead of inventing a share.
        from .penalty import PenaltyLedger

        self._pooled = PenaltyLedger()
        return self._pooled

    def _settle(
        self, tx: Transaction, verdict: str, book: _Book
    ) -> Tuple[float, str, Optional[float]]:
        """Charge or credit, and report what it cost.

        Only verified work buys trust back. An honest failure is booked and
        earns nothing: paying for accurate bad news would make failing cheap,
        and an agent that can farm trust by reporting failure has no reason to
        succeed. INVALID earns nothing either, for the same reason it is not a
        PASS -- nothing was witnessed.
        """
        ledger = self._ledger_for(tx.agent, create=True)
        if ledger is None:  # pragma: no cover - create=True always yields one
            return 0.0, "", None

        if verdict == PASS:
            if tx.check is True:
                ledger.record_success()
            return 0.0, "", float(getattr(ledger, "trust_score", 0.0))

        if verdict == FAIL:
            if tx.check is True:
                violation = ledger.record_violation(LIAR_VIOLATION)
                kind = LIAR_VIOLATION
            else:
                violation = ledger.record_violation(
                    UNDERCLAIM_VIOLATION, UNDERCLAIM_WEIGHT
                )
                kind = UNDERCLAIM_VIOLATION
            return (
                float(getattr(violation, "penalty", 0.0)),
                kind,
                float(getattr(ledger, "trust_score", 0.0)),
            )

        # INVALID: nothing witnessed, nothing charged. The cost is the lock.
        return 0.0, "", float(getattr(ledger, "trust_score", 0.0))

    def _closes_book(self, book: _Book, verdict: str) -> bool:
        """Whether booking a ``verdict`` for this book closes it.

        Two ways in, and they are different findings:

        ``INVALID``       nobody looked, so the entry stays open and the book
                          cannot be reconciled. One is enough.
        ``FAIL``          the claim and the evidence contradict. The first is a
                          charge; a second *consecutive* one means the charge did
                          not change anything, and an organism that keeps paying
                          for the same lie without stopping has not learned.

        Why the second FAIL has to stop rather than just cost more: measured on
        this ledger before the rule existed, an agent could lie five times
        running (trust to 0, penalty CRITICAL) while an agent that merely
        omitted its witness was stopped on its second attempt. So the cheapest
        path to *staying operational* was to lie. A ledger that makes fraud the
        survivable strategy is worse than no ledger, because it manufactures the
        appearance of accountability while selecting for the opposite.
        """
        if verdict == INVALID:
            return True
        if verdict == FAIL:
            return book.fail_streak + 1 >= self._fail_limit
        return False

    def _apply(self, book: _Book, receipt: Receipt) -> None:
        book.energy += receipt.cost
        if receipt.verdict == PASS:
            if receipt.check is True:
                book.verified += 1
            else:
                book.honest_failure += 1
            # An honest failure is a PASS, so it clears the streak too: an agent
            # that accurately reports a problem must not creep toward a stop.
            book.fail_streak = 0
        elif receipt.verdict == FAIL:
            book.fail_streak += 1
            if receipt.check is True:
                book.refuted += 1
            else:
                book.under_claimed += 1
            if receipt.book_locked:
                book.locked_reason = (
                    "{} consecutive failed entries, the last ({}: {!r}) at entry "
                    "{}; the charge did not change the behaviour".format(
                        book.fail_streak,
                        receipt.charged_as or "FAIL",
                        receipt.intent,
                        receipt.seq,
                    )
                )
                book.lock_kind = "repeat_fail"
        else:
            book.unwitnessed += 1
            book.fail_streak = 0
            # An open entry cannot be closed, so the next one may not be opened.
            book.locked_reason = (
                "entry {} ({!r}) has no witness, so the book cannot be closed"
                .format(receipt.seq, receipt.intent)
            )
            book.lock_kind = "unwitnessed"

    def _aggregate_trust(self, books: List[_Book]) -> float:
        """The weakest book sets the ceiling, as everywhere else in this system.

        A company is not healthier than the employee you cannot trust. With one
        pooled account there is only one book, so the pooled number is the
        honest answer rather than an average of copies of itself.
        """
        if not self.per_agent_trust:
            if self._pooled is None:
                return 100.0  # nothing has been charged against anyone
            return float(getattr(self._pooled, "trust_score", 0.0))
        known = [t for t in (self.trust_of(b.agent) for b in books) if t is not None]
        if not known:
            return 100.0
        return min(known)

    def _aggregate_state(self, books: List[_Book]) -> Optional[str]:
        states: List[str] = []
        for book in books:
            ledger = self._ledger_for(book.agent, create=False)
            if ledger is None:
                continue
            state = getattr(ledger, "penalty_state", None)
            if isinstance(state, str):
                states.append(state)
        if not states:
            return None
        return max(states, key=lambda name: _PENALTY_ORDER.get(name, 0))

    def _sorted_books(self) -> List[_Book]:
        """Most productive first, then alphabetical: the sheet reads top-down."""
        return sorted(self._books.values(), key=lambda b: (-b.verified, b.agent))

    def __repr__(self) -> str:
        return "DoubleEntryLedger(entries={}, agents={}, locked={})".format(
            len(self._receipts), len(self._books), len(self.locked_agents)
        )


def _wrap(text: str, width: int) -> List[str]:
    """Wrap a lock reason to the report width.

    Hand-rolled rather than ``textwrap`` only because the report must stay
    ASCII-only and dependency-free; ``textwrap`` is stdlib and would be fine, but
    the rule keeps the rendered output predictable across platforms.
    """
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]
