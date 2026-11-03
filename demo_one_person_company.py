#!/usr/bin/env python3
"""Demo: a one-person company of three agents, one of which lies.

The setup is the smallest thing that can be called a company: a planner, a
coder, and a tester, each handed a job. Two of them do the work. One reports
success without having produced anything, which is the ordinary failure mode of
a delegated agent -- not malice, just a plausible sentence that nobody checked.

Why the witnesses here are real
-------------------------------
A demo that passes ``witness=True`` from a literal proves nothing: it shows that
a dictionary can hold a boolean. So every witness in this script is a sha256 of a
file that this script actually wrote, compared against the digest that was
*promised before the agent started work*. The liar's promise is recorded, the
file is never written, and the comparison fails.

The order matters and is the point. If the agent supplied the expected digest
afterwards, it would be choosing its own acceptance criterion and there would be
nothing to cross. Here the promise is taken first, from the plan, while the
outcome is still unknown.

What the balance sheet answers
------------------------------
"Who is actually working?" is a question a single-entry book cannot answer,
because it only contains what each agent said about itself. Booking the claim and
the evidence separately makes the answer fall out of arithmetic.

Run: python3 demo_one_person_company.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_metabolism import (  # noqa: E402
    AgentLocked,
    DoubleEntryLedger,
    EnergyAccount,
    PenaltyLedger,
    Transaction,
    digest_file,
)


def _section(title):
    print()
    print("#" * 78)
    print("# " + title)
    print("#" * 78)


def main():
    workspace = tempfile.mkdtemp(prefix="one-person-company-")
    energy = EnergyAccount(100.0)
    books = DoubleEntryLedger(
        penalty_factory=lambda agent: PenaltyLedger(),
        energy_account=energy,
    )

    _section("PHASE 0  The company")
    print("workspace : {}".format(workspace))
    print("energy    : {:.1f} units".format(energy.current_energy))
    print()
    print("  3 agents are hired: planner, coder, tester.")
    print("  Each must promise an artefact digest BEFORE it starts work, then")
    print("  present the file it actually wrote. The two sides are booked as")
    print("  separate lines, and only their agreement makes an entry a PASS.")

    # ------------------------------------------------------------------
    # The promises. Taken before any work happens, which is what gives them
    # any force at all.
    # ------------------------------------------------------------------
    plan_path = os.path.join(workspace, "PLAN.md")
    app_path = os.path.join(workspace, "app.py")
    report_path = os.path.join(workspace, "TEST_REPORT.md")

    # A promise is the digest the agent *will* produce. Here it is modelled as
    # the digest of the content the agent committed to writing; the comparison
    # below is then a real comparison of real bytes.
    promised_plan = _promise("PLAN.md: requirement list")
    promised_app = _promise("app.py: def add(a, b)")
    promised_report = _promise("TEST_REPORT.md: 4 passed")

    _section("PHASE 1  Work happens (or does not)")

    # -- planner: does the work -------------------------------------
    _write(plan_path, "PLAN.md: requirement list")
    energy.consume(3.0)
    planner = books.record_transaction(
        Transaction(
            agent="planner",
            intent="write the requirement list",
            check=True,
            witness=digest_file(plan_path) == promised_plan,
            artefact="PLAN.md",
            cost=3.0,
        )
    )
    _report("planner", planner)

    # -- coder: claims the work, writes nothing ----------------------
    # The file is never created. The agent's report is a sentence; the witness
    # is a file that does not exist, so it cannot match.
    energy.consume(9.0)
    coder = books.record_transaction(
        Transaction(
            agent="coder",
            intent="implement add(a, b) in app.py",
            check=True,
            witness=os.path.exists(app_path)
            and digest_file(app_path) == promised_app,
            artefact="app.py",
            cost=9.0,
        )
    )
    _report("coder", coder)
    print("      coder said : 'app.py written, add(a, b) implemented'")
    print("      witness    : {} does not exist".format(
        "app.py" if not os.path.exists(app_path) else "app.py (digest mismatch)"))

    # -- tester: does the work ---------------------------------------
    _write(report_path, "TEST_REPORT.md: 4 passed")
    energy.consume(4.0)
    tester = books.record_transaction(
        Transaction(
            agent="tester",
            intent="run the test suite and record the result",
            check=True,
            witness=digest_file(report_path) == promised_report,
            artefact="TEST_REPORT.md",
            cost=4.0,
        )
    )
    _report("tester", tester)

    print()
    print("  One more entry, from the tester: a flaky check that genuinely failed.")
    print("  It reports failure, and the failure is confirmed -- both sides agree,")
    print("  so the books balance. An accurate bad report is not an offence, and")
    print("  charging it would teach agents to hide failures.")
    print()
    energy.consume(2.0)
    honest = books.record_transaction(
        Transaction(
            agent="tester",
            intent="run the flaky integration check",
            check=False,          # the agent reports failure...
            witness=False,        # ...and the evidence confirms it
            artefact="TEST_REPORT.md",
            cost=2.0,
        )
    )
    print("  tester   {:<8} {}".format(honest["verdict"], honest["intent"]))
    print("           charge: {:.1f}  (accurate news is free)".format(honest["penalty"]))

    _section("PHASE 2  The coder does it again")
    print("One false completion is a charge: a single wrong report is a normal")
    print("day, and stopping on it would punish an honest mistake. Repeating it")
    print("is a different finding -- the charge changed nothing, so more charges")
    print("will not either.")
    print()
    second = books.record_transaction(
        Transaction(
            agent="coder",
            intent="add(a, b) implemented, tests passing, ready to ship",
            check=True,
            witness=os.path.exists(app_path)
            and digest_file(app_path) == promised_app,
            artefact="app.py",
            cost=9.0,
        )
    )
    energy.consume(9.0)
    # Captured here: the lock is cleared later on purpose, and a check written
    # against the final state would assert the opposite of what happened.
    locked_then = books.is_locked("coder")
    locked_kind = _book(books, "coder")["lock_kind"]
    print("  coder    {:<8} {}   (fail streak: 2)".format(
        second["verdict"], second["intent"]))
    print("           charged {:.1f} again".format(second["penalty"]))
    print("           book_locked = {}  <- the receipt says so immediately".format(
        second["book_locked"]))
    print()
    print("  Why this is a stop and not just a bigger fine. Measured on this")
    print("  ledger before the rule existed:")
    print()
    print("      lie          attempts=5 trust=0.0   locked=False")
    print("      unwitnessed  attempts=2 trust=100.0 locked=True")
    print()
    print("  An agent could lie five times running and stay operational, while")
    print("  one that merely omitted its witness was stopped on its second")
    print("  attempt. The cheapest way to keep working was to lie. A ledger that")
    print("  makes fraud survivable is worse than no ledger: it manufactures the")
    print("  appearance of accountability while selecting for the opposite.")
    print()

    print("  Every later operation is now refused:")
    refusal_seen = False
    try:
        books.record_transaction(
            Transaction(
                agent="coder",
                intent="implement add(a, b), third attempt",
                check=True,
                witness=True,
                artefact="app.py",
                cost=9.0,
            )
        )
    except AgentLocked as refusal:
        refusal_seen = True
        print("    AgentLocked: {}".format(refusal))
    print()
    print("  The attempt is counted, not judged. A refusal is not a failure:")
    print("  no work happened, so there is nothing to have an opinion about.")

    _section("PHASE 3  A human intervenes")
    print("Unlocking is out-of-band by design. An agent that could clear its own")
    print("lock by filing a PASS would hold exactly the power the lock withholds.")
    print()
    print("And the unlock does not forgive the streak: the operator unblocked")
    print("the *book*, not the record. An agent whose first act after a second")
    print("chance is another lie has shown the intervention changed nothing.")
    print()
    books.unlock("coder", reason="spoken to; app.py was never written")
    print("  coder    relocked on the next false claim:")
    relapsed = books.record_transaction(
        Transaction(
            agent="coder",
            intent="trust me, it works this time",
            check=True,
            witness=os.path.exists(app_path)
            and digest_file(app_path) == promised_app,
            artefact="app.py",
            cost=0.0,
        )
    )
    print("           {:<8} book_locked={}  kind={}".format(
        relapsed["verdict"], relapsed["book_locked"],
        _book(books, "coder")["lock_kind"]))
    print()
    print("  A second intervention, then the coder actually does the work:")
    books.unlock("coder", reason="pair-programming session")
    _write(app_path, "app.py: def add(a, b)")
    energy.consume(9.0)
    retry = books.record_transaction(
        Transaction(
            agent="coder",
            intent="implement add(a, b), with the file actually written",
            check=True,
            witness=digest_file(app_path) == promised_app,
            artefact="app.py",
            cost=9.0,
        )
    )
    _report("coder", retry)
    print("           the PASS cleared the streak: {}".format(
        _book(books, "coder")["fail_streak"]))

    # ------------------------------------------------------------------
    # The books
    # ------------------------------------------------------------------
    _section("PHASE 4  The balance sheet")
    sheet = books.get_balance_sheet()
    print(books.render_balance_sheet())

    _section("PHASE 5  Who is actually working")
    for book in sheet["books"]:
        verdict = _verdict(book)
        print("  {:<9} {:>8} verified   {:>6} refuted   {}".format(
            book["agent"], book["verified"], book["refuted"], verdict))
    print()
    print("  The planner and the tester produced files whose digests matched the")
    print("  promises taken before they started. The coder produced a sentence.")
    print("  No entry in this journal required reading the code -- only comparing")
    print("  what was promised with what exists.")

    _section("PHASE 6  The reconciliation")
    balance = sheet["balance"]
    print("  journal energy    : {:.1f}".format(balance["journal_energy"]))
    print("  account energy    : {:.1f}".format(balance["external_energy"] or 0.0))
    print("  reconciled        : {}".format(
        "yes" if balance["reconciled"] else "NO -- an unbooked charge"))
    print()
    print("  This check is what single-entry cannot do. Every unit of energy")
    print("  spent on this workspace had to appear in the journal, and the two")
    print("  totals agreeing is the whole discipline.")

    _section("PHASE 7  A charge that never went through the journal")
    print("A fault injected on purpose, to show the check firing. The company's")
    print("own books, as reported in phase 4, are sound; this line is not.")
    print()
    energy.consume(25.0)  # spent, but never booked
    caught = books.get_balance_sheet()["balance"]
    print("  journal energy    : {:.1f}".format(caught["journal_energy"]))
    print("  account energy    : {:.1f}".format(caught["external_energy"] or 0.0))
    print("  reconciled        : {}".format(
        "yes" if caught["reconciled"] else "NO -- the books do not balance"))
    print("  difference        : {:.1f} unaccounted".format(
        (caught["external_energy"] or 0.0) - caught["journal_energy"]))
    print()
    print("  A single-entry book would report 50.0 spent and look perfectly fine.")

    _section("PHASE 8  The other lock: no evidence at all")
    print("A scratch ledger, so the company's books stay clean. The two locks")
    print("are different findings and an operator has to know which one it faces:")
    print()
    print("  INVALID  nobody looked. The entry cannot be closed, so one is enough.")
    print("  FAIL x2  somebody looked and the claim was wrong, twice in a row.")
    print()
    scratch = DoubleEntryLedger(penalty_factory=lambda agent: PenaltyLedger())
    scratch.record_transaction(
        Transaction("mute", "I implemented it, trust me", check=True, witness=None)
    )
    mute_book = scratch.get_balance_sheet()["books"][0]
    print("  mute     one claim, no witness -> locked [{}]".format(mute_book["lock_kind"]))
    print("           {}".format(mute_book["locked_reason"]))
    print()
    print("  Neither can be cleared from inside. Both need a human, and the")
    print("  history stays on the books either way.")

    _section("SUMMARY")
    for name in ("planner", "coder", "tester"):
        book = sheet["books"][_index(sheet, name)]
        print("  {:<9} {}".format(name, _verdict(book)))
    print("                 {:>2} verified, {:>2} refuted, {:>2} unwitnessed".format(
        sum(b["verified"] for b in sheet["books"]),
        sum(b["refuted"] for b in sheet["books"]),
        sum(b["unwitnessed"] for b in sheet["books"]),
    ))
    print()
    print("  Nothing here required trusting any agent's report. The claim and the")
    print("  evidence were booked as separate lines, and the disagreement -- not")
    print("  a suspicion -- is what put the coder's name in the wrong column.")
    print()
    print("  Note what the coder's row still says: the false completion is not")
    print("  erased by the honest work that followed it. A ledger that forgot")
    print("  would let any agent buy back its history with one good afternoon.")
    print()

    # The demo asserts its own claim, like the Lean one does: a silent failure
    # here would be a demo that proves nothing and exits 0.
    checks = {
        "liar caught": coder["verdict"] == "FAIL",
        "liar charged": coder["charged_as"] == "FALSE_COMPLETION",
        "second failure closed the book": second["book_locked"] and locked_then,
        "lock kind named": locked_kind == "repeat_fail",
        "lock enforced": refusal_seen,
        "unlock does not forgive the streak": relapsed["book_locked"],
        "honest work clears the streak": _book(books, "coder")["fail_streak"] == 0,
        "unwitnessed claim locks on one": mute_book["lock_kind"] == "unwitnessed",
        "honest agents passed": planner["verdict"] == "PASS"
        and tester["verdict"] == "PASS",
        "honest failure balanced": honest["verdict"] == "PASS"
        and honest["check"] is False,
        "refusal not counted as failure": sheet["totals"]["refusals"] == 1,
        "books reconciled": sheet["balance"]["reconciled"],
        "out-of-band charge detected": not caught["reconciled"],
    }
    for label, ok in checks.items():
        print("  [{}] {}".format("ok" if ok else "!!", label))
    print()
    return 0 if all(checks.values()) else 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _promise(content: str) -> str:
    """The digest of the artefact an agent committed to producing.

    Hashed through a temporary file rather than ``hashlib.sha256(content)`` so
    the promise and the witness are computed the same way. A demo that promises
    one kind of digest and checks another would 'work' while proving nothing.
    """
    handle, path = tempfile.mkstemp()
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        return digest_file(path)
    finally:
        os.unlink(path)


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _report(agent: str, receipt):
    print("  {:<8} {:<8} {}".format(agent, receipt["verdict"], receipt["intent"]))
    if receipt["charged_as"]:
        print("           charged {:.1f} as {}".format(
            receipt["penalty"], receipt["charged_as"]))
    return receipt


def _verdict(book) -> str:
    if book["locked"]:
        return "LOCKED (open entry)"
    if book["refuted"]:
        return "lied ({} false completion)".format(book["refuted"])
    if book["unwitnessed"]:
        return "unwitnessed"
    if book["verified"]:
        return "working"
    return "idle"


def _book(books, agent: str) -> dict:
    """One agent's row, straight from the ledger."""
    for book in books.get_balance_sheet()["books"]:
        if book["agent"] == agent:
            return book
    raise KeyError(agent)


def _index(sheet, agent: str) -> int:
    for position, book in enumerate(sheet["books"]):
        if book["agent"] == agent:
            return position
    raise KeyError(agent)


if __name__ == "__main__":
    sys.exit(main())
