"""Tests for the metabolic gateway.

The property under test is mostly *what the gateway refuses to assert*. It
reports five laws, one of which (L5) is hosted by a different process. A gateway
that answered "not inflamed" for an unattached brake would report the most
important safety layer as healthy precisely when it is missing, so that case is
pinned here alongside the arithmetic.

``sys.path`` is adjusted the same way the sibling test modules do it, so the
suite runs with ``python3 -m unittest discover -s tests -p 'test_*.py'`` without
an editable install.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    CONSERVE,
    EMERGENCY,
    GROW,
    PENALTY_CRITICAL,
    SLOW,
    CompartmentManager,
    DualBeatScheduler,
    EnergyAccount,
    PenaltyLedger,
)
from agent_metabolism.gateway import LAWS, MetabolicGateway  # noqa: E402


class _FakeInflamed:
    """Stand-in for the immune sidecar's state, in the shape it actually has."""

    def __init__(self, inflamed: bool) -> None:
        self._inflamed = inflamed
        self.state = "INFLAMED" if inflamed else "NORMAL"

    def is_inflamed(self) -> bool:
        return self._inflamed


class _StateOnly:
    """The second accepted shape: a ``state`` attribute and no method."""

    def __init__(self, state: str) -> None:
        self.state = state


class TestL1Energy(unittest.TestCase):
    def test_full_account_is_reported_with_its_ratio(self):
        account = EnergyAccount(100.0)
        status = MetabolicGateway(account=account).get_current_laws_status()
        energy = status["L1_Energy"]
        self.assertTrue(energy["measured"])
        self.assertEqual(energy["current"], 100.0)
        self.assertEqual(energy["budget"], 100.0)
        self.assertEqual(energy["ratio"], 1.0)
        self.assertEqual(energy["state"], GROW)

    def test_spent_account_reports_the_new_state(self):
        account = EnergyAccount(100.0)
        account.consume(75.0)
        energy = MetabolicGateway(account=account).get_current_laws_status()["L1_Energy"]
        self.assertEqual(energy["current"], 25.0)
        self.assertEqual(energy["state"], CONSERVE)

    def test_no_account_is_unmeasured_not_zero(self):
        """Zero energy and "no account" are different claims."""
        energy = MetabolicGateway().get_current_laws_status()["L1_Energy"]
        self.assertFalse(energy["measured"])
        self.assertIsNone(energy["current"])
        self.assertIsNone(energy["ratio"])

    def test_account_falls_back_to_the_scheduler_own_account(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account)
        status = MetabolicGateway(scheduler=scheduler).get_current_laws_status()
        self.assertEqual(status["L1_Energy"]["current"], 100.0)

    def test_explicit_account_overrides_the_scheduler_copy(self):
        scheduler_account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(scheduler_account)
        separate = EnergyAccount(100.0)
        separate.consume(90.0)
        status = MetabolicGateway(
            scheduler=scheduler, account=separate
        ).get_current_laws_status()
        self.assertEqual(status["L1_Energy"]["current"], 10.0)


class TestL2Beat(unittest.TestCase):
    def test_supplied_response_reports_an_observed_beat(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account)
        response = scheduler.execute("prove it", 0.95)
        status = MetabolicGateway(
            scheduler=scheduler, last_response=response
        ).get_current_laws_status()
        beat = status["L2_Beat"]
        self.assertTrue(beat["observed"])
        self.assertEqual(beat["current"], SLOW)

    def test_without_a_response_the_beat_is_predicted_not_observed(self):
        """The scheduler keeps no memory, so "current beat" needs a response."""
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account)
        beat = MetabolicGateway(scheduler=scheduler).get_current_laws_status()["L2_Beat"]
        self.assertFalse(beat["observed"])
        self.assertIsNone(beat["current"])
        self.assertEqual(beat["permitted"], SLOW)
        self.assertIn("permits", beat["reason"])

    def test_poor_account_permits_only_the_fast_beat(self):
        account = EnergyAccount(100.0)
        account.consume(95.0)
        scheduler = DualBeatScheduler(account)
        beat = MetabolicGateway(scheduler=scheduler).get_current_laws_status()["L2_Beat"]
        self.assertEqual(beat["permitted"], "fast")
        self.assertFalse(beat["slow_affordable"])

    def test_bankrupt_trust_withdraws_the_slow_beat(self):
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        self.assertEqual(ledger.get_state(), PENALTY_CRITICAL)
        scheduler = DualBeatScheduler(EnergyAccount(100.0), penalty_ledger=ledger)
        beat = MetabolicGateway(scheduler=scheduler).get_current_laws_status()["L2_Beat"]
        self.assertFalse(beat["slow_allowed_by_trust"])
        self.assertEqual(beat["permitted"], "fast")


class TestL3Regime(unittest.TestCase):
    def test_full_account_with_a_clean_record_is_grow(self):
        status = MetabolicGateway(account=EnergyAccount(100.0)).get_current_laws_status()
        self.assertEqual(status["L3_Regime"]["current"], GROW)

    def test_low_charge_forces_emergency(self):
        account = EnergyAccount(100.0)
        account.consume(80.0)
        status = MetabolicGateway(account=account).get_current_laws_status()
        self.assertEqual(status["L3_Regime"]["current"], EMERGENCY)

    def test_critical_penalty_outranks_a_full_account(self):
        """Damage outranks supply: a full account cannot buy back trust."""
        ledger = PenaltyLedger()
        ledger.record_violation("FALSE_COMPLETION")
        scheduler = DualBeatScheduler(EnergyAccount(100.0), penalty_ledger=ledger)
        status = MetabolicGateway(scheduler=scheduler).get_current_laws_status()
        self.assertEqual(status["L3_Regime"]["current"], EMERGENCY)

    def test_missing_account_leaves_regime_unmeasured(self):
        regime = MetabolicGateway().get_current_laws_status()["L3_Regime"]
        self.assertFalse(regime["measured"])
        self.assertIsNone(regime["current"])


class TestL5Brake(unittest.TestCase):
    def test_unattached_brake_is_reported_as_unattached_not_healthy(self):
        """The tempting wrong answer is ``tripped: False``.

        L5 is hosted by the immune sidecar. With nothing attached this gateway
        has no evidence either way, and reporting ``False`` would describe the
        safety layer as healthy precisely when it is absent.
        """
        brake = MetabolicGateway().get_current_laws_status()["L5_Brake"]
        self.assertFalse(brake["attached"])
        self.assertIsNone(brake["tripped"])
        self.assertIn("no immune state attached", brake["reason"])

    def test_inflamed_state_trips_the_brake(self):
        status = MetabolicGateway(
            immune_state=_FakeInflamed(True)
        ).get_current_laws_status()
        self.assertTrue(status["L5_Brake"]["attached"])
        self.assertTrue(status["L5_Brake"]["tripped"])
        self.assertEqual(status["L5_Brake"]["state"], "INFLAMED")

    def test_calm_state_reports_not_tripped(self):
        brake = MetabolicGateway(
            immune_state=_FakeInflamed(False)
        ).get_current_laws_status()["L5_Brake"]
        self.assertTrue(brake["attached"])
        self.assertFalse(brake["tripped"])

    def test_state_attribute_without_a_method_is_also_accepted(self):
        brake = MetabolicGateway(
            immune_state=_StateOnly("INFLAMED")
        ).get_current_laws_status()["L5_Brake"]
        self.assertTrue(brake["tripped"])

    def test_unreadable_immune_state_is_not_treated_as_calm(self):
        brake = MetabolicGateway(
            immune_state=object()
        ).get_current_laws_status()["L5_Brake"]
        self.assertIsNone(brake["tripped"])
        self.assertEqual(brake["state"], "unreadable")

    def test_l5_is_declared_as_hosted_elsewhere(self):
        row = [law for law in LAWS if law["law"] == "L5"][0]
        self.assertFalse(row["hosted_here"])
        self.assertTrue(all(law["hosted_here"] for law in LAWS if law["law"] != "L5"))


class TestL7Compartment(unittest.TestCase):
    def test_active_count_tracks_lysis(self):
        manager = CompartmentManager(anomaly_threshold=2)
        first = manager.create("alpha")
        manager.create("beta")
        first.record_anomaly()
        first.record_anomaly()
        self.assertFalse(first.alive)

        status = MetabolicGateway(
            compartments=manager
        ).get_current_laws_status()
        compartment = status["L7_Compartment"]
        self.assertTrue(compartment["measured"])
        self.assertEqual(compartment["total"], 2)
        self.assertEqual(compartment["active"], 1)
        self.assertEqual(compartment["names"], ["beta"])

    def test_no_manager_is_unmeasured(self):
        compartment = MetabolicGateway().get_current_laws_status()["L7_Compartment"]
        self.assertFalse(compartment["measured"])
        self.assertIsNone(compartment["active"])


class TestWholeSurface(unittest.TestCase):
    def test_all_five_requested_keys_are_present(self):
        status = MetabolicGateway().get_current_laws_status()
        for key in (
            "L1_Energy",
            "L2_Beat",
            "L3_Regime",
            "L5_Brake",
            "L7_Compartment",
        ):
            self.assertIn(key, status)

    def test_witness_names_what_was_missing(self):
        status = MetabolicGateway().get_current_laws_status()
        witness = status["_witness"]
        self.assertIn("no energy account attached", witness["L1_Energy"])
        self.assertIn("hosted by the auditor", witness["L5_Brake"])

    def test_gateway_does_not_mutate_the_account(self):
        """A read-only surface that spends energy would be a regulator that
        changes what it measures."""
        account = EnergyAccount(100.0)
        gateway = MetabolicGateway(account=account)
        before = account.current_energy
        gateway.get_current_laws_status()
        gateway.get_current_laws_status()
        self.assertEqual(account.current_energy, before)

    def test_two_reads_agree(self):
        account = EnergyAccount(100.0)
        compartment = CompartmentManager()
        compartment.create("one")
        gateway = MetabolicGateway(account=account, compartments=compartment)
        self.assertEqual(
            gateway.get_current_laws_status(), gateway.get_current_laws_status()
        )

    def test_repr_does_not_raise(self):
        self.assertIn("MetabolicGateway", repr(MetabolicGateway()))

    def test_wrong_types_are_rejected(self):
        with self.assertRaises(TypeError):
            MetabolicGateway(scheduler="not a scheduler")
        with self.assertRaises(TypeError):
            MetabolicGateway(account=42)
        with self.assertRaises(TypeError):
            MetabolicGateway(compartments=object())


if __name__ == "__main__":
    unittest.main()
