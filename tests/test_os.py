"""Tests for L3 (regime), L4 (substrate channel) and L6 (circadian).

Grouped by the invariant each block protects. The interesting cases are the
cross-layer ones: a channel that leaks mutation breaks the isolation L7 just
established, and a night pass that writes into compartments would erase the
record it was supposed to summarise.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    EMERGENCY,
    MAX_CONCURRENT_AGENTS,
    AgentProxy,
    CircadianError,
    CircadianScheduler,
    CompartmentDestroyed,
    CompartmentManager,
    EnergyAccount,
    MemoryFileError,
    MissingSubstrate,
    RegimeError,
    RegimeSwitch,
    SubstrateChannel,
    SubstrateKeyError,
    evaluate_regime,
)
from agent_metabolism.regime import CONSERVE, GROW  # noqa: E402


def timestamp_at(hour, minute=0):
    """A local timestamp with a known hour, so day/night tests are stable."""
    return time.mktime((2026, 3, 14, hour, minute, 0, 0, 0, -1))


# ---------------------------------------------------------------------------
# L3 -- budget and error feedback, with worst-wins precedence
# ---------------------------------------------------------------------------


class TestEvaluateRegime(unittest.TestCase):
    def test_rich_and_healthy_grows(self):
        self.assertEqual(evaluate_regime(0.95, 0.05), GROW)

    def test_rich_but_failing_conserves(self):
        # A full charge does not buy exploration while the system is failing.
        self.assertEqual(evaluate_regime(0.95, 0.55), CONSERVE)

    def test_middling_budget_conserves_even_without_errors(self):
        self.assertEqual(evaluate_regime(0.50, 0.00), CONSERVE)

    def test_low_budget_is_emergency(self):
        self.assertEqual(evaluate_regime(0.20, 0.00), EMERGENCY)

    def test_grow_needs_strictly_more_than_seventy_percent(self):
        # Mirrors L1: exactly on the line belongs to the safer side.
        self.assertEqual(evaluate_regime(0.70, 0.00), CONSERVE)
        self.assertEqual(evaluate_regime(0.7001, 0.00), GROW)

    def test_emergency_needs_strictly_less_than_thirty_percent(self):
        self.assertEqual(evaluate_regime(0.30, 0.00), CONSERVE)
        self.assertEqual(evaluate_regime(0.2999, 0.00), EMERGENCY)

    def test_error_rate_boundary_is_inclusive_for_grow(self):
        self.assertEqual(evaluate_regime(0.95, 0.30), GROW)
        self.assertEqual(evaluate_regime(0.95, 0.3001), CONSERVE)

    def test_critical_errors_trigger_emergency_with_a_full_charge(self):
        self.assertEqual(evaluate_regime(1.00, 0.00, critical_errors=2), EMERGENCY)
        self.assertEqual(evaluate_regime(1.00, 0.00, critical_errors=1), GROW)

    def test_emergency_beats_conserve_which_beats_grow(self):
        # Worst-wins, checked as an ordering rather than three separate cases.
        self.assertEqual(evaluate_regime(0.10, 0.90, critical_errors=5), EMERGENCY)
        self.assertEqual(evaluate_regime(0.95, 0.90, critical_errors=0), CONSERVE)

    def test_inputs_are_validated(self):
        for bad_ratio in (-0.1, 1.1, float("nan")):
            with self.assertRaises(RegimeError):
                evaluate_regime(bad_ratio, 0.0)
        with self.assertRaises(RegimeError):
            evaluate_regime(0.9, 2.0)
        with self.assertRaises(RegimeError):
            evaluate_regime(0.9, 0.0, critical_errors=-1)
        with self.assertRaises(RegimeError):
            evaluate_regime(0.9, 0.0, critical_errors=True)  # bool is not a count


class TestRegimeSwitch(unittest.TestCase):
    def setUp(self):
        self.account = EnergyAccount(100.0)
        self.switch = RegimeSwitch(self.account)

    def test_a_fresh_system_grows(self):
        self.assertEqual(self.switch.evaluate(), GROW)

    def test_budget_drives_the_regime_as_charge_falls(self):
        self.assertEqual(self.switch.evaluate(), GROW)
        self.account.consume(20.0)                       # 80% -> GROW
        self.assertEqual(self.switch.evaluate(), GROW)
        self.account.consume(20.0)                       # 60% -> CONSERVE
        self.assertEqual(self.switch.evaluate(), CONSERVE)
        self.account.consume(35.0)                       # 25% -> EMERGENCY
        self.assertEqual(self.switch.evaluate(), EMERGENCY)

    def test_error_rate_over_the_window_forces_conserve(self):
        for _ in range(4):
            self.switch.feedback(success=False)
        self.switch.feedback(success=True)
        self.assertAlmostEqual(self.switch.recent_error_rate, 0.8)
        self.assertEqual(self.switch.evaluate(), CONSERVE)

    def test_a_success_resets_the_critical_run_but_not_the_error_window(self):
        # Two separate signals, shown together. A success ends the run of major
        # failures, so the regime is no longer EMERGENCY -- but the sliding
        # window still remembers 2 failures in 3 outcomes, so exploration
        # stays closed. Recovery from an emergency is not instantaneous, and
        # these two lines are the reason the counters are kept apart.
        self.switch.feedback(success=False, critical=True)
        self.switch.feedback(success=False, critical=True)
        self.assertEqual(self.switch.evaluate(), EMERGENCY)

        self.switch.feedback(success=True)
        self.assertEqual(self.switch.consecutive_critical_errors, 0)
        self.assertAlmostEqual(self.switch.recent_error_rate, 2 / 3)
        self.assertEqual(self.switch.evaluate(), CONSERVE)

        # Only a clean window reopens growth.
        self.switch.reset_feedback()
        self.assertEqual(self.switch.evaluate(), GROW)

    def test_no_samples_is_not_perfect_health(self):
        # 0.0 is "no evidence of failure", and sample_count says which.
        self.assertEqual(self.switch.recent_error_rate, 0.0)
        self.assertEqual(self.switch.sample_count, 0)

    def test_window_is_bounded(self):
        switch = RegimeSwitch(EnergyAccount(100.0), error_window=3)
        for _ in range(10):
            switch.feedback(success=False)
        self.assertEqual(switch.sample_count, 3)
        self.assertAlmostEqual(switch.recent_error_rate, 1.0)

    def test_plans_carry_their_permissions(self):
        grow = self.switch.plan()
        self.assertEqual(grow.max_concurrent_agents, MAX_CONCURRENT_AGENTS)
        self.assertTrue(grow.allow_divergent_thinking)
        self.assertFalse(grow.interrupt_long_tasks)
        self.assertEqual(grow.allows(9), MAX_CONCURRENT_AGENTS)

        self.account.consume(60.0)  # 40%
        conserve = self.switch.plan()
        self.assertEqual(conserve.max_concurrent_agents, 1)
        self.assertFalse(conserve.allow_divergent_thinking)
        self.assertEqual(conserve.allows(5), 1)

        self.account.consume(25.0)  # 15%
        emergency = self.switch.plan()
        self.assertTrue(emergency.interrupt_long_tasks)
        self.assertTrue(emergency.is_emergency)

    def test_emergency_from_errors_alone_keeps_a_full_budget(self):
        self.switch.feedback(success=False, critical=True)
        self.switch.feedback(success=False, critical=True)
        plan = self.switch.plan()
        self.assertEqual(plan.regime, EMERGENCY)
        self.assertEqual(self.account.ratio, 1.0)  # charge was never the issue
        self.assertIn("failure", plan.reason)

    def test_what_if_queries_do_not_pollute_the_transition_memory(self):
        self.switch.evaluate()                       # GROW, committed
        self.assertEqual(
            self.switch.evaluate(budget_ratio=0.1), EMERGENCY
        )                                            # a query, committed above
        # transitioned() compares a fresh reading against the last committed
        # observation; nothing is mutated by asking.
        before = self.switch.evaluate()
        self.assertFalse(self.switch.transitioned())
        self.assertEqual(before, GROW)

    def test_transitioned_detects_a_real_change(self):
        self.switch.evaluate()
        self.assertFalse(self.switch.transitioned())
        self.account.consume(45.0)  # 55%
        self.assertTrue(self.switch.transitioned())
        # Asking twice must not consume the event.
        self.assertTrue(self.switch.transitioned())
        self.assertEqual(self.switch.evaluate(), CONSERVE)
        self.assertFalse(self.switch.transitioned())

    def test_snapshot_is_a_report(self):
        snap = self.switch.snapshot()
        self.assertEqual(snap["regime"], GROW)
        self.assertEqual(snap["max_concurrent_agents"], MAX_CONCURRENT_AGENTS)
        snap["regime"] = "FORGED"
        self.assertEqual(self.switch.evaluate(), GROW)

    def test_constructor_validation(self):
        with self.assertRaises(TypeError):
            RegimeSwitch("not an account")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            RegimeSwitch(self.account, error_window=0)
        with self.assertRaises(ValueError):
            RegimeSwitch(self.account, critical_error_limit=0)
        with self.assertRaises(TypeError):
            self.switch.feedback(success="yes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# L4 -- the channel trades keys; the payload never crosses the wire
# ---------------------------------------------------------------------------


class TestSubstrateChannel(unittest.TestCase):
    def setUp(self):
        self.channel = SubstrateChannel()
        self.payload = "spec " * 10000  # ~50 KB, far larger than any context

    def test_put_then_get_returns_the_real_object(self):
        key = self.channel.put("spec_v1", self.payload)
        self.assertEqual(key, "spec_v1")
        self.assertIs(self.channel.get(key), self.payload)
        self.assertEqual(len(self.channel.get(key)), len(self.payload))

    def test_generated_keys_are_unique(self):
        first = self.channel.put(data="a")
        second = self.channel.put(data="b")
        self.assertNotEqual(first, second)
        self.assertEqual(self.channel.get(first), "a")
        self.assertEqual(self.channel.get(second), "b")

    def test_duplicate_key_is_refused_rather_than_overwritten(self):
        self.channel.put("spec", "first")
        with self.assertRaises(SubstrateKeyError):
            self.channel.put("spec", "second")
        # The original holder still reads what it put.
        self.assertEqual(self.channel.get("spec"), "first")

    def test_missing_key_raises_and_is_a_keyerror(self):
        with self.assertRaises(MissingSubstrate):
            self.channel.get("never_put")
        with self.assertRaises(KeyError):
            self.channel.get("never_put")

    def test_get_returns_the_live_object_so_mutation_is_shared(self):
        # This is the documented hazard, pinned so it cannot change silently.
        data = {"items": [1]}
        key = self.channel.put("d", data)
        got = self.channel.get(key)
        got["items"].append(2)
        self.assertEqual(self.channel.get(key)["items"], [1, 2])
        self.assertIs(self.channel.get(key), data)

    def test_get_snapshot_breaks_the_alias(self):
        data = {"items": [1]}
        key = self.channel.put("d", data)
        snap = self.channel.get_snapshot(key)
        snap["items"].append(99)
        self.assertEqual(self.channel.get(key)["items"], [1])

    def test_put_with_copy_protects_the_sender_from_later_mutation(self):
        data = {"items": [1]}
        key = self.channel.put("d", data, copy_data=True)
        data["items"].append(2)
        self.assertEqual(self.channel.get(key)["items"], [1])

    def test_release_is_idempotent_and_reports_bytes(self):
        key = self.channel.put("spec", self.payload)
        freed = self.channel.release(key)
        self.assertGreater(freed, 0)
        self.assertEqual(self.channel.release(key), 0)  # second pass is a no-op
        self.assertNotIn(key, self.channel)

    def test_stats_and_entries(self):
        self.channel.put("spec", self.payload)
        self.channel.put("other", "x")
        self.channel.get("spec")
        with self.assertRaises(MissingSubstrate):
            self.channel.get("nope")
        stats = self.channel.stats()
        self.assertEqual(stats["entries"], 2)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        entry = self.channel.entries()[0]
        self.assertLess(entry.announced_bytes, entry.size_bytes)

    def test_pool_is_inspectable(self):
        self.channel.put("spec", "x")
        self.assertIn("spec", self.channel.shared_memory_pool)
        self.assertEqual(len(self.channel), 1)
        self.assertEqual(self.channel.keys, ("spec",))

    def test_shared_memory_pool_is_the_live_dict(self):
        # Documented as read-only; pinned so the docstring stays honest.
        self.channel.put("spec", "x")
        self.assertIs(self.channel.shared_memory_pool, self.channel.shared_memory_pool)


class TestAgentProxy(unittest.TestCase):
    def setUp(self):
        self.channel = SubstrateChannel()
        self.manager = CompartmentManager()
        self.planner = self.manager.create("planner")
        self.coder = self.manager.create("coder")
        self.producer = AgentProxy("planner", self.channel, self.planner)
        self.consumer = AgentProxy("coder", self.channel, self.coder)
        self.payload = "diff --git a/x " * 4000  # ~60 KB

    def test_a_handover_costs_the_key_not_the_payload(self):
        key = self.producer.publish(self.payload, key="patch_1")
        body = self.producer.send_substrate(self.consumer, key)
        self.assertEqual(body, "substrate:patch_1")
        self.assertLess(len(body.encode()) * 100, len(self.payload))
        self.assertLess(self.producer.announced_bytes, len(self.payload))

    def test_the_recipient_resolves_the_real_full_payload(self):
        key = self.producer.publish(self.payload, key="patch_1")
        self.producer.send_substrate(self.consumer, key)
        keys = self.consumer.receive_pending()
        self.assertEqual(keys, ("patch_1",))
        self.assertEqual(self.consumer.receive_substrate(keys[0]), self.payload)

    def test_the_payload_never_enters_the_compartment_context(self):
        key = self.producer.publish(self.payload, key="patch_1")
        self.producer.send_substrate(self.consumer, key)
        self.consumer.receive_pending()
        admitted = " ".join(str(m) for m in self.coder.local_context)
        self.assertIn("patch_1", admitted)
        self.assertNotIn("diff --git", admitted)  # the payload stayed in the channel

    def test_handover_still_obeys_the_membrane(self):
        key = self.producer.publish("x", key="k")
        # Not delivered yet: inbox has it, the context does not.
        self.producer.send_substrate(self.consumer, key)
        self.assertEqual(self.coder.inbox_depth, 1)
        self.assertEqual(self.coder.local_context, ())

    def test_handing_over_an_absent_key_is_refused(self):
        with self.assertRaises(MissingSubstrate):
            self.producer.send_substrate(self.consumer, "never_put")

    def test_proxies_without_compartments_still_pass_keys(self):
        wire = SubstrateChannel()
        a = AgentProxy("a", wire)
        b = AgentProxy("b", wire)
        key = a.publish("payload", key="k1")
        body = a.send_substrate(b, key)
        self.assertEqual(body, "substrate:k1")
        self.assertEqual(b.receive_substrate(key), "payload")
        self.assertEqual(b.receive_pending(), ())  # no membrane, nothing to admit

    def test_snapshot_receive_protects_the_next_reader(self):
        key = self.producer.publish({"items": [1]}, key="d")
        got = self.consumer.receive_substrate(key, snapshot=True)
        got["items"].append(2)
        self.assertEqual(self.channel.get(key)["items"], [1])

    def test_constructor_validation(self):
        with self.assertRaises(TypeError):
            AgentProxy("x", "not a channel")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            AgentProxy("", self.channel)
        with self.assertRaises(ValueError):
            SubstrateChannel(prefix="  ")


# ---------------------------------------------------------------------------
# L6 -- night consolidation reads, never writes
# ---------------------------------------------------------------------------


class TestCircadian(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="metab-circadian-")
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.memory_path = os.path.join(self.tempdir, "long_term_memory.json")
        self.manager = CompartmentManager()
        self.planner = self.manager.create("planner")
        self.coder = self.manager.create("coder")

    def scheduler(self, hour=2, **kwargs):
        return CircadianScheduler(
            self.manager,
            self.memory_path,
            clock=lambda: timestamp_at(hour),
            **kwargs
        )

    def test_daytime_follows_the_injected_clock(self):
        self.assertTrue(self.scheduler(hour=14).is_daytime())
        self.assertFalse(self.scheduler(hour=2).is_daytime())
        self.assertFalse(self.scheduler(hour=23).is_daytime())
        self.assertTrue(self.scheduler(hour=6).is_daytime())   # window start
        self.assertFalse(self.scheduler(hour=22).is_daytime())  # window end

    def test_state_can_be_switched_explicitly(self):
        cycle = self.scheduler(hour=14)
        self.assertTrue(cycle.is_daytime())
        self.assertFalse(cycle.enter_nighttime())
        self.assertFalse(cycle.is_daytime())
        self.assertTrue(cycle.enter_daytime())
        self.assertFalse(cycle.toggle())

    def test_refresh_from_clock_re_derives_the_state(self):
        now = {"ts": timestamp_at(14)}
        cycle = CircadianScheduler(
            self.manager, self.memory_path, clock=lambda: now["ts"]
        )
        self.assertTrue(cycle.is_daytime())
        now["ts"] = timestamp_at(3)
        self.assertFalse(cycle.refresh_from_clock())

    def test_consolidating_during_the_day_is_refused(self):
        cycle = self.scheduler(hour=14)
        with self.assertRaises(CircadianError):
            cycle.run_offline_mode()
        self.assertFalse(os.path.exists(self.memory_path))

    def test_night_pass_collects_context_into_memory(self):
        self.planner.append("the scheduler leaked energy in the fast path")
        self.planner.append("gate the slow beat on the account again")
        self.coder.append("membrane test failed twice")

        report = self.scheduler(hour=2).run_offline_mode()

        self.assertEqual(report.compartments_scanned, 2)
        self.assertEqual(report.messages_seen, 3)
        self.assertEqual(report.fragments_created, 2)
        self.assertGreater(report.bytes_written, 0)
        self.assertTrue(os.path.exists(self.memory_path))

    def test_memory_file_is_valid_json_with_labelled_method(self):
        self.planner.append("a durable note about energy accounting")
        self.scheduler(hour=2).run_offline_mode()
        with open(self.memory_path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["method"], "structural-digest")
        self.assertEqual(len(document["fragments"]), 1)
        fragment = document["fragments"][0]
        self.assertEqual(fragment["source"], "planner")
        self.assertTrue(fragment["fragment_id"])
        self.assertIn("keywords", fragment)

    def test_second_pass_appends_nothing(self):
        self.planner.append("energy accounting is the point")
        first = self.scheduler(hour=2).run_offline_mode()
        second = self.scheduler(hour=2).run_offline_mode()
        self.assertEqual(first.fragments_created, 1)
        self.assertEqual(second.fragments_created, 0)
        self.assertEqual(second.fragments_skipped, 1)
        self.assertEqual(len(self.scheduler(hour=2).load_memory()), 1)

    def test_idempotence_survives_a_new_scheduler_instance(self):
        self.planner.append("one durable fact")
        self.scheduler(hour=2).run_offline_mode()
        later = self.scheduler(hour=3).run_offline_mode()
        self.assertEqual(later.fragments_created, 0)

    def test_new_context_produces_a_new_fragment(self):
        self.planner.append("first fact about energy")
        self.scheduler(hour=2).run_offline_mode()
        self.planner.append("second fact about membranes")
        report = self.scheduler(hour=2).run_offline_mode()
        self.assertEqual(report.fragments_created, 1)
        self.assertEqual(len(self.scheduler(hour=2).load_memory()), 2)

    def test_consolidation_never_mutates_a_compartment(self):
        # L6 is a reader. A night pass that pruned its sources would delete the
        # audit trail on a schedule.
        self.planner.append("a fact that must survive the night")
        before = tuple(self.planner.local_context)
        self.scheduler(hour=2).run_offline_mode()
        self.assertEqual(tuple(self.planner.local_context), before)

    def test_lysed_compartments_are_not_scanned(self):
        self.planner.append("surviving note")
        self.coder.append("doomed note")
        self.manager.destroy("coder")
        report = self.scheduler(hour=2).run_offline_mode()
        self.assertEqual(report.compartments_scanned, 1)
        sources = [f.source for f in self.scheduler(hour=2).load_memory()]
        self.assertEqual(sources, ["planner"])

    def test_empty_compartments_produce_no_fragment(self):
        report = self.scheduler(hour=2).run_offline_mode()
        self.assertEqual(report.fragments_created, 0)
        self.assertEqual(report.messages_seen, 0)
        self.assertFalse(os.path.exists(self.memory_path))

    def test_dry_run_writes_nothing(self):
        self.planner.append("a fact worth keeping")
        report = self.scheduler(hour=2).run_offline_mode(dry_run=True)
        self.assertEqual(report.fragments_created, 0)
        self.assertEqual(report.bytes_written, 0)
        self.assertFalse(os.path.exists(self.memory_path))
        self.assertTrue(report.warnings)

    def test_missing_file_is_an_empty_memory_not_an_error(self):
        self.assertEqual(self.scheduler(hour=2).load_memory(), [])

    def test_a_corrupt_file_raises_rather_than_starting_over(self):
        with open(self.memory_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(MemoryFileError):
            self.scheduler(hour=2).load_memory()

    def test_a_file_without_fragments_raises(self):
        with open(self.memory_path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1}, handle)
        with self.assertRaises(MemoryFileError):
            self.scheduler(hour=2).load_memory()

    def test_keywords_are_deduplicated_and_ranked(self):
        self.planner.append("membrane membrane membrane energy")
        self.planner.append("energy energy scheduler")
        self.scheduler(hour=2).run_offline_mode()
        fragment = self.scheduler(hour=2).load_memory()[0]
        self.assertEqual(fragment.keywords[0], "energy")   # 3 occurrences
        self.assertEqual(fragment.keywords[1], "membrane")  # 3, alphabetical tie
        self.assertEqual(len(set(fragment.keywords)), len(fragment.keywords))

    def test_duplicate_message_text_is_compressed_away(self):
        for _ in range(5):
            self.planner.append("the same line five times")
        self.scheduler(hour=2).run_offline_mode()
        fragment = self.scheduler(hour=2).load_memory()[0]
        self.assertEqual(fragment.message_count, 1)

    def test_constructor_validation(self):
        with self.assertRaises(TypeError):
            CircadianScheduler(None, self.memory_path)
        with self.assertRaises(ValueError):
            CircadianScheduler(self.manager, self.memory_path, day_start_hour=22,
                               day_end_hour=6)
        with self.assertRaises(ValueError):
            CircadianScheduler(self.manager, "")

    def test_window_is_reported(self):
        cycle = CircadianScheduler(
            self.manager, self.memory_path, day_start_hour=7, day_end_hour=19
        )
        self.assertEqual(cycle.window(), (7, 19))


# ---------------------------------------------------------------------------
# Cross-layer: L4 inside L7 inside L6
# ---------------------------------------------------------------------------


class TestCrossLayer(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="metab-cross-")
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.channel = SubstrateChannel()
        self.manager = CompartmentManager()
        self.planner = self.manager.create("planner")
        self.coder = self.manager.create("coder")
        self.producer = AgentProxy("planner", self.channel, self.planner)
        self.consumer = AgentProxy("coder", self.channel, self.coder)

    def test_key_handover_survives_consolidation(self):
        payload = "log line " * 5000
        self.planner.append("plan: hand the build log over by key")
        key = self.producer.publish(payload, key="build_log")
        self.producer.send_substrate(self.consumer, key)
        self.consumer.receive_pending()

        memory_path = os.path.join(self.tempdir, "ltm.json")
        cycle = CircadianScheduler(self.manager, memory_path, daytime=False)
        report = cycle.run_offline_mode()

        # The context that got summarised holds the key, and the key still
        # resolves: consolidation did not reach into the channel.
        self.assertEqual(report.fragments_created, 2)
        self.assertEqual(self.consumer.receive_substrate(key), payload)
        coder_fragment = [
            f for f in cycle.load_memory() if f.source == "coder"
        ][0]
        self.assertIn("build_log", coder_fragment.summary)

    def test_a_lysed_compartment_takes_no_substrate_and_no_fragment(self):
        key = self.producer.publish("payload", key="k")
        self.manager.destroy("coder")
        with self.assertRaises(CompartmentDestroyed):
            self.producer.send_substrate(self.consumer, key)
        memory_path = os.path.join(self.tempdir, "ltm.json")
        cycle = CircadianScheduler(self.manager, memory_path, daytime=False)
        report = cycle.run_offline_mode()
        self.assertEqual(report.compartments_scanned, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
