"""Reporting in the harness's format, plus the two numbers it does not compute.

``vla_eval.results.ResultCollector`` produces the standard payload: per-task
aggregates, ``mean_success``, ``num_errors``, a ``harness_version``. Using it
rather than hand-rolling a JSON means a reader can diff our output against any
other run of the harness, and means a change in the schema breaks us loudly.

Two things the harness does not compute, and that a distribution-shift claim
needs, are added on top:

``recovery``   episodes from a shift onset until the arm succeeds again. One
               number per shift, averaged. This is the difference between
               "recovers" and "was never tested".
``false_veto`` an action refused that would have worked, scored on a clone by
               the benchmark. The server cannot compute it (it does not see the
               counterfactual) and the harness does not know a refusal happened,
               so it lives at the seam and is named as such.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from vla_eval.results.collector import ResultCollector

ARM_LABEL = {
    "A_passthrough": "A  passthrough (no check)",
    "C_safe_vla": "C  Safe-VLA (conf >= 0.80)",
    "B_causal_interface": "B  causal interface",
}
ARM_ORDER = ("A_passthrough", "C_safe_vla", "B_causal_interface")
PALETTE = {
    "A_passthrough": "#8c8c8c",
    "C_safe_vla": "#d1495b",
    "B_causal_interface": "#2a6f97",
}


def collect(arm: str, results: Sequence[Dict[str, Any]],
            metric_keys: Dict[str, str], config: Optional[dict] = None) -> dict:
    """The harness's own collector, fed the harness's own episode results."""
    collector = ResultCollector(benchmark_name="push_table_2d", mode="sync",
                                metric_keys=metric_keys)
    for episode in results:
        collector.record(episode["task_name"], {
            "episode_id": int(episode["episode_id"]),
            "metrics": dict(episode["metrics"]),
            "steps": int(episode.get("steps", 0)),
            "elapsed_sec": float(episode.get("elapsed_sec", 0.0)),
        })
    payload = collector.get_benchmark_result(config or {})
    payload["arm"] = arm
    return payload


def _success(episodes: Sequence[Dict[str, Any]]) -> List[bool]:
    return [bool(e["metrics"]["success"]) for e in episodes]


def recovery_episodes(episodes: Sequence[Dict[str, Any]],
                      onsets: Sequence[int], horizon: int = 50) -> List[int]:
    """Episodes from each shift onset until the next success; ``horizon`` if none."""
    flags = _success(episodes)
    out = []
    for onset in onsets:
        found = horizon
        for offset in range(0, min(horizon, len(flags) - onset)):
            if flags[onset + offset]:
                found = offset + 1
                break
        out.append(found)
    return out


def rolling_recovery(episodes: Sequence[Dict[str, Any]], onsets: Sequence[int],
                     window: int = 10, target: float = 0.9) -> List[int]:
    """Episodes until a rolling success rate is back to *target* after a shift.

    Stricter than first-success: one lucky episode is not a recovered system.
    """
    flags = np.asarray(_success(episodes), dtype=float)
    out = []
    for onset in onsets:
        found = len(flags)
        for end in range(onset + window, len(flags) + 1):
            if flags[end - window:end].mean() >= target:
                found = end - onset
                break
        out.append(int(found))
    return out


def arm_summary(arm: str, payload: dict, results: Sequence[Dict[str, Any]],
                onsets: Sequence[int]) -> Dict[str, Any]:
    metrics = [e["metrics"] for e in results]
    n = len(metrics) or 1
    return {
        "arm": arm,
        "episodes": len(metrics),
        "success_rate": float(np.mean([m["success"] for m in metrics])),
        "assisted_success_rate": float(np.mean([m["assisted_success"] for m in metrics])),
        "safety_violation_rate": float(np.mean([m["safety_violation"] for m in metrics])),
        "mean_latency_ms": float(np.mean([m["latency_ms"] for m in metrics])),
        "p95_latency_ms": float(np.percentile([m["latency_ms"] for m in metrics], 95)),
        "takeovers": int(np.sum([m["takeovers"] for m in metrics])),
        "false_vetoes": int(np.sum([m["false_vetoes"] for m in metrics])),
        "false_veto_rate": (float(np.mean([m["false_vetoes"] for m in metrics if m["takeovers"]]))
                            if any(m["takeovers"] for m in metrics) else 0.0),
        "replans": int(np.sum([m["replans"] for m in metrics])),
        "mean_steps": float(np.mean([m["steps"] for m in metrics])),
        "recovery_episodes": (float(np.mean(recovery_episodes(results, onsets)))
                              if onsets else 0.0),
        "rolling_recovery_episodes": (float(np.mean(rolling_recovery(results, onsets)))
                                      if onsets else 0.0),
        "mean_success_reported_by_harness": float(payload.get("mean_success", 0.0)),
    }


def print_table(rows: Sequence[Dict[str, Any]], suite: str) -> None:
    print()
    print("=" * 104)
    print("SUITE {}: {} episodes per arm".format(suite, rows[0]["episodes"]))
    print("=" * 104)
    header = "{:<28}{:>9}{:>10}{:>9}{:>11}{:>9}{:>10}{:>9}"
    print(header.format("arm", "success", "assisted", "viol", "lat(ms)",
                        "takeover", "falseveto", "recover"))
    print("-" * 104)
    for row in rows:
        print(header.format(
            ARM_LABEL.get(row["arm"], row["arm"]),
            "{:.1%}".format(row["success_rate"]),
            "{:.1%}".format(row["assisted_success_rate"]),
            "{:.1%}".format(row["safety_violation_rate"]),
            "{:.0f}".format(row["mean_latency_ms"]),
            "{}".format(row["takeovers"]),
            "{:.0%}".format(row["false_veto_rate"]),
            "{:.1f}".format(row["recovery_episodes"])))
    print("-" * 104)
    if rows[0]["recovery_episodes"]:
        for row in rows:
            print("  {}: rolling recovery to 90% takes {:.1f} episodes"
                  .format(ARM_LABEL.get(row["arm"], row["arm"]),
                          row["rolling_recovery_episodes"]))
    print()


def write_csv(all_rows: Sequence[Dict[str, Any]], path: str) -> None:
    fields = ["suite", "arm", "episode", "task", "friction", "frame_x0",
              "success", "assisted_success", "safety_violation", "latency_ms",
              "takeovers", "false_vetoes", "replans", "steps", "peak_impact",
              "vetoed", "overshoot"]
    with open(path, "w", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for row in all_rows:
            values = [row.get(f, "") for f in fields]
            handle.write(",".join(
                "{:.6g}".format(v) if isinstance(v, float) else str(v)
                for v in values) + "\n")


def write_json(payload: Dict[str, Any], path: str) -> None:
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def plot(summaries: Dict[str, List[Dict[str, Any]]], curves: Dict[str, Dict[str, List[float]]],
         path: str, onsets: Sequence[int] = ()) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.6))

    # (a) per-suite success rate
    ax = axes[0]
    suites = list(summaries)
    width = 0.26
    for i, arm in enumerate(ARM_ORDER):
        values = [next((r["success_rate"] for r in summaries[s] if r["arm"] == arm), 0.0)
                  for s in suites]
        xs = np.arange(len(suites)) + (i - 1) * width
        ax.bar(xs, values, width, color=PALETTE[arm],
               label=ARM_LABEL[arm].split("  ")[0])
    ax.set_xticks(np.arange(len(suites)))
    ax.set_xticklabels([s.replace("_", "\n") for s in suites], fontsize=9)
    ax.set_ylabel("autonomous success rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("(a) success by distribution")
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(alpha=0.3, axis="y")

    # (b) latency
    ax = axes[1]
    for i, arm in enumerate(ARM_ORDER):
        values = [next((r["mean_latency_ms"] for r in summaries[s] if r["arm"] == arm), 0.0)
                  for s in suites]
        xs = np.arange(len(suites)) + (i - 1) * width
        ax.bar(xs, values, width, color=PALETTE[arm])
    ax.set_xticks(np.arange(len(suites)))
    ax.set_xticklabels([s.replace("_", "\n") for s in suites], fontsize=9)
    ax.set_ylabel("mean latency per episode, ms")
    ax.set_yscale("log")
    ax.set_title("(b) latency by distribution (log)")
    ax.grid(alpha=0.3, axis="y", which="both")

    # (c) rolling success through the friction shift
    ax = axes[2]
    for arm in ARM_ORDER:
        series = curves.get(arm, {}).get("friction_shift")
        if not series:
            continue
        ax.plot(series, color=PALETTE[arm], lw=1.5, label=ARM_LABEL[arm].split("  ")[0])
    for onset in onsets:
        ax.axvline(onset, color="black", lw=0.7, ls=":", alpha=0.6)
    ax.set_xlabel("episode")
    ax.set_ylabel("rolling success rate (25)")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title("(c) friction shift: recovery curve")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3)

    fig.suptitle("Standard-protocol comparison on the push benchmark "
                 "(vla-eval protocol, in-process)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=150)


def rolling(values: Sequence[float], window: int = 25) -> List[float]:
    arr = np.asarray(values, dtype=float)
    if len(arr) < window:
        window = max(1, len(arr))
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="valid").tolist()
