"""Does solidification eat the marginal cost, and where does it stop?

The claim, stated so that it can lose:

    A world model that re-derives every task pays the full price every time. A
    system that *solidifies* a verified traverse into a rule should pay that
    price once and then a lookup price, so the thousandth task costs a hundredth
    of the first.

    H1  The marginal cost falls, and it falls to the lookup price --- not to
        zero, and not "to about 0.01" in general: the floor is a function of the
        environment.

    H2  Drift sets that floor. A rule compiled against yesterday's friction is
        wrong today, and the cost of finding that out is a full verification. So
        the steady-state marginal cost is

            c_ss = c_c + rho * c_v

        --- the lookup you always pay, plus the verification you pay whenever the
        world has moved. At rho = 0 it is c_c and nothing else; at rho = 0.5 it
        is 0.51, a saving of half and not of 99%.

    H3  Consequently the "seamless" limit c_ss -> c_c is the limit rho -> 0. It
        is a property of the environment, not of the cache. Below the break-even
        rho* = 1 - c_c/c_v compiling always pays; above it, never.

H2 is the part a plot of "cost goes down" hides, because the curve does go down.
It goes down to 0.01 in a frozen world and to 0.11 when one task in ten
invalidates the rule, and both curves look like success if the only thing drawn
is the shape. So the figure draws the floor as a line, and the console prints the
two numbers side by side. This experiment is not evidence that solidification
makes execution free; it is evidence that it makes execution cheap in proportion
to how still the world is.

What is real here, and what is arithmetic
-----------------------------------------
Real: the failure. Each drift moves the block's static friction, the compiled
torque was calibrated for the old value, and the block genuinely misses the
frame --- measured, not asserted. "Drift invalidates the rule" is therefore a
consequence of the physics rather than a coin flip wired to a cost counter.

Arithmetic: the prices. ``C_FULL`` and ``C_COMPILED`` are the two numbers the
system is charged; they are not measured from a machine. The experiment is about
the *shape* a cost curve must have given a drift hazard, and that shape does not
depend on whether a verification costs 1.0 or 40.0 --- only the axis does.

Honest limits: the drift population is "large enough to invalidate"; drifts the
frame's width absorbs are not counted, so ``rho`` is a hazard of invalidation and
not a diffusion coefficient. A rule that degrades gradually rather than failing
outright would produce a different floor. And each re-derivation sees the current
friction correctly --- there is no persistent model error here, only a moving
world.

Zero third-party dependencies for the measurement; matplotlib is imported only
to draw the figure and the run completes without it.

Outputs, beside this file:
    solidification_cost_data.csv
    solidification_cost_summary.json
    solidification_cost_figure.png

Usage:
    python3 experiments/solidification_cost_experiment.py
    python3 experiments/solidification_cost_experiment.py --tasks 5000 --seed 7
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))

DATA_CSV = os.path.join(HERE, "solidification_cost_data.csv")
SUMMARY_JSON = os.path.join(HERE, "solidification_cost_summary.json")
FIGURE_PNG = os.path.join(HERE, "solidification_cost_figure.png")

# Prices: the two numbers the system is charged per task.
C_FULL = 1.0        # derive the plan *and* witness it
C_COMPILED = 0.01   # look the rule up and replay it

# Drift rates under test. 0 is the frozen world, and it is the control that shows
# the floor is a property of the environment rather than of the mechanism.
RHOS = (0.0, 0.1, 0.3, 0.5)

# The rig: one push, one frame, one back wall, all in metres.
BLOCK_START_X = 0.0
FRAME_ENTRY_X = -0.62
BACK_WALL_X = -1.02
TARGET_X = 0.5 * (FRAME_ENTRY_X + BACK_WALL_X)   # -0.82, the middle of the frame
NEED_TRAVEL = BLOCK_START_X - TARGET_X
TRAVEL_GAIN = 1.6                                # metres per unit excess torque
GOAL_TOL = 0.005
DAMAGE_OVERSHOOT = 0.10                          # past the wall, the frame deforms

TAU_NOMINAL = 0.35                    # the friction the compiled rule was built on
DRIFT_LOW, DRIFT_HIGH = 0.15, 0.25    # invalidation-sized drift, in torque units

L = 4.0                               # layers, for the knee kappa* = 1 - 1/L

SOLIDIFIED = "SOLIDIFIED"
UNSOLIDIFIED = "UNSOLIDIFIED"
PASS = "PASS"
FAIL = "FAIL"


# ---------------------------------------------------------------------------
# The physical half: a drift that fails the rule, not a coin flip that bills one
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rig:
    """A push that either lands in the frame or does not.

    ``tau_static`` is the number the world changed. A compiled rule carries a
    torque calibrated for ``TAU_NOMINAL``; the task below asks the rig what that
    torque actually does, and the answer is not consulted from the rule.
    """

    tau_static: float

    def push(self, torque: float) -> Tuple[bool, float, bool, bool]:
        """Returns (in_frame, landing_x, wall_contact, damage)."""
        excess = torque - self.tau_static
        if excess <= 0.0:
            return False, BLOCK_START_X, False, False
        wanted = TRAVEL_GAIN * excess
        room = BLOCK_START_X - BACK_WALL_X
        wall = wanted >= room
        travel = min(wanted, room)
        landing = BLOCK_START_X - travel
        # The frame is the open interval (-1.02, -0.62): the wall is the far
        # side, the entry plane is the near side. A block pressed into the back
        # wall has arrived by the wrong route, which is why the comparison is
        # strict.
        in_frame = BACK_WALL_X < landing < FRAME_ENTRY_X
        damage = bool(wall and (wanted - room) > DAMAGE_OVERSHOOT)
        return in_frame, landing, wall, damage


def compiled_torque(tau: float) -> float:
    """The torque that lands the block in the middle of the frame at ``tau``."""
    return tau + NEED_TRAVEL / TRAVEL_GAIN


def drift_draw(rho: float, rng: random.Random, tau: float
               ) -> Tuple[bool, float, float, float]:
    """One task's world. Returns (drifted, tau_now, direction, magnitude)."""
    if rng.random() < rho:
        magnitude = rng.uniform(DRIFT_LOW, DRIFT_HIGH)
        direction = -1.0 if rng.random() < 0.5 else 1.0   # slipperier or rougher
        return True, tau + direction * magnitude, direction, magnitude
    return False, tau, 0.0, 0.0


# ---------------------------------------------------------------------------
# The economic half
# ---------------------------------------------------------------------------


@dataclass
class TaskRow:
    arm: str
    rho: float
    task: int
    state_before: str
    drifted: bool
    drift_direction: float
    drift_magnitude: float
    tau_now: float
    torque: float
    cost: float
    verdict: str
    revalidated: bool
    wall_contact: bool
    damage: bool
    cumulative_cost: float


def run_arm_a(tasks: int, rho: float, rng: random.Random) -> List[TaskRow]:
    """No memory. Every task pays the full price, drift or not.

    Not because this arm ignores drift --- it cannot ignore drift, and that is the
    point: paying full price every time is its *defence*, and the defence works.
    What it cannot do is get cheaper.
    """
    rows: List[TaskRow] = []
    cumulative = 0.0
    tau = TAU_NOMINAL
    for task in range(1, tasks + 1):
        drifted, tau_now, direction, magnitude = drift_draw(rho, rng, tau)
        tau = tau_now
        torque = compiled_torque(tau)          # full derivation, every time
        in_frame, _landing, wall, damage = Rig(tau).push(torque)
        cumulative += C_FULL
        rows.append(TaskRow(
            arm="A-full-derivation", rho=rho, task=task,
            state_before=UNSOLIDIFIED, drifted=drifted,
            drift_direction=direction, drift_magnitude=round(magnitude, 6),
            tau_now=round(tau, 6), torque=round(torque, 6), cost=C_FULL,
            verdict=PASS if in_frame else FAIL, revalidated=False,
            wall_contact=wall, damage=damage,
            cumulative_cost=round(cumulative, 6)))
    return rows


def run_arm_b(tasks: int, rho: float, rng: random.Random) -> List[TaskRow]:
    """The metabolism: compile once, replay cheaply, re-verify on drift."""
    rows: List[TaskRow] = []
    cumulative = 0.0
    tau = TAU_NOMINAL
    compiled_for: Optional[float] = None      # the tau the rule was proven against
    state = UNSOLIDIFIED

    for task in range(1, tasks + 1):
        drifted, tau_now, direction, magnitude = drift_draw(rho, rng, tau)
        tau = tau_now
        state_before = state
        revalidated = False

        if state == SOLIDIFIED and compiled_for is not None:
            torque = compiled_torque(compiled_for)      # replay the stored rule
            in_frame, _landing, wall, damage = Rig(tau).push(torque)
            cost = C_COMPILED
            verdict = PASS if in_frame else FAIL
            if verdict == FAIL:
                # Drift invalidated the rule. Pay full price to rebuild it ---
                # against the *current* friction, which is the whole expense.
                torque = compiled_torque(tau)
                in_frame, _landing, wall, damage = Rig(tau).push(torque)
                cost += C_FULL
                verdict = PASS if in_frame else FAIL
                revalidated = True
                compiled_for = tau if verdict == PASS else None
                state = SOLIDIFIED if verdict == PASS else UNSOLIDIFIED
        else:
            torque = compiled_torque(tau)               # full derivation
            in_frame, _landing, wall, damage = Rig(tau).push(torque)
            cost = C_FULL
            verdict = PASS if in_frame else FAIL
            revalidated = True
            compiled_for = tau if verdict == PASS else None
            state = SOLIDIFIED if verdict == PASS else UNSOLIDIFIED

        cumulative += cost
        rows.append(TaskRow(
            arm="B-solidifying", rho=rho, task=task, state_before=state_before,
            drifted=drifted, drift_direction=direction,
            drift_magnitude=round(magnitude, 6), tau_now=round(tau, 6),
            torque=round(torque, 6), cost=round(cost, 6), verdict=verdict,
            revalidated=revalidated, wall_contact=wall, damage=damage,
            cumulative_cost=round(cumulative, 6)))
    return rows


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def steady_floor(rho: float) -> float:
    """``c_c + rho * c_v``: the lookup, plus the verification drift forces."""
    return C_COMPILED + rho * C_FULL


def break_even_rho() -> float:
    """``rho* = 1 - c_c/c_v``. Past it, compiling is dearer than not."""
    return 1.0 - C_COMPILED / C_FULL


def critical_budget(rho: float, premium: float,
                    detect: float = 0.0) -> Optional[float]:
    """``B* = c_v [a(1-rho) + S] / [a(1-rho) - c_d]``, with ``a = c_v - c_c``.

    The budget at which compiling a traverse first repays the infrastructure
    premium ``S``. ``None`` at the drift rate where the denominator vanishes:
    past it no budget buys solidification, which is the ceiling H3 is about.
    """
    a = C_FULL - C_COMPILED
    denominator = a * (1.0 - rho) - detect
    if denominator <= 0.0:
        return None
    return C_FULL * (a * (1.0 - rho) + premium) / denominator


def compound_knee(layers: float = L) -> float:
    """``kappa* = 1 - 1/L``: below this reuse rate, nesting does not compound."""
    return 1.0 - 1.0 / layers


def summarise(rows: Sequence[TaskRow], warmup: int = 200) -> Dict[str, object]:
    n = len(rows)
    total = rows[-1].cumulative_cost if rows else 0.0
    tail = rows[-warmup:] if n > warmup else rows
    marginal = sum(r.cost for r in tail) / len(tail) if tail else 0.0
    revalidated = sum(1 for r in rows if r.revalidated)
    return {
        "tasks": n,
        "total_cost": round(total, 4),
        "mean_cost": round(total / n, 6) if n else 0.0,
        "steady_marginal_cost": round(marginal, 6),
        "revalidations": revalidated,
        "kappa_measured": round(1.0 - revalidated / n, 4) if n else 0.0,
        "steady_floor": round(steady_floor(rows[0].rho), 6) if rows else 0.0,
        "savings_vs_arm_a": round(1.0 - total / (C_FULL * n), 4) if n else 0.0,
        "failures": sum(1 for r in rows if r.verdict == FAIL),
        "damage_events": sum(1 for r in rows if r.damage),
        "drift_events": sum(1 for r in rows if r.drifted),
    }


def floor_tolerance(rho: float, warmup: int = 200) -> float:
    """How far the measured floor may sit from ``c_c + rho*c_v`` and still agree.

    The drift count over the warm-up window is binomial, so a fixed tolerance
    would be a test that passes at rho = 0 and flakes at rho = 0.5 for reasons
    that have nothing to do with the model. Three standard errors, floored at the
    price resolution, is the smallest band that does not mistake sampling noise
    for a wrong theory --- and it is printed with the result so a reader can see
    how weak the test is at high drift.
    """
    standard_error = math.sqrt(max(0.0, rho * (1.0 - rho)) / warmup)
    return max(0.02, 3.0 * C_FULL * standard_error)


def tasks_to_steady(rows: Sequence[TaskRow], fraction: float = 0.9) -> int:
    """Tasks before the *average* cost is within 10% of the floor.

    The per-task cost drops on the second task; the average is what an operator
    feels, and the gap between the two is the whole point of the first
    verification being an investment rather than a purchase.
    """
    floor = steady_floor(rows[0].rho)
    target = floor + (C_FULL - floor) * (1.0 - fraction)
    cumulative = 0.0
    for row in rows:
        cumulative += row.cost
        if cumulative / row.task <= target:
            return row.task
    return len(rows)


def rolling(rows: Sequence[TaskRow], window: int = 25) -> List[float]:
    out: List[float] = []
    for index in range(len(rows)):
        chunk = rows[max(0, index - window + 1):index + 1]
        out.append(sum(r.cost for r in chunk) / len(chunk))
    return out


def cumulative_mean(rows: Sequence[TaskRow]) -> List[float]:
    out: List[float] = []
    cumulative = 0.0
    for index, row in enumerate(rows, start=1):
        cumulative += row.cost
        out.append(cumulative / index)
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_csv(path: str, rows: Sequence[TaskRow]) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"
PALE = "#c9cdd3"
GREEN = "#3f7d5a"
GOLD = "#a5762a"
SERIES = {0.0: GREEN, 0.1: COOL, 0.3: GOLD, 0.5: ACCENT, 0.9: PALE}


def render(by_rho: Dict[float, Tuple[List[TaskRow], List[TaskRow]]],
           floor_rows: Sequence[Dict[str, float]],
           budgets: Dict[float, List[Tuple[float, Optional[float]]]],
           tasks: int, path: str = FIGURE_PNG) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 - a missing plot is not a missing result
        print("matplotlib unavailable: figure skipped; CSV and JSON are complete")
        return None

    plt.rcParams.update({
        "font.size": 8.5, "axes.labelsize": 8.5, "axes.titlesize": 9,
        "legend.fontsize": 6.8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
        "axes.linewidth": 0.7, "grid.linewidth": 0.4, "grid.alpha": 0.35,
        "savefig.dpi": 220, "figure.dpi": 110,
        "axes.spines.top": False, "axes.spines.right": False,
    })
    fig, axes = plt.subplots(2, 2, figsize=(7.4, 5.2), constrained_layout=True)

    # (a) the curve everyone wants to see, with the floor drawn on it ------
    ax = axes[0][0]
    a_rows, b_rows = by_rho[0.1]
    xs = [r.task for r in b_rows]
    ax.plot(xs, [r.cost for r in b_rows], color=PALE, linewidth=0.5,
            label="B, per task")
    ax.plot(xs, rolling(b_rows, 25), color=COOL, linewidth=1.5,
            label="B, 25-task mean")
    ax.plot(xs, [r.cost for r in a_rows], color=MUTED, linewidth=1.1,
            linestyle="--", label="A, full derivation")
    ax.axhline(steady_floor(0.1), color=ACCENT, linewidth=1.0, linestyle=":",
               label="floor $\\rho$=0.1 : {:.2f}".format(steady_floor(0.1)))
    ax.axhline(C_COMPILED, color=GREEN, linewidth=1.0, linestyle=":",
               label="floor $\\rho$=0 : {:.2f}".format(C_COMPILED))
    ax.set_xlabel("task")
    ax.set_ylabel("marginal cost per task")
    ax.set_ylim(0, 1.08)
    ax.set_title("(a) what falls, and where it stops")
    ax.legend(frameon=False, loc="center right")
    ax.grid(axis="y")

    # (b) the floor is a function of the world, not of the cache ----------
    ax = axes[0][1]
    rhos = [row["rho"] for row in floor_rows]
    measured = [row["measured"] for row in floor_rows]
    predicted = [row["predicted"] for row in floor_rows]
    ax.plot(rhos, predicted, color=MUTED, linewidth=1.1, linestyle="--",
            label=r"$c_c + \rho\,c_v$")
    ax.plot(rhos, measured, marker="o", color=ACCENT, linewidth=1.4,
            markersize=4.5, label="measured (last 200 tasks)")
    ax.axhline(C_COMPILED, color=GREEN, linewidth=0.9, linestyle=":")
    ax.annotate("seamless limit\n$\\rho \\to 0$", (0.02, C_COMPILED + 0.07),
                color=GREEN, fontsize=6.8)
    for rho, value in zip(rhos, measured):
        ax.annotate("{:.2f}".format(value), (rho, value + 0.04), ha="center",
                    fontsize=6.8, color=ACCENT)
    ax.set_xlabel(r"drift rate $\rho$  (per-task chance the rule is invalidated)")
    ax.set_ylabel("steady-state marginal cost")
    ax.set_ylim(0, 1.05)
    ax.set_title("(b) cheap in proportion to stillness")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(axis="y")

    # (c) what an operator feels: the running average --------------------
    ax = axes[1][0]
    for rho in sorted(by_rho):
        _, rows = by_rho[rho]
        means = cumulative_mean(rows)
        ax.plot([r.task for r in rows], means, color=SERIES[rho],
                linewidth=1.3, label=r"$\rho$ = {:.1f}".format(rho))
        ax.axhline(steady_floor(rho), color=SERIES[rho], linewidth=0.6,
                   linestyle=":", alpha=0.75)
    ax.set_xscale("log")
    ax.set_xlabel("task (log scale)")
    ax.set_ylabel("cumulative mean cost per task")
    ax.set_ylim(0, 1.05)
    ax.set_title("(c) the investment, amortised")
    ax.legend(frameon=False, loc="upper right")
    ax.grid(axis="y")

    # (d) the critical budget, and the ceiling past which none exists -----
    ax = axes[1][1]
    premiums = [0.0, 0.25, 0.5, 1.0, 2.0]
    for rho in (0.0, 0.1, 0.3, 0.5, 0.9):
        values = []
        for premium in premiums:
            budget = critical_budget(rho, premium)
            values.append(budget if budget is not None else float("nan"))
        ax.plot(premiums, values, marker="o", markersize=3.2, linewidth=1.2,
                color=SERIES[rho], label=r"$\rho$ = {:.1f}".format(rho))
    # Log axis: at rho = 0.9 the threshold is an order of magnitude above the
    # others, and on a linear axis that one curve is what the panel is about.
    ax.set_yscale("log")
    ax.set_ylim(0.9, 40.0)
    ax.set_yticks([1, 2, 5, 10, 20])
    ax.set_yticklabels(["1", "2", "5", "10", "20"])
    ax.set_xlabel("infrastructure premium  $S$  (in units of one verification)")
    ax.set_ylabel("critical budget  $B^{*}$  (log scale)")
    ax.set_title("(d) depth that must be bought before compiling pays")
    ax.legend(frameon=False, loc="upper left", ncol=2)
    ax.grid(axis="y")

    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--no-figure", action="store_true")
    args = parser.parse_args(argv)

    by_rho: Dict[float, Tuple[List[TaskRow], List[TaskRow]]] = {}
    all_rows: List[TaskRow] = []
    for rho in RHOS:
        # Same world for both arms: identical drift draws, so the only difference
        # between the columns is memory.
        rng_a = random.Random(args.seed + int(rho * 1000) + 1)
        rng_b = random.Random(args.seed + int(rho * 1000) + 1)
        a_rows = run_arm_a(args.tasks, rho, rng_a)
        b_rows = run_arm_b(args.tasks, rho, rng_b)
        by_rho[rho] = (a_rows, b_rows)
        all_rows.extend(a_rows)
        all_rows.extend(b_rows)

    stats = {rho: (summarise(a), summarise(b)) for rho, (a, b) in by_rho.items()}
    floor_rows = [
        {"rho": rho, "measured": stats[rho][1]["steady_marginal_cost"],
         "predicted": round(steady_floor(rho), 6),
         "kappa": stats[rho][1]["kappa_measured"],
         "savings": stats[rho][1]["savings_vs_arm_a"]}
        for rho in RHOS
    ]
    premiums = (0.0, 0.25, 0.5, 1.0, 2.0)
    budgets = {rho: [(premium, critical_budget(rho, premium))
                     for premium in premiums]
               for rho in (0.0, 0.1, 0.3, 0.5, 0.9)}

    write_csv(DATA_CSV, all_rows)
    figure = None if args.no_figure else render(by_rho, floor_rows, budgets,
                                                args.tasks)

    headline_a = stats[0.1][0]
    headline = stats[0.1][1]
    consumed = 1.0 - headline["steady_marginal_cost"] / C_FULL

    summary = {
        "tasks": args.tasks,
        "seed": args.seed,
        "prices": {"full": C_FULL, "compiled": C_COMPILED},
        "rhos": list(RHOS),
        "by_rho": {str(rho): {"A": stats[rho][0], "B": stats[rho][1]}
                   for rho in RHOS},
        "floor_model": "c_c + rho * c_v",
        "floor_tolerance": {str(rho): round(floor_tolerance(rho), 4)
                            for rho in RHOS},
        "break_even_rho": round(break_even_rho(), 4),
        "compound_knee": round(compound_knee(), 4),
        "tasks_to_90pct_steady": {
            str(rho): tasks_to_steady(by_rho[rho][1]) for rho in RHOS},
        "critical_budgets": {str(rho): [[p, b] for p, b in rows]
                             for rho, rows in budgets.items()},
        "headline_rho_0_1": {
            "A_total": headline_a["total_cost"],
            "B_total": headline["total_cost"],
            "A_mean": headline_a["mean_cost"],
            "steady_marginal_cost": headline["steady_marginal_cost"],
            "floor_if_world_were_frozen": C_COMPILED,
            "fraction_of_cost_eliminated": round(consumed, 4),
            "savings_vs_full_derivation": headline["savings_vs_arm_a"],
            "kappa_measured": headline["kappa_measured"],
            "revalidations": headline["revalidations"],
        },
        "claims": {
            "h1_cost_falls_below_arm_a_at_every_rho":
                all(stats[rho][1]["total_cost"] < stats[rho][0]["total_cost"]
                    for rho in RHOS),
            "h2_floor_is_c_c_plus_rho_c_v":
                all(abs(stats[rho][1]["steady_marginal_cost"] - steady_floor(rho))
                    <= floor_tolerance(rho) for rho in RHOS),
            "h2_floor_is_not_c_c_unless_rho_is_zero":
                abs(headline["steady_marginal_cost"] - C_COMPILED) > 0.05,
            "h3_compiling_still_pays_below_break_even":
                break_even_rho() > max(RHOS),
            "h3_kappa_falls_with_drift": all(
                stats[a][1]["kappa_measured"] >= stats[b][1]["kappa_measured"]
                for a, b in zip(RHOS, RHOS[1:])),
        },
    }
    with open(SUMMARY_JSON, "w") as handle:
        json.dump(summary, handle, indent=1)

    # ---- console ---------------------------------------------------------
    print("=" * 78)
    print("SOLIDIFICATION COST — {} tasks per arm, {} drift rates".format(
        args.tasks, len(RHOS)))
    print("=" * 78)
    print()
    print("  full derivation {:.2f}   compiled replay {:.2f}   "
          "break-even drift {:.3f}".format(C_FULL, C_COMPILED, break_even_rho()))
    print()
    print("  {:>5s}  {:>10s}  {:>10s}  {:>8s}  {:>8s}  {:>7s}  {:>6s}".format(
        "rho", "A total", "B total", "saving", "steady", "kappa", "reval"))
    for rho in RHOS:
        a, b = stats[rho]
        print("  {:>5.1f}  {:>10.1f}  {:>10.1f}  {:>7.1%}  {:>8.3f}  "
              "{:>7.3f}  {:>6d}".format(
                  rho, a["total_cost"], b["total_cost"], b["savings_vs_arm_a"],
                  b["steady_marginal_cost"], b["kappa_measured"],
                  b["revalidations"]))
    print()
    print("  floor model  c_c + rho*c_v :  " + "   ".join(
        "rho={:.1f} -> {:.3f}".format(row["rho"], row["predicted"])
        for row in floor_rows))

    print()
    print("  ── rho = 0.1，{} 次迭代 ──".format(args.tasks))
    print("  A 组总成本        : {:.1f}".format(headline_a["total_cost"]))
    print("  B 组总成本        : {:.1f}".format(headline["total_cost"]))
    print("  节省              : {:.1f}  = {:.1%}".format(
        headline_a["total_cost"] - headline["total_cost"],
        headline["savings_vs_arm_a"]))
    print("  稳态边际成本      : {:.4f}  （若世界冻结应为 {:.2f}）".format(
        headline["steady_marginal_cost"], C_COMPILED))
    print("  边际成本被吃掉    : {:.1%}".format(consumed))
    print("  固化率 kappa      : {:.3f}   （knee kappa* = {:.2f}）".format(
        headline["kappa_measured"], compound_knee()))
    print("  重新验证次数      : {} 次，约每 {:.1f} 个任务一次".format(
        headline["revalidations"],
        args.tasks / max(1, headline["revalidations"])))
    print("  失效漂移          : {} 次".format(headline["drift_events"]))
    print("  首次验证摊销完成  : 第 {} 个任务（累计均值进入稳态 10% 带内）".format(
        tasks_to_steady(by_rho[0.1][1])))

    print()
    print("  ── 曲线形状藏起来的那件事 ──")
    print("  rho=0   时 B 收敛到 {:.2f}，看起来是“无感”。".format(
        stats[0.0][1]["steady_marginal_cost"]))
    print("  rho=0.1 时 B 收敛到 {:.2f}，是 rho=0 的 {:.0f} 倍。".format(
        headline["steady_marginal_cost"],
        headline["steady_marginal_cost"] / C_COMPILED))
    print("  曲线下降是真的；“降到 0.01 附近”只在一个不漂移的世界里是真的。")
    print("  kappa 随 rho：{}   knee = {:.2f}".format(
        "  ".join("rho={:.1f}:{:.2f}".format(rho, stats[rho][1]["kappa_measured"])
                 for rho in RHOS), compound_knee()))
    print("  kappa 低于 knee 时嵌套不再复利（见 nested 实验）。")

    print()
    print("  ── 临界预算 B* ──")
    for rho in (0.0, 0.1, 0.3, 0.5):
        row = "  rho={:.1f}: ".format(rho)
        for premium, budget in budgets[rho]:
            row += "S={:.2f}->{:.2f}  ".format(
                premium, budget if budget is not None else float("inf"))
        print(row)
    print("  无 premium 时 B* = c_v = {:.2f}：付过一次验证之后，固化永远不亏。".format(
        C_FULL))
    print("  premium 越大，需要先买到的深度越大；rho 抬高把那条线整体推高。")

    print()
    print("  ── 判定 ──")
    for key, value in summary["claims"].items():
        print("    {:<52s} {}".format(key, "PASS" if value else "FAIL"))
    print()
    print("  H1  边际成本下降，但下限由环境决定，不由缓存决定。")
    print("  H2  稳态下限 = c_c + rho*c_v；rho=0.1 时被吃掉 {:.1%}，不是 99%。".format(
        consumed))
    print("  H3  “无感”是 rho->0 的极限；rho* = {:.2f} 以上固化反而更贵。".format(
        break_even_rho()))

    print()
    print("WROTE")
    for path in (DATA_CSV, SUMMARY_JSON, figure):
        if path:
            print("  " + os.path.relpath(path, os.path.dirname(HERE)))
    print("No commit. No push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
