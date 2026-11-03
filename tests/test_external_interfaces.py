"""Tests for the two external interfaces and the gateway that fronts them.

Three groups:

* ``TestLLMProxy*`` -- the energy and beat potential difference. The core claim
  is that a slowed-down answer cannot be obtained without paying for it, and that
  a degraded answer is *labelled* rather than passed off as the full one.
* ``TestMembraneAgent*`` -- admission. The core claim is that the promise is
  registered before the tool runs, so an agent cannot set its acceptance
  criterion after seeing what it produced.
* ``TestUnifiedGateway`` -- the router sees scalars and nothing writable.

The membrane lives in the companion package ``harness-audit-sidecar``. It is
reached through ``sys.path`` when present and the integration tests skip when it
is not, so this suite keeps the package's ``dependencies = []`` promise. A small
in-test double pins the *contract* those tests rely on, so a change to either side
fails here rather than silently agreeing with itself.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    CONSERVE,
    EMERGENCY,
    GROW,
    SLOW,
    AgentWarrantError,
    ArtefactMismatchError,
    DualBeatScheduler,
    EnergyAccount,
    EnergyExhausted,
    LLMProxy,
    LedgerLockedError,
    MetabolicGateway,
    MembraneAgent,
    MissingPromiseError,
    PenaltyLedger,
    ToolOutcome,
)
from agent_metabolism.llm_proxy import LLM_RECORD_TYPE  # noqa: E402

# The sidecar is an adjacent checkout, not an installed dependency.
_SIDECAR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agent-harness-audit",
)
if os.path.isdir(_SIDECAR) and _SIDECAR not in sys.path:
    sys.path.insert(0, _SIDECAR)

try:
    from audit_sidecar.tools.ledger import AppendOnlyLedger
    from audit_sidecar.tools.membrane import MembraneGateway, MembraneRejectError
except Exception:  # pragma: no cover - exercised only where the sidecar is absent
    AppendOnlyLedger = None
    MembraneGateway = None
    MembraneRejectError = Exception

_HAS_SIDECAR = AppendOnlyLedger is not None

needs_sidecar = unittest.skipUnless(
    _HAS_SIDECAR, "harness-audit-sidecar not available"
)


# ---------------------------------------------------------------------------
# A contract double, so the SDK tests do not depend on the sidecar being present
# ---------------------------------------------------------------------------


class _RecordingMembrane:
    """The smallest object satisfying the protocol ``MembraneAgent`` requires.

    It records the *order* of calls, because the property under test is that
    registration happens before execution, not merely that it happens.
    """

    def __init__(self, refuse: bool = False) -> None:
        self.events = []
        self.registered = []
        self.refuse = refuse
        self.calls = []

    def register_digest(self, capability, digest, **kwargs):
        self.events.append(("register", capability, digest))
        self.registered.append((capability, digest))
        return digest

    def request_action_warrant(self, payload, capability):
        self.events.append(("warrant", capability, payload.get("digest")))
        if self.refuse:
            raise RuntimeError("refused by the double")
        return "warrant-token"

    def redeem(self, warrant):
        self.events.append(("redeem", warrant))
        return {"event": "redeem"}

    def append_call(self, tool, verdict, **extra):
        self.events.append(("record", tool, verdict))
        self.calls.append((tool, verdict, extra))
        return {"tool": tool, "verdict": verdict}


# ---------------------------------------------------------------------------
# LLMProxy -- L1 charge and L2 beat
# ---------------------------------------------------------------------------


class TestLLMProxyEnergy(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "audit.jsonl")

    def make(self, budget: float = 100.0, spend: float = 0.0):
        account = EnergyAccount(budget)
        if spend:
            account.consume(spend)
        scheduler = DualBeatScheduler(account)
        return account, scheduler

    def test_slow_beat_runs_when_the_account_is_full_and_the_task_is_hard(self):
        account, scheduler = self.make()
        proxy = LLMProxy(scheduler)
        result = proxy.generate("prove the invariant", 0.95)
        self.assertEqual(result.mode, SLOW)
        self.assertTrue(result.is_slow)
        self.assertFalse(result.degraded)
        self.assertGreater(result.tokens_used, 0)

    def test_slow_beat_refused_once_the_charge_cannot_fund_it(self):
        """The required case: not enough balance, no slow beat.

        The slow beat costs five times the fast one. At 34% the account is still
        ``GROW`` -- so the regime would permit expensive thinking -- but the
        charge cannot pay for it, and the scheduler degrades instead of
        overdrawing. That degeneration is recorded rather than hidden.
        """
        account, scheduler = self.make(spend=66.0)
        self.assertEqual(account.state, GROW)
        self.assertFalse(account.can_afford(scheduler.slow_cost))

        result = LLMProxy(scheduler).generate("prove the invariant", 0.95)
        self.assertEqual(result.mode, "fast")
        self.assertFalse(result.is_slow)
        self.assertTrue(result.degraded)
        self.assertEqual(result.degraded_reason, "insufficient_energy")
        self.assertFalse(result.answer_is_full)

    def test_conserving_regime_never_offers_the_slow_beat(self):
        """Below 30% the beat is refused by regime, not by price.

        The two refusals are different: one says "you cannot", the other says
        "you may not". Collapsing them would send an operator to the wrong lever.
        """
        account, scheduler = self.make(spend=75.0)
        self.assertEqual(account.state, CONSERVE)
        result = LLMProxy(scheduler).generate("prove the invariant", 0.95)
        self.assertEqual(result.mode, "fast")
        # Nothing was substituted: the slow beat was never on offer.
        self.assertFalse(result.degraded)

    def test_exhausted_account_never_reaches_the_model(self):
        account, scheduler = self.make(spend=95.0)
        proxy = LLMProxy(scheduler)
        before = proxy.calls
        with self.assertRaises(EnergyExhausted):
            proxy.generate("anything", 0.5)
        # The call counter is the cheap witness that the model was not asked.
        self.assertEqual(proxy.calls, before)

    def test_trust_bankruptcy_degrades_the_slow_beat_and_says_so(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, penalty_ledger=ledger)
        result = LLMProxy(scheduler).generate("prove it", 0.95)
        self.assertTrue(result.degraded)
        self.assertFalse(result.answer_is_full)
        self.assertEqual(result.degraded_reason, "penalty_critical")

    def test_a_degraded_answer_is_labelled_not_hidden(self):
        account, scheduler = self.make(spend=66.0)
        result = LLMProxy(scheduler).generate("hard task", 0.95)
        self.assertTrue(result.degraded)
        self.assertFalse(result.answer_is_full)
        self.assertEqual(result.mode, "fast")

    def test_no_ledger_is_reported_as_unlocked(self):
        account, scheduler = self.make()
        proxy = LLMProxy(scheduler)
        proxy.generate("hi", 0.1)
        status = proxy.lock_status()
        self.assertFalse(status["locked"])
        self.assertEqual(status["unlocked_calls"], 1)


class TestLLMProxyLocking(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "audit.jsonl")

    @needs_sidecar
    def test_every_call_is_appended_to_the_chain(self):
        ledger = AppendOnlyLedger(self.path, anchor_path=self.path + ".head")
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        proxy = LLMProxy(scheduler, ledger)

        proxy.generate("first", 0.1)
        proxy.generate("second", 0.9)

        entries = [e for e in ledger.entries() if e.get("record_type") == LLM_RECORD_TYPE]
        self.assertEqual(len(entries), 2)
        self.assertEqual([e["seq"] for e in entries], [1, 2])
        self.assertEqual(ledger.verify_chain()["status"], "PASS")

    @needs_sidecar
    def test_record_carries_tokens_latency_and_beat(self):
        ledger = AppendOnlyLedger(self.path, anchor_path=self.path + ".head")
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        result = LLMProxy(scheduler, ledger).generate("prove it", 0.95)

        record = result.record
        self.assertIsNotNone(record)
        self.assertEqual(record["tokens_used"], result.tokens_used)
        self.assertEqual(record["elapsed_ms"], result.latency_ms)
        self.assertEqual(record["beat"], result.beat)
        self.assertEqual(record["mode"], SLOW)
        self.assertIn("energy_before", record)
        self.assertIn("energy_after", record)

    @needs_sidecar
    def test_tampering_with_a_model_record_breaks_the_chain(self):
        ledger = AppendOnlyLedger(self.path, anchor_path=self.path + ".head")
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        proxy = LLMProxy(scheduler, ledger)
        proxy.generate("one", 0.1)
        proxy.generate("two", 0.1)

        with open(self.path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        import json

        entry = json.loads(lines[0])
        entry["tokens_used"] = 1  # pretend the call was cheap
        lines[0] = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        verdict = ledger.verify_chain()
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(verdict["first_bad_seq"], 1)

    @needs_sidecar
    def test_model_records_are_not_counted_as_verdict_history(self):
        """An LLM call carries a cost, not a judgement.

        Counting it as a PASS would fabricate a verdict nobody made; counting it
        as an unparseable line would report a healthy ledger as damaged.
        """
        from audit_sidecar.tools.eco_api import get_evolution_history

        ledger = AppendOnlyLedger(self.path, anchor_path=self.path + ".head")
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        LLMProxy(scheduler, ledger).generate("hi", 0.1)

        history = get_evolution_history(self.path)
        self.assertEqual(history["skipped_lines"], 0)
        self.assertEqual(history["counts"]["PASS"], 0)
        self.assertGreaterEqual(history["bookkeeping"], 1)

    @needs_sidecar
    def test_model_cost_makes_the_energy_gradient_answerable(self):
        from audit_sidecar.tools.eco_api import get_energy_gradient

        ledger = AppendOnlyLedger(self.path, anchor_path=self.path + ".head")
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        proxy = LLMProxy(scheduler, ledger)
        for _ in range(3):
            proxy.generate("a task of reasonable length", 0.1)

        gradient = get_energy_gradient(self.path)
        self.assertEqual(gradient["status"], "PASS")
        self.assertEqual(gradient["tokens"]["measured_calls"], 3)

    def test_a_broken_ledger_raises_instead_of_returning_silently(self):
        class Broken:
            def append(self, payload, record_type=None):
                raise OSError("disk gone")

        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        proxy = LLMProxy(scheduler, Broken())
        with self.assertRaises(LedgerLockedError):
            proxy.generate("hi", 0.1)

    def test_ledger_without_record_type_is_still_used(self):
        class Narrow:
            def __init__(self):
                self.rows = []

            def append(self, payload):
                self.rows.append(payload)
                return payload

        narrow = Narrow()
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        LLMProxy(scheduler, narrow).generate("hi", 0.1)
        self.assertEqual(len(narrow.rows), 1)
        self.assertEqual(narrow.rows[0]["event"], "llm_call")

    def test_wrong_ledger_type_is_rejected_at_construction(self):
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        with self.assertRaises(TypeError):
            LLMProxy(scheduler, ledger=object())


# ---------------------------------------------------------------------------
# MembraneAgent -- admission, promise first
# ---------------------------------------------------------------------------


class TestMembraneAgentPromise(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def artefact(self, name: str = "out.txt", body: str = "produced\n") -> str:
        return os.path.join(self.tmp.name, name)

    def digest(self, body: str) -> str:
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    def test_missing_expected_hash_is_refused_and_the_tool_never_runs(self):
        """The required case: no promise, no execution, no exception leniency."""
        ran = []

        def write_file(path, body):
            ran.append(True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        membrane = _RecordingMembrane()
        agent = MembraneAgent(membrane, tools={"write_file": write_file})

        with self.assertRaises(MissingPromiseError) as caught:
            agent.execute_tool_with_warrant(
                "write_file", {"path": self.artefact(), "body": "x\n"}, None
            )
        self.assertEqual(caught.exception.reason, "NO_EXPECTED_HASH")
        self.assertEqual(ran, [], "the tool must not run without a promise")
        self.assertFalse(os.path.exists(self.artefact()))

    def test_malformed_hash_is_treated_as_no_promise(self):
        agent = MembraneAgent(_RecordingMembrane(), tools={"t": lambda **k: None})
        with self.assertRaises(MissingPromiseError):
            agent.execute_tool_with_warrant("t", {}, "not-a-hash")

    def test_promise_is_registered_before_the_tool_runs(self):
        """Ordering is the potential difference, so it is asserted on order."""
        order = []

        def write_file(path, body):
            order.append("tool")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        membrane = _RecordingMembrane()
        original = membrane.register_digest

        def register_then_note(capability, digest, **kwargs):
            order.append("register")
            return original(capability, digest, **kwargs)

        membrane.register_digest = register_then_note
        agent = MembraneAgent(membrane, tools={"write_file": write_file})
        agent.execute_tool_with_warrant(
            "write_file", {"path": self.artefact(), "body": "produced\n"},
            self.digest("produced\n"),
        )
        self.assertEqual(order, ["register", "tool"])

    def test_kept_promise_returns_the_tool_outcome_and_a_pass_record(self):
        def write_file(path, body):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        membrane = _RecordingMembrane()
        agent = MembraneAgent(membrane, tools={"write_file": write_file})
        outcome = agent.execute_tool_with_warrant(
            "write_file", {"path": self.artefact(), "body": "produced\n"},
            self.digest("produced\n"),
        )
        self.assertIsInstance(outcome, ToolOutcome)
        self.assertEqual(
            [call[1] for call in membrane.calls][-1], "PASS"
        )

    def test_broken_promise_raises_and_records_fail(self):
        def write_file(path, body):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        membrane = _RecordingMembrane()
        agent = MembraneAgent(membrane, tools={"write_file": write_file})
        with self.assertRaises(ArtefactMismatchError) as caught:
            agent.execute_tool_with_warrant(
                "write_file", {"path": self.artefact(), "body": "something else\n"},
                self.digest("what I promised\n"),
            )
        self.assertEqual(caught.exception.reason, "ARTEFACT_MISMATCH")
        verdicts = [call[1] for call in membrane.calls]
        self.assertIn("FAIL", verdicts)

    def test_a_tool_that_writes_nothing_cannot_meet_a_promise(self):
        agent = MembraneAgent(_RecordingMembrane(), tools={"noop": lambda **k: None})
        with self.assertRaises(ArtefactMismatchError) as caught:
            agent.execute_tool_with_warrant("noop", {}, self.digest("anything"))
        self.assertEqual(caught.exception.reason, "ARTEFACT_MISSING")

    def test_unknown_tool_is_refused(self):
        agent = MembraneAgent(_RecordingMembrane(), tools={})
        with self.assertRaises(AgentWarrantError) as caught:
            agent.execute_tool_with_warrant("ghost", {}, self.digest("x"))
        self.assertEqual(caught.exception.reason, "UNKNOWN_TOOL")

    def test_refused_warrant_does_not_produce_a_pass(self):
        def write_file(path, body):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        membrane = _RecordingMembrane(refuse=True)
        agent = MembraneAgent(membrane, tools={"write_file": write_file})
        with self.assertRaises(AgentWarrantError):
            agent.execute_tool_with_warrant(
                "write_file", {"path": self.artefact(), "body": "produced\n"},
                self.digest("produced\n"),
            )
        self.assertNotIn("PASS", [call[1] for call in membrane.calls])

    def test_agent_without_a_membrane_is_refused_at_construction(self):
        """Nothing to cross means no interface, so it must not be constructible."""
        with self.assertRaises(ValueError):
            MembraneAgent(None)

    def test_membrane_must_expose_the_protocol(self):
        with self.assertRaises(TypeError):
            MembraneAgent(object())


@needs_sidecar
class TestMembraneAgentAgainstTheRealMembrane(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = os.path.join(self.tmp.name, "audit.jsonl")

    def make_agent(self, **kwargs):
        gateway = MembraneGateway(self.ledger_path)
        ran = []

        def write_file(path, body):
            ran.append(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        agent = MembraneAgent(gateway, name="solver", tools={"write_file": write_file}, **kwargs)
        return gateway, agent, ran

    def test_no_promise_is_stopped_by_the_membrane_and_logged(self):
        gateway, agent, ran = self.make_agent()
        target = os.path.join(self.tmp.name, "out.txt")
        with self.assertRaises(MissingPromiseError):
            agent.execute_tool_with_warrant("write_file", {"path": target, "body": "x\n"}, None)
        self.assertEqual(ran, [])
        self.assertEqual(gateway.verify()["status"], "PASS")
        denials = [e for e in gateway.ledger.entries() if e.get("verdict") == "FAIL"]
        self.assertEqual(len(denials), 1)

    def test_promise_kept_lands_a_pass_in_the_chain(self):
        gateway, agent, _ = self.make_agent()
        target = os.path.join(self.tmp.name, "out.txt")
        body = "the promised content\n"
        agent.execute_tool_with_warrant(
            "write_file", {"path": target, "body": body},
            "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        )
        verdicts = [e.get("verdict") for e in gateway.ledger.entries() if e.get("record_type") == "call"]
        self.assertEqual(verdicts, ["PASS"])

    def test_mismatch_is_caught_by_the_membranes_own_recomputation(self):
        gateway, agent, _ = self.make_agent()
        target = os.path.join(self.tmp.name, "out.txt")
        promised = "sha256:" + hashlib.sha256(b"what I promised\n").hexdigest()
        with self.assertRaises(ArtefactMismatchError):
            agent.execute_tool_with_warrant(
                "write_file", {"path": target, "body": "something else\n"}, promised
            )
        verdicts = [e.get("verdict") for e in gateway.ledger.entries() if e.get("record_type") == "call"]
        self.assertEqual(verdicts, ["FAIL"])

    def test_the_trail_is_tamper_evident(self):
        gateway, agent, _ = self.make_agent()
        target = os.path.join(self.tmp.name, "out.txt")
        body = "content\n"
        agent.execute_tool_with_warrant(
            "write_file", {"path": target, "body": body},
            "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        )
        with open(self.ledger_path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        import json

        entry = json.loads(lines[-1])
        entry["verdict"] = "PASS"
        entry["agent"] = "somebody-else"
        lines[-1] = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        with open(self.ledger_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        self.assertEqual(gateway.verify()["status"], "FAIL")


# ---------------------------------------------------------------------------
# Unified gateway
# ---------------------------------------------------------------------------


class TestUnifiedGateway(unittest.TestCase):
    def setUp(self) -> None:
        self.account = EnergyAccount(100.0)
        self.scheduler = DualBeatScheduler(self.account)

    def test_routing_view_reports_charge_and_headroom_only(self):
        view = MetabolicGateway(scheduler=self.scheduler).routing_view()
        self.assertEqual(set(view), {"L1_Energy", "L3_Regime", "heavy_work_allowed"})
        self.assertTrue(view["heavy_work_allowed"])
        self.assertEqual(view["L3_Regime"]["current"], GROW)

    def test_heavy_work_is_disallowed_once_the_charge_falls(self):
        self.account.consume(40.0)
        view = MetabolicGateway(scheduler=self.scheduler).routing_view()
        self.assertEqual(view["L3_Regime"]["current"], CONSERVE)
        self.assertFalse(view["heavy_work_allowed"])

    def test_routing_view_carries_no_writable_or_reachable_object(self):
        """Everything must be a scalar or a dict of scalars."""
        view = MetabolicGateway(scheduler=self.scheduler).routing_view()

        def scalars_only(value):
            if isinstance(value, dict):
                return all(scalars_only(item) for item in value.values())
            return isinstance(value, (str, int, float, bool, type(None)))

        self.assertTrue(scalars_only(view))
        # No route back to the account from the view.
        for value in view.values():
            self.assertNotIsInstance(value, EnergyAccount)

    def test_gateway_has_no_mutator_for_the_ledger(self):
        gateway = MetabolicGateway(scheduler=self.scheduler)
        for name in dir(gateway):
            self.assertFalse(
                name in ("consume", "spend", "append", "append_call"),
                "gateway must not expose {}()".format(name),
            )

    def test_external_block_reports_both_interfaces(self):
        proxy = LLMProxy(self.scheduler)
        status = MetabolicGateway(
            scheduler=self.scheduler, llm_proxy=proxy
        ).get_current_laws_status()
        self.assertTrue(status["External"]["llm_proxy"]["attached"])
        self.assertFalse(status["External"]["llm_proxy"]["locked"])

    def test_missing_external_interfaces_are_reported_as_missing(self):
        external = MetabolicGateway(
            scheduler=self.scheduler
        ).get_current_laws_status()["External"]
        self.assertFalse(external["llm_proxy"]["attached"])
        self.assertEqual(external["agents"], [])

    def test_llm_proxy_without_lock_status_is_rejected(self):
        with self.assertRaises(TypeError):
            MetabolicGateway(scheduler=self.scheduler, llm_proxy=object())

    def test_emergency_regime_disallows_heavy_work(self):
        self.account.consume(80.0)
        view = MetabolicGateway(scheduler=self.scheduler).routing_view()
        self.assertEqual(view["L3_Regime"]["current"], EMERGENCY)
        self.assertFalse(view["heavy_work_allowed"])

    def test_no_account_leaves_the_router_unmeasured_rather_than_cheerful(self):
        view = MetabolicGateway().routing_view()
        self.assertFalse(view["L1_Energy"]["measured"])
        self.assertIsNone(view["L1_Energy"]["current"])
        self.assertFalse(view["heavy_work_allowed"])


if __name__ == "__main__":
    unittest.main()
