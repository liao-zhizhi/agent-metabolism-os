"""Nested solidification: does compiling one layer make the next layer cheaper?

The critical-budget experiment priced a *single* interface. It answered "should
this rung be compiled?" and, honestly, it answered "amortisation is a ratio of
two prices". A ratio is arithmetic: it buys a constant factor and nothing more.
The claim this file tests is the one that would make the mechanism interesting:

    once a layer is compiled, a *higher* layer can be verified by *re-labelling*
    the compiled objects -- a lookup -- instead of re-verifying their internals,
    so the higher layer's marginal cost stops growing with its own complexity.

If that is true, the constant factor is not the story: the story is that the
per-task price becomes independent of how many dimensions the task spans, while
the un-nested price is exponential in that number. Compounding, not amortisation.

The two-layer system
--------------------
Layer 0 is the base: physics, hashing, whatever the certificate ultimately
rests on. It costs ``c_v = 1`` to verify a leaf, and solidifying it costs a
budget ``B*`` -- the previous section's threshold, recomputed here from the same
closed form with ``S = 5`` so the two experiments cannot drift apart. Once layer
0 is solid, consulting a leaf costs ``c_bar = 0.109``, the drift-adjusted replay
price, not ``c_v``; that factor ``G = c_v/c_bar = 9.174`` is the *whole* of what
amortisation can buy, and it is a constant.

Layer 1 is a task family of *dimension depth* ``d``: each level composes ``L``
objects from the level below. Two arms:

* **A, flat.** No nesting and no borrowing. A task at depth ``d`` touches
  ``L^d`` leaves and re-verifies every one: cost ``c_v * L^d``. This is
  exponential in the number of dimensions, which is the honest price of
  complexity when nothing is shared.
* **B, nested.** Layer 0 is compiled. Level ``j`` is then built by consulting
  its ``L`` children. If a child's compiled label is usable -- probability
  ``kappa`` -- the consult costs ``c_bar``; otherwise the child must be
  traversed, which costs whatever a depth ``j-1`` traversal costs. Hence

      m_0 = c_bar,      m_j = kappa*c_bar + (1-kappa)*L*m_{j-1},

  and ``m_d`` is the marginal price of one more task at depth ``d``.

``kappa = 1`` is pure re-labelling: a compiled child is one lookup, at any
depth. ``kappa = 0`` is the opposite: labels exist but buy nothing above layer 0,
so ``m_d = c_bar*L^d`` and nesting reproduces the flat shape with a constant
factor ``G`` -- arithmetic, exactly as the critique says.

The two theorems this file is built around
------------------------------------------
1. **The compound base.** Write ``u_d = m_d/c_bar``, so ``u_0 = 1`` and
   ``u_d = kappa + (1-kappa)L u_{d-1}``. The compound multiple against the flat
   arm is

       M(d, kappa) = c_v*L^d / m_d = G * L^d / u_d.

   For ``kappa > 0``, ``u_d`` grows no faster than ``beta^d`` with
   ``beta = (1-kappa)L``, so ``M`` grows like ``(L/beta)^d``. Therefore

       compound base = min( L, 1/(1-kappa) ),

   which is ``1`` at ``kappa = 0`` (no compounding at all), rises to ``L`` at
   ``kappa* = 1 - 1/L``, and saturates there. Compounding is not a property of
   compiling; it is a property of *how much of the child the parent's label
   makes redundant*.
2. **The marginal-cost knee.** ``m_d`` is bounded in ``d`` iff
   ``(1-kappa)L < 1``, i.e. ``kappa > kappa*``. At ``kappa*`` it grows linearly;
   below it, geometrically, and the flat exponential comes back in the limit
   ``kappa -> 0``. So ``kappa*`` is the point at which a system stops paying for
   depth and starts paying for breadth.

The budget axis
---------------
``B*`` is where layer 0 becomes compilable; it is not where the nested arm
starts winning, because the relabelling itself costs ``S`` per level and buys
nothing until the whole chain of ``d`` levels is mapped. That gives a second,
later threshold: the *recovery budget* ``B_rec`` at which the nested arm's
output overtakes the flat arm's. Between the two lies a real loss region --
the first one in this paper that is not degenerate, and it exists for a reason
the previous section could not have: the premium is paid *per level*, before any
level delivers anything.

What is simulated
-----------------
The only simulated quantity is ``m_d``: a task is walked down the ``L``-ary tree,
each consult using its label with probability ``kappa`` and recursing otherwise,
and the sample mean is compared with the recursion above. That is a check that
the process is the one the recursion integrates, not evidence about a physical
system. ``kappa`` is an *assumption* here and the report says so; what the
experiment establishes is what follows from it.

Run:
    python3 -m experiments.nested_solidification_experiment

Writes:
    experiments/nested_data.csv
    experiments/nested_summary.json
    experiments/nested_figure.png

No network. No commit. No push.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from typing import Dict, List, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments import critical_budget_experiment as CB  # noqa: E402

DATA_CSV = os.path.join(HERE, "nested_data.csv")
SUMMARY_JSON = os.path.join(HERE, "nested_summary.json")
FIGURE_PNG = os.path.join(HERE, "nested_figure.png")

# ---------------------------------------------------------------------------
# 1. parameters, each tied to something already in the paper
# ---------------------------------------------------------------------------

#: Verification of a leaf. Same unit of budget as every other experiment.
CV = CB.CV

#: Drift-adjusted replay of a compiled leaf: ``c_c + rho*(c_v-c_c) = 0.109``.
#: This is the lookup price, so the ``kappa = 0`` limit of this experiment is
#: the previous section's ``G_inf = 9.174`` and not a new constant.
LOOKUP = CB.c_bar()

#: Fixed premium per solidified level -- the layer-0 infrastructure of the
#: brief, charged again for each relabelling level. ``S = 5`` is the brief's
#: own number, kept rather than fitted, and it is what makes ``B*`` interior.
S_INFRA = 5.0

#: Branching factor: objects composed per level. Four is the paper's three
#: generations plus the leaf, and it is swept below so the laws are not a
#: property of the value.
BRANCH = 4

#: Dimension depths reported. Eight levels is enough for the two regimes to
#: separate by four orders of magnitude at the baseline prices.
DEPTHS = tuple(range(0, 9))

#: Reuse fractions. ``0`` and ``1`` are the degenerate endpoints; ``0.75`` is
#: ``1 - 1/L``, the knee; ``0.9`` is a plausible real compiler.
KAPPAS = (0.0, 0.5, 0.75, 0.9, 1.0)

#: Baseline: pure re-labelling, the assumption the architect asked to test.
KAPPA = 1.0

#: Monte Carlo draws. The estimator is a mean of a bounded cost, so this size
#: recovers the recursion to about a per cent at depth six.
DRAWS = 4000
SEED = 20261104

#: Budget window for the kink table and the figure.
B_MAX = 200.0
B_STEPS = 600


# ---------------------------------------------------------------------------
# 2. the analytic model
# ---------------------------------------------------------------------------


def critical_budget(S: float = S_INFRA) -> float:
    """``B*`` for layer 0, from the previous section's closed form.

    Imported rather than re-derived: if the two experiments ever disagreed
    about the price of solidifying the base, every number below would be
    wrong in a way no test would catch.
    """
    return CB.critical_budget(S=S, cd=0.0)


def granular_critical_budget(S: float = S_INFRA) -> float:
    """``B*`` for a whole-rung scheduler: ``c_v + S + k*c_c`` at the first ``k``.

    ``6.08`` at the baseline prices, against the fluid ``6.61``. This is the
    budget at which a real scheduler first *sees* the compiled layer ahead.
    """
    return CB.granular_threshold_form(S=S)


def flat_cost(depth: int, cv: float = CV, branch: int = BRANCH) -> float:
    """``c_v * L^d``: one task at depth ``d`` with nothing shared.

    Exponential in the number of dimensions. This is the arm the nested one has
    to beat, and it is also exactly what ``marginal_cost(d, 0)`` returns -- the
    two facts are the same fact.
    """
    _check_depth(depth)
    return cv * float(branch) ** depth


def marginal_cost(depth: int, kappa: float = KAPPA,
                  lookup: float = LOOKUP, branch: int = BRANCH) -> float:
    """``m_d``: the price of one more task at dimension depth ``d``.

    The recursion ``m_0 = lookup``, ``m_j = kappa*lookup + (1-kappa)*L*m_{j-1}``.
    At ``kappa = 1`` it is the constant ``lookup`` for every depth -- the slope
    mutation the brief asked to verify. At ``kappa = 0`` it is the flat
    exponential times ``lookup/c_v``, i.e. amortisation with no compounding.
    """
    _check_depth(depth)
    if not 0.0 <= kappa <= 1.0:
        raise ValueError("kappa must lie in [0, 1]")
    m = lookup
    for _ in range(depth):
        m = kappa * lookup + (1.0 - kappa) * branch * m
    return m


def ratio_sequence(depth: int, kappa: float = KAPPA,
                   lookup: float = LOOKUP, branch: int = BRANCH) -> List[float]:
    """``u_d = m_d/lookup`` for ``d = 0..depth``, for tables and fits."""
    _check_depth(depth)
    out = [1.0]
    for _ in range(depth):
        out.append(kappa + (1.0 - kappa) * branch * out[-1])
    return out


def reuse_knee(branch: int = BRANCH) -> float:
    """``kappa* = 1 - 1/L``: bounded marginal cost above it, growth below.

    ``0.75`` at ``L = 4``. The same expression as a critical drift rate in the
    previous section, with ``L`` in the place of a price ratio, and the same
    meaning: past it the cheap path stops being cheap.
    """
    return 1.0 - 1.0 / branch


def compound_base(kappa: float, branch: int = BRANCH) -> float:
    """``min(L, 1/(1-kappa))``: the base of the exponential in depth.

    ``1`` at ``kappa = 0`` -- the multiple is the constant ``G`` and never
    grows; ``L`` for ``kappa >= kappa*``; between, it interpolates. This is the
    quantity that separates "arithmetic" from "compounding", and it is a single
    scalar a reader can estimate for their own system.
    """
    if kappa == 1.0:
        return float(branch)
    if not 0.0 <= kappa < 1.0:
        raise ValueError("kappa must lie in [0, 1]")
    return min(float(branch), 1.0 / (1.0 - kappa))


def compound_multiple(depth: int, kappa: float = KAPPA,
                      cv: float = CV, lookup: float = LOOKUP,
                      branch: int = BRANCH) -> float:
    """``M(d) = c_v L^d / m_d``: how many flat tasks one nested task buys.

    Equals ``c_v/c_bar = 9.174`` at ``kappa = 0`` for every depth (arithmetic),
    and ``9.174 * L^d`` at ``kappa = 1`` (compounding). Everything in between
    follows ``G * (L/u_d^(1/d))^d`` with the base above.
    """
    return flat_cost(depth, cv, branch) / marginal_cost(depth, kappa, lookup,
                                                        branch)


def one_time_cost(depth: int, kappa: float = KAPPA, S: float = S_INFRA,
                  lookup: float = LOOKUP, branch: int = BRANCH) -> float:
    """Budget spent before the first nested task exists.

    ``B*`` for layer 0, then ``S + L*m_{j-1}`` for each of the ``d`` levels:
    consult the ``L`` children and store the composed label. At ``kappa = 1``
    this is ``B* + d*(S + L*c_bar)`` -- linear in depth, which is the whole
    point: the *build* is linear while the flat *task* is exponential.
    """
    _check_depth(depth)
    total = critical_budget(S)
    m = lookup
    for _ in range(depth):
        total += S + branch * m
        m = kappa * lookup + (1.0 - kappa) * branch * m
    return total


def tasks_flat(budget: float, depth: int, cv: float = CV,
               branch: int = BRANCH) -> float:
    """Fractional top-level tasks completed by the flat arm."""
    if budget <= 0.0:
        return 0.0
    return budget / flat_cost(depth, cv, branch)


def tasks_nested(budget: float, depth: int, kappa: float = KAPPA,
                 S: float = S_INFRA, lookup: float = LOOKUP,
                 branch: int = BRANCH) -> float:
    """Fractional top-level tasks completed by the nested arm.

    Below ``B*`` nothing can be compiled, so the arm is the flat arm. Between
    ``B*`` and the one-time cost the premium is being paid and no task is
    finished -- the loss region. Above it, each task costs ``m_d``.
    """
    if budget <= 0.0:
        return 0.0
    if budget < critical_budget(S):
        return tasks_flat(budget, depth)
    spend = one_time_cost(depth, kappa, S, lookup, branch)
    if budget <= spend:
        return 0.0
    return (budget - spend) / marginal_cost(depth, kappa, lookup, branch)


def efficiency(budget: float, depth: int, kappa: float = KAPPA,
               S: float = S_INFRA) -> float:
    """``N_nested/N_flat``: zero inside the loss region, tending to ``M(d)``."""
    flat = tasks_flat(budget, depth)
    if flat <= 0.0:
        return 1.0
    return tasks_nested(budget, depth, kappa, S) / flat


def recovery_budget(depth: int, kappa: float = KAPPA, S: float = S_INFRA,
                    lookup: float = LOOKUP, branch: int = BRANCH) -> float:
    """``B`` at which the nested arm catches the flat arm: ``T/(1 - m_d/c_A)``.

    The root of ``N_nested(B) = N_flat(B)`` with ``T`` the one-time cost. It
    sits just above ``T`` because ``m_d/c_A`` is tiny whenever nesting is worth
    doing at all -- which is the honest reading of the loss region: the arm is
    behind for a *budget* interval but never by more than a fraction of one
    flat task.
    """
    m = marginal_cost(depth, kappa, lookup, branch)
    c_a = flat_cost(depth)
    if m >= c_a:
        return math.inf
    return one_time_cost(depth, kappa, S, lookup, branch) / (1.0 - m / c_a)


def marginal_growth_base(depth: int, kappa: float = KAPPA,
                         branch: int = BRANCH) -> float:
    """``m_d/m_{d-1}`` at the requested depth: ``1`` above the knee."""
    _check_depth(depth)
    if depth == 0:
        return 1.0
    return (marginal_cost(depth, kappa, 1.0, branch)
            / marginal_cost(depth - 1, kappa, 1.0, branch))


def measured_base(kappa: float, depth: int = 8, window: int = 3,
                  lookup: float = LOOKUP, branch: int = BRANCH) -> float:
    """``(M(d)/M(d-window))^{1/window}``: the base, read off the multiples.

    A ratio of two multiples, so the constant ``G`` cancels and what is left is
    the growth in the dimension. This is the estimator the figure and the check
    both use; computing it from the same function keeps them from disagreeing.
    At the knee it sits below ``L`` because ``u_d = 1 + kappa*d`` there, so the
    base is ``L*(u_{d-w}/u_d)^{1/w}`` --- a finite-window effect that closes as
    ``d -> infinity`` and is not a fitted offset.
    """
    top = compound_multiple(depth, kappa, CV, lookup, branch)
    low = compound_multiple(depth - window, kappa, CV, lookup, branch)
    return (top / low) ** (1.0 / float(window))


def _check_depth(depth: int) -> None:
    if not isinstance(depth, int) or depth < 0:
        raise ValueError("depth must be a non-negative integer")


# ---------------------------------------------------------------------------
# 3. the simulation: walk the tree, use labels when they are usable
# ---------------------------------------------------------------------------


def simulate_task_cost(depth: int, kappa: float = KAPPA,
                       lookup: float = LOOKUP, branch: int = BRANCH,
                       draws: int = DRAWS, seed: int = SEED
                       ) -> Tuple[float, float]:
    """Sample mean and standard error of one nested task's price.

    A consult uses the child's compiled label with probability ``kappa``
    (cost ``lookup``) and otherwise traverses the child, recursively. The
    recursion's expectation is ``m_d``; the estimator is a check that the walk
    is the process the algebra integrates, and its standard error is the honest
    statement of how well the figure agrees.
    """
    import numpy as np

    rng = np.random.default_rng(seed + 17 * depth)

    def walk(level: int) -> float:
        if level == 0:
            return lookup
        # The node's own label is usable with probability kappa: consulting it
        # is one lookup, at any depth. Otherwise the L children must be
        # traversed. This is the per-node decision the recursion integrates;
        # a per-child decision would multiply the lookup term by L and would
        # not be "c_top = lookup(L0)".
        if rng.random() < kappa:
            return lookup
        return sum(walk(level - 1) for _ in range(branch))

    values = np.array([walk(depth) for _ in range(draws)], dtype=float)
    return float(values.mean()), float(values.std(ddof=1) / math.sqrt(draws))


# ---------------------------------------------------------------------------
# 4. the sweeps
# ---------------------------------------------------------------------------


def sweep_depth() -> List[Dict[str, float]]:
    """Marginal cost and compound multiple against depth, for each ``kappa``."""
    rows: List[Dict[str, float]] = []
    for kappa in KAPPAS:
        for depth in DEPTHS:
            m = marginal_cost(depth, kappa)
            row: Dict[str, float] = {
                "kind": "depth",
                "kappa": kappa,
                "depth": depth,
                "marginal": m,
                "flat": flat_cost(depth),
                "multiple": flat_cost(depth) / m,
                "growth": marginal_growth_base(depth, kappa),
                "one_time": one_time_cost(depth, kappa),
                "recovery": recovery_budget(depth, kappa),
            }
            if kappa in (0.5, 0.75, 0.9, 1.0) and depth <= 6:
                mean, se = simulate_task_cost(depth, kappa)
                row["measured"] = mean
                row["stderr"] = se
            rows.append(row)
    return rows


def sweep_budget(depth: int = 6, kappa: float = KAPPA) -> List[Dict[str, float]]:
    """The kink: both arms against budget, with marginals and averages."""
    rows: List[Dict[str, float]] = []
    c_a = flat_cost(depth)
    m = marginal_cost(depth, kappa)
    for index in range(B_STEPS + 1):
        budget = B_MAX * index / B_STEPS
        n_a = tasks_flat(budget, depth)
        n_b = tasks_nested(budget, depth, kappa)
        rows.append({
            "kind": "budget",
            "kappa": kappa,
            "depth": depth,
            "budget": budget,
            "tasks_flat": n_a,
            "tasks_nested": n_b,
            "efficiency": (n_b / n_a) if n_a > 0.0 else 1.0,
            "avg_flat": c_a,
            "avg_nested": (budget / n_b) if n_b > 0.0 else math.inf,
            "marginal_flat": c_a,
            "marginal_nested": m,
        })
    return rows


def sweep_knee(depth: int = 8) -> List[Dict[str, float]]:
    """The compound base against ``kappa``: the knee at ``1 - 1/L``."""
    rows: List[Dict[str, float]] = []
    for index in range(101):
        kappa = index / 100.0
        rows.append({
            "kind": "knee",
            "depth": depth,
            "kappa": kappa,
            "base_predicted": compound_base(kappa),
            "base_measured": measured_base(kappa, depth),
            "multiple": compound_multiple(depth, kappa),
        })
    return rows


def sweep_branch(branch: int) -> List[Dict[str, float]]:
    """The knee and the base at another branching factor, to show they move."""
    rows = []
    for kappa in (0.0, 0.5, 0.75, 0.8, 0.9, 0.95, 1.0):
        rows.append({
            "kind": "branch",
            "branch": branch,
            "kappa": kappa,
            "knee": 1.0 - 1.0 / branch,
            "base": compound_base(kappa, branch),
            "multiple_at_eight": compound_multiple(8, kappa, branch=branch),
        })
    return rows


# ---------------------------------------------------------------------------
# 5. falsification checks
# ---------------------------------------------------------------------------


def _geometric_fit(values: Sequence[float]) -> float:
    """Base of an exponential fit to the tail of a positive sequence."""
    tail = [v for v in values[-4:] if v > 0.0]
    if len(tail) < 2:
        return 1.0
    return (tail[-1] / tail[0]) ** (1.0 / (len(tail) - 1))


def checks(rows_depth: Sequence[Dict[str, float]]) -> List[Dict[str, object]]:
    """Seven predictions, each with the measurement that could break it."""
    out: List[Dict[str, object]] = []
    by = {(r["kappa"], r["depth"]): r for r in rows_depth}

    # P1 -- kappa = 0 reproduces the flat arm up to the constant G.
    worst = 0.0
    for depth in DEPTHS:
        r = by[(0.0, depth)]
        worst = max(worst, abs(r["multiple"] - CB.gain_infinite())
                    / CB.gain_infinite())
    out.append({
        "id": "P1",
        "claim": "kappa=0 gives M(d) = G_inf for every d: amortisation is arithmetic",
        "predicted": CB.gain_infinite(),
        "measured": by[(0.0, 8)]["multiple"],
        "worst_relative_error": worst,
        "tolerance": 1e-9,
        "verdict": "pass" if worst <= 1e-9 else "FAIL",
    })

    # P2 -- kappa = 1 gives a marginal cost that does not move with depth.
    values = [by[(1.0, d)]["marginal"] for d in DEPTHS]
    spread = (max(values) - min(values)) / values[0]
    out.append({
        "id": "P2",
        "claim": "kappa=1: marginal cost is constant in depth at c_bar",
        "predicted": LOOKUP,
        "measured": values[-1],
        "relative_spread": spread,
        "tolerance": 1e-12,
        "verdict": "pass" if spread <= 1e-12 else "FAIL",
    })

    # P3 -- the knee: above it the marginal is bounded, below it geometric.
    knee = reuse_knee()
    bounded = [by[(0.9, d)]["marginal"] for d in DEPTHS]
    bounded_drift = abs(bounded[-1] - bounded[-2]) / bounded[-1]
    growth = by[(0.5, 8)]["growth"]
    predicted_growth = (1.0 - 0.5) * BRANCH
    out.append({
        "id": "P3",
        "claim": "knee at kappa*=1-1/L: bounded above, growth (1-kappa)L below",
        "knee": knee,
        "predicted": predicted_growth,
        "measured": growth,
        "bounded_drift_at_ninety": bounded_drift,
        "tolerance": 0.01,
        "verdict": "pass" if (abs(growth - predicted_growth) <= 0.01
                              and bounded_drift <= 0.01) else "FAIL",
    })

    # P4 -- the simulated walk is the process the recursion integrates.
    worst_z = 0.0
    worst_gap = 0.0
    pairs = 0
    for (kappa, depth), row in sorted(by.items()):
        if "measured" not in row:
            continue
        pairs += 1
        gap = abs(row["measured"] - row["marginal"])
        worst_gap = max(worst_gap, gap)
        if row["stderr"] > 1e-12:
            worst_z = max(worst_z, gap / row["stderr"])
        else:
            worst_z = max(worst_z, gap / max(row["marginal"], 1e-12) / 1e-3)
    out.append({
        "id": "P4",
        "claim": "Monte Carlo task price matches m_d within four standard errors",
        "predicted": 0.0,
        "measured": worst_z,
        "worst_absolute_gap": worst_gap,
        "pairs": pairs,
        "tolerance": 4.0,
        "verdict": "pass" if worst_z <= 4.0 else "FAIL",
    })

    # P5 -- the compound base is min(L, 1/(1-kappa)) away from the knee.
    #     At the knee the approach to L carries a 1/(1+kappa*d) factor, so the
    #     finite-depth base sits *below* L; that is reported, not fitted away.
    errors = []
    for kappa in (0.0, 0.5, 0.9, 1.0):
        measured = measured_base(kappa)
        predicted = compound_base(kappa)
        errors.append((kappa, measured, predicted,
                       abs(measured - predicted) / predicted))
    knee_measured = measured_base(0.75)
    worst = max(e[3] for e in errors)
    out.append({
        "id": "P5",
        "claim": "compound base = min(L, 1/(1-kappa)) away from the knee",
        "predicted": compound_base(0.9),
        "measured": next(e[1] for e in errors if e[0] == 0.9),
        "worst_relative_error": worst,
        "base_at_the_knee": knee_measured,
        "table": errors,
        "tolerance": 0.02,
        "verdict": "pass" if worst <= 0.02 else "FAIL",
    })

    # P6 -- the loss region exists, and the recovery budget is its right edge.
    rows_b = sweep_budget(6, 1.0)
    loss = [r["budget"] for r in rows_b
            if r["budget"] >= critical_budget() and r["efficiency"] <= 0.0]
    recovery = recovery_budget(6, 1.0)
    at_recovery = efficiency(recovery, 6, 1.0)
    first_above = next((r["budget"] for r in rows_b
                        if r["budget"] > critical_budget()
                        and r["efficiency"] >= 1.0), math.inf)
    crosses = (abs(at_recovery - 1.0) <= 1e-9
               and math.isfinite(first_above)
               and abs(first_above - recovery) <= 2.0 * B_MAX / B_STEPS)
    out.append({
        "id": "P6",
        "claim": "a loss region exists between B* and B_rec, and B_rec is its edge",
        "predicted": recovery,
        "measured": max(loss) if loss else 0.0,
        "loss_exists": bool(loss),
        "efficiency_at_recovery": at_recovery,
        "first_crossing": first_above,
        "crosses_at_recovery": crosses,
        "tolerance": B_MAX / B_STEPS,
        "verdict": "pass" if (loss and crosses) else "FAIL",
    })

    # P7 -- below B* the arms coincide: the premium has not been paid yet.
    worst = 0.0
    for r in rows_b:
        if r["budget"] < critical_budget():
            worst = max(worst, abs(r["tasks_nested"] - r["tasks_flat"])
                        / max(r["tasks_flat"], 1e-12))
    out.append({
        "id": "P7",
        "claim": "below B* the nested arm is the flat arm exactly; no free lunch",
        "predicted": 0.0,
        "measured": worst,
        "tolerance": 1e-12,
        "verdict": "pass" if worst <= 1e-12 else "FAIL",
    })
    return out


# ---------------------------------------------------------------------------
# 6. rendering
# ---------------------------------------------------------------------------

INK = "#1f2d3d"
ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"
PALE = "#c9cdd3"
GREEN = "#3f7d5a"
GOLD = "#a5762a"


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


def render(rows_depth: Sequence[Dict[str, float]],
           rows_budget: Sequence[Dict[str, float]],
           rows_knee: Sequence[Dict[str, float]],
           path: str = FIGURE_PNG) -> str:
    _style()
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.9),
                             constrained_layout=True)
    colours = {0.0: ACCENT, 0.5: GOLD, 0.75: MUTED, 0.9: GREEN, 1.0: COOL}

    # (a) marginal cost against depth: the slope mutation --------------------
    ax = axes[0][0]
    for kappa in KAPPAS:
        ys = [marginal_cost(d, kappa) for d in DEPTHS]
        ax.semilogy(DEPTHS, ys, color=colours[kappa], marker="o",
                    markersize=2.4, linewidth=1.4,
                    label="$\\kappa={:g}$".format(kappa))
    ax.semilogy(DEPTHS, [flat_cost(d) for d in DEPTHS], color=INK,
                linewidth=1.0, linestyle=":", label="flat $c_vL^d$")
    ax.set_xlabel("dimension depth $d$")
    ax.set_ylabel("marginal cost per task")
    ax.set_title("(a) the slope mutation", loc="left")
    ax.legend(frameon=False, loc="lower right", handlelength=1.2,
              borderaxespad=0.3, fontsize=6.0)
    ax.grid(True, which="both")

    # (b) compound multiple against depth -----------------------------------
    ax = axes[0][1]
    for kappa in KAPPAS:
        ys = [compound_multiple(d, kappa) for d in DEPTHS]
        ax.semilogy(DEPTHS, ys, color=colours[kappa], marker="o",
                    markersize=2.4, linewidth=1.4,
                    label="$\\kappa={:g}$".format(kappa))
    ax.set_xlabel("dimension depth $d$")
    ax.set_ylabel("$M(d)$: flat tasks per nested task")
    ax.set_title("(b) compounding in the dimension", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.2,
              borderaxespad=0.3, fontsize=6.0)
    ax.grid(True, which="both")

    # (c) average cost per task against budget: the kink --------------------
    ax = axes[0][2]
    ax.axvspan(critical_budget(), recovery_budget(6), color=PALE, alpha=0.45,
               linewidth=0, label="loss region")
    budgets = [r["budget"] for r in rows_budget if r["budget"] > 0.0]
    ax.semilogy(budgets, [flat_cost(6) for _ in budgets], color=ACCENT,
                linewidth=1.6, label="A flat")
    live = [r for r in rows_budget if r["tasks_nested"] > 0.0]
    ax.semilogy([r["budget"] for r in live],
                [r["avg_nested"] for r in live],
                color=COOL, linewidth=1.6, label="B nested")
    for value, label in ((critical_budget(), "$B^*$"),
                         (recovery_budget(6), "$B_{rec}$")):
        ax.axvline(value, color=MUTED, linewidth=0.9, zorder=3)
        ax.annotate(label, xy=(value, 3e5), fontsize=6.0, color=INK,
                    xytext=(2, 0), textcoords="offset points")
    ax.set_xlim(0, B_MAX)
    ax.set_xlabel("budget $B$")
    ax.set_ylabel("average cost per task")
    ax.set_title("(c) the kink, and the loss region", loc="left")
    ax.legend(frameon=False, loc="upper right", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True, which="both")

    # (d) tasks completed against budget ------------------------------------
    ax = axes[1][0]
    ax.semilogy([r["budget"] for r in rows_budget],
                [max(r["tasks_flat"], 1e-4) for r in rows_budget],
                color=ACCENT, linewidth=1.5, label="A flat")
    live = [r for r in rows_budget if r["tasks_nested"] > 0.0]
    ax.semilogy([r["budget"] for r in live],
                [r["tasks_nested"] for r in live],
                color=COOL, linewidth=1.7, label="B nested")
    recovery = recovery_budget(6)
    ax.plot([recovery, recovery],
            [1e-4, max(r["tasks_nested"] for r in live)],
            color=COOL, linewidth=0.9, linestyle=":")
    ax.annotate("$B^*$", xy=(critical_budget(), 3.0), fontsize=6.2,
                color=MUTED, ha="right", va="bottom")
    ax.annotate("$B_{rec}$", xy=(recovery, 3.0), fontsize=6.2,
                color=MUTED, ha="left", va="bottom")
    ax.set_xlim(0, B_MAX)
    ax.set_xlabel("budget $B$")
    ax.set_ylabel("top-level tasks completed")
    ax.set_title("(d) the same kink, four decades apart", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True, which="both")

    # (e) the two thresholds against depth ----------------------------------
    ax = axes[1][1]
    ax.semilogy(DEPTHS, [recovery_budget(d) for d in DEPTHS], color=GOLD,
                marker="s", markersize=2.4, linewidth=1.4,
                label="$B_{rec}$")
    ax.semilogy(DEPTHS, [one_time_cost(d) for d in DEPTHS], color=COOL,
                marker="o", markersize=2.4, linewidth=1.4,
                label="one-time $T(d)$")
    ax.axhline(critical_budget(), color=ACCENT, linewidth=1.2,
               label="$B^*$")
    ax.set_xlabel("dimension depth $d$")
    ax.set_ylabel("budget")
    ax.set_title("(e) both thresholds are linear in $d$", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.0)
    ax.grid(True, which="both")

    # (f) the compound base against kappa -----------------------------------
    ax = axes[1][2]
    xs = [r["kappa"] for r in rows_knee]
    ax.semilogy(xs, [r["base_predicted"] for r in rows_knee], color=COOL,
                linewidth=1.6, label="$\\min(L,1/(1-\\kappa))$")
    ax.semilogy(xs, [r["base_measured"] for r in rows_knee],
                color=ACCENT, linewidth=1.0, linestyle="--",
                label="measured, $(M_d/M_{d-3})^{1/3}$")
    ax.axvline(reuse_knee(), color=PALE, linewidth=1.0, zorder=0)
    ax.annotate("$\\kappa^*=1-1/L$", xy=(reuse_knee(), 1.05), fontsize=6.0,
                color=MUTED, ha="right", va="bottom")
    ax.set_xlabel("reuse fraction $\\kappa$")
    ax.set_ylabel("base of the exponential")
    ax.set_title("(f) what compounding actually needs", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.0)
    ax.grid(True, which="both")

    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 7. the report
# ---------------------------------------------------------------------------


def _num(value: object) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isinf(number):
        return "inf"
    if number == 0:
        return "0"
    if abs(number) >= 1000 or abs(number) < 1e-3:
        return "{:.3e}".format(number)
    return "{:.4f}".format(number)


def report(rows_depth: Sequence[Dict[str, float]],
           rows_budget: Sequence[Dict[str, float]],
           results: Sequence[Dict[str, object]]) -> None:
    line = "=" * 78
    print(line)
    print("NESTED SOLIDIFICATION: does a compiled layer make the next one cheap?")
    print(line)
    print("c_v {:.2f}   lookup c_bar {:.4f}   G {:.4f}   S {:.2f}   "
          "L {}   B* {:.4f} (fluid) / {:.2f} (whole rungs)".format(
              CV, LOOKUP, CB.gain_infinite(), S_INFRA, BRANCH,
              critical_budget(), granular_critical_budget()))
    print()

    print("-" * 78)
    print("1. THE PRICE OF A TASK, AND WHAT NESTING DOES TO IT")
    print("-" * 78)
    print("  flat (no borrowing)      c_A(d) = c_v * L^d")
    print("  nested                   m_0 = c_bar")
    print("                           m_j = kappa*c_bar + (1-kappa)*L*m_{j-1}")
    print()
    print("      d     c_A(d)       m_d (k=0)     m_d (k=.75)    m_d (k=.9)"
          "     m_d (k=1)")
    for depth in DEPTHS:
        print("  {:4d}   {:10.3f}   {:12.3f}   {:12.3f}   {:12.3f}   {:12.3f}"
              .format(depth, flat_cost(depth),
                      marginal_cost(depth, 0.0),
                      marginal_cost(depth, 0.75),
                      marginal_cost(depth, 0.9),
                      marginal_cost(depth, 1.0)))
    print()
    print("  The kappa = 1 column does not move: the price of a task stops")
    print("  depending on how many dimensions it spans. That is the mutation,")
    print("  and it is a property of the *dimension* axis, not of the budget")
    print("  axis -- on the budget axis the same fact appears as a kink.")
    print()

    print("-" * 78)
    print("2. THE MUTATION AT B*: BOTH ARMS ACROSS THE THRESHOLD")
    print("-" * 78)
    print("      B     tasks A     tasks B      eff B/A    avg A      avg B"
          "     marg A   marg B")
    for budget in (4.0, 5.0, 6.0, critical_budget(), 6.7, 7.0, 10.0, 20.0,
                   30.0, 40.0, 60.0, 100.0, 200.0):
        n_a = tasks_flat(budget, 6)
        n_b = tasks_nested(budget, 6)
        eff = (n_b / n_a) if n_a > 0 else 1.0
        avg_b = (budget / n_b) if n_b > 0 else math.inf
        print("  {:6.2f}   {:9.6f}   {:10.6f}   {:9.4f}   {:9.1f}   {:>9}"
              "   {:8.4f}  {:8.4f}".format(
                  budget, n_a, n_b, eff, flat_cost(6), _num(avg_b),
                  flat_cost(6), marginal_cost(6)))
    print()
    print("  Kink one, at B* = {:.4f} (fluid) / {:.2f} (whole rungs): layer 0".format(
        critical_budget(), granular_critical_budget()))
    print("  becomes compilable. Between B* and the one-time cost")
    print("  T(d=6) = {:.4f} the arm pays the premium for all six levels and".format(
        one_time_cost(6)))
    print("  finishes nothing: efficiency is exactly 0, which is the first")
    print("  non-degenerate loss region in this paper.")
    print("  Kink two, at B_rec = {:.4f}: the nested arm catches the flat arm".format(
        recovery_budget(6)))
    print("  and then diverges away from it, approaching {:.0f}x.".format(
        compound_multiple(6)))
    print("  Marginal cost is a step: {:.4f} -> {:.4f} at B*, a factor {:.0f},".format(
        flat_cost(6), marginal_cost(6), compound_multiple(6)))
    print("  and it is the *dimension* count that the step stops depending on.")
    print()

    print("-" * 78)
    print("3. THE COMPOUND BASE: min(L, 1/(1-kappa))")
    print("-" * 78)
    print("   kappa   base predicted   base measured   M(8)         marginal at d=8")
    for kappa in (0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0):
        print("  {:6.2f}   {:14.3f}   {:13.3f}   {:12.4g}   {:12.4f}".format(
            kappa, compound_base(kappa), measured_base(kappa),
            compound_multiple(8, kappa), marginal_cost(8, kappa)))
    print()
    print("  At kappa = 0 the multiple is G = {:.4f} at every depth: the".format(
        CB.gain_infinite()))
    print("  amortisation ratio, a constant, exactly the architect's objection")
    print("  to calling that a mechanism. At kappa = 1 it is G*L^d and the")
    print("  base is L = {}. The knee is kappa* = 1-1/L = {:.2f}; above it".format(
        BRANCH, reuse_knee()))
    print("  the marginal cost is bounded and the base saturates at L. There is")
    print("  no compounding without reuse, and its ceiling is the branching")
    print("  factor -- not the compiler, not the budget.")
    print()

    print("-" * 78)
    print("4. THE FORMULAS, IN ONE PLACE")
    print("-" * 78)
    print("  a compiled child is consulted, not re-proved:")
    print()
    print("      c_top = lookup(L0) = c_bar,        not c_v(L1)")
    print()
    print("  the task price, the multiple, and the base:")
    print()
    print("      m_d     = kappa*c_bar + (1-kappa)*L*m_{d-1},   m_0 = c_bar")
    print("      M(d)    = c_v*L^d / m_d")
    print("      base    = min(L, 1/(1-kappa))")
    print("      B*      = c_v*[p(c_v-c_c)+S] / [p(c_v-c_c)]   (layer 0)")
    print("      T(d)    = B* + sum_{j=1..d} (S + L*m_{j-1})")
    print("      B_rec   = T(d) / (1 - m_d/(c_v*L^d))")
    print()
    print("  The first line is the whole mechanism: the top layer's price is a")
    print("  *lookup* into the compiled bottom, and the composition of lookups")
    print("  is itself a lookup. Everything else is bookkeeping -- which is the")
    print("  point: once that line holds, the exponential is bookkeeping too.")
    print()

    print("-" * 78)
    print("5. THE PREDICTIONS, CHECKED")
    print("-" * 78)
    for result in results:
        status = "PASS" if result["verdict"] == "pass" else "FAIL"
        print("  [{}] {}  {}".format(status, result["id"], result["claim"]))
        print("        predicted {:>12}   measured {:>12}".format(
            _num(result.get("predicted")), _num(result.get("measured"))))
        for key in ("worst_relative_error", "relative_spread", "knee",
                    "bounded_drift_at_ninety", "loss_exists",
                    "crosses_at_recovery"):
            if key in result:
                print("        {:26s} {}".format(key, _num(result[key])))
    print()

    print("-" * 78)
    print("WHAT THIS DOES NOT SHOW")
    print("-" * 78)
    print("  1. It does not show that any real system has kappa = 1. kappa is")
    print("     an assumption -- the fraction of a child's verification that its")
    print("     parent's label makes redundant -- and the experiment's job is to")
    print("     say what follows from it, including that kappa = 0 gives back")
    print("     arithmetic and kappa = 1 gives back the exponential.")
    print("  2. 'Exponential' here is in the number of dimensions, at fixed")
    print("     prices. It is not exponential in time, and it is not free: the")
    print("     one-time cost is linear in d, so a system that never runs enough")
    print("     tasks loses to the flat arm -- that is the loss region.")
    print("  3. Nothing here rescues reach from the previous section's horizon.")
    print("     If a top-level task's value saturates in d the way reach")
    print("     saturates in depth, the multiple in value is capped even though")
    print("     the multiple in cost is not. Cost compounding is not value.")
    print("  4. B* is imported from the critical-budget experiment, not")
    print("     re-measured. If that model is wrong, this one inherits it.")
    print()


# ---------------------------------------------------------------------------
# 8. artifacts
# ---------------------------------------------------------------------------


def write_data(rows_depth: Sequence[Dict[str, float]],
               rows_budget: Sequence[Dict[str, float]],
               rows_knee: Sequence[Dict[str, float]],
               rows_branch: Sequence[Dict[str, float]]) -> str:
    fields = ["kind", "kappa", "depth", "budget", "marginal", "flat",
              "multiple", "growth", "one_time", "recovery", "measured",
              "stderr", "tasks_flat", "tasks_nested", "efficiency",
              "avg_flat", "avg_nested", "marginal_flat", "marginal_nested",
              "base_predicted", "branch", "knee", "base",
              "multiple_at_eight"]
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows_depth:
            writer.writerow(row)
        for row in rows_budget:
            writer.writerow(row)
        for row in rows_knee:
            writer.writerow(row)
        for row in rows_branch:
            writer.writerow(row)
    return DATA_CSV


def write_paper_artifacts() -> List[str]:
    """Macros, the loss-region table and the figure, for Section IV-H.

    The table is the one the subsection's argument turns on: budgets chosen to
    straddle both thresholds, so a reader can see the arm go to zero at $B^{*}$
    and come back at $B_{\\text{rec}}$ four decades to the right. Every number
    is recomputed here from the same functions the report prints, so the table
    cannot drift from the text.
    """
    paper_data = os.path.join(HERE, "paper", "data")
    paper_figures = os.path.join(HERE, "paper", "figures")
    os.makedirs(paper_data, exist_ok=True)
    os.makedirs(paper_figures, exist_ok=True)

    b_star = critical_budget()
    spend = one_time_cost(6)
    recovery = recovery_budget(6)
    flat_six = flat_cost(6)
    nested_six = marginal_cost(6)
    multiple_six = compound_multiple(6)
    power = int(math.floor(math.log10(multiple_six)))
    mantissa = multiple_six / (10.0 ** power)
    eff_far = efficiency(200.0, 6)

    macro = os.path.join(paper_data, "nested_macros.tex")
    with open(macro, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/nested_solidification_experiment.py"
                     " -- do not edit\n")
        # Names end in words, never digits: TeX would read \nestedDepth2 as
        # \nestedDepth followed by "2".
        handle.write(r"\newcommand{\nestedLookup}{%.3f}" % LOOKUP + "\n")
        handle.write(r"\newcommand{\nestedGain}{%.3f}" % CB.gain_infinite() + "\n")
        handle.write(r"\newcommand{\nestedBranch}{%d}" % BRANCH + "\n")
        handle.write(r"\newcommand{\nestedKnee}{%.2f}" % reuse_knee() + "\n")
        handle.write(r"\newcommand{\nestedPremium}{%.0f}" % S_INFRA + "\n")
        handle.write(r"\newcommand{\nestedBstar}{%.2f}" % b_star + "\n")
        handle.write(r"\newcommand{\nestedBstarGranular}{%.2f}"
                     % granular_critical_budget() + "\n")
        handle.write(r"\newcommand{\nestedBuild}{%.2f}" % spend + "\n")
        handle.write(r"\newcommand{\nestedBrec}{%.2f}" % recovery + "\n")
        handle.write(r"\newcommand{\nestedFlatCost}{%.0f}" % flat_six + "\n")
        handle.write(r"\newcommand{\nestedMarginalNested}{%.3f}"
                     % nested_six + "\n")
        handle.write(r"\newcommand{\nestedMultipleMantissa}{%.2f}" % mantissa + "\n")
        handle.write(r"\newcommand{\nestedMultiplePower}{%d}" % power + "\n")
        handle.write(r"\newcommand{\nestedEffFar}{%.0f}" % eff_far + "\n")
        handle.write(r"\newcommand{\nestedBaseHalf}{%.1f}"
                     % compound_base(0.5) + "\n")
        handle.write(r"\newcommand{\nestedBaseNinety}{%.2f}"
                     % compound_base(0.9) + "\n")
        handle.write(r"\newcommand{\nestedMultipleDeep}{%.2f}"
                     % (compound_multiple(8) / 1.0e5) + "\n")
        handle.write(r"\newcommand{\nestedMultipleDeepExp}{5}" + "\n")

    table = os.path.join(paper_data, "nested_table.tex")
    with open(table, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/nested_solidification_experiment.py"
                     " -- do not edit\n")
        handle.write(r"\begin{tabular}{@{}rrrrr@{}}" + "\n")
        handle.write(r"\toprule" + "\n")
        handle.write(r"$B$ & $N_{\text{flat}}$ & $N_{\text{nested}}$ & "
                    r"$N_{\text{nested}}/N_{\text{flat}}$ & regime \\" + "\n")
        handle.write(r"\midrule" + "\n")
        rows = (
            (5.0, "flat"),
            (b_star, "$B^{*}$: investing"),
            (20.0, "no output"),
            (spend - 0.5, "no output"),
            (recovery, "$B_{\\text{rec}}$: break-even"),
            (40.0, "compounding"),
            (60.0, "compounding"),
            (100.0, "compounding"),
            (200.0, "compounding"),
        )
        for budget, label in rows:
            n_a = tasks_flat(budget, 6)
            n_b = tasks_nested(budget, 6)
            eff = (n_b / n_a) if n_a > 0.0 else 0.0
            eff_cell = "--" if eff <= 0.0 else "{:.0f}".format(eff)
            handle.write("{} & {} & {} & {} & {} \\\\\n".format(
                "{:.2f}".format(budget),
                "{:.5f}".format(n_a),
                "{:.4f}".format(n_b),
                eff_cell,
                label))
        handle.write(r"\bottomrule" + "\n")
        handle.write(r"\end{tabular}" + "\n")

    figure = os.path.join(paper_figures, "nested.png")
    render(sweep_depth(), sweep_budget(6, KAPPA), sweep_knee(8), path=figure)
    return [macro, table, figure]


def write_summary(rows_depth: Sequence[Dict[str, float]],
                  rows_budget: Sequence[Dict[str, float]],
                  results: Sequence[Dict[str, object]]) -> str:
    payload = {
        "parameters": {
            "c_v": CV, "lookup": LOOKUP, "S": S_INFRA, "branch": BRANCH,
            "kappa": KAPPA, "draws": DRAWS, "seed": SEED,
            "gain": CB.gain_infinite(), "knee": reuse_knee(),
            "B_star_fluid": critical_budget(),
            "B_star_granular": granular_critical_budget(),
        },
        "thresholds": {
            "B_star": critical_budget(),
            "B_star_granular": granular_critical_budget(),
            "one_time_at_six": one_time_cost(6),
            "recovery_at_six": recovery_budget(6),
            "multiple_at_six": compound_multiple(6),
            "multiple_at_eight": compound_multiple(8),
            "marginal_flat_at_six": flat_cost(6),
            "marginal_nested_at_six": marginal_cost(6),
        },
        "depth": list(rows_depth),
        "budget": list(rows_budget),
        "checks": list(results),
        "all_pass": all(r["verdict"] == "pass" for r in results),
    }
    with open(SUMMARY_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return SUMMARY_JSON


# ---------------------------------------------------------------------------
# 9. entry point
# ---------------------------------------------------------------------------


def main() -> int:
    rows_depth = sweep_depth()
    rows_budget = sweep_budget(6, 1.0)
    rows_knee = sweep_knee(8)
    rows_branch = sweep_branch(4) + sweep_branch(3) + sweep_branch(8)
    results = checks(rows_depth)

    render(rows_depth, rows_budget, rows_knee)
    report(rows_depth, rows_budget, results)

    written = [FIGURE_PNG]
    written.append(write_data(rows_depth, rows_budget, rows_knee, rows_branch))
    written.append(write_summary(rows_depth, rows_budget, results))
    written += write_paper_artifacts()

    print("=" * 78)
    print("WROTE")
    print("=" * 78)
    for path in written:
        print("  " + os.path.relpath(path, ROOT))
    print()
    print("Reproduce with:  python3 -m experiments.nested_solidification_experiment")
    print("No network access. No commit. No push.")
    return 0 if all(r["verdict"] == "pass" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
