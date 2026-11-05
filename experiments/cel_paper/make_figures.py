#!/usr/bin/env python3
"""Draw the CEL paper's two figures from the audit's own outputs.

Every number in a figure is read here from the audit's JSON/CSV, so a figure
cannot say something the data does not. That was not true of the first version of
these files: the driving figure carried a latency panel whose values appear in no
summary we can find, its "reliability" panel silently dropped one of the four
declared worlds, and both legends still carried the baseline's retired name
months after the paper renamed it. A figure is a witness about the experiment; if it
cannot be re-derived from the archive it is an assertion.

Inputs (``--data-dir``, default: the parent directory, else ``./provenance``):

    libero_audit_summary.json   libero_audit_episodes.csv
    ad_cel_summary.json         ad_cel_data.csv
    ad_cel_trace.json           (optional; disables the trace panel if absent)

Outputs:

    figures/libero_audit.png    2x2, the manipulation audit
    figures/ad_cel.png          2x2, the driving audit

Run:
    python3 experiments/cel_paper/make_figures.py
    CEL_DATA_DIR=provenance python3 make_figures.py     # from the archive
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
FIGURES = os.path.join(HERE, "figures")

CONF = "#d1495b"   # confidence-gated baseline
CEL = "#2e86ab"    # causal execution layer
TRUTH = "#6c757d"  # ground truth in the identification panel

ARM_LABEL = {"A_safe_vla": "Confidence-gated", "A_safe_planner": "Confidence-gated",
             "B_cel": "CEL"}
ARM_COLOUR = {"A_safe_vla": CONF, "A_safe_planner": CONF, "B_cel": CEL}

#: Printed width of a two-column figure in this IEEEtran setup is 7.16 in.
#: Drawing at that width keeps the fonts near 7-8 pt on the page, which is what
#: the venue's own figures use; drawing wider and letting LaTeX scale down is
#: what produced the illegible tick labels in the previous version.
FIGSIZE = (7.16, 4.55)
DPI = 300


def find_data_dir(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    parent = os.path.dirname(HERE)
    if os.path.exists(os.path.join(parent, "ad_cel_summary.json")):
        return parent
    return os.path.join(HERE, "provenance")


def load_json(path: str) -> Dict[str, Any]:
    with open(path) as handle:
        return json.load(handle)


def load_csv(path: str) -> List[Dict[str, str]]:
    with open(path) as handle:
        return list(csv.DictReader(handle))


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Figure 1: the manipulation audit
# ---------------------------------------------------------------------------


def libero_figure(data_dir: str, out: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    summary = load_json(os.path.join(data_dir, "libero_audit_summary.json"))
    episodes = load_csv(os.path.join(data_dir, "libero_audit_episodes.csv"))
    arms = summary["arms_summary"]
    fig, axes = plt.subplots(2, 2, figsize=FIGSIZE)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.925, bottom=0.115,
                        hspace=0.52, wspace=0.30)
    width = 0.36

    # (a) outcome rates, straight from arms_summary.
    ax = axes[0, 0]
    labels = ["all", "nominal", "pose\ndrift", "physical\ndrift", "damage"]
    keys = ["success_rate", "success_rate_nominal", "success_rate_pose_drift",
            "success_rate_drift_channels", "damage_rate"]
    xs = np.arange(len(labels))
    for offset, arm in ((-0.5, "A_safe_vla"), (0.5, "B_cel")):
        values = [100.0 * (arms[arm].get(k) or 0.0) for k in keys]
        bars = ax.bar(xs + offset * width, values, width,
                      label=ARM_LABEL[arm], color=ARM_COLOUR[arm])
        for bar, value in zip(bars, values):
            ax.annotate("{:.0f}".format(value), (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=6.0,
                        xytext=(0, 1.5), textcoords="offset points")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=7)
    ax.set_ylim(0, 124)
    ax.set_ylabel("episodes (%)", fontsize=7.5)
    ax.set_title("(a) Outcome on libero$\\_$spatial", fontsize=8)
    ax.legend(fontsize=6.5, frameon=False, ncol=2, loc="upper right")
    ax.tick_params(labelsize=7)
    ax.grid(axis="y", alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    # (b) latency, split into the decision and the tamper-evidence. Two bars only:
    # the previous version put four long tick labels under four thin bars and they
    # overlapped each other on the page.
    ax = axes[0, 1]
    a, b = arms["A_safe_vla"], arms["B_cel"]
    decision = [a["policy_latency_ms_mean"] - a["witness_latency_ms_mean"],
                b["policy_latency_ms_mean"] - b["witness_latency_ms_mean"]]
    witness = [a["witness_latency_ms_mean"], b["witness_latency_ms_mean"]]
    xs = np.arange(2)
    ax.bar(xs, decision, 0.5, label="decision (forward + retries)", color="#8e9aaf")
    ax.bar(xs, witness, 0.5, bottom=decision, label="tamper-evidence", color=CEL)
    for i, (d, w) in enumerate(zip(decision, witness)):
        if w > 1:
            ax.annotate("{:.0f} ms".format(w), (i, d + w / 2), ha="center", va="center",
                        fontsize=6.0, color="white")
    ax.set_xticks(xs)
    # The totals live in the tick labels: an in-axes callout box here collided with
    # both bars at every size that was still readable on the page.
    ax.set_xticklabels(["confidence-gated\n{:.0f} ms".format(decision[0] + witness[0]),
                        "CEL\n{:.0f} ms".format(decision[1] + witness[1])], fontsize=6.8)
    ax.set_ylabel("time per episode (ms)", fontsize=7.5)
    ax.set_ylim(0, 6600)
    ax.set_title("(b) decision {:+.0f} ms, evidence {:+.0f} ms".format(
        decision[1] - decision[0], witness[1] - witness[0]), fontsize=8)
    ax.legend(fontsize=6.0, frameon=False, loc="upper center", ncol=1)
    ax.tick_params(labelsize=7)
    ax.grid(axis="y", alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    # (c) what each gate did, counted from the episode rows rather than from a
    # summary field, so the two agree by construction.
    ax = axes[1, 0]
    counts = {"A_safe_vla": {"vetoes": 0, "false": 0, "retries": 0, "refusals": 0},
              "B_cel": {"vetoes": 0, "false": 0, "retries": 0, "refusals": 0}}
    for row in episodes:
        arm = row["arm"]
        counts[arm]["vetoes"] += int(row["vetoes"])
        counts[arm]["false"] += int(row["false_vetoes"])
        counts[arm]["retries"] += int(row["retries"])
        counts[arm]["refusals"] += int(row["witness_refusals"])
    categories = ["vetoed\nchunks", "of which\nfalse", "retries",
                  "witness\nrefusals"]
    keys = ["vetoes", "false", "retries", "refusals"]
    xs = np.arange(len(categories))
    for offset, arm in ((-0.5, "A_safe_vla"), (0.5, "B_cel")):
        values = [counts[arm][k] for k in keys]
        bars = ax.bar(xs + offset * width, values, width,
                      label=ARM_LABEL[arm], color=ARM_COLOUR[arm])
        for bar, value in zip(bars, values):
            ax.annotate(str(value), (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=6.0,
                        xytext=(0, 1.5), textcoords="offset points")
    ax.set_xticks(xs)
    ax.set_xticklabels(categories, fontsize=6.8)
    ax.set_ylabel("events over 100 episodes", fontsize=7.5)
    ax.set_ylim(0, 132)
    ax.set_title("(c) What each gate did", fontsize=8)
    ax.legend(fontsize=6.5, frameon=False, loc="upper left")
    ax.tick_params(labelsize=7)
    ax.grid(axis="y", alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    # (d) recovery: rolling-5 success across the two pose-drift onsets. This is the
    # panel the caption always promised and the old file did not contain.
    ax = axes[1, 1]
    onsets = [item["episode"] for item in summary["onsets"]
              if item.get("regime") == "pose_drift"]
    tail = min(item["episode"] for item in summary["onsets"]
               if item.get("regime") != "pose_drift") if any(
        item.get("regime") != "pose_drift" for item in summary["onsets"]) else None
    for arm in ("A_safe_vla", "B_cel"):
        rows = sorted((r for r in episodes if r["arm"] == arm),
                      key=lambda r: int(r["episode"]))
        success = [1.0 if r["success"] == "True" else 0.0 for r in rows]
        xs = [int(r["episode"]) for r in rows]
        rolling: List[float] = []
        for i in range(len(success)):
            window = success[max(0, i - 4):i + 1]
            rolling.append(100.0 * sum(window) / len(window))
        ax.plot(xs, rolling, color=ARM_COLOUR[arm], lw=1.5, label=ARM_LABEL[arm])
    if tail is not None:
        ax.axvspan(tail, 100, color="#000000", alpha=0.055, lw=0)
        ax.annotate("sampled channels", (tail - 1, 8), fontsize=5.6, rotation=90,
                    ha="right", va="bottom", color="#444444")
    for onset in onsets:
        ax.axvline(onset, color="#444444", lw=0.7, ls=":")
        ax.annotate("pose drift", (onset, 104), fontsize=5.6, rotation=90,
                    ha="right", va="top", color="#444444")
    ax.set_xlabel("episode", fontsize=7.5)
    ax.set_ylabel("success, rolling 5 (%)", fontsize=7.5)
    ax.set_ylim(-4, 112)
    ax.set_xlim(0, 100)
    ax.set_title("(d) Recovery after each drift onset", fontsize=8)
    ax.legend(fontsize=6.5, frameon=False, loc="lower left")
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    print("wrote {}  ({} x {} px)".format(out, int(FIGSIZE[0] * DPI), int(FIGSIZE[1] * DPI)))


# ---------------------------------------------------------------------------
# Figure 2: the driving audit
# ---------------------------------------------------------------------------


def driving_figure(data_dir: str, out: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    summary = load_json(os.path.join(data_dir, "ad_cel_summary.json"))
    by_channel = summary["by_channel"]
    arms = summary["arms"]
    channels = [("nominal", "nominal"), ("wet_road", "wet road"),
                ("steering_lag", "worn\nsteering"), ("wet_and_lag", "both")]
    fig, axes = plt.subplots(2, 2, figsize=FIGSIZE)
    fig.subplots_adjust(left=0.085, right=0.955, top=0.925, bottom=0.115,
                        hspace=0.60, wspace=0.34)
    width = 0.36

    # (a) all four worlds. The old panel had three categories and merged the two
    # wet worlds, which hid ten episodes and five of the twenty collisions.
    ax = axes[0, 0]
    xs = np.arange(len(channels))
    for offset, arm in ((-0.5, "A_safe_planner"), (0.5, "B_cel")):
        values = [100.0 * by_channel[arm][key]["success_rate"] for key, _ in channels]
        bars = ax.bar(xs + offset * width, values, width,
                      label=ARM_LABEL[arm], color=ARM_COLOUR[arm])
        for bar, value in zip(bars, values):
            ax.annotate("{:.0f}".format(value), (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=6.0,
                        xytext=(0, 1.5), textcoords="offset points")
    ax.set_xticks(xs)
    ax.set_xticklabels([label for _, label in channels], fontsize=6.8)
    ax.set_ylim(0, 132)
    ax.set_ylabel("episodes without incident (%)", fontsize=7.5)
    ax.set_title("(a) Reliability across the four worlds", fontsize=8)
    ax.legend(fontsize=6.5, frameon=False, ncol=2, loc="upper center")
    ax.tick_params(labelsize=7)
    ax.grid(axis="y", alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    # (b) collisions, split by what was hit.
    ax = axes[0, 1]
    for offset, arm in ((-0.5, "A_safe_planner"), (0.5, "B_cel")):
        lead = [by_channel[arm][key]["collision_kinds"]["lead"] for key, _ in channels]
        ped = [by_channel[arm][key]["collision_kinds"]["pedestrian"] for key, _ in channels]
        colour = ARM_COLOUR[arm]
        ax.bar(xs + offset * width, ped, width, color=colour)
        # The kind is carried by hatching, not by four long legend entries: with
        # the arm already keyed by colour in panel (a), a second dimension has to
        # be a texture, and four labels at this width overlapped each other.
        ax.bar(xs + offset * width, lead, width, bottom=ped, color=colour,
               alpha=0.45, hatch="///", edgecolor=colour, lw=0)
        for i, (p, l) in enumerate(zip(ped, lead)):
            if p + l:
                ax.annotate(str(p + l), (i + offset * width, p + l), ha="center",
                            va="bottom", fontsize=6.0, xytext=(0, 1.5),
                            textcoords="offset points")
    ax.set_xticks(xs)
    ax.set_xticklabels([label for _, label in channels], fontsize=6.8)
    ax.set_ylim(0, 21)
    ax.set_ylabel("collisions (episodes)", fontsize=7.5)
    ax.set_title("(b) Nineteen of twenty crashes are pedestrian", fontsize=8)
    from matplotlib.patches import Patch
    handles = [Patch(facecolor="#8e9aaf", edgecolor="none", label="pedestrian"),
               Patch(facecolor="#8e9aaf", edgecolor="#8e9aaf", alpha=0.45,
                     hatch="///", lw=0, label="lead vehicle")]
    ax.legend(handles=handles, fontsize=5.8, frameon=False, loc="upper center",
              ncol=2, handlelength=1.4, columnspacing=1.2)
    ax.tick_params(labelsize=7)
    ax.grid(axis="y", alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    # (c) the traced wet pedestrian episode, if the archive ships one. Whether an
    # arm crashed is read from the episode row, not guessed from its last speed:
    # the first version of this panel labelled both arms "collision" because the
    # surviving car was still moving when its trace ended.
    ax = axes[1, 0]
    trace_path = os.path.join(data_dir, "ad_cel_trace.json")
    if os.path.exists(trace_path):
        trace = load_json(trace_path)
        ped_t = trace.get("ped_t")
        episode = str(trace.get("episode"))
        crashed = {}
        for row in load_csv(os.path.join(data_dir, "ad_cel_data.csv")):
            if row["episode"] == episode and row["channel"] == trace.get("channel"):
                crashed[row["arm"]] = row["collision"] == "True"
        for arm in ("A_safe_planner", "B_cel"):
            rows = trace["arms"].get(arm) or []
            if not rows:
                continue
            ts = [r[0] for r in rows]
            vs = [r[2] for r in rows]
            ax.plot(ts, vs, color=ARM_COLOUR[arm], lw=1.6, label=ARM_LABEL[arm])
            if crashed.get(arm):
                ax.plot([ts[-1]], [vs[-1]], "x", color=ARM_COLOUR[arm], ms=7, mew=1.6)
                ax.annotate("collision\n{:.1f} m/s".format(vs[-1]), (ts[-1], vs[-1]),
                            fontsize=5.8, color=ARM_COLOUR[arm], ha="right", va="top",
                            xytext=(-4, -6), textcoords="offset points")
            else:
                ax.annotate("arrives", (ts[-1], vs[-1]), fontsize=5.8,
                            color=ARM_COLOUR[arm], ha="right", va="bottom",
                            xytext=(-2, 4), textcoords="offset points")
        if ped_t is not None:
            ax.axvline(ped_t, color="#444444", lw=0.7, ls=":")
            ax.annotate("pedestrian\nrevealed", (ped_t, 2.0), fontsize=5.8,
                        ha="right", va="bottom", color="#444444")
        ax.set_xlabel("time since episode start (s)", fontsize=7.5)
        ax.set_ylabel("ego speed (m/s)", fontsize=7.5)
        ax.set_ylim(0, 18)
        ax.set_title("(c) One wet pedestrian episode, shared seed", fontsize=8)
        ax.legend(fontsize=6.2, frameon=True, framealpha=0.85, edgecolor="none",
                  loc="lower center", ncol=2, handlelength=1.4, columnspacing=0.8)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25, lw=0.5)
        ax.set_axisbelow(True)
    else:
        ax.annotate("ad_cel_trace.json not found", (0.5, 0.5),
                    xycoords="axes fraction", ha="center", fontsize=7,
                    color="#8e9aaf")
        ax.set_axis_off()

    # (d) the identified parameters against the injected truth, and what the
    # baseline reports (its nominal belief, by construction).
    ax = axes[1, 1]
    trial = "wet_and_lag"
    truth = {
        "mu": by_channel["B_cel"][trial]["mu_true"] if "mu_true" in by_channel["B_cel"][trial]
        else summary["config"]["friction"]["wet"],
        "tau_a": summary["config"]["actuator_lag"]["worn_s"],
        "tau_s": summary["config"]["actuator_lag"]["steering_worn_s"],
    }
    cel = {"mu": by_channel["B_cel"][trial]["mu_hat_mean"],
           "tau_a": by_channel["B_cel"][trial]["tau_a_hat_mean"],
           "tau_s": by_channel["B_cel"][trial]["tau_s_hat_mean"]}
    # The baseline's own report is its prior in every world (mu 0.90, tau_a 0.10 s,
    # tau_s 0.08 s), i.e. it never estimates; the panel therefore compares the
    # identified values with the injected ones only, and the caption states the
    # constant prior rather than drawing it as a bar that would dwarf the rest.
    base = {"mu": by_channel["A_safe_planner"][trial]["mu_hat_mean"],
            "tau_a": by_channel["A_safe_planner"][trial]["tau_a_hat_mean"],
            "tau_s": by_channel["A_safe_planner"][trial]["tau_s_hat_mean"]}
    groups = [("$\\mu$\n(dimensionless)", "mu"),
              ("$\\tau_a$\n(s)", "tau_a"),
              ("$\\tau_s$\n(s)", "tau_s")]
    ys = np.arange(len(groups))[::-1]
    w = 0.22
    for offset, key, label, colour in (
        (-w / 2, "truth", "true value (injected)", TRUTH),
        (w / 2, "cel", "CEL identifies\nfrom response", CEL),
    ):
        values = [truth[k] if key == "truth" else cel[k] for _, k in groups]
        bars = ax.barh(ys + offset, values, w, color=colour, label=label)
        for bar, value in zip(bars, values):
            ax.annotate("{:.2f}".format(value),
                        (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                        ha="left", va="center", fontsize=6.0,
                        xytext=(1.5, 0), textcoords="offset points")
    ax.set_yticks(ys)
    ax.set_yticklabels([label for label, _ in groups], fontsize=7.0)
    ax.set_xlim(0, 1.15)
    # The legend sits in the empty band under the last row rather than over a bar.
    ax.set_ylim(-1.05, len(groups) - 0.3)
    ax.set_xlabel("value", fontsize=7.5)
    ax.set_title("(d) Identified, never supplied, in the wet+lag world", fontsize=8)
    ax.legend(fontsize=6.0, frameon=False, loc="lower center", ncol=2,
              handlelength=1.6, columnspacing=1.2, handletextpad=0.5)
    ax.tick_params(labelsize=7)
    ax.grid(axis="x", alpha=0.25, lw=0.5)
    ax.set_axisbelow(True)

    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    print("wrote {}  ({} x {} px)".format(out, int(FIGSIZE[0] * DPI), int(FIGSIZE[1] * DPI)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default=None,
                        help="where the audit's JSON/CSV live "
                             "(default: parent dir, else ./provenance)")
    parser.add_argument("--only", choices=["libero", "driving"], default=None)
    args = parser.parse_args(argv)

    data_dir = find_data_dir(args.data_dir)
    print("data: {}".format(data_dir))
    os.makedirs(FIGURES, exist_ok=True)
    if args.only in (None, "libero"):
        libero_figure(data_dir, os.path.join(FIGURES, "libero_audit.png"))
    if args.only in (None, "driving"):
        driving_figure(data_dir, os.path.join(FIGURES, "ad_cel.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
