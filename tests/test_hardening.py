"""Regression tests for the three defects the final ecosystem audit found.

Each class here exists because the corresponding behaviour was *wrong* in a way
the existing suite did not catch, and was found by running an adversarial agent
against the system rather than by reading the code:

* F1 -- a blocked request was recorded as ``FAIL``, so the immune layer counted
  the defence working as damage and inflamed itself.
* F3 -- the energy account never learned which witness tier a call used, so the
  adaptive check was free and escalation cost nothing.
* F4 -- a ``CRITICAL`` penalty ledger was a computation with no enforcement
  point, and its ``.state`` attribute collided with the immune layer's, so a
  duck-typed brake read "CRITICAL" as "not INFLAMED".

The point of pinning them here is that all three failed *quietly*: nothing
raised, nothing logged an error, and the system reported itself healthy. A test
that merely asserts a return value would not have caught any of them, so these
assert on the thing that actually went wrong -- what entered the damage count,
what was charged, and which object the gate read.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    ADAPTIVE_SURCHARGE,
    TIER_ADAPTIVE,
    TIER_INNATE,
    DualBeatScheduler,
    EnergyAccount,
    LLMProxy,
    MembraneAgent,
    MissingPromiseError,
    PenaltyLedger,
)

_SIDECAR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agent-harness-audit",
)
if os.path.isdir(_SIDECAR) and _SIDECAR not in sys.path:
    sys.path.insert(0, _SIDECAR)

try:
    from audit_sidecar.immune_state import (
        ImmuneState,
        STATE_INFLAMED,
        STATE_NORMAL,
        TIER_ADAPTIVE as SIDECAR_TIER_ADAPTIVE,
        TIER_INNATE as SIDECAR_TIER_INNATE,
    )
    from audit_sidecar.tools.membrane import MembraneGateway, MembraneRejectError
    from audit_sidecar.verdict import Verdict

    _HAVE_SIDECAR = True
except ImportError:  # pragma: no cover - the package ships without the auditor
    _HAVE_SIDECAR = False


def _needs_sidecar(test):
    return unittest.skipUnless(_HAVE_SIDECAR, "harness-audit-sidecar not installed")(test)


class _CallOnlyMembrane:
    """A membrane double with the narrow contract: ``append_call`` only.

    Used to prove the fallback path is safe. A membrane that does not offer
    ``append_denial`` must not be able to turn a refusal into damage just by
    being older.
    """

    def __init__(self) -> None:
        self.calls = []

    def register_digest(self, capability, digest, **kwargs):
        return digest

    def request_action_warrant(self, payload, capability):
        return "token"

    def append_call(self, tool, verdict, **extra):
        self.calls.append((tool, verdict, extra))
        return {"tool": tool, "verdict": verdict}


class TestARefusalIsNotAFailure(unittest.TestCase):
    """F1. A blocked request must not enter the immune layer's damage count."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "trace.jsonl")

    @_needs_sidecar
    def test_a_refusal_is_a_deny_record_with_no_verdict(self):
        gateway = MembraneGateway(self.path)
        agent = MembraneAgent(gateway, name="liar", tools={"t": lambda **k: None})
        with self.assertRaises(MissingPromiseError):
            agent.execute_tool_with_warrant("t", {}, None)

        records = gateway.ledger.entries()
        denies = [r for r in records if r.get("record_type") == "deny"]
        self.assertEqual(len(denies), 1)
        self.assertEqual(denies[0]["outcome"], "DENIED")
        self.assertEqual(denies[0]["reason"], "NO_EXPECTED_HASH")
        # A deny carries no verdict at all: none of the four lattice values
        # describes "nothing ran because the gate held".
        self.assertNotIn("verdict", denies[0])
        # And nothing was written as a call, so there is nothing for a verdict
        # counter to pick up.
        self.assertEqual([r for r in records if r.get("record_type") == "call"], [])

    @_needs_sidecar
    def test_five_refusals_leave_the_immune_system_calm(self):
        """The exact failure: five blocked requests used to mean INFLAMED."""
        gateway = MembraneGateway(self.path)
        immune = ImmuneState(fail_threshold=2, invalid_threshold=1)
        agent = MembraneAgent(gateway, name="liar", tools={"t": lambda **k: None})

        for _ in range(5):
            with self.assertRaises(MissingPromiseError):
                agent.execute_tool_with_warrant("t", {}, None)
            # Only genuine verdicts advance the state. A deny is not one.
            latest = list(gateway.ledger.entries())[-1]
            if latest.get("record_type") == "call":
                immune.record_verdict(Verdict(latest["verdict"]))

        self.assertEqual(immune.state, STATE_NORMAL)
        self.assertEqual(immune.danger_count, 0)

    @_needs_sidecar
    def test_a_post_execution_refusal_is_still_damage(self):
        """The distinction is *when*, not *whether*, the request was refused.

        Here the tool runs and writes, and the membrane then refuses the
        warrant. The effect landed unsanctioned, so this must count -- otherwise
        the fix would have created the opposite hole.
        """
        class RefusingMembrane:
            def register_digest(self, capability, digest, **kwargs):
                return digest

            def request_action_warrant(self, payload, capability):
                raise MembraneRejectError("BRAKE_TRIPPED", "closed")

            def append_call(self, tool, verdict, **extra):
                self.calls.append((tool, verdict, extra))
                return {}

        membrane = RefusingMembrane()
        membrane.calls = []
        target = os.path.join(self.tmp.name, "written.txt")

        def write_empty(path):
            with open(path, "wb") as handle:
                handle.write(b"")
            return path

        agent = MembraneAgent(membrane, tools={"w": write_empty})
        empty_digest = "sha256:" + __import__("hashlib").sha256(b"").hexdigest()
        with self.assertRaises(Exception):
            agent.execute_tool_with_warrant("w", {"path": target}, empty_digest)
        self.assertTrue(os.path.exists(target), "the tool did write, so it ran")
        self.assertEqual(membrane.calls[0][1], "FAIL")

    @_needs_sidecar
    def test_an_old_membrane_without_append_denial_does_not_inflame(self):
        """The fallback must use NOT RUN, which the immune layer ignores.

        Using INVALID here would have been worse than the original bug: INVALID
        inflames after a *single* occurrence.
        """
        membrane = _CallOnlyMembrane()
        agent = MembraneAgent(membrane, tools={"t": lambda **k: None})
        with self.assertRaises(MissingPromiseError):
            agent.execute_tool_with_warrant("t", {}, None)

        tool, verdict, extra = membrane.calls[0]
        self.assertEqual(verdict, "NOT RUN")
        self.assertEqual(extra.get("outcome"), "DENIED")

        immune = ImmuneState(fail_threshold=2, invalid_threshold=1)
        for _ in range(5):
            immune.record_verdict(Verdict("NOT RUN"))
        self.assertEqual(immune.state, STATE_NORMAL)


class TestBPenaltyClosesTheWriteGate(unittest.TestCase):
    """F4. A CRITICAL ledger must deny writes even when the brake is calm."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.artefact = os.path.join(self.tmp.name, "config.json")
        with open(self.artefact, "w", encoding="utf-8") as handle:
            handle.write("{}\n")

    def _critical_ledger(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(ledger.penalty_state, "CRITICAL")
        return ledger

    @_needs_sidecar
    def test_critical_penalty_refuses_a_write_with_a_calm_brake(self):
        gateway = MembraneGateway(
            os.path.join(self.tmp.name, "a.jsonl"),
            immune_state=ImmuneState(),
            penalty_ledger=self._critical_ledger(),
        )
        gateway.register_artefact("write", self.artefact)
        with self.assertRaises(MembraneRejectError) as caught:
            gateway.request_action_warrant(
                {"kind": "file", "path": self.artefact, "digest": gateway._digest_of_file(self.artefact)},
                "write",
            )
        self.assertEqual(caught.exception.reason, "PENALTY_CRITICAL")

    @_needs_sidecar
    def test_a_healthy_ledger_does_not_close_the_gate(self):
        """The control. Without it, the test above could pass for any reason."""
        gateway = MembraneGateway(
            os.path.join(self.tmp.name, "b.jsonl"),
            immune_state=ImmuneState(),
            penalty_ledger=PenaltyLedger(),
        )
        gateway.register_artefact("write", self.artefact)
        warrant = gateway.request_action_warrant(
            {"kind": "file", "path": self.artefact, "digest": gateway._digest_of_file(self.artefact)},
            "write",
        )
        self.assertEqual(warrant.capability, "write")

    @_needs_sidecar
    def test_a_critical_ledger_does_not_block_reads(self):
        """Damage closes writes, not the ability to look at what happened."""
        gateway = MembraneGateway(
            os.path.join(self.tmp.name, "c.jsonl"),
            immune_state=ImmuneState(),
            penalty_ledger=self._critical_ledger(),
            strict_capabilities={"write", "execute"},
        )
        gateway.register_artefact("read", self.artefact)
        warrant = gateway.request_action_warrant(
            {"kind": "file", "path": self.artefact, "digest": gateway._digest_of_file(self.artefact)},
            "read",
        )
        self.assertEqual(warrant.capability, "read")

    @_needs_sidecar
    def test_brake_reports_both_layers_separately(self):
        gateway = MembraneGateway(
            os.path.join(self.tmp.name, "d.jsonl"),
            immune_state=ImmuneState(),
            penalty_ledger=self._critical_ledger(),
        )
        brake = gateway.brake()
        self.assertFalse(brake["tripped"], "the immune layer is calm")
        self.assertEqual(brake["penalty_state"], "CRITICAL")
        self.assertTrue(brake["penalty_tripped"])

    @_needs_sidecar
    def test_a_penalty_ledger_cannot_be_passed_as_the_brake(self):
        """The confused-deputy bug, now a TypeError instead of a calm answer."""
        with self.assertRaises(TypeError):
            MembraneGateway(
                os.path.join(self.tmp.name, "e.jsonl"),
                immune_state=self._critical_ledger(),
            )

    @_needs_sidecar
    def test_an_immune_state_cannot_be_passed_as_the_penalty_ledger(self):
        with self.assertRaises(TypeError):
            MembraneGateway(
                os.path.join(self.tmp.name, "f.jsonl"),
                penalty_ledger=ImmuneState(),
            )

    @_needs_sidecar
    def test_the_two_vocabularies_are_disjoint(self):
        """If these ever collide, the role check above stops working."""
        self.assertEqual(PenaltyLedger().penalty_state, "HEALTHY")
        self.assertEqual(ImmuneState().state, STATE_NORMAL)
        self.assertNotIn("state", dir(PenaltyLedger))
        self.assertNotIn("penalty_state", dir(ImmuneState))

    @_needs_sidecar
    def test_inflammation_and_damage_are_reported_as_different_reasons(self):
        """Both tripped: the ledger is named, because it does not heal by itself."""
        immune = ImmuneState(fail_threshold=2)
        immune.record_verdict(Verdict.FAIL)
        immune.record_verdict(Verdict.FAIL)
        self.assertEqual(immune.state, STATE_INFLAMED)
        gateway = MembraneGateway(
            os.path.join(self.tmp.name, "g.jsonl"),
            immune_state=immune,
            penalty_ledger=self._critical_ledger(),
        )
        gateway.register_artefact("write", self.artefact)
        with self.assertRaises(MembraneRejectError) as caught:
            gateway.request_action_warrant(
                {"kind": "file", "path": self.artefact, "digest": gateway._digest_of_file(self.artefact)},
                "write",
            )
        self.assertEqual(caught.exception.reason, "PENALTY_CRITICAL")


class TestCAdaptiveCheckCostsEnergy(unittest.TestCase):
    """F3. Escalating to tier 2 must cost what the extra verification costs."""

    def test_tier_two_costs_the_surcharge_on_top_of_the_beat(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        cheap = scheduler.execute("rename a variable", 0.2, tier=TIER_INNATE)
        self.assertEqual(cheap.mode, "fast")
        self.assertEqual(cheap.energy_cost, 7.0)
        self.assertEqual(cheap.tier_surcharge, 0.0)

        dear = scheduler.execute("rename a variable", 0.2, tier=TIER_ADAPTIVE)
        self.assertEqual(dear.mode, "fast")
        self.assertEqual(dear.energy_cost, 7.0 + ADAPTIVE_SURCHARGE)
        self.assertEqual(dear.tier_surcharge, ADAPTIVE_SURCHARGE)
        self.assertEqual(dear.tier, TIER_ADAPTIVE)

    def test_the_surcharge_applies_to_the_slow_beat_too(self):
        # The check is about what must be proved, not about which beat thought
        # of the answer, so it is charged on both.
        scheduler = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0)
        response = scheduler.execute("design the membrane", 0.95, tier=TIER_ADAPTIVE)
        self.assertEqual(response.mode, "slow")
        self.assertEqual(response.energy_cost, 35.0 + ADAPTIVE_SURCHARGE)

    def test_the_surcharge_is_actually_deducted(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        scheduler.execute("x", 0.2, tier=TIER_ADAPTIVE)
        self.assertEqual(account.current_energy, 100.0 - 17.0)

    def test_an_unaffordable_surcharge_refuses_rather_than_overdrawing(self):
        account = EnergyAccount(100.0)
        account.consume(88.0)  # 12 left: the beat fits, the check does not
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        with self.assertRaises(Exception):
            scheduler.execute("x", 0.2, tier=TIER_ADAPTIVE)
        self.assertEqual(account.current_energy, 12.0, "nothing was charged")

    def test_an_unknown_tier_is_rejected_rather_than_treated_as_cheap(self):
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        with self.assertRaises(ValueError):
            scheduler.execute("x", 0.2, tier="tier3")

    def test_the_default_is_the_cheap_tier(self):
        """Escalating by default would make the safe path the costly one."""
        response = DualBeatScheduler(EnergyAccount(100.0)).execute("x", 0.2)
        self.assertEqual(response.tier, TIER_INNATE)
        self.assertEqual(response.tier_surcharge, 0.0)

    def test_the_proxy_records_the_tier_and_its_price(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account)
        proxy = LLMProxy(scheduler)
        result = proxy.generate("x", 0.2, tier=TIER_ADAPTIVE)
        self.assertEqual(result.tier, TIER_ADAPTIVE)
        self.assertEqual(result.tier_surcharge, ADAPTIVE_SURCHARGE)

    @_needs_sidecar
    def test_the_tier_vocabularies_agree_across_the_two_packages(self):
        """Two modules independently naming "tier2" is a coincidence; pin it.

        This package cannot import the auditor (``dependencies = []``), so the
        strings match by contract. Without this test the contract would be a
        comment, and a rename on either side would silently make escalation
        free again -- which is F3 all over.
        """
        self.assertEqual(TIER_INNATE, SIDECAR_TIER_INNATE)
        self.assertEqual(TIER_ADAPTIVE, SIDECAR_TIER_ADAPTIVE)


if __name__ == "__main__":
    unittest.main()
