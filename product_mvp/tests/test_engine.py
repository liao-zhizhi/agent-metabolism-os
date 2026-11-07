"""Engine-level tests -- no HTTP, no third-party dependency.

Run from the repository's ``product_mvp/``::

    python3 -m unittest discover -s tests -v

These are the claims the dashboard makes, checked:
  * a false completion is a FAIL, charged, and it moves trust;
  * an honest failure is a PASS, because accurate bad news is not a lie;
  * no witness is INVALID and locks the book (nobody looked);
  * three consecutive FAILs quarantine the agent, and the fourth is refused;
  * the audit chain recomputes, and breaks if a single field is edited.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cel_engine import (  # noqa: E402
    REFUSED,
    CelGateway,
    UnwitnessedClaim,
    VerifyError,
    _Refused,
)

#: Shape-valid digest for calls that exercise the verdict, not the digest.
#: This layer validates form; it never opens a file, and a fixture that implied
#: otherwise would paper over the boundary ``WitnessHashLimits`` documents.
_H = "sha256:" + "b" * 64

_TRACE = [0]


def _trace() -> str:
    _TRACE[0] += 1
    return "engine-trace-{}".format(_TRACE[0])


class LayoutTests(unittest.TestCase):
    """The service must find the core library in either checkout layout.

    ``product_mvp`` exists both as a sibling of ``agent-metabolism-os`` and as a
    directory inside it. A hard-coded relative path works in exactly one, and
    the failure mode is an ImportError on someone else's machine.
    """

    def test_the_core_is_found_and_is_the_real_library(self) -> None:
        import cel_engine

        root = cel_engine._CORE_ROOT
        self.assertIsNotNone(root)
        self.assertTrue((root / "agent_metabolism" / "double_entry.py").is_file())

    def test_an_override_that_is_not_the_library_refuses_to_start(self) -> None:
        import cel_engine

        saved = os.environ.get("CEL_CORE_PATH")
        os.environ["CEL_CORE_PATH"] = str(Path(__file__).resolve().parent)
        try:
            with self.assertRaises(SystemExit):
                cel_engine.core_root()
        finally:
            if saved is None:
                os.environ.pop("CEL_CORE_PATH", None)
            else:
                os.environ["CEL_CORE_PATH"] = saved

    def test_a_valid_override_is_used(self) -> None:
        import cel_engine

        saved = os.environ.get("CEL_CORE_PATH")
        os.environ["CEL_CORE_PATH"] = str(cel_engine._CORE_ROOT)
        try:
            self.assertEqual(cel_engine.core_root(), cel_engine._CORE_ROOT)
        finally:
            if saved is None:
                os.environ.pop("CEL_CORE_PATH", None)
            else:
                os.environ["CEL_CORE_PATH"] = saved


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = CelGateway(fail_limit=3)

    def test_verified_work_is_a_pass(self) -> None:
        out = self.gw.verify(
            agent_id="robot_arm_01", intent="seal vial", check=True, witness=True, witness_hash=_H, trace_id=_trace()
        )
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["trust_score"], 100.0)

    def test_honest_failure_is_a_pass(self) -> None:
        out = self.gw.verify(
            agent_id="robot_arm_01", intent="grasp vial", check=False, witness=False
        )
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["trust_score"], 100.0)
        # Accurate bad news is booked and earns nothing -- not a 2.0 credit.
        self.assertEqual(out["penalty"], 0.0)

    def test_false_completion_is_a_fail_and_costs_trust(self) -> None:
        out = self.gw.verify(
            agent_id="robot_arm_01",
            intent="write to database",
            check=True,
            witness=False,
        )
        self.assertEqual(out["verdict"], "FAIL")
        self.assertEqual(out["charged_as"], "FALSE_COMPLETION")
        self.assertEqual(out["penalty"], 50.0)
        self.assertEqual(out["trust_score"], 75.0)  # 100 - 50 * 0.5
        self.assertEqual(out["penalty_state"], "CRITICAL")
        self.assertTrue(out["inflamed"])

    def test_under_claim_is_a_fail_but_cheaper(self) -> None:
        out = self.gw.verify(
            agent_id="nav_stack_02", intent="battery swap", check=False, witness=True, witness_hash=_H, trace_id=_trace()
        )
        self.assertEqual(out["verdict"], "FAIL")
        self.assertEqual(out["charged_as"], "FALSE_REPORT")
        self.assertLess(out["penalty"], 50.0)

    def test_no_witness_is_invalid_not_a_fail(self) -> None:
        out = self.gw.verify(
            agent_id="nav_stack_02", intent="corridor cleared", check=True, witness=None
        )
        self.assertEqual(out["verdict"], "INVALID")
        # One INVALID closes the book outright: nothing was witnessed.
        self.assertTrue(out["locked"])

    def test_rejects_non_boolean_check(self) -> None:
        with self.assertRaises(VerifyError):
            self.gw.verify(
                agent_id="a", intent="b", check="yes", witness=False  # type: ignore[arg-type]
            )

    def test_rejects_empty_intent(self) -> None:
        with self.assertRaises(VerifyError):
            self.gw.verify(agent_id="a", intent="   ", check=True, witness=True, witness_hash=_H, trace_id=_trace())


class QuarantineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = CelGateway(fail_limit=3)

    def test_three_consecutive_fails_lock_the_book(self) -> None:
        for _ in range(2):
            out = self.gw.verify(
                agent_id="liar", intent="x", check=True, witness=False
            )
            self.assertFalse(out["locked"])

        third = self.gw.verify(agent_id="liar", intent="x", check=True, witness=False)
        self.assertTrue(third["locked"])
        self.assertEqual(third["trust_score"], 25.0)

    def test_fourth_attempt_is_refused_and_still_logged(self) -> None:
        for _ in range(3):
            self.gw.verify(agent_id="liar", intent="x", check=True, witness=False)

        with self.assertRaises(_Refused) as caught:
            self.gw.verify(agent_id="liar", intent="x", check=True, witness=False)

        body = self.gw.settle_refusal(caught.exception)
        self.assertEqual(body["verdict"], REFUSED)
        # The refusal is on the chain, because "someone tried again" is evidence.
        ledger = self.gw.ledger()
        self.assertEqual(ledger["entries"][-1]["verdict"], REFUSED)

    def test_a_pass_resets_the_streak(self) -> None:
        self.gw.verify(agent_id="a", intent="x", check=True, witness=False)
        self.gw.verify(agent_id="a", intent="y", check=True, witness=False)
        self.gw.verify(agent_id="a", intent="z", check=True, witness=True, witness_hash=_H, trace_id=_trace())  # honest pass
        out = self.gw.verify(agent_id="a", intent="w", check=True, witness=False)
        self.assertFalse(out["locked"])

    def test_unlock_requires_a_locked_book(self) -> None:
        self.gw.verify(agent_id="a", intent="x", check=True, witness=True, witness_hash=_H, trace_id=_trace())
        with self.assertRaises(VerifyError):
            self.gw.unlock("a")

    def test_unlock_restores_access(self) -> None:
        for _ in range(3):
            self.gw.verify(agent_id="a", intent="x", check=True, witness=False)
        self.gw.unlock("a", "operator reviewed the log")
        out = self.gw.verify(agent_id="a", intent="y", check=True, witness=True, witness_hash=_H, trace_id=_trace())
        self.assertEqual(out["verdict"], "PASS")
        self.assertFalse(out["locked"])


class StatsTests(unittest.TestCase):
    def test_stats_are_derived_from_entries(self) -> None:
        gw = CelGateway(fail_limit=3)
        gw.verify(agent_id="a", intent="x", check=True, witness=False)
        gw.verify(agent_id="b", intent="y", check=True, witness=True, witness_hash=_H, trace_id=_trace())

        stats = gw.stats()
        self.assertEqual(stats["intercepted_false_completions"], 1)
        self.assertEqual(stats["verified_passes"], 1)
        self.assertEqual(stats["agents_total"], 2)
        self.assertEqual(stats["inflamed_agents"], 1)
        # (75.0 + 100.0) / 2
        self.assertEqual(stats["avg_trust_score"], 87.5)
        self.assertEqual(stats["health_state"], "CRITICAL")

    def test_empty_gateway_is_healthy_and_undeclared(self) -> None:
        stats = CelGateway().stats()
        self.assertEqual(stats["total_entries"], 0)
        self.assertEqual(stats["avg_trust_score"], 100.0)
        self.assertEqual(stats["health_state"], "HEALTHY")
        self.assertEqual(stats["inflamed_agents"], 0)


class ChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = CelGateway(fail_limit=3)
        for i in range(5):
            self.gw.verify(
                agent_id="agent_{}".format(i % 2),
                intent="task {}".format(i),
                check=bool(i % 3),
                witness=False,
            )

    def test_chain_verifies(self) -> None:
        self.assertTrue(self.gw.verify_chain())
        self.assertTrue(self.gw.ledger()["chain_ok"])

    def test_chain_detects_an_edited_entry(self) -> None:
        self.gw._entries[2]["intent"] = "a different story"
        self.assertFalse(self.gw.verify_chain())

    def test_chain_links_are_contiguous(self) -> None:
        entries = self.gw.ledger(limit=10)["entries"]
        for prev, cur in zip(entries, entries[1:]):
            self.assertEqual(cur["prev_hash"], prev["hash"])

    def test_ledger_limit_is_bounded(self) -> None:
        self.assertEqual(len(self.gw.ledger(limit=3)["entries"]), 3)
        self.assertLessEqual(len(self.gw.ledger(limit=10_000)["entries"]), 500)

    def test_seq_is_monotonic_across_refusals_and_unlocks(self) -> None:
        """The feed polls ``seq > lastSeq``, so a reused number loses a line.

        A refusal and an unlock are not judged entries, so the core's own
        receipt counter does not advance for them; the chain must not borrow it.
        """
        gw = CelGateway(fail_limit=2)
        for _ in range(2):
            gw.verify(agent_id="a", intent="x", check=True, witness=False)
        with self.assertRaises(_Refused):
            gw.verify(agent_id="a", intent="x", check=True, witness=False)
        gw.unlock("a")
        gw.verify(agent_id="a", intent="y", check=True, witness=True, witness_hash=_H, trace_id=_trace())

        seqs = [e["seq"] for e in gw.ledger(limit=20)["entries"]]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertTrue(gw.verify_chain())


class WitnessHashTests(unittest.TestCase):
    """The door: a success claim must carry a digest the cloud can hold onto.

    The old behaviour hashed ``artefact`` with ``os.path.isfile`` on the
    *server's* disk. For a remote client that path can never exist, so the
    branch never fired -- a check that read as coverage and could not run. These
    tests pin the replacement, including the case that is deliberately allowed.
    """

    def setUp(self) -> None:
        self.gw = CelGateway(fail_limit=3)

    def test_a_pass_needs_a_digest(self) -> None:
        with self.assertRaises(UnwitnessedClaim):
            self.gw.verify(agent_id="a", intent="x", check=True, witness=True)
        # Refused at the door: no entry, no trust spent.
        self.assertEqual(self.gw.ledger()["total_entries"], 0)

    def test_a_pass_needs_a_trace_id(self) -> None:
        with self.assertRaises(UnwitnessedClaim):
            self.gw.verify(
                agent_id="a", intent="x", check=True, witness=True, witness_hash=_H
            )
        self.assertEqual(self.gw.ledger()["total_entries"], 0)

    def test_free_text_is_not_a_digest(self) -> None:
        for bogus in ("yes", "sha256:", "a" * 63, "a" * 65, "z" * 64, "", "0x" + "a" * 64):
            with self.subTest(bogus=bogus):
                with self.assertRaises(VerifyError) as caught:
                    self.gw.verify(
                        agent_id="a",
                        intent="x",
                        check=True,
                        witness=True,
                        witness_hash=bogus,
                        trace_id=_trace(),
                    )
                self.assertEqual(caught.exception.status_code, 422)

    def test_bare_hex_and_prefixed_form_are_both_accepted(self) -> None:
        bare = "c" * 64
        out = self.gw.verify(
            agent_id="a", intent="x", check=True, witness=True,
            witness_hash=bare, trace_id=_trace(),
        )
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["witness_hash"], bare)

        prefixed = self.gw.verify(
            agent_id="a", intent="y", check=True, witness=True,
            witness_hash="sha256:" + "d" * 64, trace_id=_trace(),
        )
        self.assertEqual(prefixed["witness_hash"], "d" * 64)

    def test_an_honest_failure_needs_no_digest(self) -> None:
        """A negative result has nothing to hash.

        Requiring one here would make the only affordable strategy "never report
        a failure", which is the failure mode the whole design round removed.
        """
        out = self.gw.verify(
            agent_id="a", intent="assay failed", check=False, witness=False
        )
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["penalty"], 0.0)
        self.assertFalse(out["grounded"])

    def test_a_replayed_trace_id_is_refused(self) -> None:
        trace = _trace()
        self.gw.verify(
            agent_id="a", intent="x", check=True, witness=True,
            witness_hash=_H, trace_id=trace,
        )
        with self.assertRaises(VerifyError) as caught:
            self.gw.verify(
                agent_id="a", intent="x again", check=True, witness=True,
                witness_hash=_H, trace_id=trace,
            )
        self.assertEqual(caught.exception.status_code, 409)
        # The replay booked nothing: one action, one entry.
        self.assertEqual(self.gw.ledger()["total_entries"], 1)

    def test_the_digest_reaches_the_chain(self) -> None:
        trace = _trace()
        self.gw.verify(
            agent_id="a", intent="x", check=True, witness=True,
            witness_hash=_H, trace_id=trace,
        )
        entry = self.gw.ledger()["entries"][-1]
        self.assertEqual(entry["witness_hash"], _H.replace("sha256:", ""))
        self.assertEqual(entry["trace_id"], trace)
        self.assertTrue(self.gw.verify_chain())

    def test_the_server_never_reads_the_client_path(self) -> None:
        """``artefact`` is a pointer, recorded verbatim, and never opened.

        A path that exists on this machine proves nothing about a remote client,
        so opening it would only manufacture confidence.
        """
        out = self.gw.verify(
            agent_id="a", intent="x", check=True, witness=True,
            witness_hash=_H, trace_id=_trace(), artefact=str(Path(__file__)),
        )
        self.assertEqual(out["artefact"], str(Path(__file__)))
        self.assertNotIn("#", out["artefact"])

    def test_refusals_are_counted_but_not_chained(self) -> None:
        for _ in range(3):
            with self.assertRaises(UnwitnessedClaim):
                self.gw.verify(agent_id="a", intent="x", check=True, witness=True)
        stats = self.gw.stats()
        self.assertEqual(stats["rejected_unwitnessed"], 3)
        self.assertEqual(stats["total_entries"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
