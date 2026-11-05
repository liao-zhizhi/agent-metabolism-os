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

from cel_engine import REFUSED, CelGateway, VerifyError, _Refused  # noqa: E402


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
            agent_id="robot_arm_01", intent="seal vial", check=True, witness=True
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
            agent_id="nav_stack_02", intent="battery swap", check=False, witness=True
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
            self.gw.verify(agent_id="a", intent="   ", check=True, witness=True)


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
        self.gw.verify(agent_id="a", intent="z", check=True, witness=True)  # honest pass
        out = self.gw.verify(agent_id="a", intent="w", check=True, witness=False)
        self.assertFalse(out["locked"])

    def test_unlock_requires_a_locked_book(self) -> None:
        self.gw.verify(agent_id="a", intent="x", check=True, witness=True)
        with self.assertRaises(VerifyError):
            self.gw.unlock("a")

    def test_unlock_restores_access(self) -> None:
        for _ in range(3):
            self.gw.verify(agent_id="a", intent="x", check=True, witness=False)
        self.gw.unlock("a", "operator reviewed the log")
        out = self.gw.verify(agent_id="a", intent="y", check=True, witness=True)
        self.assertEqual(out["verdict"], "PASS")
        self.assertFalse(out["locked"])


class StatsTests(unittest.TestCase):
    def test_stats_are_derived_from_entries(self) -> None:
        gw = CelGateway(fail_limit=3)
        gw.verify(agent_id="a", intent="x", check=True, witness=False)
        gw.verify(agent_id="b", intent="y", check=True, witness=True)

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
        gw.verify(agent_id="a", intent="y", check=True, witness=True)

        seqs = [e["seq"] for e in gw.ledger(limit=20)["entries"]]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertTrue(gw.verify_chain())


class ArtefactTests(unittest.TestCase):
    def test_a_real_file_is_hashed_into_the_entry(self) -> None:
        gw = CelGateway()
        target = Path(__file__)  # any readable file will do
        out = gw.verify(
            agent_id="a",
            intent="verify a file",
            check=True,
            witness=True,
            artefact=str(target),
        )
        self.assertIn("sha256:", out["artefact"])
        self.assertTrue(out["grounded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
