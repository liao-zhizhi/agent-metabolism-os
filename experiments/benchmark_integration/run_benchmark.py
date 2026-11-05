"""Run the standard-protocol comparison: three suites, three arms, one episode list.

    .venv/bin/python -m experiments.benchmark_integration.run_benchmark

The arms are served as ``vla_eval`` model servers and stepped by the harness's
``SyncEpisodeRunner``; the results are aggregated by the harness's
``ResultCollector``. What this file adds is the matrix (suite x arm), the
distribution-shift accounting, and the report.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time
from typing import Any, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from . import report, shift_suites
from .env_adapter import HORIZON, PushTableBenchmark
from .runner import run_arm

ARMS = ("A", "C", "B")


def environment_report() -> str:
    """Which solver, which harness, and whether the real simulators are importable."""
    lines = []
    lines.append("vla-eval: installed ({})".format(_version("vla_eval")))
    lines.append("physics solver: {}".format(_physics_engine()))
    for module in ("libero.libero", "robosuite", "mujoco", "pybullet", "pymunk"):
        lines.append("{}: {}".format(module, "importable" if _importable(module)
                                     else "not importable"))
    clone = os.environ.get("LIBERO_ROOT", "/tmp/LIBERO")
    lines.append("LIBERO checkout: {} ({})".format(
        clone, "present" if os.path.isdir(clone) else "absent"))
    return "\n".join(lines)


def _version(name: str) -> str:
    try:
        module = __import__(name)
    except Exception:  # noqa: BLE001
        return "missing"
    return str(getattr(module, "__version__", "?"))


def _importable(name: str) -> bool:
    try:
        __import__(name)
    except Exception:  # noqa: BLE001
        return False
    return True


def _physics_engine() -> str:
    from experiments.physics_sandbox import engine_name, engine_notice
    return "{} ({})".format(engine_name(), engine_notice())


def _replot(outdir: str) -> int:
    """Re-render the figure from the artifacts already on disk."""
    import csv
    import json

    data = json.load(open(os.path.join(outdir, "benchmark_results.json")))
    summaries = {k: [{**row, "suite": k} for row in v]
                 for k, v in data["suites"].items()}
    curves: Dict[str, Dict[str, List[float]]] = {}
    grouped: Dict[tuple, List[int]] = {}
    with open(os.path.join(outdir, "benchmark_episodes.csv")) as handle:
        for row in csv.DictReader(handle):
            grouped.setdefault((row["arm"], row["suite"]), []).append(
                int(row["success"]))
    for (arm, suite), values in grouped.items():
        curves.setdefault(arm, {})[suite] = report.rolling(values)
    path = os.path.join(outdir, "benchmark_figure.png")
    onsets = shift_suites.build("friction_shift",
                                data.get("episodes_per_arm_per_suite", 300))["onsets"]
    report.plot(summaries, curves, path, onsets=onsets)
    print("wrote {}".format(path))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="all",
                        choices=("all",) + shift_suites.SUITES)
    parser.add_argument("--arms", default="ABC",
                        help="subset of A (passthrough), C (safe filter), B (causal)")
    parser.add_argument("--episodes", type=int, default=shift_suites.DEFAULT_EPISODES)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--replot", action="store_true",
                        help="re-render the figure from saved artifacts only")
    parser.add_argument("--outdir",
                        default=os.path.dirname(os.path.abspath(__file__)))
    args = parser.parse_args(argv)

    if args.replot:
        return _replot(args.outdir)

    suites = list(shift_suites.SUITES) if args.suite == "all" else [args.suite]
    arms = [a for a in args.arms if a in ARMS] or list(ARMS)

    print(environment_report())
    print("episodes per arm per suite: {}  (horizon {} control steps at 10 Hz)"
          .format(args.episodes, HORIZON))
    print()

    episode_rows: List[Dict[str, Any]] = []
    summaries: Dict[str, List[Dict[str, Any]]] = {}
    curves: Dict[str, Dict[str, List[float]]] = {}
    standard: Dict[str, Any] = {}
    counters: Dict[str, Any] = {}
    started = time.time()

    for suite in suites:
        definition = shift_suites.build(suite, args.episodes)
        benchmark = PushTableBenchmark(suite=suite, seed=args.seed)
        metric_keys = benchmark.get_metric_keys()
        summaries[suite] = []
        for arm in arms:
            # A fresh server per (suite, arm): the belief carried across episodes
            # is part of the arm, but it does not leak from one suite to another.
            episodes = copy.deepcopy(definition["episodes"])
            results, server = run_arm(arm, episodes, suite, seed=args.seed)
            payload = report.collect(arm, results, metric_keys, {
                "suite": suite,
                "seed": args.seed,
                "episodes": args.episodes,
                "params": {"seed": args.seed},
            })
            standard["{}/{}".format(suite, arm)] = payload
            counters.setdefault(arm, {})[suite] = server.counters
            row = report.arm_summary(server.arm_name, payload, results,
                                     definition["onsets"])
            row["suite"] = suite
            summaries[suite].append(row)
            curves.setdefault(server.arm_name, {})[suite] = report.rolling(
                [1.0 if e["metrics"]["success"] else 0.0 for e in results])
            for episode in results:
                task = episodes[int(episode["episode_id"])]
                episode_rows.append({
                    "suite": suite,
                    "arm": server.arm_name,
                    "episode": int(episode["episode_id"]),
                    "task": episode["task_name"],
                    "friction": float(task["friction"]),
                    "frame_x0": float(task["frame"][0]),
                    "success": int(episode["metrics"]["success"]),
                    "assisted_success": int(episode["metrics"]["assisted_success"]),
                    "safety_violation": int(episode["metrics"]["safety_violation"]),
                    "latency_ms": float(episode["metrics"]["latency_ms"]),
                    "takeovers": int(episode["metrics"]["takeovers"]),
                    "false_vetoes": int(episode["metrics"]["false_vetoes"]),
                    "replans": int(episode["metrics"]["replans"]),
                    "steps": int(episode["metrics"]["steps"]),
                    "peak_impact": float(episode["metrics"]["peak_impact"]),
                    "vetoed": int(episode["metrics"]["vetoed"]),
                    "overshoot": int(episode["metrics"]["overshoot"]),
                })
        report.print_table(summaries[suite], suite)

    print("=" * 104)
    print("DISTRIBUTION-SHIFT VERDICT")
    print("=" * 104)
    nominal = {r["arm"]: r for r in summaries.get("nominal", [])}
    shifted = {r["arm"]: r for r in summaries.get("friction_shift", [])}
    if not nominal or not shifted:
        print("  (the verdict needs both the nominal and the friction_shift suites; "
              "run with --suite all)")
    for arm in [a for a in report.ARM_ORDER if a in nominal and a in shifted]:
        label = report.ARM_LABEL.get(arm, arm)
        drop = nominal[arm]["success_rate"] - shifted[arm]["success_rate"]
        print("  {:<30} nominal {:.1%}  shifted {:.1%}  drop {:+.1f} pp  "
              "recovery {:.1f} episodes  latency {:.0f} ms"
              .format(label, nominal[arm]["success_rate"],
                      shifted[arm]["success_rate"], 100.0 * drop,
                      shifted[arm]["recovery_episodes"],
                      shifted[arm]["mean_latency_ms"]))
    print("=" * 104)

    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "benchmark_episodes.csv")
    json_path = os.path.join(args.outdir, "benchmark_results.json")
    standard_path = os.path.join(args.outdir, "benchmark_standard_results.json")
    figure_path = os.path.join(args.outdir, "benchmark_figure.png")
    report.write_csv(episode_rows, csv_path)
    report.write_json({
        "protocol": "vla-eval 0.9.0 (in-process: harness server, Connection, "
                    "SyncEpisodeRunner, ResultCollector)",
        "physics": _physics_engine(),
        "episodes_per_arm_per_suite": args.episodes,
        "seed": args.seed,
        "horizon_steps": HORIZON,
        "suites": {k: v for k, v in summaries.items()},
        "server_counters": counters,
        "wall_clock_s": round(time.time() - started, 1),
    }, json_path)
    report.write_json(standard, standard_path)
    onsets = shift_suites.build("friction_shift", args.episodes)["onsets"]
    report.plot(summaries, curves, figure_path, onsets=onsets)
    print("wrote {}".format(csv_path))
    print("wrote {}".format(json_path))
    print("wrote {} (the harness's own BenchmarkResult payloads)".format(standard_path))
    print("wrote {}".format(figure_path))
    print("wall clock: {:.0f} s".format(time.time() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
