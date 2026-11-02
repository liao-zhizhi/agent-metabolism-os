"""Behavioural tests for the three metabolic layers.

The tests are organised by the invariant they protect, not by module: an
invariant that spans L1 and L2 (a poor account cannot buy the slow beat) is
worth more than two unit tests that each hold only half of it.
"""

import ast
import os
import sys
import unittest

# Running `python3 tests/test_metabolism.py` directly puts `tests/` on the path;
# this makes the package importable either way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    ALIVE,
    CONSERVE,
    EXHAUSTED,
    GROW,
    LYSED,
    CompartmentDestroyed,
    CompartmentManager,
    DualBeatScheduler,
    EnergyAccount,
    EnergyExhausted,
    InsufficientEnergy,
    UnknownCompartment,
)
from agent_metabolism.scheduler import FAST, SLOW, OXPHOS, SLP  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# L1 -- the account is a constraint, not a counter
# ---------------------------------------------------------------------------


class TestEnergyAccount(unittest.TestCase):
    def test_starts_full_and_growing(self):
        account = EnergyAccount(100.0)
        self.assertEqual(account.current_energy, 100.0)
        self.assertEqual(account.ratio, 1.0)
        self.assertEqual(account.state, GROW)

    def test_just_below_thirty_percent_enters_conserve(self):
        account = EnergyAccount(100.0)
        account.consume(71.0)
        self.assertAlmostEqual(account.ratio, 0.29)
        self.assertEqual(account.state, CONSERVE)

    def test_exactly_thirty_percent_is_still_grow(self):
        # The threshold is "below 30%", so 30.0 itself is not below it. Tying
        # the boundary to the charge rather than to a rounding convention is
        # what lets the demo show the exact crossing.
        account = EnergyAccount(100.0)
        account.consume(70.0)
        self.assertAlmostEqual(account.current_energy, 30.0)
        self.assertEqual(account.state, GROW)

    def test_just_below_ten_percent_is_exhausted(self):
        account = EnergyAccount(100.0)
        account.consume(91.0)
        self.assertAlmostEqual(account.ratio, 0.09)
        self.assertEqual(account.state, EXHAUSTED)

    def test_exactly_ten_percent_is_conserve_not_exhausted(self):
        account = EnergyAccount(100.0)
        account.consume(90.0)
        self.assertAlmostEqual(account.current_energy, 10.0)
        self.assertEqual(account.state, CONSERVE)

    def test_overdraft_is_refused_and_leaves_the_account_untouched(self):
        account = EnergyAccount(100.0)
        with self.assertRaises(InsufficientEnergy):
            account.consume(100.01)
        self.assertEqual(account.current_energy, 100.0)
        self.assertEqual(account.ledger, ())

    def test_spending_while_exhausted_is_refused(self):
        account = EnergyAccount(100.0)
        account.consume(95.0)
        self.assertEqual(account.state, EXHAUSTED)
        # 5.0 is still on the clock, but the floor forbids funding work.
        with self.assertRaises(EnergyExhausted):
            account.consume(1.0)
        self.assertAlmostEqual(account.current_energy, 5.0)

    def test_exhausted_and_insufficient_are_different_failures(self):
        # A pricing problem and a policy floor must not be conflated: callers
        # degrade on one and refill on the other.
        account = EnergyAccount(100.0)
        account.consume(95.0)
        with self.assertRaises(EnergyExhausted):
            account.consume(1.0)
        rich = EnergyAccount(100.0)
        with self.assertRaises(InsufficientEnergy):
            rich.consume(101.0)
        self.assertNotIsInstance(InsufficientEnergy(), EnergyExhausted)

    def test_restore_lifts_the_state(self):
        account = EnergyAccount(100.0)
        account.consume(95.0)
        self.assertEqual(account.state, EXHAUSTED)
        account.restore(60.0)
        self.assertEqual(account.state, GROW)

    def test_restore_cannot_exceed_the_budget(self):
        account = EnergyAccount(100.0)
        account.consume(50.0)
        self.assertEqual(account.restore(1e9), 100.0)

    def test_zero_cost_is_a_noop(self):
        account = EnergyAccount(100.0)
        self.assertEqual(account.consume(0.0), 100.0)
        self.assertEqual(account.ledger, ())

    def test_bad_costs_are_rejected(self):
        account = EnergyAccount(100.0)
        for bad in (-1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                account.consume(bad)
        with self.assertRaises(ValueError):
            account.consume("ten")  # type: ignore[arg-type]

    def test_construction_is_validated(self):
        with self.assertRaises(ValueError):
            EnergyAccount(0.0)
        with self.assertRaises(ValueError):
            EnergyAccount(100.0, initial=101.0)
        with self.assertRaises(ValueError):
            EnergyAccount(100.0, conserve_threshold=0.9, exhausted_threshold=0.95)
        with self.assertRaises(ValueError):
            EnergyAccount(100.0, conserve_threshold=1.5)

    def test_ledger_records_order_and_state(self):
        account = EnergyAccount(100.0)
        account.consume(40.0)
        account.consume(40.0)
        self.assertEqual([e.cost for e in account.ledger], [40.0, 40.0])
        self.assertEqual(account.ledger[0].state, GROW)
        self.assertEqual(account.ledger[1].state, CONSERVE)  # 20% after the spend
        self.assertEqual(account.total_spent, 80.0)

    def test_snapshot_is_a_report_not_a_handle(self):
        account = EnergyAccount(100.0)
        snap = account.snapshot()
        self.assertEqual(snap["state"], GROW)
        snap["energy"] = -1.0
        self.assertEqual(account.current_energy, 100.0)


# ---------------------------------------------------------------------------
# L2 -- the beat is chosen from complexity *and* affordability
# ---------------------------------------------------------------------------


class TestDualBeatScheduler(unittest.TestCase):
    def test_complex_task_on_a_rich_account_runs_slow(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        response = scheduler.execute("design the membrane", 0.9)
        self.assertEqual(response.mode, SLOW)
        self.assertEqual(response.beat, SLP)
        self.assertEqual(response.energy_cost, 35.0)
        self.assertFalse(response.degraded)
        self.assertEqual(response.state_after, GROW)

    def test_simple_task_runs_fast_even_when_rich(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        response = scheduler.execute("rename a variable", 0.2)
        self.assertEqual(response.mode, FAST)
        self.assertEqual(response.beat, OXPHOS)
        self.assertEqual(response.energy_cost, 7.0)

    def test_slow_beat_costs_five_times_the_fast_beat(self):
        prompt = "the same task on both beats"
        fast = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0).execute(prompt, 0.1)
        slow = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0).execute(prompt, 0.9)
        self.assertEqual(slow.energy_cost, fast.energy_cost * 5.0)

    def test_slow_beat_doubles_the_wall_clock(self):
        prompt = "the same task on both beats"
        fast = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0).execute(prompt, 0.1)
        slow = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0).execute(prompt, 0.9)
        self.assertEqual(slow.latency_ms, fast.latency_ms * 2.0)
        self.assertGreater(slow.tokens_used, fast.tokens_used)

    def test_conserving_account_is_forced_onto_the_fast_beat(self):
        # L1 gating L2: the task is as complex as it gets, and the mode is
        # still fast because the charge says so.
        account = EnergyAccount(100.0)
        account.consume(80.0)  # 20% -> CONSERVE
        self.assertEqual(account.state, CONSERVE)
        response = DualBeatScheduler(account, base_cost=7.0).execute("hard", 1.0)
        self.assertEqual(response.mode, FAST)
        self.assertFalse(response.degraded)
        self.assertEqual(response.state_after, CONSERVE)

    def test_exhausted_account_refuses_to_schedule(self):
        account = EnergyAccount(100.0)
        account.consume(95.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        with self.assertRaises(EnergyExhausted):
            scheduler.execute("anything", 0.1)
        self.assertAlmostEqual(account.current_energy, 5.0)

    def test_degrades_rather_than_overdrawing(self):
        # 30.0 left, the slow beat costs 35.0. The scheduler must not borrow:
        # it runs fast and says that it wanted slow.
        account = EnergyAccount(100.0)
        account.consume(70.0)
        self.assertEqual(account.state, GROW)
        response = DualBeatScheduler(account, base_cost=7.0).execute("hard", 0.95)
        self.assertEqual(response.mode, FAST)
        self.assertTrue(response.degraded)
        self.assertEqual(response.energy_cost, 7.0)
        self.assertAlmostEqual(account.current_energy, 23.0)
        self.assertEqual(response.state_after, CONSERVE)

    def test_exactly_at_the_threshold_there_is_no_slow_beat(self):
        account = EnergyAccount(100.0)
        account.consume(70.0)  # exactly 30%, still GROW
        response = DualBeatScheduler(account, base_cost=7.0).execute("hard", 0.7000001)
        self.assertEqual(response.mode, FAST)  # wanted it, could not pay
        self.assertTrue(response.degraded)

    def test_cheap_base_cost_lets_a_poor_account_keep_thinking(self):
        account = EnergyAccount(100.0)
        account.consume(70.0)
        scheduler = DualBeatScheduler(account, base_cost=1.0)  # slow == 5.0
        response = scheduler.execute("hard", 0.9)
        self.assertEqual(response.mode, SLOW)
        self.assertFalse(response.degraded)
        self.assertAlmostEqual(account.current_energy, 25.0)

    def test_complexity_threshold_is_exclusive(self):
        scheduler = DualBeatScheduler(EnergyAccount(100.0), base_cost=7.0)
        self.assertEqual(scheduler.execute("at the line", 0.7).mode, FAST)

    def test_inputs_are_validated(self):
        scheduler = DualBeatScheduler(EnergyAccount(100.0))
        with self.assertRaises(ValueError):
            scheduler.execute("   ", 0.5)
        with self.assertRaises(ValueError):
            scheduler.execute("ok", 1.5)
        with self.assertRaises(ValueError):
            scheduler.execute("ok", float("nan"))
        with self.assertRaises(TypeError):
            scheduler.execute(None, 0.5)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            DualBeatScheduler("not an account")  # type: ignore[arg-type]

    def test_response_carries_the_energy_arc(self):
        account = EnergyAccount(100.0)
        response = DualBeatScheduler(account, base_cost=7.0).execute("hard", 0.9)
        self.assertEqual(response.energy_before, 100.0)
        self.assertEqual(response.energy_after, 65.0)
        self.assertEqual(response.energy_cost, 35.0)
        self.assertAlmostEqual(response.thinking_seconds, response.latency_ms / 1000.0)


# ---------------------------------------------------------------------------
# L7 -- the membrane is structural
# ---------------------------------------------------------------------------


class TestCompartmentIsolation(unittest.TestCase):
    def setUp(self):
        self.manager = CompartmentManager(anomaly_threshold=3)
        self.planner = self.manager.create("planner")
        self.coder = self.manager.create("coder")
        self.critic = self.manager.create("critic")

    def test_a_message_lands_in_the_inbox_not_the_context(self):
        self.planner.send_message("coder", "spec v1")
        self.assertEqual(self.planner.local_context, ())
        self.assertEqual(self.coder.local_context, ())   # not admitted yet
        self.assertEqual(self.coder.inbox_depth, 1)      # arrived at the membrane

    def test_the_recipient_admits_it_on_its_own_schedule(self):
        self.planner.send_message("coder", "spec v1")
        admitted = self.coder.receive()
        self.assertEqual(len(admitted), 1)
        self.assertEqual(len(self.coder.local_context), 1)
        self.assertEqual(self.coder.inbox_depth, 0)
        self.assertIn("spec v1", str(self.coder.local_context[0]))

    def test_context_snapshot_cannot_be_mutated_from_outside(self):
        self.planner.append("plan drafted")
        with self.assertRaises(AttributeError):
            self.coder.local_context.append("injected")  # type: ignore[attr-defined]
        self.assertIsInstance(self.coder.local_context, tuple)

    def test_receiving_does_not_touch_the_sender(self):
        self.planner.append("plan drafted")
        self.planner.send_message("coder", "spec v1")
        self.coder.receive()
        self.assertEqual(len(self.planner.local_context), 1)
        self.assertEqual(len(self.coder.local_context), 1)

    def test_two_compartments_never_share_a_context(self):
        self.planner.append("planner-private")
        self.coder.append("coder-private")
        self.planner.send_message("coder", "one message")
        self.coder.receive()
        planner_memory = [str(m) for m in self.planner.local_context]
        coder_memory = [str(m) for m in self.coder.local_context]
        self.assertEqual(planner_memory, ["planner -> planner: planner-private"])
        self.assertNotIn("planner-private", " ".join(coder_memory))
        self.assertNotIn("coder-private", " ".join(planner_memory))

    def test_a_compartment_cannot_append_to_another(self):
        # The only public write path takes a note for *self*; there is no API
        # to name a target, which is the structural half of the guarantee.
        self.assertFalse(hasattr(self.coder, "append_to"))
        with self.assertRaises(TypeError):
            self.planner.append("self note", self.coder)  # type: ignore[misc]

    def test_message_sequence_is_monotonic(self):
        a = self.planner.send_message("coder", "first")
        b = self.coder.send_message("critic", "second")
        self.assertLess(a.seq, b.seq)
        self.assertEqual((a.sender, a.recipient), ("planner", "coder"))

    def test_unknown_and_duplicate_names(self):
        with self.assertRaises(UnknownCompartment):
            self.manager.get("nobody")
        with self.assertRaises(ValueError):
            self.manager.create("planner")
        with self.assertRaises(KeyError):
            self.manager.get("nobody")

    def test_destroying_one_compartment_leaves_the_others_whole(self):
        self.planner.append("plan")
        self.critic.append("concern")
        self.planner.send_message("critic", "review please")
        self.critic.receive()

        self.manager.destroy("coder", reason="mode 4")

        self.assertEqual(self.coder.status, LYSED)
        self.assertFalse(self.coder.alive)
        self.assertEqual(self.coder.local_context, ())
        # The neighbours did not notice.
        self.assertEqual(self.planner.status, ALIVE)
        self.assertEqual(self.critic.status, ALIVE)
        self.assertEqual(len(self.planner.local_context), 1)
        self.assertEqual(len(self.critic.local_context), 2)

    def test_routing_to_a_lysed_compartment_is_refused(self):
        self.manager.destroy("coder")
        with self.assertRaises(CompartmentDestroyed):
            self.planner.send_message("coder", "anyone there?")
        with self.assertRaises(CompartmentDestroyed):
            self.coder.receive()

    def test_system_keeps_routing_after_a_lysis(self):
        self.manager.destroy("coder")
        self.manager.create("coder-2")
        self.planner.send_message("coder-2", "spec again")
        self.assertEqual(self.manager.get("coder-2").inbox_depth, 1)
        self.assertEqual(self.manager.alive_names(), ("planner", "critic", "coder-2"))

    def test_consecutive_anomalies_lyse_at_the_threshold(self):
        self.coder.append("partial work")
        self.assertEqual(self.coder.record_anomaly(), 1)
        self.assertEqual(self.coder.record_anomaly(), 2)
        self.assertEqual(self.coder.status, ALIVE)
        self.assertEqual(self.coder.record_anomaly(), 3)  # threshold reached
        self.assertEqual(self.coder.status, LYSED)
        self.assertIn("3 consecutive anomalies", self.coder.lysis_reason)
        self.assertEqual(self.coder.local_context, ())

    def test_a_success_resets_the_consecutive_counter(self):
        self.coder.record_anomaly()
        self.coder.record_anomaly()
        self.coder.record_success()
        self.assertEqual(self.coder.consecutive_anomalies, 0)
        self.coder.record_anomaly()
        self.coder.record_anomaly()
        self.assertEqual(self.coder.status, ALIVE)  # 2 < 3, the run restarted

    def test_manager_snapshot_is_a_report(self):
        self.planner.append("plan")
        self.planner.send_message("coder", "spec")
        snap = self.manager.snapshot()
        self.assertEqual(snap["coder"]["inbox"], 1)
        self.assertEqual(snap["coder"]["context"], [])
        self.assertEqual(snap["planner"]["status"], ALIVE)
        snap["planner"]["context"].append("forged")
        self.assertEqual(len(self.planner.local_context), 1)

    def test_threshold_validation(self):
        with self.assertRaises(ValueError):
            CompartmentManager(anomaly_threshold=0)
        with self.assertRaises(TypeError):
            CompartmentManager(anomaly_threshold=2.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The whole cycle: L1 + L2 + L7 in one run
# ---------------------------------------------------------------------------


class TestLifecycle(unittest.TestCase):
    def test_a_full_run_walks_from_growth_to_exhaustion(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)

        modes, degraded, states = [], [], []
        for complexity in (0.92, 0.88, 0.95, 0.91, 0.25):
            response = scheduler.execute("work item", complexity)
            modes.append(response.mode)
            degraded.append(response.degraded)
            states.append(response.state_after)

        self.assertEqual(modes, [SLOW, SLOW, FAST, FAST, FAST])
        self.assertEqual(degraded, [False, False, True, False, False])
        self.assertEqual(states, [GROW, GROW, CONSERVE, CONSERVE, EXHAUSTED])
        self.assertAlmostEqual(account.current_energy, 9.0)

        with self.assertRaises(EnergyExhausted):
            scheduler.execute("one more", 0.99)
        self.assertAlmostEqual(account.current_energy, 9.0)

    def test_the_run_is_auditable_after_the_fact(self):
        account = EnergyAccount(100.0)
        scheduler = DualBeatScheduler(account, base_cost=7.0)
        scheduler.execute("a", 0.9)
        scheduler.execute("b", 0.9)
        self.assertEqual([e.cost for e in account.ledger], [35.0, 35.0])
        self.assertEqual(account.total_spent, 70.0)
        self.assertEqual(account.snapshot()["state"], GROW)


# ---------------------------------------------------------------------------
# The zero-dependency claim, checked rather than asserted in prose
# ---------------------------------------------------------------------------


class TestNoThirdPartyImports(unittest.TestCase):
    def test_only_the_standard_library_is_imported(self):
        if not hasattr(sys, "stdlib_module_names"):
            self.skipTest("sys.stdlib_module_names needs Python 3.10+")
        stdlib = set(sys.stdlib_module_names) | {__name__.split(".")[0], "agent_metabolism"}

        sources = [
            os.path.join(PROJECT_ROOT, "agent_metabolism", name)
            for name in sorted(os.listdir(os.path.join(PROJECT_ROOT, "agent_metabolism")))
            if name.endswith(".py")
        ]
        sources.append(os.path.join(PROJECT_ROOT, "demo.py"))

        offenders = []
        for path in sources:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue  # relative import inside the package
                    names = [node.module.split(".")[0]] if node.module else []
                else:
                    continue
                for name in names:
                    if name not in stdlib:
                        offenders.append("{}: {}".format(os.path.basename(path), name))

        self.assertEqual(offenders, [], "third-party imports found: {}".format(offenders))

    def test_no_metadata_declares_a_dependency(self):
        pyproject = os.path.join(PROJECT_ROOT, "pyproject.toml")
        with open(pyproject, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("dependencies = []", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
