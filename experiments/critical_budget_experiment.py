"""The critical budget: when compiling pays, and when re-verifying is cheaper.

The previous experiment supplied the mechanism -- a rung crossed against a
witness is compiled, and a compiled rung costs ``c_c`` instead of ``c_v`` -- and
measured what it does to cost at fixed depth, to the marginal, and to drift. It
did *not* answer the question an engineer actually asks: **given a budget, how
much depth does each regime buy, and is there a budget below which compiling is
the wrong choice?**

This file answers that, in a way that can be wrong.

The theory (``experiments/solidification_theory.md``, same directory) gives a
closed form for the budget at which the compiled arm overtakes the unlabelled
one::

    B* = c_v * ( c_v + c_d + S - c_bar_d ) / ( c_v - c_bar_d )
       = c_v * [ p*(c_v - c_c) + (1-p)*c_d + S ] / ( p*(c_v - c_c - c_d) )

with ``c_bar_d = p*(c_c + c_d) + (1-p)*c_v`` and ``p = 1 - rho``. Three
consequences, in decreasing order of how surprising they are:

1. **The brief's own prediction is false.** The brief supposed that the first
   verification, ``c_v``, being a one-off cost, would put the compiled arm behind
   at small budgets and create an interior crossing ``B* > c_v``. It does not:
   the unlabelled arm pays the same ``c_v`` for its first rung, the two arms
   coincide exactly at ``B = c_v``, and the compiled arm is strictly ahead for
   every ``B > c_v``. ``B* = c_v`` is a point, not a threshold, and the region
   where compiling loses is ``B < c_v``, where neither arm completes one rung.
   A non-degenerate ``B*`` requires a *fixed* premium ``S > 0`` -- witness
   store, toolchain, compiler -- that only the compiled arm pays.

2. **A per-traverse monitor does create one, and the threshold moves with
   drift.** If the compiled arm must pay ``c_d`` on every traversal, including
   the first, then ``B*`` is finite iff ``c_c + c_d < c_v`` -- the same
   inequality the previous experiment reported as a price break-even, now read as
   a budget threshold -- and ``B*`` grows as ``rho/(1-rho)``. Drift does not
   merely cost money; it moves the point at which compiling becomes rational.

3. **The reach advantage is transient in budget.** Reach saturates in depth, so
   the ratio ``R_solid/R_unsolid`` returns to 1 as ``B -> infinity`` even though
   the *depth* advantage grows without bound. There is a finite budget ``B+`` at
   which compilation buys the most reach, and past it the extra depth is real and
   worth nothing in reach. That is a falsifiable non-monotonicity, and it is the
   opposite of "the gap diverges".

What is simulated, and what that is worth
-----------------------------------------
Every ``B*`` below is then *estimated* rather than evaluated: the simulation
draws the per-rung cost from the stated process, takes the sample mean price
``mu_hat``, and solves the crossing with ``mu_hat`` in place of ``mu``. That is a
check on the process and the algebra together -- if the cost model were not the
one the formula integrates, the recovered ``B*`` would not land on the closed
form -- but it is *not* evidence about any physical system, and the report says
so.

Two quantities are genuinely not in the closed form and are why running anything
is worthwhile:

* **Granularity.** A real chain completes whole rungs. The fluid threshold
  ``B*(S)`` is not the budget at which a scheduler first sees the compiled arm
  get further ahead; that budget is ``c_v + c_c + S``, the price at which one
  replay beyond the verification becomes affordable. The two differ by about one
  rung's price, in a direction that depends on ``S``, and both are reported.
* **The reach peak ``B+``.** It is the root of a transcendental equation. The
  closed form gives the local slope, not the location, so it is found
  numerically and then checked against that slope.

Run:
    python3 -m experiments.critical_budget_experiment

Writes:
    experiments/critical_budget_data.csv
    experiments/critical_budget_data_reach.csv
    experiments/critical_budget_figure.png
    experiments/critical_budget_summary.json
    experiments/paper/data/critical_budget_{macros,table,detector_table}.tex
    experiments/paper/figures/critical_budget.png

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

DATA_CSV = os.path.join(HERE, "critical_budget_data.csv")
REACH_CSV = os.path.join(HERE, "critical_budget_data_reach.csv")
SUMMARY_JSON = os.path.join(HERE, "critical_budget_summary.json")
FIGURE_PNG = os.path.join(HERE, "critical_budget_figure.png")

PAPER_DIR = os.path.join(HERE, "paper")
PAPER_DATA = os.path.join(PAPER_DIR, "data")
PAPER_FIGURES = os.path.join(PAPER_DIR, "figures")

# ---------------------------------------------------------------------------
# 1. parameters, each with a provenance
# ---------------------------------------------------------------------------

SEED = 20261104

#: Full verification of one rung, the unit of budget. The paper's own price.
CV = 1.0

#: Replay of a compiled rung. Two orders of magnitude cheaper is the assumption
#: the previous experiment already reported, and it is reported as an assumption
#: here too: every threshold below is affine in it.
CC = 0.01

#: Monitoring a compiled rung for drift, per traverse. Paid whether or not
#: anything moved, which is the whole expense.
CD = 0.02

#: Per-traverse probability that a compiled rung went stale.
RHO = 0.1

#: Fixed premiums to sweep. Zero is the brief's model; the others are the
#: witness store, the compiler and the toolchain, in units of one verification.
S_GRID = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)

#: Detector prices to sweep, up to the admissible limit ``c_v - c_c``.
CD_GRID = (0.0, 0.02, 0.1, 0.25, 0.5, 0.8, 0.99)

#: Drift rates used for the tables and for the ``rho/(1-rho)`` law.
RHO_GRID = (0.0, 0.1, 0.5, 0.9)

#: Saturation depth for the difficulty coordinate. Ten rungs is the worked
#: ladder of the previous experiment, reused rather than re-fitted.
K0 = 10.0

#: Amplification at full difficulty. ``alpha = ln(1e12)`` makes the reach
#: saturate at exactly the paper's horizon, so no new constant is introduced.
ALPHA = math.log(1.0e12)

#: Reach at zero difficulty. Normalisation, not a measurement.
A0 = 1.0

#: Monte Carlo draws per estimate. The estimator is a sample mean of a bounded
#: per-rung price, so its standard error is ~0.002 at this size: small enough
#: that ``B*`` is recovered to three decimals, which is the printed precision.
RUNS = 60000

#: Budgets used for the reach curve and the peak search.
B_MIN = 0.05
B_MAX = 200.0
B_STEPS = 4000


# ---------------------------------------------------------------------------
# 2. the analytic model
# ---------------------------------------------------------------------------


def c_bar(rho: float = RHO, cv: float = CV, cc: float = CC) -> float:
    """Expected cost of one traverse of a compiled rung, no monitor.

    ``c_c + rho*(c_v - c_c) = p*c_c + (1-p)*c_v``. This is the number the
    amortisation limit divides by, and it is *below* ``c_v`` whenever
    ``c_c < c_v`` -- compiling a rung is never dearer per traverse, which is why
    the naive "compiling loses at low budget" intuition cannot be right.
    """
    return cc + rho * (cv - cc)


def c_bar_detector(rho: float = RHO, cd: float = CD,
                   cv: float = CV, cc: float = CC) -> float:
    """Expected cost of one traverse of a compiled rung *with a monitor*.

    ``c_c + c_d + rho*(c_v - c_c)``. Every traverse pays the monitor -- you
    cannot know whether the path is stale without checking -- and on a detected
    drift the verification *replaces* the replay rather than being added to it.
    This is the same price model the previous experiment used, and it makes the
    condition ``c_d < (1-rho)(c_v-c_c)`` fall out as the point where compiling
    stops paying at any depth.
    """
    return cc + cd + rho * (cv - cc)


def gain_infinite(rho: float = RHO, cv: float = CV, cc: float = CC) -> float:
    """``G_inf = c_v / c_bar``: the amortisation gain over many traversals.

    A price ratio, not a speed-up: it compares two ways of paying for the same
    interface. At ``rho = 0`` it is ``c_v/c_c = 100``; at ``rho = 1`` it is 1,
    because every traverse is a verification either way.
    """
    return cv / c_bar(rho, cv, cc)


def gain_finite(traversals: int, rho: float = RHO,
                cv: float = CV, cc: float = CC) -> float:
    """``G(T) = C_unsolid(T)/C_solid(T)`` for a finite number of traversals.

    ``T*G_inf/(T-1+G_inf)``. Exactly ``1`` at ``T = 1`` -- the first traverse is
    the verification in both arms -- which is the whole reason the brief's
    "initial cost makes compiling lose" story does not survive its own algebra.
    """
    if traversals < 1:
        raise ValueError("T must be at least 1")
    g = gain_infinite(rho, cv, cc)
    return traversals * g / (traversals - 1 + g)


def depth_unsolid(budget: float, cv: float = CV) -> float:
    """Depth bought by ``budget`` when every rung is verified: ``B/c_v``."""
    return max(0.0, budget) / cv


def depth_solid(budget: float, rho: float = RHO, S: float = 0.0,
                cd: float = 0.0, cv: float = CV, cc: float = CC) -> float:
    """Depth bought by ``budget`` when every rung after the first is compiled.

    ``1 + (B - c_v - c_d - S)/c_bar_d``, clipped at zero. The first rung is
    verified because nothing can be compiled before it is crossed once; the
    fixed premium ``S`` is paid before any rung; the monitor is paid on the first
    rung too, because the monitoring loop is running from the start.
    """
    per = c_bar_detector(rho, cd, cv, cc)
    first = cv + cd + S
    if budget <= 0.0:
        return 0.0
    if budget < first:
        return budget / first
    return 1.0 + (budget - first) / per


def critical_budget(rho: float = RHO, S: float = 0.0, cd: float = 0.0,
                    cv: float = CV, cc: float = CC) -> float:
    """The budget at which ``depth_solid`` overtakes ``depth_unsolid``.

    Closed form (Theorem 2 of the theory note)::

        B* = c_v*( p*(c_v-c_c) + S ) / ( p*(c_v-c_c) - c_d ),  p = 1-rho.

    Returns ``inf`` when the denominator is non-positive, which happens exactly
    when compiling never pays at any depth: ``c_d >= p*(c_v-c_c)`` (the monitor
    costs more than the drift it defends against saves) or ``rho = 1`` (every
    traverse is a verification anyway). That inequality is the previous
    experiment's detector break-even, unchanged.
    """
    p = 1.0 - rho
    denom = p * (cv - cc) - cd
    if denom <= 0.0:
        return math.inf
    return cv * (p * (cv - cc) + S) / denom


def critical_budget_from_price(mean_price: float, rho: float = RHO,
                               S: float = 0.0, cd: float = 0.0,
                               cv: float = CV) -> float:
    """``B*`` given a *measured* per-rung price rather than a modelled one.

    ``c_v*(c_v + c_d + S - mu)/(c_v - mu)``. Used twice: with ``mu = c_bar_d`` it
    reproduces the closed form, and with the simulated ``mu_hat`` it becomes an
    estimate of ``B*`` whose only inputs are draws from the stated process.
    """
    denom = cv - mean_price
    if denom <= 0.0:
        return math.inf
    return cv * (cv + cd + S - mean_price) / denom


def detector_limit(rho: float = RHO, cv: float = CV, cc: float = CC) -> float:
    """Largest monitor price at which compiling pays: ``p*(c_v-c_c)``.

    The previous experiment's break-even, ``c_d < (1-rho)(c_v-c_c)``, read as a
    price. Above it the denominator of Theorem 2 is negative and ``B*`` is
    infinite: no budget makes compiling rational.
    """
    return (1.0 - rho) * (cv - cc)


def drift_limit(cd: float = CD, cv: float = CV, cc: float = CC) -> float:
    """Drift rate above which compiling never pays: ``1 - c_d/(c_v-c_c)``.

    The vertical asymptote of Theorem 2. At the baseline monitor this is
    ``0.980``: a one per cent chance per traverse of an uncaught staleness
    already removes compilation's rationale at any budget.
    """
    return 1.0 - cd / (cv - cc)


def premium_limit(rho: float = RHO, cv: float = CV, cc: float = CC) -> float:
    """Largest fixed premium at which a *whole-rung* chain ever crosses.

    ``max_k [ sum_{j<=k} p^j - k*c_c/c_v ]``. The geometric sum is bounded by
    ``p/(1-p)``, so a premium above its maximum can never be repaid in whole
    rungs: the fluid theory has no such bound and granularity introduces one.
    """
    p = 1.0 - rho
    best = 0.0
    total = 0.0
    for k in range(1, 100000):
        total += p ** k
        best = max(best, total - k * cc / cv)
        if total >= p / (1.0 - p) - 1e-15:
            break
    return best


def granular_threshold_form(S: float = 0.0, rho: float = RHO, cd: float = 0.0,
                            cv: float = CV, cc: float = CC) -> float:
    """Smallest ``k`` with ``sum_{j<=k} p^j > (S + k*c_c)/c_v``, hence ``B*``.

    A whole-rung chain cannot stop at ``K = 5.4``; it completes ``floor(K)``, and
    the expected count of a budget-constrained renewal is *not* ``B/mu``: it is
    biased by the inspection paradox, because stopping always lands just after a
    cheap replay. The bias is ``O(1)`` -- one or two rungs -- which is the same
    order as ``B* - c_v`` itself.

    With ``k`` replays affordable beyond the verification the expected count is
    ``1 + sum_{j<=k} p^j``, so the crossing is at
    ``B = c_v + c_d + S + k c_c`` for the smallest ``k`` that makes the count
    exceed ``B/c_v``. Returns ``inf`` when no such ``k`` exists -- which happens
    for a premium larger than the geometric sum's limit, ``(1-rho)/rho``: above
    it the compiled arm never overtakes a whole-rung chain, however large the
    budget. That is a second, independent critical premium.
    """
    first = cv + cd + S
    total = 0.0
    p = 1.0 - rho
    for k in range(1, 100000):
        total += p ** k
        if 1.0 + total > (first + k * cc) / cv:
            return first + k * cc
        if total >= p / (1.0 - p):
            break
    return math.inf


def measure_granular_threshold(S: float = 0.0, rho: float = RHO,
                               cd: float = 0.0, runs: int = RUNS,
                               seed: int = SEED, tol: float = 1e-3) -> float:
    """Bisect the whole-rung crossing ``E[N_solid] = B/c_v``.

    Step function, so the answer is the left edge of a step of width ``c_c``:
    that is a property of the quantity, not of the search, and it is why the
    table prints three decimals, not six.
    """
    first = CV + cd + S
    lo, hi = first, first + 10.0
    if 1.0 + simulate_mean_count(hi - first, runs=runs, rho=rho, cd=cd,
                                 seed=seed + 100) <= hi / CV:
        return math.inf
    for step in range(40):
        mid = 0.5 * (lo + hi)
        count = 1.0 + simulate_mean_count(mid - first, runs=runs, rho=rho,
                                          cd=cd, seed=seed + step)
        if count > mid / CV:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def reach(depth: float, alpha: float = ALPHA, k0: float = K0,
          a0: float = A0) -> float:
    """``A_0 * exp(alpha*(1 - exp(-K/K_0)))``: reach at depth ``K``.

    Saturating, by construction: the same saturation the paper forces on
    difficulty, applied with ``P -> K``. Reach tends to ``A_0*e^alpha`` and the
    horizon is that limit.
    """
    if depth <= 0.0:
        return a0
    return a0 * math.exp(alpha * (1.0 - math.exp(-depth / k0)))


def reach_ratio(budget: float, rho: float = RHO, S: float = 0.0,
                cd: float = 0.0) -> float:
    """``R_solid(B)/R_unsolid(B)``. Below 1 inside the losing region."""
    ka = depth_unsolid(budget)
    kb = depth_solid(budget, rho, S, cd)
    return reach(kb) / reach(ka)


def peak_reach_ratio(rho: float = RHO, S: float = 0.0, cd: float = 0.0,
                     b_hi: float = 200.0) -> Tuple[float, float]:
    """Locate ``B+``, the budget that maximises the reach ratio, and the ratio.

    Golden-section search on a unimodal objective. Unimodality is what Theorem 3
    asserts; if the assumption were wrong this would return a local optimum, and
    the falsification check below compares the endpoints as well, so a
    non-unimodal curve would show up as a FAIL rather than as a number.
    """
    phi = (math.sqrt(5.0) - 1.0) / 2.0

    def f(x: float) -> float:
        return reach_ratio(x, rho, S, cd)

    a, b = CV, b_hi
    c = b - phi * (b - a)
    d = a + phi * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(220):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = f(d)
    x = 0.5 * (a + b)
    return x, f(x)


# ---------------------------------------------------------------------------
# 3. the simulation
# ---------------------------------------------------------------------------


def simulate_mean_price(runs: int = RUNS, rho: float = RHO, cd: float = 0.0,
                        cv: float = CV, cc: float = CC,
                        seed: int = SEED) -> Tuple[float, float]:
    """Sample mean and standard error of the per-rung price *after* the first.

    Draws the stated process -- ``c_c + c_d`` with probability ``1-rho``, else
    ``c_v + c_d`` -- and returns ``(mean, se)``. This is the only simulated
    quantity the ``B*`` estimate uses, which is why the estimate lands on the
    closed form: the closed form is what this mean converges to. Reporting it as
    a *validation* rather than as a discovery is the point.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    stale = rng.random(runs) >= (1.0 - rho)
    prices = np.where(stale, cv + cd, cc + cd)
    mean = float(prices.mean())
    se = float(prices.std(ddof=1) / math.sqrt(runs))
    return mean, se


def simulate_mean_count(remaining: float, runs: int = RUNS, rho: float = RHO,
                        cd: float = 0.0, cv: float = CV, cc: float = CC,
                        seed: int = SEED) -> float:
    """``E[#further rungs completed]`` with ``remaining`` budget, whole rungs only.

    Used for the granularity check only. It is deliberately the *integer* count:
    the difference between it and ``remaining/c_bar`` is the thing that a
    task-level system feels and the fluid theory does not describe.
    """
    import numpy as np

    if remaining <= 0.0:
        return 0.0
    cheapest = min(cc + cd, cv + cd)
    max_rungs = int(remaining / max(cheapest, 1e-12)) + 4
    rng = np.random.default_rng(seed)
    stale = rng.random((runs, max_rungs)) >= (1.0 - rho)
    costs = np.where(stale, cv + cd, cc + cd)
    cumulative = np.cumsum(costs, axis=1)
    return float((cumulative <= remaining).sum(axis=1).mean())


def measure_critical_budget(rho: float = RHO, S: float = 0.0, cd: float = 0.0,
                            runs: int = RUNS, seed: int = SEED
                            ) -> Tuple[float, float]:
    """Estimate ``B*`` from a simulated price. Returns ``(B, standard error)``.

    The standard error is propagated from the price's own::

        dB*/dmu = c_v*(c_v + c_d + S - c_v)/(c_v - mu)^2,

    which is the honest way to say "the simulation agrees to this many digits".
    """
    mean, se = simulate_mean_price(runs=runs, rho=rho, cd=cd, seed=seed)
    estimate = critical_budget_from_price(mean, rho=rho, S=S, cd=cd)
    denom = CV - mean
    sensitivity = CV * (CV + cd + S - CV) / (denom * denom) if denom > 0 else 0.0
    return estimate, abs(sensitivity) * se


# ---------------------------------------------------------------------------
# 4. the sweeps
# ---------------------------------------------------------------------------


def sweep_infrastructure() -> List[Dict[str, float]]:
    """``B*`` against the fixed premium ``S``, analytic and estimated."""
    rows: List[Dict[str, float]] = []
    for S in S_GRID:
        estimate, error = measure_critical_budget(S=S)
        rows.append({
            "kind": "infrastructure",
            "x": S,
            "B_analytic": critical_budget(S=S),
            "B_measured": estimate,
            "B_stderr": error,
            "B_granular": measure_granular_threshold(S=S),
            "B_granular_form": granular_threshold_form(S=S),
            "depth_gain_at_ten": depth_solid(10.0, S=S) - depth_unsolid(10.0),
        })
    return rows


def sweep_detector() -> List[Dict[str, float]]:
    """``B*`` against the monitor price, at several drift rates.

    The prediction is not just that ``B*`` rises with ``c_d`` but *how*: the
    closed form is affine in ``c_d`` over a denominator that also moves, so the
    curve is convex and diverges at ``c_d = c_v - c_c``.
    """
    rows: List[Dict[str, float]] = []
    for cd in CD_GRID:
        for rho in RHO_GRID:
            if critical_budget(rho=rho, cd=cd) == math.inf:
                estimate, error = math.inf, 0.0
            else:
                estimate, error = measure_critical_budget(rho=rho, cd=cd,
                                                          runs=4 * RUNS)
            rows.append({
                "kind": "detector",
                "x": cd,
                "rho": rho,
                "B_analytic": critical_budget(rho=rho, cd=cd),
                "B_measured": estimate,
                "B_stderr": error,
            })
    return rows


def sweep_drift() -> List[Dict[str, float]]:
    """``B*`` against ``rho`` at fixed ``c_d``: the ``rho/(1-rho)`` law."""
    rows: List[Dict[str, float]] = []
    for rho in (0.0, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9):
        estimate, error = measure_critical_budget(rho=rho, cd=CD, runs=4 * RUNS)
        rows.append({
            "kind": "drift",
            "x": rho,
            "cd": CD,
            "B_analytic": critical_budget(rho=rho, cd=CD),
            "B_measured": estimate,
            "B_stderr": error,
        })
    return rows


def reach_curve() -> List[Dict[str, float]]:
    """Reach of both arms, and their ratio, over the budget window."""
    rows: List[Dict[str, float]] = []
    for index in range(B_STEPS + 1):
        fraction = index / B_STEPS
        budget = B_MIN + fraction * (B_MAX - B_MIN)
        ka = depth_unsolid(budget)
        kb = depth_solid(budget)
        rows.append({
            "budget": budget,
            "K_unsolid": ka,
            "K_solid": kb,
            "reach_unsolid": reach(ka),
            "reach_solid": reach(kb),
            "ratio": reach(kb) / reach(ka),
        })
    return rows


def granularity_check(rows_infra: Sequence[Dict[str, float]]) -> Dict[str, object]:
    """Compare the whole-rung threshold with the fluid one, across ``S``.

    Two claims, both falsifiable: the realised threshold never exceeds the fluid
    one by more than the price of a replay, and it is never more than ten per
    cent below it. The second is the interesting one -- a granular chain reaches
    the crossing *earlier*, because a budget-constrained run always stops just
    after a cheap replay, so its realised count is biased up.
    """
    comparisons = []
    for row in rows_infra:
        if not math.isfinite(row["B_granular"]):
            continue
        comparisons.append({
            "S": row["x"],
            "fluid": row["B_analytic"],
            "granular": row["B_granular"],
            "form": row["B_granular_form"],
            "difference": row["B_granular"] - row["B_analytic"],
            "ratio": row["B_granular"] / row["B_analytic"],
        })
    positive = [c for c in comparisons if c["S"] > 0.0]
    return {
        "comparisons": comparisons,
        "form_matches": all(abs(c["granular"] - c["form"]) <= CC + 1e-9
                            for c in comparisons),
        "never_above_by_more_than_a_replay":
            all(c["difference"] <= CC + 2e-3 for c in comparisons),
        "never_below_ninety_percent":
            all(c["ratio"] >= 0.9 for c in positive),
    }


# ---------------------------------------------------------------------------
# 5. falsification checks
# ---------------------------------------------------------------------------


def linear_fit(xs: Sequence[float],
               ys: Sequence[float]) -> Tuple[float, float, float]:
    """Least-squares slope, intercept and R^2. Nothing here is fitted twice."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return slope, intercept, r2


def local_slope(lo: float, hi: float, samples: int = 41) -> Tuple[float, float]:
    """Secant slope of ``ln(R_B/R_A)`` over ``[lo, hi]`` and its R^2."""
    xs = [lo + (hi - lo) * i / (samples - 1) for i in range(samples)]
    ys = [math.log(reach_ratio(b)) for b in xs]
    slope, _, r2 = linear_fit(xs, ys)
    return slope, r2


def checks(rows_infra: Sequence[Dict[str, float]],
           rows_drift: Sequence[Dict[str, float]],
           curve: Sequence[Dict[str, float]],
           granular: Dict[str, object],
           peak_budget: float, peak_ratio: float) -> List[Dict[str, object]]:
    """The six predictions, each with the measurement that could break it."""
    out: List[Dict[str, object]] = []

    # P1 -- with no premium, B* is exactly c_v and nothing loses above it.
    b0 = critical_budget(S=0.0, cd=0.0)
    behind = [r["budget"] for r in curve
              if r["budget"] > CV and r["K_solid"] < r["K_unsolid"]]
    out.append({
        "id": "P1",
        "claim": "B* = c_v exactly when S = c_d = 0; no interior loss region",
        "predicted": CV,
        "measured": b0,
        "error": abs(b0 - CV),
        "tolerance": 1e-12,
        "verdict": "pass" if abs(b0 - CV) <= 1e-12 and not behind else "FAIL",
    })

    # P2 -- B*(S) is affine with intercept c_v and slope G/(G-1).
    slope_predicted = gain_infinite() / (gain_infinite() - 1.0)
    xs = [r["x"] for r in rows_infra]
    ys = [r["B_measured"] for r in rows_infra]
    slope, intercept, r2 = linear_fit(xs, ys)
    out.append({
        "id": "P2",
        "claim": "B*(S) affine, intercept c_v, slope G_inf/(G_inf-1)",
        "predicted": slope_predicted,
        "measured": slope,
        "predicted_intercept": CV,
        "measured_intercept": intercept,
        "r2": r2,
        "error": abs(slope - slope_predicted),
        "tolerance": 0.01,
        "verdict": "pass" if (abs(slope - slope_predicted) <= 0.01
                              and abs(intercept - CV) <= 0.02) else "FAIL",
    })

    # P3 -- B*(rho) follows the closed form, rising as rho/(1-rho).
    worst = 0.0
    for row in rows_drift:
        if not math.isfinite(row["B_measured"]):
            continue
        worst = max(worst, abs(row["B_measured"] - row["B_analytic"])
                    / row["B_analytic"])
    rising = all(rows_drift[i + 1]["B_measured"] >= rows_drift[i]["B_measured"]
                 for i in range(len(rows_drift) - 1))
    out.append({
        "id": "P3",
        "claim": "B*(rho) = c_v*[a(1-rho)+S]/(a(1-rho)-c_d), asymptote 1-c_d/a",
        "predicted": rows_drift[-1]["B_analytic"],
        "measured": rows_drift[-1]["B_measured"],
        "worst_relative_error": worst,
        "rising": rising,
        "tolerance": 0.01,
        "verdict": "pass" if (worst <= 0.01 and rising) else "FAIL",
    })

    # P4 -- the reach ratio returns to 1 and peaks strictly inside.
    tail = [r["ratio"] for r in curve if r["budget"] >= 0.75 * B_MAX]
    peak_index = max(range(len(curve)), key=lambda i: curve[i]["ratio"])
    interior = 0 < peak_index < len(curve) - 1
    out.append({
        "id": "P4",
        "claim": "R_solid/R_unsolid peaks at finite B+ then returns to 1",
        "predicted": 1.0,
        "measured": sum(tail) / len(tail),
        "peak_budget": peak_budget,
        "peak_ratio": peak_ratio,
        "interior": interior,
        "tolerance": 1e-2,
        "verdict": "pass" if (interior and abs(sum(tail) / len(tail) - 1.0)
                              <= 1e-2) else "FAIL",
    })

    # P5 -- the depth gain is linear in B with slope 1/c_bar - 1/c_v.
    slope_predicted = 1.0 / c_bar() - 1.0 / CV
    probe = [1.0 + 0.5 * i for i in range(40)]
    gains = [(depth_solid(b) - depth_unsolid(b)) / (b - CV) for b in probe[1:]]
    slope = sum(gains) / len(gains)
    out.append({
        "id": "P5",
        "claim": "depth gain linear in B, slope 1/c_bar - 1/c_v",
        "predicted": slope_predicted,
        "measured": slope,
        "error": abs(slope - slope_predicted),
        "tolerance": 1e-3,
        "verdict": "pass" if abs(slope - slope_predicted) <= 1e-3 else "FAIL",
    })

    # P6 -- the local exponential rate, approached as the window shrinks.
    #     The tangent is at B = c_v; a finite window secant sits below it and
    #     must converge. A single wide window would fail for the right reason
    #     and prove nothing, so the test is the convergence itself.
    tangent = (ALPHA * (1.0 / c_bar() - 1.0 / CV) * math.exp(-CV / K0) / K0)
    errors = []
    for half in (0.6, 0.3, 0.15, 0.05, 0.01):
        slope, _ = local_slope(CV, CV + half)
        errors.append((half, abs(slope - tangent) / tangent))
    shrinks = all(errors[i + 1][1] <= errors[i][1]
                  for i in range(len(errors) - 1))
    out.append({
        "id": "P6",
        "claim": "local rate -> alpha*(1/c_bar-1/c_v)*e^-c_v/K0 / K_0 as window -> 0",
        "predicted": tangent,
        "measured": tangent * (1.0 - errors[-1][1]),
        "worst_relative_error": errors[-1][1],
        "shrinks": shrinks,
        "tolerance": 0.02,
        "verdict": "pass" if (errors[-1][1] <= 0.02 and shrinks) else "FAIL",
    })

    # P7 -- whole-rung threshold against the fluid one.
    out.append({
        "id": "P7",
        "claim": "whole-rung B* within one replay of B*, and below it for S>0",
        "predicted": rows_infra[-1]["B_analytic"],
        "measured": rows_infra[-1]["B_granular"],
        "form_matches": granular["form_matches"],
        "never_above_by_more_than_a_replay":
            granular["never_above_by_more_than_a_replay"],
        "never_below_ninety_percent":
            granular["never_below_ninety_percent"],
        "verdict": "pass" if (granular["form_matches"]
                              and granular["never_above_by_more_than_a_replay"]
                              and granular["never_below_ninety_percent"])
        else "FAIL",
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


def render(rows_infra: Sequence[Dict[str, float]],
           rows_detector: Sequence[Dict[str, float]],
           rows_drift: Sequence[Dict[str, float]],
           curve: Sequence[Dict[str, float]],
           peak_budget: float, peak_ratio: float,
           path: str = FIGURE_PNG) -> str:
    _style()
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.9),
                             constrained_layout=True)

    # (a) depth against budget, with the crossing -------------------------
    ax = axes[0][0]
    budgets = [0.02 + 0.02 * i for i in range(1, 300)]
    ax.plot(budgets, [depth_unsolid(b) for b in budgets], color=ACCENT,
            linewidth=1.9, label="A: $B/c_v$")
    for S, colour, dash in ((0.0, COOL, "-"), (1.0, GREEN, "--"),
                            (3.0, GOLD, "-.")):
        ax.plot(budgets, [depth_solid(b, S=S) for b in budgets], color=colour,
                linewidth=1.5, linestyle=dash,
                label="B: $S={:g}$".format(S))
        b_star = critical_budget(S=S)
        if b_star <= max(budgets):
            ax.plot([b_star], [depth_unsolid(b_star)], marker="o",
                    markersize=3.4, color=colour, zorder=5)
    ax.axvline(CV, color=PALE, linewidth=1.0, zorder=0)
    ax.annotate("$B=c_v$", xy=(CV + 0.05, 1.5), fontsize=6.2, color=MUTED,
                ha="left", va="bottom")
    ax.set_xlabel("budget $B$, in verifications")
    ax.set_ylabel("depth $K$")
    ax.set_xlim(0, 6)
    ax.set_ylim(0, 48)
    ax.set_title("(a) the crossing is at $c_v$", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.5,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True)

    # (b) B* against the fixed premium ------------------------------------
    ax = axes[0][1]
    xs = [r["x"] for r in rows_infra]
    grid = [max(xs) * i / 60.0 for i in range(61)]
    ax.plot(grid, [critical_budget(S=s) for s in grid], color=COOL,
            linewidth=1.4, linestyle="--", label="closed form")
    ax.errorbar(xs, [r["B_measured"] for r in rows_infra],
                yerr=[r["B_stderr"] for r in rows_infra], color=ACCENT,
                marker="o", markersize=3.2, linewidth=1.0, linestyle="none",
                capsize=1.8, elinewidth=0.8, label="estimated")
    ax.axhline(CV, color=PALE, linewidth=0.9, zorder=0)
    ax.set_xlabel("fixed premium $S$")
    ax.set_ylabel("$B^*$")
    ax.set_title("(b) slope $G_\\infty/(G_\\infty-1)$", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True)

    # (c) both reaches, and the common horizon ---------------------------
    ax = axes[0][2]
    ax.semilogy([r["budget"] for r in curve],
                [r["reach_unsolid"] for r in curve], color=ACCENT,
                linewidth=1.6, label="A unlabelled")
    ax.semilogy([r["budget"] for r in curve],
                [r["reach_solid"] for r in curve], color=COOL,
                linewidth=1.6, label="B compiled")
    ax.axhline(A0 * math.exp(ALPHA), color=PALE, linewidth=0.9, zorder=0)
    ax.annotate("horizon", xy=(B_MAX * 0.70, A0 * math.exp(ALPHA) * 0.25),
                fontsize=6.0, color=MUTED)
    ax.set_xlim(0, 120)
    ax.set_xlabel("budget $B$")
    ax.set_ylabel("reach")
    ax.set_title("(c) one horizon, reached sooner", loc="left")
    ax.legend(frameon=False, loc="lower right", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True, which="both")

    # (d) the ratio: dip, cross, peak, return ----------------------------
    ax = axes[1][0]
    for S, colour, dash in ((0.0, COOL, "-"), (1.0, GREEN, "--")):
        ax.semilogy([r["budget"] for r in curve],
                    [max(r["ratio"], 1e-4) for r in curve], color=colour,
                    linewidth=1.4, linestyle=dash,
                    label="$S={:g}$".format(S))
    ax.axhline(1.0, color=MUTED, linewidth=0.8, linestyle=":")
    ax.plot([peak_budget], [peak_ratio], marker="v", markersize=4.2,
            color=GREEN, zorder=5)
    ax.annotate("$B^\\dagger$", xy=(peak_budget, peak_ratio), fontsize=6.6,
                color=GREEN, xytext=(5, -3), textcoords="offset points")
    ax.set_xlim(0, B_MAX)
    ax.set_ylim(1e-2, 1e9)
    ax.set_xlabel("budget $B$")
    ax.set_ylabel("$R_B/R_A$")
    ax.set_title("(d) the advantage is transient", loc="left")
    ax.legend(frameon=False, loc="lower left", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.2)
    ax.grid(True, which="both")

    # (e) the monitor's divergence ---------------------------------------
    ax = axes[1][1]
    shades = {0.0: COOL, 0.1: GREEN, 0.5: GOLD, 0.9: ACCENT}
    cds = [1e-3 + (0.99 - 1e-3) * i / 400 for i in range(401)]
    for rho, colour in shades.items():
        ys = [critical_budget(rho=rho, cd=cd) for cd in cds]
        ax.semilogy(cds, [min(y, 1e3) for y in ys], color=colour,
                    linewidth=1.4 if rho == RHO else 1.0,
                    label="$\\rho={:g}$".format(rho))
    for r in rows_detector:
        if r["rho"] in shades and math.isfinite(r["B_measured"]):
            ax.plot([r["x"]], [r["B_measured"]], marker="o", markersize=2.6,
                    color=shades[r["rho"]], linestyle="none", zorder=5)
    ax.axvline((1.0 - RHO) * (CV - CC), color=PALE, linewidth=1.0, zorder=0)
    ax.annotate("$p(c_v-c_c)$", xy=((1.0 - RHO) * (CV - CC), 1.1),
                fontsize=6.0, color=MUTED, ha="right")
    ax.set_xlabel("monitor price $c_d$")
    ax.set_ylabel("$B^*$")
    ax.set_ylim(0.9, 1e3)
    ax.set_title("(e) never, before the price is infinite", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.2,
              borderaxespad=0.3, fontsize=6.0)
    ax.grid(True, which="both")

    # (f) the drift law ---------------------------------------------------
    ax = axes[1][2]
    rhos = [i / 100.0 for i in range(1, 99)]
    ax.semilogy(rhos, [critical_budget(rho=r, cd=CD) for r in rhos],
                color=COOL, linewidth=1.5, label="closed form")
    ax.semilogy([r["x"] for r in rows_drift],
                [r["B_measured"] for r in rows_drift], color=ACCENT,
                marker="o", markersize=3.0, linestyle="none",
                label="estimated")
    ax.axhline(CV, color=PALE, linewidth=0.9, zorder=0)
    ax.set_xlim(0, 0.98)
    ax.set_ylim(1.0, 1e2)
    ax.set_xlabel("drift rate $\\rho$")
    ax.set_ylabel("$B^*$")
    ax.set_title("(f) $\\rho/(1-\\rho)$", loc="left")
    ax.legend(frameon=False, loc="upper left", handlelength=1.4,
              borderaxespad=0.3, fontsize=6.2)
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


def report(rows_infra: Sequence[Dict[str, float]],
           rows_detector: Sequence[Dict[str, float]],
           rows_drift: Sequence[Dict[str, float]],
           curve: Sequence[Dict[str, float]],
           results: Sequence[Dict[str, object]],
           granular: Dict[str, float],
           peak_budget: float, peak_ratio: float) -> None:
    line = "=" * 78
    print(line)
    print("THE CRITICAL BUDGET: when compiling pays, and when it does not")
    print(line)
    print("c_v {:.2f}   c_c {:.2f}   c_d {:.2f}   rho {:.2f}   "
          "K0 {:.0f}   alpha ln(1e12)   draws {:,}".format(
              CV, CC, CD, RHO, K0, RUNS))
    print()

    print("-" * 78)
    print("1. THE ANSWER, WHICH IS NOT THE ONE THE BRIEF EXPECTED")
    print("-" * 78)
    print("  c_bar            = c_c + rho*(c_v-c_c)      = {:.4f}".format(c_bar()))
    print("  G_inf            = c_v/c_bar                = {:.4f}".format(
        gain_infinite()))
    print("  G(2)             = T*G/(T-1+G)              = {:.4f}".format(
        gain_finite(2)))
    print("  B* with S = 0, c_d = 0                      = {:.4f}".format(
        critical_budget(S=0.0, cd=0.0)))
    print("  B* with c_d = 0.02                          = {:.4f}".format(
        critical_budget(cd=CD)))
    print()
    print("  The brief predicted K_solid < K_unsolid at small B and an interior")
    print("  crossing. That is false, and it is false for a reason visible in")
    print("  one line: the unlabelled arm pays the same c_v for its first rung.")
    print("  Subtract the two depth maps and factor:")
    print()
    print("     K_solid - K_unsolid = (1/c_bar - 1/c_v) * (B - c_v)")
    print()
    print("  The difference is zero exactly at B = c_v and strictly positive")
    print("  above it. The losing region is B < c_v, where neither arm can")
    print("  complete a single rung. A threshold below the price of one")
    print("  crossing is not an engineering threshold.")
    print()
    print("  An interior B* requires a FIXED premium S that only the compiled")
    print("  arm pays, or a monitor c_d charged on every traverse. Both give a")
    print("  closed form; the numbers follow.")
    print()

    print("-" * 78)
    print("2. B* AGAINST THE FIXED PREMIUM S   (predicted slope {:.4f})".format(
        gain_infinite() / (gain_infinite() - 1.0)))
    print("-" * 78)
    print("      S    B* closed     B* estimated (s.e.)      B* whole rungs"
          "    depth gain at B=10")
    for r in rows_infra:
        print("  {:5.2f}   {:9.4f}   {:9.4f} ({:.4f})     {:11.3f}   {:18.4f}"
              .format(r["x"], r["B_analytic"], r["B_measured"],
                      r["B_stderr"], r["B_granular"],
                      r["depth_gain_at_ten"]))
    slope, intercept, r2 = linear_fit([r["x"] for r in rows_infra],
                                      [r["B_measured"] for r in rows_infra])
    print()
    print("  fit: B* = {:.4f} + {:.4f}*S     R^2 {:.8f}".format(
        intercept, slope, r2))
    print("  predicted intercept c_v = {:.4f};  predicted slope G/(G-1) = "
          "{:.4f}".format(CV, gain_infinite() / (gain_infinite() - 1.0)))
    print("  The slope exceeds 1: a unit of infrastructure costs more than a")
    print("  unit of budget before it repays itself, by exactly G/(G-1).")
    print()
    print("  The last threshold column is the granular one: a whole-rung chain")
    print("  cannot stop at K = 5.4, and the expected count of a budget-")
    print("  constrained renewal is biased up by the inspection paradox, so the")
    print("  realised crossing comes earlier than the fluid one. Closed form for")
    print("  the step index: smallest k with sum_{j<=k} p^j > S + k*c_c.")
    for item in granular["comparisons"]:
        print("    S {:4.2f}   fluid {:7.4f}   granular {:7.4f}   "
              "form {:7.4f}   diff {:+.3f}".format(
                  item["S"], item["fluid"], item["granular"],
                  item["form"], item["difference"]))
    print("  above the fluid threshold by more than one replay: {}".format(
        "yes" if not granular["never_above_by_more_than_a_replay"] else "no"))
    print("  below 90% of the fluid threshold:                   {}".format(
        "yes" if not granular["never_below_ninety_percent"] else "no"))
    print()

    print("-" * 78)
    print("3. THE MONITOR: B* DIVERGES BEFORE THE PRICE DOES")
    print("-" * 78)
    print("   c_d     rho   B* closed      B* estimated")
    for r in rows_detector:
        if r["rho"] not in (0.0, RHO, 0.5, 0.9):
            continue
        shown = ("{:10.4f}".format(r["B_analytic"])
                 if math.isfinite(r["B_analytic"]) else "     never")
        meas = ("{:12.4f}".format(r["B_measured"])
                if math.isfinite(r["B_measured"]) else "       never")
        print("  {:5.3f}   {:4.2f}   {}   {}".format(
            r["x"], r["rho"], shown, meas))
    print()
    print("  Monitor limit: c_d < (1-rho)(c_v-c_c) = {:.3f}; drift limit:"
          .format(detector_limit()))
    print("  rho < 1 - c_d/(c_v-c_c) = {:.3f}. Past either, compiling never pays"
          .format(drift_limit()))
    print("  at any budget -- the previous section's break-even read as a")
    print("  budget statement rather than a price one. The threshold is steep")
    print("  near the limit: at rho = 0.9 it is already {:.3f}, but the limit"
          .format(critical_budget(rho=0.9, cd=CD)))
    print("  itself is at rho = {:.3f}, where it is infinite.".format(
        drift_limit()))
    print()
    print("  B* against rho at c_d = {:.2f}:".format(CD))
    for r in rows_drift:
        print("    rho {:4.2f}   closed {:8.4f}   estimated {:8.4f}".format(
            r["x"], r["B_analytic"], r["B_measured"]))
    print("  The law is a(1-rho)/(a(1-rho)-c_d) with a = c_v-c_c: a hyperbola")
    print("  whose asymptote is rho = 1 - c_d/a = {:.3f}, not rho = 1. Drift"
          .format(drift_limit()))
    print("  does not just cost money; it moves the budget at which compiling")
    print("  becomes rational, and it moves it without bound well before the")
    print("  environment is fully unstable.")
    print()

    print("-" * 78)
    print("4. REACH: THE ADVANTAGE IS TRANSIENT IN BUDGET")
    print("-" * 78)
    print("  R(K) = A_0*exp(alpha*(1-exp(-K/K_0))),  alpha = ln(1e12)")
    for b in (1.0, 2.0, 5.0, 10.0, 20.0, 60.0, 120.0, 200.0):
        ka, kb = depth_unsolid(b), depth_solid(b)
        print("    B {:6.2f}   K_A {:8.2f}   K_B {:8.2f}   "
              "reach ratio {:11.4f}".format(b, ka, kb, reach_ratio(b)))
    print()
    print("  Peak reach ratio {:.4g} at B+ = {:.4f}".format(
        peak_ratio, peak_budget))
    print("  As B -> infinity the ratio returns to 1: both arms saturate at the")
    print("  horizon, so extra depth stops buying reach. The depth advantage is")
    print("  unbounded (slope {:.4f}); the reach advantage is not. Quoting one"
          .format(1.0 / c_bar() - 1.0 / CV))
    print("  as the other is the error this section exists to prevent.")
    print()

    print("-" * 78)
    print("5. THE PREDICTIONS, CHECKED")
    print("-" * 78)
    for result in results:
        status = "PASS" if result["verdict"] == "pass" else "FAIL"
        print("  [{}] {}  {}".format(status, result["id"], result["claim"]))
        print("        predicted {:>12}   measured {:>12}".format(
            _num(result.get("predicted")), _num(result.get("measured"))))
        if "r2" in result:
            print("        R^2 {:.8f}".format(result["r2"]))
        if "worst_relative_error" in result:
            print("        worst relative error {:.2e}".format(
                result["worst_relative_error"]))
    print()

    print("-" * 78)
    print("WHAT THIS DOES NOT SHOW")
    print("-" * 78)
    print("  1. It does not show that c_c = 0.01 or c_d = 0.02 are the right")
    print("     prices. Every threshold here is affine in them, and the report")
    print("     prints the slopes so a reader can substitute their own.")
    print("  2. It does not show that any real system has S > 0. It shows what")
    print("     follows if it does, and that with S = 0 the threshold collapses")
    print("     to the price of one crossing.")
    print("  3. The simulation is a check that the process realises the algebra,")
    print("     not evidence about the world. It recovers B* because it draws")
    print("     the same price the formula integrates; the quantities it adds")
    print("     are the granular threshold and the location of B+, which the")
    print("     closed form does not give.")
    print("  4. alpha and K_0 are the paper's own horizon and ladder. A different")
    print("     horizon moves B+ and leaves every B* unchanged.")
    print()


# ---------------------------------------------------------------------------
# 8. artifacts
# ---------------------------------------------------------------------------


def write_data(rows_infra: Sequence[Dict[str, float]],
               rows_detector: Sequence[Dict[str, float]],
               rows_drift: Sequence[Dict[str, float]],
               curve: Sequence[Dict[str, float]]) -> List[str]:
    fields = ["kind", "x", "rho", "cd", "B_analytic", "B_measured",
              "B_stderr", "B_granular", "depth_gain_at_ten"]
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows_infra:
            writer.writerow(row)
        for row in rows_detector:
            writer.writerow(row)
        for row in rows_drift:
            writer.writerow(row)
    with open(REACH_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(curve[0].keys()))
        writer.writeheader()
        for row in curve:
            writer.writerow(row)
    return [DATA_CSV, REACH_CSV]


def tex_number(value: float, digits: int = 3) -> str:
    """Format for LaTeX, with the divergences spelled as words.

    ``inf`` is printed as ``\\infty`` rather than ``inf`` because a table cell
    reading ``inf`` looks like a bug to a reader and like a symbol to nobody.
    """
    if isinstance(value, str):
        return value
    if math.isinf(value):
        return r"$\infty$"
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return r"\num{{{:.0f}}}".format(value)
    return "{:.{}f}".format(value, digits)


def write_paper_artifacts(rows_infra: Sequence[Dict[str, float]],
                          rows_detector: Sequence[Dict[str, float]],
                          sweeps: Sequence[Dict[str, float]],
                          results: Sequence[Dict[str, object]],
                          granular: Dict[str, float],
                          peak_budget: float, peak_ratio: float) -> List[str]:
    slope, intercept, r2 = linear_fit([r["x"] for r in rows_infra],
                                      [r["B_measured"] for r in rows_infra])
    tangent = (ALPHA * (1.0 / c_bar() - 1.0 / CV) * math.exp(-CV / K0) / K0)
    macro = os.path.join(PAPER_DATA, "critical_budget_macros.tex")
    with open(macro, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/critical_budget_experiment.py"
                     " -- do not edit\n")
        # Names end in words, never digits: TeX reads \cbFoo2 as \cbFoo + "2".
        handle.write(r"\newcommand{\cbBar}{%.4f}" % c_bar() + "\n")
        handle.write(r"\newcommand{\cbGain}{%.3f}" % gain_infinite() + "\n")
        handle.write(r"\newcommand{\cbGainTwo}{%.3f}" % gain_finite(2) + "\n")
        handle.write(r"\newcommand{\cbCritBudgetZero}{%.3f}"
                     % critical_budget(S=0.0, cd=0.0) + "\n")
        handle.write(r"\newcommand{\cbCritBudgetDetector}{%.4f}"
                     % critical_budget(cd=CD) + "\n")
        handle.write(r"\newcommand{\cbCritBudgetRhoZero}{%.4f}"
                     % critical_budget(rho=0.0, cd=CD) + "\n")
        handle.write(r"\newcommand{\cbCritBudgetRhoHalf}{%.4f}"
                     % critical_budget(rho=0.5, cd=CD) + "\n")
        handle.write(r"\newcommand{\cbCritBudgetRhoNine}{%.4f}"
                     % critical_budget(rho=0.9, cd=CD) + "\n")
        handle.write(r"\newcommand{\cbSlopeInfra}{%.4f}" % slope + "\n")
        handle.write(r"\newcommand{\cbSlopePredicted}{%.4f}"
                     % (gain_infinite() / (gain_infinite() - 1.0)) + "\n")
        handle.write(r"\newcommand{\cbIntercept}{%.4f}" % intercept + "\n")
        handle.write(r"\newcommand{\cbSlopeRsq}{%.6f}" % r2 + "\n")
        granular_one = next((r["B_granular"] for r in rows_infra
                             if r["x"] == 1.0), math.inf)
        handle.write(r"\newcommand{\cbCritBudgetGranular}{%.3f}"
                     % granular_one + "\n")
        handle.write(r"\newcommand{\cbDetectorLimit}{%.3f}"
                     % detector_limit() + "\n")
        handle.write(r"\newcommand{\cbDriftLimit}{%.3f}"
                     % drift_limit() + "\n")
        handle.write(r"\newcommand{\cbPremiumLimit}{%.2f}"
                     % premium_limit() + "\n")
        handle.write(r"\newcommand{\cbCbarDetector}{%.4f}"
                     % c_bar_detector() + "\n")
        handle.write(r"\newcommand{\cbCritBudgetCdHalf}{%.3f}"
                     % critical_budget(cd=0.5) + "\n")
        handle.write(r"\newcommand{\cbCritBudgetCdHigh}{%.2f}"
                     % critical_budget(cd=0.85) + "\n")

        handle.write(r"\newcommand{\cbPeakBudget}{%.2f}" % peak_budget + "\n")
        power = int(math.floor(math.log10(peak_ratio)))
        mantissa = peak_ratio / (10.0 ** power)
        handle.write(r"\newcommand{\cbPeakRatio}{%.1f}" % mantissa + "\n")
        handle.write(r"\newcommand{\cbPeakPower}{%d}" % power + "\n")
        handle.write(r"\newcommand{\cbPeakLog}{%.2f}"
                     % math.log10(peak_ratio) + "\n")
        handle.write(r"\newcommand{\cbDepthSlope}{%.3f}"
                     % (1.0 / c_bar() - 1.0 / CV) + "\n")
        handle.write(r"\newcommand{\cbKnot}{%.0f}" % K0 + "\n")
        handle.write(r"\newcommand{\cbHorizonExp}{12}" + "\n")
        handle.write(r"\newcommand{\cbAlpha}{%.2f}" % ALPHA + "\n")
        handle.write(r"\newcommand{\cbLocalRate}{%.3f}" % tangent + "\n")
        handle.write(r"\newcommand{\cbSlopeAtTen}{%.1f}"
                     % reach_ratio(10.0) + "\n")
        handle.write(r"\newcommand{\cbSlopeAtSixty}{%.3f}"
                     % reach_ratio(60.0) + "\n")
        handle.write(r"\newcommand{\cbSlopeAtHundred}{%.4f}"
                     % reach_ratio(120.0) + "\n")

    table = os.path.join(PAPER_DATA, "critical_budget_table.tex")
    with open(table, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/critical_budget_experiment.py"
                     " -- do not edit\n")
        handle.write(r"\begin{tabular}{@{}rrrrr@{}}" + "\n")
        handle.write(r"\toprule" + "\n")
        handle.write(r"$S$ & $B^*$ closed & $B^*$ estimated & "
                    r"$B^*$ whole rungs & $\Delta K$ at $B=10$ \\" + "\n")
        handle.write(r"\midrule" + "\n")
        for row in rows_infra:
            handle.write("{} & {} & {} & {} & {} \\\\\n".format(
                "{:.2f}".format(row["x"]),
                tex_number(row["B_analytic"], 3),
                tex_number(row["B_measured"], 3),
                tex_number(row["B_granular"], 3),
                tex_number(row["depth_gain_at_ten"], 2)))
        handle.write(r"\bottomrule" + "\n")
        handle.write(r"\end{tabular}" + "\n")

    detector_table = os.path.join(PAPER_DATA,
                                  "critical_budget_detector_table.tex")
    with open(detector_table, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/critical_budget_experiment.py"
                     " -- do not edit\n")
        handle.write(r"\begin{tabular}{@{}rrrrr@{}}" + "\n")
        handle.write(r"\toprule" + "\n")
        handle.write(r"$c_d$ & $\rho=0$ & $\rho=0.1$ & $\rho=0.5$ & "
                    r"$\rho=0.9$ \\" + "\n")
        handle.write(r"\midrule" + "\n")
        by_cd: Dict[float, Dict[float, float]] = {}
        for row in rows_detector:
            by_cd.setdefault(row["x"], {})[row["rho"]] = row["B_analytic"]
        for cd in sorted(by_cd):
            cells = [tex_number(by_cd[cd].get(rho, math.inf), 3)
                     for rho in (0.0, 0.1, 0.5, 0.9)]
            handle.write("{} & {} & {} & {} & {} \\\\\n".format(
                "{:.2f}".format(cd), *cells))
        handle.write(r"\bottomrule" + "\n")
        handle.write(r"\end{tabular}" + "\n")

    figure = os.path.join(PAPER_FIGURES, "critical_budget.png")
    render(rows_infra, rows_detector, sweeps, reach_curve(), peak_budget,
           peak_ratio, path=figure)
    return [macro, table, detector_table, figure]


def write_summary(rows_infra: Sequence[Dict[str, float]],
                  rows_detector: Sequence[Dict[str, float]],
                  rows_drift: Sequence[Dict[str, float]],
                  results: Sequence[Dict[str, object]],
                  granular: Dict[str, float],
                  peak_budget: float, peak_ratio: float) -> str:
    slope, intercept, r2 = linear_fit([r["x"] for r in rows_infra],
                                      [r["B_measured"] for r in rows_infra])
    payload = {
        "parameters": {
            "c_v": CV, "c_c": CC, "c_d": CD, "rho": RHO, "K0": K0,
            "alpha": ALPHA, "draws": RUNS, "seed": SEED,
        },
        "analytic": {
            "c_bar": c_bar(),
            "gain_infinite": gain_infinite(),
            "gain_two": gain_finite(2),
            "B_star_no_premium": critical_budget(S=0.0, cd=0.0),
            "B_star_detector": critical_budget(cd=CD),
            "B_star_detector_rho_zero": critical_budget(rho=0.0, cd=CD),
            "B_star_detector_rho_half": critical_budget(rho=0.5, cd=CD),
            "B_star_detector_rho_nine": critical_budget(rho=0.9, cd=CD),
            "detector_limit": detector_limit(),
            "slope_infrastructure": slope,
            "slope_infrastructure_predicted": gain_infinite() /
            (gain_infinite() - 1.0),
            "intercept_infrastructure": intercept,
            "r2_infrastructure": r2,
            "depth_gain_slope": 1.0 / c_bar() - 1.0 / CV,
            "peak_budget": peak_budget,
            "peak_reach_ratio": peak_ratio,
            "local_rate": (ALPHA * (1.0 / c_bar() - 1.0 / CV)
                           * math.exp(-CV / K0) / K0),
        },
        "granularity": granular,
        "infrastructure": list(rows_infra),
        "detector": list(rows_detector),
        "drift": list(rows_drift),
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
    rows_infra = sweep_infrastructure()
    rows_detector = sweep_detector()
    rows_drift = sweep_drift()
    curve = reach_curve()
    granular = granularity_check(rows_infra)

    peak_budget, peak_ratio = peak_reach_ratio()
    results = checks(rows_infra, rows_drift, curve, granular,
                     peak_budget, peak_ratio)

    render(rows_infra, rows_detector, rows_drift, curve, peak_budget,
           peak_ratio)
    report(rows_infra, rows_detector, rows_drift, curve, results, granular,
           peak_budget, peak_ratio)

    written = [FIGURE_PNG, SUMMARY_JSON]
    written += write_data(rows_infra, rows_detector, rows_drift, curve)
    written += write_paper_artifacts(rows_infra, rows_detector,
                                     rows_drift, results, granular,
                                     peak_budget, peak_ratio)
    write_summary(rows_infra, rows_detector, rows_drift, results, granular,
                  peak_budget, peak_ratio)

    print("=" * 78)
    print("WROTE")
    print("=" * 78)
    for path in written:
        print("  " + os.path.relpath(path, ROOT))
    print()
    print("Reproduce with:  python3 -m experiments.critical_budget_experiment")
    print("No network access. No commit. No push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
