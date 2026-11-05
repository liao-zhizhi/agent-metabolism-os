"""What the benchmark integration has to be true about, independent of its numbers.

The experiment reports success rates and milliseconds; none of those are asserted
here. What is asserted is everything that would make the numbers meaningless if it
were wrong:

* the environment is a ``vla_eval`` benchmark, and the harness is the thing
  stepping it -- one episode really is run through ``SyncEpisodeRunner`` over the
  WebSocket protocol, not by a loop in this repository;
* the friction inversion recovers the coefficient from one observed
  deceleration, refuses to answer when the block was pushed by something else,
  and does not need the final pose (the pose is what a wall-contact push hides);
* the safe filter's false veto is counted only when the refused action would in
  fact have worked, and the counterfactual is evaluated on a clone so that the
  refusal does not change the world it is scored against;
* a regime change is the same regime change for every arm: the suite is an
  explicit episode list, and two runs of it produce identical frictions, frames
  and lanes;
* the causal arm does not get a compiled rule for free -- the registry is only in
  the causal arm, and a fresh server pays the derivation;
* the latency an arm reports is what its own operations cost, so a compiled push
  cannot be billed as a derivation and a veto cannot be billed as a roll-out.

Run: ``python3 -m pytest tests/test_benchmark_integration.py -q``.
Tests that need the harness's venv are skipped when ``vla_eval`` is absent.
"""
from __future__ import annotations

import copy
import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.benchmark_integration import policies  # noqa: E402
from experiments.benchmark_integration import shift_suites  # noqa: E402


def _has_vla_eval() -> bool:
    try:
        import vla_eval  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


HAVE_HARNESS = _has_vla_eval()


class TestPlanner(unittest.TestCase):
    """The arithmetic that makes a wrong belief legible."""

    def test_planned_speed_stops_at_the_target(self):
        obs = policies.Obs(x=0.16, y=0.14, vx=0.0, vy=0.0, target_x=0.66,
                           target_y=0.14, frame_x0=0.60, frame_x1=0.72,
                           steps_remaining=30, elapsed=0.0)
        chunk = policies.plan_chunk(obs, mu=0.35)
        launch = float(chunk[0, 0])
        self.assertAlmostEqual(launch, policies.required_speed(0.5, 0.35), places=6)
        # Braking distance under the *true* friction, in continuous time.
        travel = launch ** 2 / (2.0 * 0.35 * policies.GRAVITY)
        self.assertAlmostEqual(travel, 0.5, places=6)

    def test_only_the_first_row_of_a_chunk_is_a_command(self):
        obs = policies.Obs(0.16, 0.14, 0.0, 0.0, 0.66, 0.14, 0.60, 0.72, 30, 0.0)
        chunk = policies.plan_chunk(obs, mu=0.35, chunk_size=10)
        self.assertEqual(chunk.shape, (10, 2))
        self.assertTrue(np.all(chunk[1:] == 0.0))

    def test_wrong_belief_overshoots_or_undershoots_by_the_ratio(self):
        launch = policies.required_speed(0.5, 0.35)
        # Same command, different table.
        slippery = launch ** 2 / (2.0 * 0.18 * policies.GRAVITY)
        rough = launch ** 2 / (2.0 * 0.54 * policies.GRAVITY)
        self.assertGreater(slippery, 0.9)
        self.assertAlmostEqual(slippery / rough, 3.0, places=6)

    def test_distance_already_covered_asks_for_nothing(self):
        obs = policies.Obs(0.70, 0.14, 0.0, 0.0, 0.66, 0.14, 0.60, 0.72, 10, 0.5)
        self.assertTrue(np.all(policies.plan_chunk(obs, 0.35) == 0.0))


class TestFrictionInversion(unittest.TestCase):
    """One difference quotient, and the cases where it must refuse."""

    def test_recovers_the_true_coefficient(self):
        for mu in (0.18, 0.35, 0.54):
            v0 = 1.2
            v1 = v0 - mu * policies.GRAVITY * policies.CONTROL_DT
            self.assertAlmostEqual(
                policies.estimate_friction(v0, v1, policies.CONTROL_DT), mu, places=6)

    def test_refuses_when_the_block_sped_up(self):
        self.assertIsNone(
            policies.estimate_friction(0.8, 0.95, policies.CONTROL_DT))

    def test_refuses_when_the_block_was_already_stopped(self):
        self.assertIsNone(
            policies.estimate_friction(0.0, 0.0, policies.CONTROL_DT))

    def test_refuses_a_deceleration_too_small_to_be_a_measurement(self):
        self.assertIsNone(
            policies.estimate_friction(0.05, 0.045, policies.CONTROL_DT))

    def test_refuses_an_implausible_coefficient(self):
        # A wall contact can produce an arbitrarily large deceleration. That is
        # not a friction and the inversion says so instead of clamping a number.
        self.assertIsNone(
            policies.estimate_friction(3.0, 0.0, policies.CONTROL_DT))

    def test_clamps_a_reading_inside_the_tables_range(self):
        # A near-stationary block gives a small quotient; it is a friction, just
        # a low one, so it is clamped to the floor rather than discarded.
        self.assertAlmostEqual(
            policies.estimate_friction(0.40, 0.38, policies.CONTROL_DT),
            policies.MU_FLOOR, places=6)


class TestSuites(unittest.TestCase):
    """A distribution shift has to be an explicit, shared list of worlds."""

    def test_shift_windows_are_coherent(self):
        frictions, onsets = shift_suites.friction_shift_episodes(300)
        self.assertEqual(len(frictions), 300)
        self.assertTrue(all(o % shift_suites.WINDOW == 0 for o in onsets))
        # Each window is one value: drift arrives and stays, it does not flicker.
        for start in range(0, 300, shift_suites.WINDOW):
            window = set(frictions[start:start + shift_suites.WINDOW])
            self.assertEqual(len(window), 1, (start, window))

    def test_both_arms_would_see_the_same_episode_list(self):
        first = shift_suites.build("friction_shift", 120)
        second = shift_suites.build("friction_shift", 120)
        self.assertEqual([t["friction"] for t in first["episodes"]],
                         [t["friction"] for t in second["episodes"]])
        self.assertEqual([t["frame"] for t in first["episodes"]],
                         [t["frame"] for t in second["episodes"]])
        self.assertEqual(first["onsets"], second["onsets"])

    def test_layout_shift_moves_the_task_and_not_the_physics(self):
        suite = shift_suites.build("layout_shift", 120)
        self.assertEqual(len(set(suite["frictions"])), 1)
        self.assertEqual(len(set(suite["frames"])), 2)

    def test_nominal_has_no_onsets(self):
        suite = shift_suites.build("nominal", 60)
        self.assertEqual(suite["onsets"], [])


@unittest.skipUnless(HAVE_HARNESS, "vla_eval not installed in this interpreter")
class TestHarnessContract(unittest.TestCase):
    """The parts that only mean something with the harness present."""

    def test_benchmark_satisfies_the_harness_abc(self):
        from vla_eval.benchmarks.base import Benchmark
        from experiments.benchmark_integration.env_adapter import PushTableBenchmark

        benchmark = PushTableBenchmark()
        self.assertIsInstance(benchmark, Benchmark)
        self.assertEqual(len(benchmark.get_tasks()), 5)
        self.assertIn("success", benchmark.get_metric_keys())
        self.assertEqual(benchmark.get_action_spec()["actions"].dims, 2)

    def test_a_real_episode_runs_through_the_harness_runner(self):
        from experiments.benchmark_integration.env_adapter import episode_task
        from experiments.benchmark_integration.runner import run_arm

        episodes = [episode_task(0, "nominal", 0.35)]
        results, server = run_arm("B", copy.deepcopy(episodes), "nominal")
        self.assertEqual(len(results), 1)
        self.assertEqual(server.witness_calls > 0, True)
        metrics = results[0]["metrics"]
        for key in ("success", "safety_violation", "latency_ms", "takeovers"):
            self.assertIn(key, metrics)
        self.assertTrue(metrics["success"])


@unittest.skipUnless(HAVE_HARNESS, "vla_eval not installed in this interpreter")
class TestArms(unittest.TestCase):
    """Each arm's mechanism, tested on its own bookkeeping."""

    @staticmethod
    def _obs(x=0.16, vx=0.0, steps=30):
        from experiments.benchmark_integration.policies import Obs
        return Obs(x=x, y=0.14, vx=vx, vy=0.0, target_x=0.66, target_y=0.14,
                   frame_x0=0.60, frame_x1=0.72, steps_remaining=steps, elapsed=0.0)

    class _Row:
        class _Episode:
            episode_id = "e"

        episode = _Episode()
        step = 0

    def test_passthrough_pays_a_derivation_for_every_chunk(self):
        from experiments.benchmark_integration.servers import build_arm

        server = build_arm("A", seed=0)
        obs = {"state": np.array([0.16, 0.14, 0.0, 0.0, 0.66, 0.14, 0.60, 0.72, 30, 0.0],
                                 dtype=np.float32)}
        first = server.predict(obs, self._Row())
        self.assertEqual(first["inference_ms"], policies.VLA_MS)
        self.assertEqual(server.lookup_calls, 0)
        # Step through the chunk; the next derivation is billed again.
        for _ in range(policies.CHUNK_SIZE - 1):
            server.predict(obs, self._Row())
        again = server.predict(obs, self._Row())
        self.assertEqual(again["inference_ms"], policies.VLA_MS)

    def test_causal_arm_reuses_a_compiled_rule(self):
        from experiments.benchmark_integration.servers import build_arm

        server = build_arm("B", seed=0)
        obs = {"state": np.array([0.16, 0.14, 0.0, 0.0, 0.66, 0.14, 0.60, 0.72, 30, 0.0],
                                 dtype=np.float32)}
        server.predict(obs, self._Row())
        server.episodes.clear()  # a new episode, same regime
        for _ in range(policies.CHUNK_SIZE):
            last = server.predict(obs, self._Row())
        self.assertGreaterEqual(server.lookup_calls, 1)
        self.assertLess(last["inference_ms"], policies.VLA_MS)

    def test_causal_arm_corrects_its_belief_from_a_deceleration(self):
        from experiments.benchmark_integration.servers import build_arm

        server = build_arm("B", seed=0)
        self.assertAlmostEqual(server.belief, 0.35, places=6)
        first = self._obs(x=0.16)
        server.predict({"state": np.array([first.x, first.y, first.vx, first.vy,
                                           first.target_x, first.target_y,
                                           first.frame_x0, first.frame_x1, 30, 0.0],
                                          dtype=np.float32)}, self._Row())
        # The block decelerated faster than 0.35 would explain.
        drift = self._obs(x=0.20, vx=0.80)
        server.predict({"state": np.array([drift.x, drift.y, drift.vx, drift.vy,
                                           drift.target_x, drift.target_y,
                                           drift.frame_x0, drift.frame_x1, 28, 0.2],
                                          dtype=np.float32)}, self._Row())
        self.assertGreater(server.belief, 0.35)

    def test_safe_filter_veto_carries_the_action_it_refused(self):
        from experiments.benchmark_integration.servers import build_arm

        server = build_arm("C", seed=0)
        server.confidence_model.alert = 1.0  # a failure was just observed
        obs = {"state": np.array([0.16, 0.14, 0.0, 0.0, 0.66, 0.14, 0.60, 0.72, 30, 0.0],
                                 dtype=np.float32)}
        action = server.predict(obs, self._Row())
        self.assertTrue(action.get("vetoed"))
        self.assertTrue(action.get("takeover"))
        self.assertIn("proposed_actions", action)
        # The benchmark cannot score the refusal without the refused action.
        self.assertGreater(float(np.abs(action["proposed_actions"]).sum()), 0.0)


@unittest.skipUnless(HAVE_HARNESS, "vla_eval not installed in this interpreter")
class TestBenchmarkScoring(unittest.TestCase):
    """The environment's own judgement, on hand-fed actions."""

    @staticmethod
    def _benchmark(friction=0.35):
        from experiments.benchmark_integration.env_adapter import (
            PushTableBenchmark, episode_task,
        )
        benchmark = PushTableBenchmark()
        benchmark.reset(episode_task(0, "nominal", friction))
        return benchmark

    def test_a_veto_is_not_a_success_even_when_the_action_would_have_worked(self):
        benchmark = self._benchmark(friction=0.35)
        launch = policies.required_speed(0.5, 0.35)
        chunk = np.zeros((10, 2))
        chunk[0, 0] = launch
        veto = {"vetoed": True, "takeover": True, "proposed_actions": chunk}
        result = benchmark.step(veto)
        metrics = benchmark.get_step_result(result)
        self.assertFalse(metrics["success"])
        self.assertTrue(metrics["assisted_success"])
        self.assertEqual(metrics["false_vetoes"], 1)
        self.assertGreaterEqual(metrics["latency_ms"], policies.HUMAN_MS)

    def test_a_correct_push_succeeds_and_costs_only_the_inference(self):
        benchmark = self._benchmark()
        launch = policies.required_speed(0.5, 0.35)
        result = None
        benchmark.step({"actions": np.array([[launch, 0.0]]), "inference_ms": 200.0})
        for _ in range(20):
            result = benchmark.step({"actions": np.zeros(2)})
            if benchmark.check_done(result):
                break
        metrics = benchmark.get_step_result(result)
        self.assertTrue(metrics["success"])
        self.assertFalse(metrics["safety_violation"])
        self.assertLess(metrics["latency_ms"], 400.0)

    def test_a_slippery_table_overshoots_and_is_flagged(self):
        benchmark = self._benchmark(friction=0.18)
        launch = policies.required_speed(0.5, 0.35)
        result = None
        benchmark.step({"actions": np.array([[launch, 0.0]])})
        for _ in range(20):
            result = benchmark.step({"actions": np.zeros(2)})
            if benchmark.check_done(result):
                break
        metrics = benchmark.get_step_result(result)
        self.assertFalse(metrics["success"])
        self.assertTrue(metrics["safety_violation"] or metrics["overshoot"])


if __name__ == "__main__":
    unittest.main()
