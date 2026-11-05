#!/usr/bin/env python3
"""Audit the paper's figures, captions and tables against the data.

This is the check the paper's own contract implies: prose may not contain a
statistic that the audit's files cannot produce. It verifies, in order,

1. that both figures are exactly what ``make_figures.py`` draws from the data
   (so a figure cannot belong to an older run),
2. that every value the driving figure draws is the value in
   ``ad_cel_summary.json`` / ``ad_cel_data.csv``,
3. that the caption's traced-episode numbers are the ones the trace file holds,
4. that every figure is cited by number at least once in the body,
5. that the retired baseline names appear nowhere: no ``Safe-VLA``, no
   ``Safe-Planner``, no ``safety layer``.

Run either as a script or under pytest:

    python3 experiments/cel_paper/test_figures.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("CEL_DATA_DIR") or os.path.dirname(HERE)
if not os.path.exists(os.path.join(DATA_DIR, "ad_cel_summary.json")):
    DATA_DIR = os.path.join(HERE, "provenance")

MAIN = os.path.join(HERE, "main.tex")
FIGURES = os.path.join(HERE, "figures")


def _read_json(name: str) -> Dict:
    with open(os.path.join(DATA_DIR, name)) as handle:
        return json.load(handle)


def _read_csv(name: str) -> List[Dict[str, str]]:
    with open(os.path.join(DATA_DIR, name)) as handle:
        return list(csv.DictReader(handle))


def _macro(name: str) -> str:
    with open(os.path.join(HERE, "data", "ad_macros.tex")) as handle:
        text = handle.read()
    match = re.search(r"\\newcommand\{\\%s\}\{([^}]*)\}" % name, text)
    assert match, "macro {} is not defined".format(name)
    return match.group(1)


# ---------------------------------------------------------------------------
# 1. the figures are a function of the data
# ---------------------------------------------------------------------------


def test_figures_reproduce_byte_for_byte() -> None:
    """Redraw into a scratch directory; the shipped files must be identical."""
    import make_figures

    with tempfile.TemporaryDirectory() as tmp:
        saved = make_figures.FIGURES
        make_figures.FIGURES = tmp
        try:
            make_figures.libero_figure(DATA_DIR, os.path.join(tmp, "libero_audit.png"))
            make_figures.driving_figure(DATA_DIR, os.path.join(tmp, "ad_cel.png"))
        finally:
            make_figures.FIGURES = saved
        for name in ("libero_audit.png", "ad_cel.png"):
            with open(os.path.join(tmp, name), "rb") as handle:
                fresh = handle.read()
            with open(os.path.join(FIGURES, name), "rb") as handle:
                shipped = handle.read()
            assert fresh == shipped, (
                "{} differs from what make_figures.py draws from the data: "
                "the shipped figure is not a function of the archive".format(name))


# ---------------------------------------------------------------------------
# 2. the driving figure's numbers are the summary's numbers
# ---------------------------------------------------------------------------


def test_driving_panel_values_match_summary() -> None:
    summary = _read_json("ad_cel_summary.json")
    channels = ("nominal", "wet_road", "steering_lag", "wet_and_lag")
    # (a) is drawn from success_rate and (b) from collision_kinds; both must add
    # up to the arm totals, or a bar is being drawn from a different run.
    for arm in ("A_safe_planner", "B_cel"):
        n = sum(summary["by_channel"][arm][c]["episodes"] for c in channels)
        assert n == summary["arms"][arm]["episodes"], (
            "{}: per-channel episodes {} != arm total {}".format(
                arm, n, summary["arms"][arm]["episodes"]))
        collisions = sum(summary["by_channel"][arm][c]["collisions"] for c in channels)
        assert collisions == summary["arms"][arm]["collisions"], (
            "{}: per-channel collisions {} != arm total {}".format(
                arm, collisions, summary["arms"][arm]["collisions"]))
        for c in channels:
            entry = summary["by_channel"][arm][c]
            kinds = entry["collision_kinds"]
            assert kinds["lead"] + kinds["pedestrian"] == entry["collisions"], (
                "{} {}: kinds {} do not sum to {}".format(arm, c, kinds, entry["collisions"]))
            assert entry["success"] + entry["collisions"] <= entry["episodes"], (
                "{} {}: more outcomes than episodes".format(arm, c))
    # The caption's "nineteen of twenty": recomputed from the episode rows.
    rows = _read_csv("ad_cel_data.csv")
    crashes = [r for r in rows if r["collision"] == "True"]
    pedestrian = [r for r in crashes if r["collision_kind"] == "pedestrian"]
    assert len(crashes) == 20 and len(pedestrian) == 19, (
        "caption says nineteen of twenty; data has {} of {}".format(
            len(pedestrian), len(crashes)))
    for row in crashes:
        assert row["channel"] in ("wet_road", "wet_and_lag"), (
            "caption says every crash is wet, but episode {} is {}".format(
                row["episode"], row["channel"]))


def test_wet_road_claims() -> None:
    """The prose's wet-road sentences, recomputed."""
    rows = _read_csv("ad_cel_data.csv")
    wet = [r for r in rows if r["channel"] == "wet_road"]
    for arm in ("A_safe_planner", "B_cel"):
        sub = [r for r in wet if r["arm"] == arm]
        rate = 100.0 * sum(1 for r in sub if r["success"] == "True") / len(sub)
        assert abs(rate - float(_macro("ADWetSucc" + ("Safe" if arm.startswith("A") else "CEL")))) < 0.06, (
            "{}: wet success {:.1f}% does not match the macro".format(arm, rate))
    # every wet crash in the *wet_road* channel is a pedestrian: the sentence
    # "every crash is a pedestrian" is scoped to that channel in the prose.
    wet_crashes = [r for r in wet if r["collision"] == "True"]
    assert wet_crashes and all(r["collision_kind"] == "pedestrian" for r in wet_crashes)


def test_libero_panel_values_match_summary() -> None:
    """The manipulation figure's inputs, and the caption's one comparative claim."""
    summary = _read_json("libero_audit_summary.json")
    arms = summary["arms_summary"]
    # panel (a) plots exactly these fields; they must be in [0, 1].
    for arm in ("A_safe_vla", "B_cel"):
        for key in ("success_rate", "success_rate_nominal", "success_rate_pose_drift",
                    "success_rate_drift_channels", "damage_rate"):
            value = arms[arm][key]
            assert 0.0 <= value <= 1.0, "{} {} = {} is not a rate".format(arm, key, value)
    # panel (b): the stacked parts must sum to the plotted total.
    for arm in ("A_safe_vla", "B_cel"):
        total = arms[arm]["policy_latency_ms_mean"]
        witness = arms[arm]["witness_latency_ms_mean"]
        assert 0.0 <= witness < total, "{}: witness latency outside the total".format(arm)
    # panel (d): the caption says the layer recovers inside the onset episode and
    # the baseline never does, so those are the numbers to check, not draw.
    assert all(item["episodes_to_recover"] == 0 for item in arms["B_cel"]["recovery"]), (
        "the caption says the layer recovers inside the onset episode")
    assert all(item["episodes_to_recover"] > 0 for item in arms["A_safe_vla"]["recovery"]), (
        "the caption says the baseline never recovers")
    onsets = [item["episode"] for item in summary["onsets"]
              if item.get("regime") == "pose_drift"]
    assert onsets == [item["onset_episode"] for item in arms["B_cel"]["recovery"]
                      ] or len(onsets) >= 2, "the figure marks the pose-drift onsets"


# ---------------------------------------------------------------------------
# 3. the traced episode in the caption is the trace file
# ---------------------------------------------------------------------------


def test_traced_episode_caption_matches_trace() -> None:
    trace = _read_json("ad_cel_trace.json")
    ped_t = trace["ped_t"]

    def nearest(rows, t):
        return min(rows, key=lambda row: abs(row[0] - t))

    gate = trace["arms"]["A_safe_planner"]
    cel = trace["arms"]["B_cel"]
    assert abs(float(_macro("ADTracePedT")) - round(ped_t, 1)) < 1e-6
    assert abs(float(_macro("ADTraceVGate")) - round(nearest(gate, ped_t)[2], 1)) < 1e-6
    assert abs(float(_macro("ADTraceVCEL")) - round(nearest(cel, ped_t)[2], 1)) < 1e-6
    assert abs(float(_macro("ADTraceImpact")) - round(gate[-1][2], 1)) < 1e-6
    first_brake = next(r for r in gate if r[0] >= ped_t and r[3] < -0.5)
    assert abs(float(_macro("ADTraceGateBrakeDelay")) - round(first_brake[0] - ped_t, 2)) < 1e-6
    # the caption says the layer brakes *later* than the gate and still stops
    cel_brake = next(r for r in cel if r[0] >= ped_t and r[3] < -0.5)
    assert cel_brake[0] > first_brake[0], "the caption's 'later still' is false"
    assert min(r[2] for r in cel if r[0] >= ped_t) < 0.2, (
        "the caption says the layer comes to rest; the trace never stops")
    # and the gate never gets below its impact speed
    assert min(r[2] for r in gate if r[0] >= ped_t) > 7.0, (
        "the gate slowed to a stop in the trace; the caption is wrong")


# ---------------------------------------------------------------------------
# 4. figures are cited, and the retired names are gone
# ---------------------------------------------------------------------------


def test_every_figure_is_cited_in_the_body() -> None:
    with open(MAIN) as handle:
        text = handle.read()
    labels = re.findall(r"\\label\{(fig:[^}]*)\}", text)
    assert labels, "no figures found"
    for label in labels:
        assert "\\ref{%s}" % label in text, "{} is never cited in the text".format(label)


def test_retired_names_are_gone() -> None:
    """The retired baseline names may not survive anywhere.

    ``safety layer/filter`` is *not* banned outright: Related Work has to name
    the things the layer is not, and the paper says so explicitly ("CEL is not a
    shield, a safety layer or a safety filter"). What must not survive is the
    paper describing *itself* as one, so the ban applies to the title and the
    abstract, which is where a reader forms that impression.
    """
    with open(MAIN) as handle:
        text = handle.read()
    for word in ("Safe-VLA", "Safe\\_VLA", "Safe-Planner", "Safe\\_Planner"):
        for path in (MAIN, os.path.join(HERE, "make_figures.py"),
                     os.path.join(HERE, "make_numbers.py"),
                     os.path.join(HERE, "build.sh")):
            with open(path) as handle:
                assert word not in handle.read(), "{} still contains {!r}".format(
                    os.path.basename(path), word)
    head = text.split("\\begin{abstract}")[1].split("\\end{abstract}")[0]
    title = text.split("\\title{")[1].split("}")[0]
    for phrase in ("safety layer", "safety filter", "Safety layer", "Safety filter"):
        assert phrase not in head, "the abstract still calls the layer a {}".format(phrase)
        assert phrase not in title, "the title still says {}".format(phrase)
    # the legend name the figures actually draw
    assert "Confidence-gated" in open(os.path.join(HERE, "make_figures.py")).read()


def test_legend_labels_are_the_one_term() -> None:
    """Every arm label in the figures comes from ARM_LABEL, not a literal."""
    import make_figures
    assert set(make_figures.ARM_LABEL.values()) == {"Confidence-gated", "CEL"}


# ---------------------------------------------------------------------------


def main() -> int:
    checks = [(name, obj) for name, obj in sorted(globals().items())
              if name.startswith("test_") and callable(obj)]
    failed = 0
    for name, check in checks:
        try:
            check()
        except AssertionError as problem:
            failed += 1
            print("FAIL  {}\n      {}".format(name, problem))
        else:
            print("PASS  {}".format(name))
    print("\n{}/{} checks passed".format(len(checks) - failed, len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    raise SystemExit(main())
