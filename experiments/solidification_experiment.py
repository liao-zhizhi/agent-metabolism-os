"""What eats the marginal cost: solidification, and how far it can take you.

The previous experiment refuted one claim and left a question standing. If total
reach cannot diverge, *why does the marginal cost per rung fall?* Eq. (19) of the
manuscript already answers it in nines -- a path of K boundaries costs
``n + log10 K`` rather than ``nK`` -- but an accounting identity is not a
mechanism. This experiment supplies the mechanism and prices it.

The mechanism is **solidification**. A rung that has been crossed against a
witness does not have to be verified again; the verification is replaced by a
replay of the path it validated. That is what a cache, a compiled binary and a
learned reflex have in common, and it is why rung ``j`` pays for the boundary
that is new rather than for the prefix: the prefix has already been paid for and
labelled. The label is the whole asset. Without it, "replay" is indistinguishable
from "assert".

What the experiment measures
----------------------------
1. **Total cost against depth.** Unlabelled is linear, ``K * c_v``. Labelled is
   ``c_v + (K-1) * c_bar``. The ratio at fixed depth is therefore *bounded* by
   ``c_v / c_bar`` and saturates -- it does not grow exponentially, and cannot,
   for the same reason a sum of positive terms cannot fall. Reported plainly,
   because the previous section earned the right to say so.

2. **The marginal cost of rung j.** Unlabelled: constant. Labelled under the
   paper's own budget: ``log10(j/(j-1))``, harmonic, tending to zero. A harmonic
   marginal integrates to a *logarithmic* total, and a logarithmic total is what
   makes depth exponential in budget. That is where the exponential actually
   lives, and it is measured rather than asserted: the fitted slope of total cost
   against ``log10 K`` must come out at ``1/log10(2) = 3.3219`` for the budget
   model.

3. **Reach per unit budget.** Reach is exponential in depth, so a logarithmic
   cost per rung makes reach exponential *in the budget* with a much steeper
   exponent. The difference between the two groups is then exponential in the
   budget even though it is bounded in the depth. Same fact, different variable,
   which is the whole confusion the previous section recorded.

4. **Drift, and why solidification is not free.** With probability ``rho`` per
   traverse a compiled rung goes stale. A detector that notices with probability
   ``delta`` restores it. Without a detector the label is worse than useless: the
   stale rung never resets, so the compiled path is wrong *forever after the
   first change*. This is measured, and it is the strongest argument in the file
   for keeping the witness attached to the label.

The asymmetry, which is a principle rather than an implementation detail
-----------------------------------------------------------------------
Moving to **more** checking is always safe, so the world may force it at any
time: ``drift()`` is ungated. Moving to **less** checking requires a verified
witness bound to this gap and this difficulty: ``solidify()`` is gated. A door
that opened both ways with the same key would let a system buy cheapness by
asserting that nothing had changed, which is exactly the trade Experiment G
measured and rejected.

Run:
    python3 -m experiments.solidification_experiment

Writes:
    experiments/solidification_data.csv
    experiments/solidification_figure.png
    experiments/solidification_summary.json
    experiments/paper/data/solidification_{macros,table}.tex
    experiments/paper/figures/solidification.png

No network. No commit. No push.
"""

from __future__ import annotations

import csv
import json
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from agent_metabolism.double_entry import PASS  # noqa: E402
    from agent_metabolism.interface_engine import (  # noqa: E402
        SOLIDIFIED,
        SOLIDIFIED_COST,
        SOLIDIFICATION_GAIN,
        UNSOLIDIFIED,
        VERIFY_COST,
        FractalEvolver,
        Interface,
        InterfaceError,
        Layer,
        NarrowCondition,
        verify_witness,
    )
except ImportError as exc:  # pragma: no cover - the paper's archive ships no engine
    # Deliberately fatal. Every other experiment in this package degrades to a
    # model when its auditor is missing; this one cannot, because the thing it
    # measures *is* the engine's label. A solidification experiment without the
    # engine would compute the algebra in section 2 and call it a result.
    raise SystemExit(
        "this experiment needs the agent_metabolism package, which ships with the "
        "repository and not with the paper's source archive.\n"
        "Run it from the repository root:\n"
        "    python3 -m experiments.solidification_experiment"
    ) from exc

DATA_CSV = os.path.join(HERE, "solidification_data.csv")
SUMMARY_JSON = os.path.join(HERE, "solidification_summary.json")
FIGURE_PNG = os.path.join(HERE, "solidification_figure.png")

PAPER_DIR = os.path.join(HERE, "paper")
PAPER_DATA = os.path.join(PAPER_DIR, "data")
PAPER_FIGURES = os.path.join(PAPER_DIR, "figures")

# ---------------------------------------------------------------------------
# 1. the parameters, named
# ---------------------------------------------------------------------------

SEED = 20261104

#: How deep the worked ladder is. Ten is the number the brief names, and it is
#: also about where a human-run pipeline stops being inspectable by hand.
DEPTH = 10

#: How far the cost-against-depth curve is drawn. Beyond ~200 the harmonic total
#: is visually flat, so the extra points would add ink and no information.
DEPTH_MAX = 200

#: Traversals per cell. One thousand is enough for the mean cost to be stable to
#: three decimals, which is all the table reports.
RUNS = 1000

#: Environment drift, per rung per traverse. The grid is log-spaced because the
#: interesting behaviour is at small rho: by rho = 1 the cache is worthless.
RHO_GRID = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0)

#: Detector sensitivity: the probability that a drift is noticed. One is the
#: best case and is used for the "labelled" arm; zero is the naive arm, which is
#: the one that looks cheapest.
DETECTOR_SENSITIVITY = 1.0
NAIVE_SENSITIVITY = 0.0

#: What the detector costs, per replayed rung. Paid whether or not anything
#: drifted, because you cannot know without checking -- that is the whole
#: expense, and it is why the break-even is ``c_d < c_v - c_c``.
DETECTOR_COST = 0.02


# ---------------------------------------------------------------------------
# 2. the analytic model
# ---------------------------------------------------------------------------


def replay_fraction(rho: float, delta: float) -> float:
    """Share of traverses served entirely from the compiled path.

    ``1 - rho*delta``. Derived from the label's own dynamics: once a rung is
    compiled it *stays* compiled -- a detected drift costs a verification but
    does not withdraw the label. So the cache is live at every entry, and the
    only traverses that are not a pure replay are the ones where drift happened
    and the detector caught it.

    This is the number the paper means by "solidification rate", and it is worth
    separating from "fraction of rungs that are labelled": a rung can be labelled
    and still be re-verified on a third of its traversals.
    """
    return max(0.0, 1.0 - rho * delta)


def rung_cost(rho: float, delta: float,
              verify: float = VERIFY_COST,
              cached: float = SOLIDIFIED_COST,
              detect: float = DETECTOR_COST) -> float:
    """Expected cost of one traverse of one compiled rung.

    ``c_c + c_d + rho*delta*c_v``. The cache is live at every entry, so the
    cached price is paid unconditionally and the verification price is paid only
    on a detected drift.

    Note that this can exceed ``c_v``: at ``rho = 1`` and ``delta = 1`` it is
    ``c_c + c_d + c_v``, which is dearer than never compiling at all. The
    crossover solves to ``rho* = 1 - (c_c + c_d)/c_v``, and its existence is the
    quantitative form of "solidification is not a one-off purchase".
    """
    return cached + detect + rho * delta * verify


def break_even_rho(detect: float = DETECTOR_COST) -> float:
    """The drift rate beyond which compiling is a net loss.

    ``rho* = 1 - (c_c + c_d)/c_v``. Above it the detector fires so often that
    the system pays a verification *and* a cache lookup for every drift, which is
    strictly worse than paying the verification alone. This is why the immune
    response has to be priced, not assumed.
    """
    return 1.0 - (SOLIDIFIED_COST + detect) / VERIFY_COST


def break_even_detector(rho: float) -> float:
    """The most a detector may cost and still pay for itself, at a given rho."""
    return (1.0 - rho) * (VERIFY_COST - SOLIDIFIED_COST)


def false_rate(rho: float, delta: float) -> float:
    """Per-traverse false-completion rate for a *live* cache.

    ``rho * (1 - delta)``. A missed drift is not forgotten -- the realised rate
    over a long run is far worse, because the stale rung keeps reporting success
    -- so the simulation reports both and this is only the first step.
    """
    return rho * (1.0 - delta)


def cascade_survival(j: int, rho: float, delta: float) -> float:
    """Probability rung ``j`` is a *pure* replay: nothing upstream or on it moved.

    ``(1 - rho*delta)**j``. This is the modularity assumption failing, and it
    costs almost everything -- see :func:`cascade_cost`.
    """
    return (1.0 - rho * delta) ** j


def cascade_cost(depth: int, rho: float, delta: float,
                 verify: float = VERIFY_COST,
                 cached: float = SOLIDIFIED_COST,
                 detect: float = DETECTOR_COST) -> float:
    """Cost of one traverse of a chain whose invalidation cascades.

    ``K*(c_c+c_d) + c_v * rho*delta * K(K+1)/2``. A drift at rung ``j`` forces the
    suffix ``j..K`` to be re-derived, and a suffix of length ``K-j+1`` is re-derived
    once per drift it sees, so the number of verifications per traverse is
    ``rho*delta * sum_j (K-j+1)``. Note the quadratic: **cascading invalidation
    makes depth cost O(K^2) instead of O(K)**, which is the price of compiling a
    chain whose parts are not independent.
    """
    total = float(depth) * (cached + detect)
    for j in range(1, depth + 1):
        total += rho * delta * (depth - j + 1) * verify
    return total


def cascade_break_even_rho(depth: int,
                           detect: float = DETECTOR_COST) -> float:
    """Drift rate above which a cascading chain is worse than never compiling.

    ``rho* = 2*(1 - (c_c+c_d)/c_v) / (K+1)``. It *falls* as the chain gets
    deeper, so a long chain cannot be compiled at all unless invalidation is
    local. That is the quantitative form of "modularity before speed".
    """
    per_rung = SOLIDIFIED_COST + detect
    return 2.0 * (1.0 - per_rung / VERIFY_COST) / (depth + 1.0)



def unlabelled_total(depth: int, verify: float = VERIFY_COST) -> float:
    """``K * c_v``: every rung is verified on every traverse."""
    return depth * verify


def replay_total(depth: int, rho: float, delta: float) -> float:
    """One cold traverse: ``c_v`` compiles rung 1, the rest replay."""
    if depth <= 0:
        return 0.0
    return VERIFY_COST + (depth - 1) * rung_cost(rho, delta)


def warm_total(depth: int, rho: float, delta: float) -> float:
    """One traverse of an already-compiled ladder."""
    return depth * rung_cost(rho, delta)


def harmonic_total(depth: int, verify: float = VERIFY_COST) -> float:
    """Depth priced at the paper's own marginal, ``log10(j/(j-1))``.

    Normalised so that rung 2 costs one full verification, which makes the total
    ``c_v * log2(K)``. Written as a sum rather than as the closed form on purpose:
    a closed form that disagrees with the loop is a closed form nobody checks.
    """
    total = 0.0
    for j in range(1, depth + 1):
        if j == 1:
            total += verify
        else:
            total += verify * math.log10(j / (j - 1.0)) / math.log10(2.0)
    return total


def marginal_nines(j: int) -> float:
    """Eq. (19)'s marginal cost of the ``j``-th rung, in nines."""
    if j <= 1:
        return float("nan")
    return math.log10(j / (j - 1.0))


def marginal_harmonic(j: int, verify: float = VERIFY_COST) -> float:
    """The same shape, priced in verifications: ``c_v * log2(j/(j-1))``."""
    if j <= 1:
        return float("nan")
    return verify * math.log10(j / (j - 1.0)) / math.log10(2.0)


def depths_at_budget(budget: float, per_rung: float) -> float:
    """How many rungs a fixed budget buys, given a per-rung price.

    The first rung always costs a full verification, so a budget below ``c_v``
    buys nothing rather than a fraction of a rung.
    """
    if budget < VERIFY_COST:
        return 0.0
    if per_rung <= 0:
        return float("inf")
    return 1.0 + (budget - VERIFY_COST) / per_rung



# ---------------------------------------------------------------------------
# 3. the simulated pipeline
# ---------------------------------------------------------------------------


@dataclass
class Traverse:
    cost: float
    false: int
    solid: int
    crossed: int


def simulate(depth: int, runs: int, rho: float, delta: float,
             rng: random.Random,
             verify: float = VERIFY_COST,
             cached: float = SOLIDIFIED_COST,
             detect: float = DETECTOR_COST,
             labelled: bool = True,
             cascade: bool = False) -> Traverse:
    """Walk a depth-``depth`` ladder ``runs`` times and price every rung.

    Three states per rung, not two, and the third is the point: a compiled rung
    is either **fresh** or **stale**, and the engine's label does not distinguish
    them. A stale rung still reports success, because the label says so -- that
    is exactly the failure mode the previous section measured in prose. The
    detector is the only thing that can tell the two apart, and it is why
    ``delta`` is a parameter rather than an assumption.

    ``cascade`` selects what a drift invalidates. ``False`` is the modular
    reading: only the drifted rung is withdrawn. ``True`` is the monolithic one:
    the whole suffix is re-derived, because a result computed through a stale
    prefix is a result about nothing. The difference between the two arms is the
    price of coupling, and it is most of the price of depth.
    """
    state = [UNSOLIDIFIED] * depth      # UNSOLIDIFIED / fresh / stale
    total = 0.0
    false_count = 0
    replay_count = 0
    crossed = 0

    for _ in range(runs):
        for j in range(depth):
            crossed += 1
            current = state[j]
            if not labelled:
                # No label system at all: the rung is checked in full, every
                # time, and there is nothing to go stale.
                total += verify
                continue
            if current == UNSOLIDIFIED:
                total += verify
                state[j] = "fresh"
                continue
            total += cached + detect
            if current == "stale":
                # Still wrong, and still cheap. The detector gets another chance
                # on every traverse, which is the only reason this ever ends.
                if rng.random() < delta:
                    total += verify
                    state[j] = "fresh"
                else:
                    false_count += 1
                continue
            if rng.random() < rho:
                if rng.random() < delta:
                    total += verify
                    state[j] = "fresh"
                    if cascade:
                        for k in range(j + 1, depth):
                            if state[k] != UNSOLIDIFIED:
                                state[k] = "fresh"
                                total += verify
                else:
                    false_count += 1
                    state[j] = "stale"
            else:
                # A pure replay: the cache was live and nothing moved.
                replay_count += 1
    return Traverse(cost=total, false=false_count, solid=replay_count,
                    crossed=crossed)


def sweep(runs: int = RUNS, seed: int = SEED) -> List[Dict[str, object]]:
    """The CSV: every (scenario, depth, rho) cell, simulated then compared."""
    rows: List[Dict[str, object]] = []
    rng = random.Random(seed)

    def record(name: str, depth: int, rho: float, delta: float,
               row: Traverse, analytic_cost: float, analytic_false: float,
               analytic_solid: float) -> None:
        rows.append({
            "scenario": name,
            "depth": depth,
            "rho": rho,
            "delta": delta,
            "runs": runs,
            "cost": row.cost,
            "cost_per_rung": row.cost / row.crossed,
            "false_rate": row.false / row.crossed,
            "solid_rate": row.solid / row.crossed,
            "analytic_cost": analytic_cost,
            "analytic_false": analytic_false,
            "analytic_solid": analytic_solid,
        })

    for depth in range(1, DEPTH + 1):
        row = simulate(depth, runs, 0.0, 0.0, rng, labelled=False)
        record("unlabelled", depth, 0.0, 0.0, row,
               unlabelled_total(depth), 0.0, 0.0)
        for rho in RHO_GRID:
            for name, delta, casc in (
                ("labelled", DETECTOR_SENSITIVITY, False),
                ("labelled_naive", NAIVE_SENSITIVITY, False),
                ("labelled_cascade", DETECTOR_SENSITIVITY, True),
            ):
                row = simulate(depth, runs, rho, delta, rng, cascade=casc)
                analytic = (cascade_cost(depth, rho, delta) if casc
                            else warm_total(depth, rho, delta))
                record(name, depth, rho, delta, row, analytic,
                       false_rate(rho, delta), replay_fraction(rho, delta))
    return rows


def depth_curve() -> List[Dict[str, object]]:
    """Cost against depth, for the price models, on one grid."""
    out = []
    for depth in range(1, DEPTH_MAX + 1):
        out.append({
            "depth": depth,
            "unlabelled": unlabelled_total(depth),
            "replay": warm_total(depth, 0.0, DETECTOR_SENSITIVITY),
            "cold": replay_total(depth, 0.0, DETECTOR_SENSITIVITY),
            "harmonic": harmonic_total(depth),
            "cascade": cascade_cost(depth, 0.1, DETECTOR_SENSITIVITY),
            "marginal_nines": marginal_nines(depth),
            "marginal_harmonic": marginal_harmonic(depth),
        })
    return out


# ---------------------------------------------------------------------------
# 4. the real engine, not a model of it
# ---------------------------------------------------------------------------


def build_ladder(depth: int = DEPTH) -> Tuple[FractalEvolver, List[Interface]]:
    """A real ladder, crossed rung by rung, each rung compiled against its witness.

    Uses the public door only: ``cross_interface`` raises ``condition_met``, and
    ``solidify=True`` compiles. Nothing here reaches into a private field, so if
    the engine's rules change this block changes behaviour rather than lying.
    """
    evolver = FractalEvolver(layers=[
        Layer("Macro", "M", measured={"t": 1.0}),
        Layer("Quantum", "Q", measured={"t": 1e-12}),
    ])
    node = evolver.seed(
        Interface("Macro", "Quantum", "phase coherence", span=1.0, difficulty=0.81)
    )
    rungs: List[Interface] = []
    for j in range(depth):
        result = evolver.cross_interface(
            node,
            NarrowCondition(
                "mechanism {}".format(j), "dilution refrigeration",
                "deposition record {}".format(j), 100.0,
                replicable=True, cost=0.9,
            ),
            next_condition="boundary {}".format(j + 1),
            solidify=True,
        )
        if result["verdict"] != PASS:
            raise AssertionError("rung {} did not cross: {}".format(j, result))
        rungs.append(_rung(evolver, result["interface"]))
        child = result["sub_interface"]
        node = Interface(
            source="Macro",
            target="Quantum",
            condition=child["condition"],
            span=child["span"],
            difficulty=child["difficulty"],
            generation=child["generation"],
        )
    return evolver, rungs


def _rung(evolver: FractalEvolver, key: Sequence[object]) -> Interface:
    for root in evolver.chains():
        for node in root.walk():
            if list(node.key) == list(key):
                return node
    raise AssertionError("rung {} not found".format(list(key)))


def witness_block() -> Dict[str, object]:
    """What the label accepts and what it refuses, checked against real records.

    Every case is a witness the engine could be handed. The interesting ones are
    the refusals: a witness with nothing behind it, a witness for another gap, and
    a witness edited after the fact. All three would compile a rung under a weaker
    rule, and all three are the same defect as ERR-003 with a different noun.
    """
    evolver, rungs = build_ladder(DEPTH)
    report = evolver.solidification_report()
    history = evolver.history
    first = rungs[0]

    checks: List[Dict[str, object]] = []

    # No witness at all.
    evolver.drift(first, "control: withdraw before the refusal test")
    result = evolver.solidify(first, None)
    checks.append({"case": "no witness", "verdict": result["verdict"],
                   "state": result["state"]})

    # A witness for a different gap: take the last rung's record.
    other = history[-1]
    result = evolver.solidify(first, other)
    checks.append({"case": "witness for another gap", "verdict": result["verdict"],
                   "state": result["state"]})

    # A witness edited after the fact.
    tampered = dict(history[0])
    tampered["amplification"] = 1.0e6
    result = evolver.solidify(first, tampered)
    checks.append({"case": "edited witness", "verdict": result["verdict"],
                   "state": result["state"]})

    # The right witness, which should compile.
    result = evolver.solidify(first, history[0])
    checks.append({"case": "its own witness", "verdict": result["verdict"],
                   "state": result["state"]})

    return {
        "depth": DEPTH,
        "report": report,
        "checks": checks,
        "all_solidified": all(rung.is_solidified for rung in rungs),
        "witness_verifies": all(verify_witness(record) for record in history),
        "compiled_cost": report["cost"],
        "uncompiled_cost": report["uncompiled_cost"],
    }


def drift_block(depth: int = DEPTH) -> List[Dict[str, object]]:
    """Withdraw one compiled rung and re-price the whole ladder, two ways.

    The engine prices exactly the rung whose label was withdrawn. A caller that
    depends on that rung being right cannot: a result computed through a stale
    prefix is a result about a different chain, so every rung after it has to be
    re-derived too. The two columns are those two prices, and the gap between
    them is the cost of trusting a prefix -- the same gap that makes the cascade
    arm of the drift table expensive.
    """
    rows = []
    for withdrawn in range(depth):
        evolver, rungs = build_ladder(depth)
        evolver.drift(rungs[withdrawn], "simulated environment change")
        report = evolver.solidification_report()
        cascade = 0.0
        for index, rung in enumerate(rungs):
            if index >= withdrawn:
                cascade += VERIFY_COST
            else:
                cascade += rung.crossing_cost
        rows.append({
            "withdrawn_rung": withdrawn + 1,
            "solidified": report["solidified"],
            "fraction": report["fraction"],
            "cost": report["cost"],
            "cascade_cost": cascade,
            "affected": depth - withdrawn,
        })
    return rows


# ---------------------------------------------------------------------------
# 5. rendering
# ---------------------------------------------------------------------------

INK = "#1f2d3d"
ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"
PALE = "#c9cdd3"
GREEN = "#3f7d5a"


def _style() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 8.5,
        "axes.labelsize": 8.5,
        "axes.titlesize": 9,
        "legend.fontsize": 7.0,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "axes.linewidth": 0.7,
        "grid.linewidth": 0.4,
        "grid.alpha": 0.35,
        "savefig.dpi": 220,
        "figure.dpi": 110,
    })


def render(curve: Sequence[Dict[str, object]], rows: Sequence[Dict[str, object]],
           path: str = FIGURE_PNG) -> str:
    _style()
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.7), constrained_layout=True)

    # (a) total cost against depth -----------------------------------------
    ax = axes[0][0]
    depths = [c["depth"] for c in curve]
    ax.loglog(depths, [c["unlabelled"] for c in curve], color=ACCENT,
              linewidth=1.7, label="A: unlabelled, $K\\,c_v$")
    ax.loglog(depths, [c["replay"] for c in curve], color=COOL, linewidth=1.7,
              label="B: compiled replay")
    ax.loglog(depths, [c["harmonic"] for c in curve], color=GREEN,
              linewidth=1.5, linestyle="--", label="B: budget, $c_v\\log_2 K$")
    ax.axvline(DEPTH, color=PALE, linewidth=0.9, zorder=0)
    ax.annotate("$K={}$".format(DEPTH), xy=(DEPTH, 0.02), fontsize=6.2,
                color=MUTED, ha="right")
    ax.set_xlabel("depth $K$")
    ax.set_ylabel("total cost, in verifications")
    ax.set_title("(a) linear against logarithmic", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.5,
              borderaxespad=0.3)
    ax.grid(True, which="both")

    # (b) the marginal -------------------------------------------------------
    ax = axes[0][1]
    js = [c["depth"] for c in curve if c["depth"] <= 40]
    ax.semilogy(js, [marginal_nines(j) for j in js], color=GREEN,
                linewidth=1.5, label="Eq. (19), nines")
    ax.semilogy(js, [marginal_harmonic(j) for j in js], color=COOL,
                linewidth=1.5, linestyle="--", label="$c_v\\log_2\\frac{j}{j-1}$")
    ax.semilogy(js, [VERIFY_COST for _ in js], color=ACCENT, linewidth=1.6,
                label="unlabelled, constant")
    ax.semilogy(js, [SOLIDIFIED_COST for _ in js], color=MUTED, linewidth=1.2,
                linestyle=":", label="replay floor")
    ax.set_xlabel("rung $j$")
    ax.set_ylabel("marginal cost")
    ax.set_title("(b) why the marginal falls: $1/j$", loc="left")
    ax.legend(frameon=False, loc="lower left", handlelength=1.5,
              borderaxespad=0.3, fontsize=6.4)
    ax.grid(True, which="both")

    # (c) solidification rate against depth ---------------------------------
    ax = axes[1][0]
    shades = {0.0: GREEN, 0.05: COOL, 0.1: "#a0605e", 0.25: ACCENT}
    positions = list(range(1, DEPTH + 1))
    for rho, colour in shades.items():
        survival = [cascade_survival(j, rho, DETECTOR_SENSITIVITY)
                    for j in positions]
        ax.plot(positions, survival, color=colour, linewidth=1.5,
                marker="o", markersize=2.2,
                label="cascade, $\\rho={}$".format(rho))
        ax.axhline(replay_fraction(rho, DETECTOR_SENSITIVITY), color=colour,
                   linewidth=0.8, linestyle=":", alpha=0.85)
    ax.set_xlabel("rung $j$ (position in the ladder)")
    ax.set_ylabel("compiled at entry")
    ax.set_ylim(0, 1.04)
    ax.set_title("(c) a long chain is the least compiled", loc="left")
    ax.legend(frameon=False, loc="lower left", ncol=2, handlelength=1.2,
              fontsize=6.2, columnspacing=0.7)
    ax.grid(True)

    # (d) drift, cost and false completion ----------------------------------
    ax = axes[1][1]
    lab = [r for r in rows if r["scenario"] == "labelled" and r["depth"] == DEPTH]
    naive = [r for r in rows if r["scenario"] == "labelled_naive"
             and r["depth"] == DEPTH]
    cas = [r for r in rows if r["scenario"] == "labelled_cascade"
           and r["depth"] == DEPTH]
    unlab = [r for r in rows if r["scenario"] == "unlabelled"
             and r["depth"] == DEPTH][0]
    rhos = [r["rho"] for r in lab]
    ax.semilogy(rhos, [r["cost_per_rung"] for r in lab], color=COOL,
                linewidth=1.6, marker="o", markersize=2.4,
                label="B modular, detector")
    ax.semilogy(rhos, [r["cost_per_rung"] for r in cas], color=GREEN,
                linewidth=1.4, marker="^", markersize=2.2,
                label="B cascading")
    ax.semilogy(rhos, [r["cost_per_rung"] for r in naive], color=ACCENT,
                linewidth=1.6, marker="s", markersize=2.2,
                label="B no detector")
    ax.axhline(unlab["cost_per_rung"], color=MUTED, linewidth=1.4,
               linestyle="--", label="A unlabelled")
    ax.set_xscale("symlog", linthresh=0.005)
    ax.set_xlabel("drift rate $\\rho$")
    ax.set_ylabel("cost per rung")
    ax.set_title("(d) the cheapest path is the wrong one", loc="left")

    ax2 = ax.twinx()
    ax2.plot(rhos, [100 * r["false_rate"] for r in lab], color=COOL,
             linewidth=0.9, linestyle=":")
    ax2.plot(rhos, [100 * r["false_rate"] for r in naive], color=ACCENT,
             linewidth=0.9, linestyle=":")
    ax2.set_ylabel("false completion (\\%)", color=MUTED)
    ax2.tick_params(axis="y", colors=MUTED)
    ax2.set_ylim(-3, 103)
    ax.legend(frameon=False, loc="upper left", handlelength=1.5,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True, which="both")

    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 6. the report
# ---------------------------------------------------------------------------


def _money(value: float) -> str:
    if value >= 100:
        return "{:.1f}".format(value)
    if value >= 1:
        return "{:.3f}".format(value)
    return "{:.4f}".format(value)


def slope_log_depth(curve: Sequence[Dict[str, object]], key: str,
                    upto: int = 40) -> Tuple[float, float]:
    """Least-squares slope and R^2 of total cost against ``log10 K``.

    The budget model should return ``1/log10(2) = 3.3219`` with R^2 = 1: total
    cost is *linear* in ``log10 K``, which is the statement "depth is logarithmic
    in cost". The unlabelled arm is linear in ``K`` and therefore not linear on
    this axis at all, and its R^2 says so -- which is the numerical form of the
    difference the section is about.

    Fitted against the raw total rather than against ``log10`` of the total. The
    log-log version is the tempting one and it is wrong here: a logarithmic total
    has no power-law slope, and fitting one reports a small number that looks like
    agreement.
    """
    points = [c for c in curve if 2 <= c["depth"] <= upto]
    xs = [math.log10(c["depth"]) for c in points]
    ys = [float(c[key]) for c in points]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    intercept = my - slope * mx
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return slope, r2



def report(rows: Sequence[Dict[str, object]], curve: Sequence[Dict[str, object]],
           witness: Dict[str, object], drift: Sequence[Dict[str, object]]) -> None:
    depth = DEPTH
    lab0 = next(r for r in rows if r["scenario"] == "labelled"
                and r["depth"] == depth and r["rho"] == 0.0)
    naive0 = next(r for r in rows if r["scenario"] == "labelled_naive"
                  and r["depth"] == depth and r["rho"] == 0.0)
    unlab = next(r for r in rows if r["scenario"] == "unlabelled"
                 and r["depth"] == depth)

    cold_a = unlabelled_total(depth)
    cold_b = replay_total(depth, 0.0, DETECTOR_SENSITIVITY)
    cold_pure = VERIFY_COST + (depth - 1) * SOLIDIFIED_COST
    warm_a = unlab["cost_per_rung"]
    slope_u, r2_u = slope_log_depth(curve, "unlabelled")
    slope_b, r2_b = slope_log_depth(curve, "harmonic")

    print("=" * 78)
    print("SOLIDIFICATION: what eats the marginal cost, and how far it goes")
    print("=" * 78)
    print("depth {}   runs {}   c_verify {:.2f}   c_cached {:.3f}   "
          "c_detect {:.2f}".format(depth, RUNS, VERIFY_COST, SOLIDIFIED_COST,
                                   DETECTOR_COST))
    print("labelling gain per rung: {:.0f}x      detector sensitivity: {:.0f}"
          "      seed {}".format(SOLIDIFICATION_GAIN, DETECTOR_SENSITIVITY, SEED))
    print()

    print("-" * 78)
    print("1. TOTAL COST, ONE TRAVERSE OF A DEPTH-{} LADDER".format(depth))
    print("-" * 78)
    print("  A  unlabelled, every rung verified      {:>9.3f}".format(cold_a))
    print("  B  labelled, every rung compiled         {:>9.3f}".format(cold_b))
    print("  B' labelled, no detector charge          {:>9.3f}".format(cold_pure))
    print("  ratio A/B                                {:>9.2f}x".format(
        cold_a / cold_b))
    print("  ratio A/B'                               {:>9.2f}x".format(
        cold_a / cold_pure))
    print()
    print("  Warm (same ladder, already compiled), per traverse:")
    print("  A {:>9.3f}   B {:>9.3f}   ratio {:.2f}x".format(
        warm_a, warm_total(depth, 0.0, DETECTOR_SENSITIVITY),
        warm_a / warm_total(depth, 0.0, DETECTOR_SENSITIVITY)))
    print()
    print("  Over {} traversals, the totals are {:.0f} against {:.0f}."
          .format(RUNS, unlab["cost"], lab0["cost"]))
    print("  The ratio is *bounded* by c_v/(c_c+c_d) = {:.1f}x and approaches it"
          .format(VERIFY_COST / (SOLIDIFIED_COST + DETECTOR_COST)))
    print("  from below. It cannot be exponential: a fixed depth is a sum of")
    print("  positive per-rung costs. Anyone reporting a larger number for this")
    print("  quantity has changed the variable, which is item 3.")
    print()

    print("-" * 78)
    print("2. THE MARGINAL COST OF RUNG j  (the mechanism)")
    print("-" * 78)
    print("  {:>4} {:>16} {:>18} {:>12}".format(
        "j", "Eq.(19) nines", "in verifications", "unlabelled"))
    for j in (2, 3, 4, 6, 10, 20, 50, 100):
        print("  {:>4} {:>16.4f} {:>18.4f} {:>12.2f}".format(
            j, marginal_nines(j), marginal_harmonic(j), VERIFY_COST))
    print()
    print("  The budget marginal is harmonic: it falls as 1/j toward zero, which")
    print("  is the identity Eq. (19) already states in nines. Solidification is")
    print("  the mechanism behind it: rung j verifies the boundary that is new and")
    print("  replays the prefix, so it is charged for 1/j of the ladder rather")
    print("  than for all of it. Nothing about the gap changes.")
    print()
    print("  Total cost against log10 K, least squares over K in [2, 40]:")
    print("    unlabelled   slope {:>7.3f}   R2 {:>6.4f}   (not a line: K*c_v)"
          .format(slope_u, r2_u))
    print("    budget model slope {:>7.3f}   R2 {:>6.4f}   (predicted {:.4f})"
          .format(slope_b, r2_b, 1.0 / math.log10(2.0)))
    print()
    print("  The second line is the check that matters: the coefficient was not")
    print("  fitted to the data, it follows from Eq. (19) plus the normalisation")
    print("  that rung 2 costs one verification. It comes out at {:.4f}."
          .format(1.0 / math.log10(2.0)))
    print()

    print("-" * 78)
    print("3. WHERE THE EXPONENTIAL ACTUALLY IS")
    print("-" * 78)
    per_rung = SOLIDIFIED_COST + DETECTOR_COST
    print("  Budget buys depth: A at c_v per rung, B at {:.2f} per rung."
          .format(per_rung))
    for budget in (10.0, 100.0, 1000.0):
        k_a = depths_at_budget(budget, VERIFY_COST)
        k_b = depths_at_budget(budget, per_rung)
        print("    budget {:>6.0f}   A depth {:>7.1f}   B depth {:>8.1f}   "
              "({:.1f}x deeper)".format(budget, k_a, k_b, k_b / k_a))
    print()
    print("  The depth ratio is a constant, because depth is linear in budget in")
    print("  both arms. Reach is exponential in depth, so the *reach* ratio is")
    print("  exponential in the budget:")
    for budget in (5.0, 10.0, 20.0):
        k_a = depths_at_budget(budget, VERIFY_COST)
        k_b = depths_at_budget(budget, per_rung)
        gain = k_b - k_a
        print("    budget {:>4.0f}: {:>5.0f} extra rungs -> reach ratio "
              "A^{:.0f}; at A = 100 that is 10^{:.0f}".format(
                  budget, gain, gain, gain * 2.0))
    print()
    print("  That is the honest form of the claim. The exponential lives in the")
    print("  inverse map -- reach per unit budget -- and not in cost per unit")
    print("  depth, where at most {:.0f}x exists. It also saturates: the engine"
          .format(VERIFY_COST / per_rung))
    print("  clamps reach at the horizon (10^12), so the window is real and")
    print("  finite. That is the previous section's result, unchanged.")
    print()

    print("-" * 78)
    print("4. DRIFT: A LABEL WITHOUT A DETECTOR IS WORSE THAN NO LABEL")
    print("-" * 78)
    print("  {:>6} {:>8} {:>9} {:>9} {:>9} {:>8} {:>8}".format(
        "rho", "A", "B", "B casc", "B naive", "B FCR", "naive FCR"))
    for rho in RHO_GRID:
        lab = next(r for r in rows if r["scenario"] == "labelled"
                   and r["depth"] == depth and r["rho"] == rho)
        cas = next(r for r in rows if r["scenario"] == "labelled_cascade"
                   and r["depth"] == depth and r["rho"] == rho)
        nai = next(r for r in rows if r["scenario"] == "labelled_naive"
                   and r["depth"] == depth and r["rho"] == rho)
        print("  {:>6.3f} {:>8.3f} {:>9.4f} {:>9.4f} {:>9.4f} "
              "{:>7.1f}% {:>7.1f}%".format(
                  rho, unlab["cost_per_rung"], lab["cost_per_rung"],
                  cas["cost_per_rung"], nai["cost_per_rung"],
                  100 * lab["false_rate"], 100 * nai["false_rate"]))
    print()
    print("  Three things to read off this table.")
    print("  (a) The naive arm is the cheapest column in it and the only one")
    print("      whose false completion runs to 100%. A missed drift is never")
    print("      forgotten, so the compiled path stays wrong for the rest of the")
    print("      run: a cache without invalidation is a transcript with better")
    print("      latency.")
    print("  (b) Cost *rises* with rho in both correct arms. Solidification is")
    print("      not a one-off purchase; it is rented at the drift rate. The")
    print("      crossover is rho* = 1 - (c_c+c_d)/c_v = {:.3f}, beyond which"
          .format(break_even_rho()))
    print("      compiling is a net loss as well as a risk.")
    print("  (c) The cascade column is what depth really costs when a drift")
    print("      invalidates the suffix. At rho = 0.1 it is {:.2f} against {:.2f}"
          .format(
              cascade_cost(depth, 0.1, DETECTOR_SENSITIVITY) / depth,
              rung_cost(0.1, DETECTOR_SENSITIVITY)))
    print("      for the modular arm: {:.1f}x worse, and quadratic in depth."
          .format(
              (cascade_cost(depth, 0.1, DETECTOR_SENSITIVITY) / depth)
              / rung_cost(0.1, DETECTOR_SENSITIVITY)))
    print("      The break-even drift for a cascading chain is")
    print("      rho* = 2(1-(c_c+c_d)/c_v)/(K+1) = {:.3f} at K = {}, so it"
          .format(cascade_break_even_rho(depth), depth))
    print("      *falls* as the chain deepens: {:.3f} at K = 100. Compilation"
          .format(cascade_break_even_rho(100)))
    print("      needs modularity before it needs speed, which is why interfaces")
    print("      that leak state across a boundary cannot be compiled cheaply.")
    print()
    print("  Detector break-even: it pays while")
    print("    c_d < (1-rho)(c_v - c_c),  i.e. {:>5.2f} at rho=0 and {:>5.2f} at "
          "rho=0.5".format(break_even_detector(0.0), break_even_detector(0.5)))
    print("  A statement about prices, not about probability.")
    print()
    print("  Solidification rate (share of traverses served from cache):")
    for rho in (0.0, 0.01, 0.1, 0.5, 1.0):
        print("    rho {:>5.2f} -> {:>6.1f}% of traverses are pure replay"
              .format(rho, 100 * replay_fraction(rho, DETECTOR_SENSITIVITY)))
    print("  It reaches 100% only at rho = 0. That is the sense in which")
    print("  seamlessness is a limit and not a property: any drifting environment")
    print("  caps it strictly below 1.")
    print()

    print("-" * 78)
    print("5. THE LABEL, CHECKED AGAINST REAL WITNESSES")
    print("-" * 78)
    print("  ladder depth {}   rungs compiled {}/{}   cost {} vs {}".format(
        witness["depth"], witness["report"]["solidified"],
        witness["report"]["crossed"], _money(witness["compiled_cost"]),
        _money(witness["uncompiled_cost"])))
    print("  every stored witness re-verifies: {}".format(
        witness["witness_verifies"]))
    print()
    for check in witness["checks"]:
        print("  {:<28} -> {:<8} state {}".format(
            check["case"], check["verdict"], check["state"]))
    print()
    print("  The three refusals are the section's actual content. A label that")
    print("  could be set without a witness would be an assertion, and an")
    print("  assertion is what the unlabelled arm already had.")
    print()

    print("-" * 78)
    print("6. WITHDRAWAL IS LOCAL IN THE LABEL, GLOBAL IN THE PRICE")
    print("-" * 78)
    print("  {:>9} {:>10} {:>13} {:>13}".format(
        "withdrawn", "compiled", "engine cost", "cascade cost"))
    for row in drift:
        print("  {:>9} {:>10} {:>13.3f} {:>13.3f}".format(
            row["withdrawn_rung"], row["solidified"], row["cost"],
            row["cascade_cost"]))
    print()
    print("  The engine prices exactly the rungs whose label was withdrawn. A")
    print("  caller downstream of a withdrawn rung has to discount every rung")
    print("  after it, and the last column is that stricter price. Withdrawing")
    print("  rung 1 is cheap in the label and costs the whole chain in fact,")
    print("  which is the same asymmetry the cascade arm measures.")
    print()

    print("-" * 78)
    print("WHAT THIS DOES NOT SHOW")
    print("-" * 78)
    print("  1. It does not show that c_c = {:.2f} is the right price for a"
          .format(SOLIDIFIED_COST))
    print("     compiled rung. The experiment reports the ratio it assumes and")
    print("     every curve as a function of it; a different price moves the")
    print("     numbers and not the ordering.")
    print("  2. It does not show that total cost falls with depth. It cannot:")
    print("     per-rung costs are positive, so the sum rises. What falls is the")
    print("     marginal, and a falling marginal is a logarithmic total.")
    print("  3. It does not show that a detector of sensitivity 1 exists. delta is")
    print("     a parameter, and the naive arm is what delta = 0 looks like: the")
    print("     cheapest configuration in the table and the least true.")
    print()


# ---------------------------------------------------------------------------
# 7. artifacts
# ---------------------------------------------------------------------------


def write_data(rows: Sequence[Dict[str, object]]) -> str:
    fields = ["scenario", "depth", "rho", "delta", "runs", "cost",
              "cost_per_rung", "false_rate", "solid_rate", "analytic_cost",
              "analytic_false", "analytic_solid"]
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([
                row["scenario"],
                row["depth"],
                "{:.6f}".format(row["rho"]),
                "{:.1f}".format(row["delta"]),
                row["runs"],
                "{:.6f}".format(row["cost"]),
                "{:.9f}".format(row["cost_per_rung"]),
                "{:.9f}".format(row["false_rate"]),
                "{:.9f}".format(row["solid_rate"]),
                "{:.6f}".format(row["analytic_cost"]),
                "{:.9f}".format(row["analytic_false"]),
                "{:.9f}".format(row["analytic_solid"]),
            ])
    return DATA_CSV


def tex_number(value: float, digits: int = 3) -> str:
    if value == 0:
        return "$0$"
    magnitude = abs(value)
    if 1e-2 <= magnitude < 1e4:
        if magnitude >= 100:
            return "${:.0f}$".format(value)
        if magnitude >= 10:
            return "${:.1f}$".format(value)
        return "${:.3g}$".format(value)
    exponent = int(math.floor(math.log10(magnitude)))
    mantissa = value / 10.0 ** exponent
    if abs(abs(mantissa) - 10.0) < 5e-3:
        mantissa /= 10.0
        exponent += 1
    return "${:.2f}\\times10^{{{}}}$".format(mantissa, exponent)


def write_paper_artifacts(rows: Sequence[Dict[str, object]],
                          curve: Sequence[Dict[str, object]],
                          witness: Dict[str, object]) -> List[str]:
    os.makedirs(PAPER_DATA, exist_ok=True)
    os.makedirs(PAPER_FIGURES, exist_ok=True)

    deepest = DEPTH
    lab0 = next(r for r in rows if r["scenario"] == "labelled"
                and r["depth"] == deepest and r["rho"] == 0.0)
    unlab = next(r for r in rows if r["scenario"] == "unlabelled"
                 and r["depth"] == deepest)
    naive = next(r for r in rows if r["scenario"] == "labelled_naive"
                 and r["depth"] == deepest and r["rho"] == 0.1)
    slope_u, r2_u = slope_log_depth(curve, "unlabelled")
    slope_b, r2_b = slope_log_depth(curve, "harmonic")
    cold_a = unlabelled_total(deepest)
    cold_b = replay_total(deepest, 0.0, DETECTOR_SENSITIVITY)
    cold_pure = VERIFY_COST + (deepest - 1) * SOLIDIFIED_COST

    macros = [
        "% generated by experiments/solidification_experiment.py -- do not edit",
        "\\newcommand{\\sdDepth}{%d}" % deepest,
        "\\newcommand{\\sdRuns}{%d}" % RUNS,
        "\\newcommand{\\sdVerifyCost}{%.2f}" % VERIFY_COST,
        "\\newcommand{\\sdCachedCost}{%.2f}" % SOLIDIFIED_COST,
        "\\newcommand{\\sdDetectCost}{%.2f}" % DETECTOR_COST,
        "\\newcommand{\\sdGain}{%.0f}" % SOLIDIFICATION_GAIN,
        "\\newcommand{\\sdColdUnlabelled}{%.2f}" % cold_a,
        "\\newcommand{\\sdColdLabelled}{%.2f}" % cold_b,
        "\\newcommand{\\sdColdPure}{%.2f}" % cold_pure,
        "\\newcommand{\\sdColdPureRatio}{%.2f}" % (cold_a / cold_pure),
        "\\newcommand{\\sdColdRatio}{%.2f}" % (cold_a / cold_b),
        "\\newcommand{\\sdWarmUnlabelled}{%.2f}" % VERIFY_COST,
        "\\newcommand{\\sdWarmLabelled}{%.2f}" % rung_cost(0.0, DETECTOR_SENSITIVITY),
        "\\newcommand{\\sdRatio}{%.2f}" % (unlab["cost"] / lab0["cost"]),
        "\\newcommand{\\sdBound}{%.0f}"
        % (VERIFY_COST / (SOLIDIFIED_COST + DETECTOR_COST)),
        "\\newcommand{\\sdDepthGain}{%.0f}"
        % (VERIFY_COST / (SOLIDIFIED_COST + DETECTOR_COST)),
        "\\newcommand{\\sdSlopeUnlabelled}{%.2f}" % slope_u,
        "\\newcommand{\\sdSlopeUnlabelledRsq}{%.3f}" % r2_u,
        "\\newcommand{\\sdSlopeBudget}{%.4f}" % slope_b,
        "\\newcommand{\\sdSlopeBudgetRsq}{%.3f}" % r2_b,
        "\\newcommand{\\sdSlopePredicted}{%.4f}" % (1.0 / math.log10(2.0)),
        "\\newcommand{\\sdNaiveFcr}{%.0f}" % (100 * naive["false_rate"]),
        "\\newcommand{\\sdNaiveRho}{%.1f}" % naive["rho"],
        "\\newcommand{\\sdReplayRate}{%.0f}"
        % (100 * replay_fraction(0.1, DETECTOR_SENSITIVITY)),
        "\\newcommand{\\sdCascadeRate}{%.0f}"
        % (100 * cascade_survival(10, 0.1, DETECTOR_SENSITIVITY)),
        "\\newcommand{\\sdBreakEvenRho}{%.2f}" % break_even_rho(),
        "\\newcommand{\\sdRhoCascadeTen}{%.3f}" % cascade_break_even_rho(10),
        "\\newcommand{\\sdRhoCascadeHundred}{%.3f}" % cascade_break_even_rho(100),
        "\\newcommand{\\sdCascadePenalty}{%.2f}"
        % ((cascade_cost(deepest, 0.1, DETECTOR_SENSITIVITY) / deepest)
           / rung_cost(0.1, DETECTOR_SENSITIVITY)),
        "\\newcommand{\\sdCompiledRungs}{%d}" % witness["report"]["solidified"],
        "\\newcommand{\\sdCrossedRungs}{%d}" % witness["report"]["crossed"],
        "\\newcommand{\\sdBreakEven}{%.2f}" % (VERIFY_COST - SOLIDIFIED_COST),
    ]
    macros_path = os.path.join(PAPER_DATA, "solidification_macros.tex")
    with open(macros_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(macros) + "\n")

    table = [
        "% generated by experiments/solidification_experiment.py -- do not edit",
        "% rho, unlabelled cost per rung, modular labelled, cascading labelled,",
        "% naive labelled, naive false-completion rate",
        "\\begin{tabular}{@{}rrrrrr@{}}",
        "\\toprule",
        "$\\rho$ & A & B & B$_{\\text{casc}}$ & B$_{\\delta=0}$ & "
        "B$_{\\delta=0}$ FCR (\\%) \\\\",
        "\\midrule",
    ]
    for rho in RHO_GRID:
        lab = next(r for r in rows if r["scenario"] == "labelled"
                   and r["depth"] == deepest and r["rho"] == rho)
        cas = next(r for r in rows if r["scenario"] == "labelled_cascade"
                   and r["depth"] == deepest and r["rho"] == rho)
        nai = next(r for r in rows if r["scenario"] == "labelled_naive"
                   and r["depth"] == deepest and r["rho"] == rho)
        table.append(
            "{:.3f} & {:.3f} & {:.4f} & {:.4f} & {:.4f} & {:.1f} \\\\".format(
                rho, unlab["cost_per_rung"], lab["cost_per_rung"],
                cas["cost_per_rung"], nai["cost_per_rung"],
                100 * nai["false_rate"]))
    table += ["\\bottomrule", "\\end{tabular}"]
    table_path = os.path.join(PAPER_DATA, "solidification_table.tex")
    with open(table_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(table) + "\n")

    figure_path = os.path.join(PAPER_FIGURES, "solidification.png")
    with open(FIGURE_PNG, "rb") as source, open(figure_path, "wb") as target:
        target.write(source.read())

    return [macros_path, table_path, figure_path]


def write_summary(rows: Sequence[Dict[str, object]],
                  curve: Sequence[Dict[str, object]],
                  witness: Dict[str, object],
                  drift: Sequence[Dict[str, object]]) -> str:
    payload = {
        "parameters": {
            "seed": SEED,
            "depth": DEPTH,
            "runs": RUNS,
            "verify_cost": VERIFY_COST,
            "cached_cost": SOLIDIFIED_COST,
            "detector_cost": DETECTOR_COST,
            "detector_sensitivity": DETECTOR_SENSITIVITY,
            "rho_grid": list(RHO_GRID),
        },
        "slopes": {
            "unlabelled": slope_log_depth(curve, "unlabelled"),
            "budget": slope_log_depth(curve, "harmonic"),
            "predicted_budget": 1.0 / math.log10(2.0),
        },
        "closed_forms": {
            "replay_fraction_rho_0.1": replay_fraction(0.1, DETECTOR_SENSITIVITY),
            "cascade_survival_rung_10": cascade_survival(10, 0.1,
                                                         DETECTOR_SENSITIVITY),
            "rung_cost_rho_0.1": rung_cost(0.1, DETECTOR_SENSITIVITY),
            "break_even_rho": break_even_rho(),
            "break_even_detector_rho_0": break_even_detector(0.0),
            "break_even_detector_rho_0.5": break_even_detector(0.5),
        },
        "witness_block": witness,
        "drift_block": list(drift),
        "grid": list(curve),
        "rows": list(rows),
    }
    with open(SUMMARY_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return SUMMARY_JSON


# ---------------------------------------------------------------------------
# 8. main
# ---------------------------------------------------------------------------


def main() -> int:
    rows = sweep()
    curve = depth_curve()
    witness = witness_block()
    drift = drift_block()

    report(rows, curve, witness, drift)
    figure = render(curve, rows)
    write_data(rows)
    write_summary(rows, curve, witness, drift)
    paper_files = write_paper_artifacts(rows, curve, witness)

    print("=" * 78)
    print("WROTE")
    print("=" * 78)
    for path in [figure, DATA_CSV, SUMMARY_JSON] + paper_files:
        print("  {}".format(os.path.relpath(path, ROOT)))
    print()
    print("Reproduce with:  python3 -m experiments.solidification_experiment")
    print("No network access. No commit. No push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
