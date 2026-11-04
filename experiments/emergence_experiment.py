"""Does AI "emergence" reduce to the fractal interface? A test, not a tribute.

The hypothesis under test, stated as sharply as it can be stated:

    As parameters and compute grow, a model overcomes the attenuation of the
    semantic medium, attains the narrow condition, and its connection span
    (Reach) explodes exponentially. Emergence is therefore the physical
    expression of a fractal interface being crossed, and the point at which it
    happens is the point at which per-boundary fidelity crosses a whole number
    of nines.

Four sub-claims hide in that sentence, and this script tests them separately
because they do not stand or fall together:

.. code-block:: text

    C1  a scaling law maps scale -> fidelity, monotonically          holds
    C2  Reach obeys Reach = A0 (1-D)^-k with D driven by scale       holds, vacuously
    C3  there is a *critical* scale, i.e. a slope discontinuity      holds for a step
                                                                     metric only
    C4  the critical scale is where p = -log10(1-F) crosses 2        false, by 1800x

The headline, stated before the evidence so that a reader can discount it:
**the 20B emergence point is real and reproducible, and it is not caused by
fidelity crossing 99%.** At 20B the per-boundary fidelity is 0.88 -- 0.92 nines,
not 2. The 2-nine point is at 3.5e13 parameters, some 1800x further out. What
does happen at 20B is that a five- to six-step task first clears a 50% bar, and
that number is a property of the benchmark, not of the boundary.

Why this is written this way
----------------------------
The house rule in this package is that a simulation may report a hypothesis as
rejected (``run_experiments.py`` does exactly that for the ``D=0.999`` guess).
A script that generated a sigmoid and then declared "emergence proven" would be
worth less than no script, because it would be unfalsifiable. So the data comes
from a published scaling law, the metric is explicit, and every number the
console prints is computed rather than chosen -- including the numbers that
contradict the brief.

The scaling laws
----------------
``Chinchilla`` (Hoffmann et al., *Training Compute-Optimal Large Language
Models*, NeurIPS 2022), eq. 4 and Table A3::

    L(N, D) = E + A/N**alpha + B/D**beta
    E = 1.69,  A = 406.4,  alpha = 0.34,  B = 410.7,  beta = 0.28

``Kaplan`` (Kaplan et al., *Scaling Laws for Neural Language Models*, 2020),
eq. 1.4, the non-embedding loss::

    L(N) = (N_c / N)**alpha_N
    N_c = 8.8e13,  alpha_N = 0.076

These two disagree about the *exponent* by a factor of 4.5 (0.34 against 0.076),
which is a known discrepancy in the literature and not a transcription error
(Besiroglu et al. 2024 re-fit Chinchilla and found the published coefficients do
not reproduce the published optima). Both are carried here, because the
disagreement is the robustness test: whichever exponent you believe, the
conclusion has to survive it.

The loss -> fidelity step
-------------------------
A scaling law gives a loss in nats per token. The interface model needs a
probability that a boundary is crossed. The bridge used here is::

    F(N) = exp( -(L(N) - E) )

and it must be read as a modelling choice, not a derivation. The argument for
it: ``exp(-L)`` is the geometric-mean probability the model assigns to the
correct token, and the irreducible floor ``E`` is a property of the corpus that
no amount of scale spends on any particular boundary -- only the *reducible*
part measures model-specific competence. So the reducible loss is a log-odds
against crossing, and fidelity is its exponential.

The argument against it, which belongs in the same paragraph: real benchmarks do
not report a per-token probability, and ``exp(-L)`` is not top-1 accuracy. This
bridge is the softest joint in the chain, and every quantitative conclusion
below is stated with the bridge in view rather than downstream of it.

What is exact, and what is not
------------------------------
Two of the reported fits have ``R**2 = 1`` to machine precision. **That is not
evidence.** Reach here is a function of ``N`` and the narrowing coordinate ``u``
is a function of ``N``; both are affine in ``ln N``, so the regression is an
algebraic identity: any monotone loss law gives ``R**2 = 1``. The script proves
this by fitting Kaplan's law -- exponent 0.076 against 0.34 -- and getting
``R**2 = 1`` again, with a different ``k``.

The falsifiable content is therefore in ``k``, and the useful statement is the
scale-free invariant ``k / P0 = alpha * ln 10``, which holds to sixteen digits
and does not depend on the saturation constant. The number that makes the
mapping *die* if it is wrong is the implied selectivity ``s = exp(-k/P0)``: the
Chinchilla exponent is observationally equivalent to a narrow condition
satisfied at 46% of sites. A direct measurement of that selectivity elsewhere,
disagreeing, would refute the mapping. A good fit would not confirm it.

The same caution applies in the other direction, which is easier to miss: the
exponential-in-``D`` fit fails at ``R**2 = 0.897``, and that number is *also*
not evidence against the exponential. It comes out identical under both scaling
laws, because it measures the curvature of the saturation map ``D(N)`` and
nothing else. Neither fit is a test of anything. Only ``k`` is.

The one genuinely new result here
---------------------------------
The family of depth-``m`` accuracy curves satisfies, exactly,

    A_m(N) = A_1( N / m**(1/alpha) )

so composing boundaries does **not** sharpen the transition. It translates it.
Every depth shows the same 10-90 width, 3.94 decades, and the same slope at the
50% point, 0.271 per decade. The apparent sharpening that the emergence
literature reports is therefore not in the model at all under this bridge: it is
a sampling artefact. Model sizes are chosen at roughly decade spacing, the
transition is four decades wide, so four points land inside it and a smooth
sigmoid reads as a step. That is a falsifiable prediction (deeper tasks should
show the *same* width, not a narrower one) and it is tested here.

Run::

    python3 -m experiments.emergence_experiment
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:  # numpy / scipy for regressions and the figure; the algebra is stdlib.
    import numpy as np
    from scipy import stats
except ImportError as exc:  # pragma: no cover
    raise SystemExit("this script needs numpy and scipy: {}".format(exc))

from . import model  # noqa: E402
from . import parameters as P  # noqa: E402
from . import topology  # noqa: E402


FIGURE_PNG = os.path.join(HERE, "emergence_figure.png")
DATA_CSV = os.path.join(HERE, "emergence_data.csv")
SUMMARY_JSON = os.path.join(HERE, "emergence_summary.json")
PAPER_DATA = os.path.join(HERE, "paper", "data")
PAPER_FIGURE = os.path.join(HERE, "paper", "figures", "emergence.png")


# ---------------------------------------------------------------------------
# 1. the scaling laws
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScalingLaw:
    """A cited loss law, plus the provenance a referee will ask for."""

    name: str
    source: str
    irreducible: float
    coefficient: float
    exponent: float

    def loss(self, params: float) -> float:
        """Nats per token at ``params`` parameters (data term folded in)."""
        if params <= 0:
            raise ValueError("params must be positive, got {!r}".format(params))
        return self.irreducible + self.reducible(params)

    def reducible(self, params: float) -> float:
        """The part of the loss that scale can actually remove."""
        if params <= 0:
            raise ValueError("params must be positive, got {!r}".format(params))
        return self.coefficient * params ** -self.exponent

    def __post_init__(self) -> None:
        if not 0.0 < self.exponent < 1.0:
            raise ValueError(
                "exponent {!r} is outside (0, 1); a law that falls faster than "
                "1/N is not a scaling law".format(self.exponent)
            )
        if self.coefficient <= 0.0:
            raise ValueError("coefficient must be positive")


#: Hoffmann et al. 2022, eq. 4 with the Table A3 coefficients. The data term
#: ``B/D**beta`` is folded into the parameter term by evaluating on the
#: compute-optimal frontier, where ``N`` and ``D`` co-scale with nearly the same
#: exponent (Approach 3: a = 0.49, b = 0.51), so a one-term law in ``N`` is the
#: right reduction and ``alpha`` absorbs the data axis.
CHINCHILLA = ScalingLaw(
    name="Chinchilla",
    source="Hoffmann et al., NeurIPS 2022, eq. 4 + Table A3 "
           "(E=1.69, A=406.4, alpha=0.34)",
    irreducible=1.69,
    coefficient=406.4,
    exponent=0.34,
)

#: Kaplan et al. 2020, eq. 1.4. No irreducible term in the published form, so
#: the floor is zero and the whole loss counts as reducible. That makes this law
#: the *pessimistic* bound on fidelity: with no floor to subtract, the
#: per-boundary probability starts lower and climbs more slowly.
KAPLAN = ScalingLaw(
    name="Kaplan",
    source="Kaplan et al. 2020, eq. 1.4 (N_c=8.8e13, alpha_N=0.076)",
    irreducible=0.0,
    coefficient=8.8e13 ** 0.076,
    exponent=0.076,
)

LAWS: Tuple[ScalingLaw, ...] = (CHINCHILLA, KAPLAN)


# ---------------------------------------------------------------------------
# 2. scale -> effort -> difficulty -> fidelity
# ---------------------------------------------------------------------------

#: The reference model below which the narrowing budget is defined to be zero.
#: A *policy* constant, in this package's vocabulary: 10M parameters is the
#: smallest model in the Chinchilla sweep, and calling its narrowing zero is a
#: choice about where to put the origin, not a fact about 10M-parameter models.
SCALE_FLOOR = 1.0e7

#: Probability at which a task counts as passed. 0.5 is the conventional place
#: to put an emergence threshold, and it is the value used throughout.
CRITERION = 0.5


def effort_decades(params: float, floor: float = SCALE_FLOOR) -> float:
    """Scale as decades above the reference: the interface model's ``P``."""
    if params <= 0:
        raise ValueError("params must be positive, got {!r}".format(params))
    return math.log10(params / floor)


def semantic_difficulty(params: float, floor: float = SCALE_FLOOR,
                        p0: float = P.P0) -> float:
    """``D = 1 - exp(-P/P0)``: the model's own saturation map (eq. 1).

    Reused rather than reinvented so that the LLM experiment and the quantum one
    inhabit the same coordinate system. ``P0`` is the paper's 2.606 decades --
    the scale that puts the twelve-decade coherence gap at ``D=0.99`` -- and
    reusing it here is a declared borrowing, not a fit.

    ``P <= 0`` returns exactly 0.0 rather than raising: at or below the
    reference scale there is no narrowing budget left to spend, and ``D = 0`` is
    the correct limit of ``1 - exp(-P/P0)``, not an error.
    """
    budget = effort_decades(params, floor)
    if budget <= 0.0:
        return 0.0
    return model.difficulty_from_potential(budget, p0)


def per_boundary_fidelity(loss: float, floor: float) -> float:
    """``F = exp(-(L - E))``: the bridge, stated as a choice.

    Returns a number in ``(0, 1]``. A loss at the floor gives ``F = 1`` exactly:
    a model that has removed all reducible loss has a transparent interface.
    That is the definition of the asymptote, not a measurement of one.
    """
    return math.exp(-(loss - floor))


def resistance(fidelity: float) -> float:
    """``R = 1 - F``. The interface model's quantity, computed not assumed."""
    return 1.0 - fidelity


def nines(value: float) -> float:
    """``p = -log10 R``: fidelity counted in decades of remaining failure."""
    return topology.nines(value)


def depth_reach(fidelity: float, criterion: float = CRITERION) -> float:
    """Deepest composed task the interface completes at the criterion.

    A task of depth ``m`` needs ``m`` nested boundaries all crossed, so success
    is ``F**m``. Solving ``F**m = criterion`` for ``m``::

        Reach = ln(criterion) / ln(F)

    Not a fitted curve -- the definition of reach in this framework, evaluated.
    Limits: ``F -> 0`` gives ``Reach -> 0`` (nothing composes out of a boundary
    that never holds); ``F -> 1`` diverges (a transparent interface has no depth
    limit). Both are the right behaviour, which is why the function is written
    as the definition rather than as a fit to a shape.
    """
    if fidelity <= 0.0:
        raise ValueError("fidelity must be positive, got {!r}".format(fidelity))
    if fidelity >= 1.0:
        return float("inf")
    return math.log(criterion) / math.log(fidelity)


def task_accuracy(fidelity: float, depth: int) -> float:
    """``A_m = F**m``: all-or-nothing success on an ``m``-deep task."""
    if depth < 1:
        raise ValueError("depth must be at least 1, got {!r}".format(depth))
    return fidelity ** depth


def emergence_scale(law: ScalingLaw, depth: int,
                    criterion: float = CRITERION) -> float:
    """Parameters at which the ``depth``-deep task first reaches the criterion.

    Closed form. With ``x = L - E = A N**-alpha``::

        (e**-x)**depth = criterion   =>   N = (A * depth / -ln criterion)**(1/alpha)

    Exact, parameter-free, and monotone in ``depth`` -- which is the whole
    problem with calling any one of these points "*the*" emergence point.
    """
    if depth < 1:
        raise ValueError("depth must be at least 1, got {!r}".format(depth))
    if not 0.0 < criterion < 1.0:
        raise ValueError("criterion must be in (0, 1), got {!r}".format(criterion))
    target = -math.log(criterion) / depth
    return (law.coefficient / target) ** (1.0 / law.exponent)


def scale_for_nines(law: ScalingLaw, target_nines: float) -> float:
    """Parameters at which per-boundary resistance first reaches ``10**-p``.

    Separate from :func:`emergence_scale` on purpose. That one asks when a *task*
    passes; this one asks when the *boundary* is 99% transparent. The script's
    central result is that these two answers differ by ~1800x.
    """
    if target_nines <= 0.0:
        raise ValueError("target nines must be positive, got {!r}".format(target_nines))
    target_resistance = 10.0 ** -target_nines
    if target_resistance >= 1.0:
        raise ValueError("target resistance must be below 1")
    needed = -math.log(1.0 - target_resistance)
    return (law.coefficient / needed) ** (1.0 / law.exponent)


# ---------------------------------------------------------------------------
# 3. fitting the fractal law
# ---------------------------------------------------------------------------


def _linear_fit(x: Sequence[float], y: Sequence[float]) -> Dict[str, float]:
    """Least squares on centred data, for the reason documented in Experiment E.

    At ``D ~ 0.93`` the narrowing coordinate ranges over 0.4 to 2.7 while the
    intercept is order 1, so an uncentred design matrix has columns differing by
    a factor of a few and the solver's cutoff is closer to the data than it
    looks. Centring costs one subtraction and removes the question. ``R**2`` is
    invariant to it.
    """
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    xc = xa - xa.mean()
    yc = ya - ya.mean()
    denominator = float(np.dot(xc, xc))
    if denominator <= 0.0:
        raise ValueError("regressor has zero variance; nothing to fit")
    slope = float(np.dot(xc, yc) / denominator)
    intercept = float(ya.mean() - slope * xa.mean())
    predicted = slope * xa + intercept
    ss_res = float(np.sum((ya - predicted) ** 2))
    ss_tot = float(np.sum((ya - ya.mean()) ** 2))
    dof = max(len(xa) - 2, 1)
    return {
        "slope": slope,
        "intercept": intercept,
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0,
        "stderr": math.sqrt((ss_res / dof) / denominator),
        "max_residual": float(np.max(np.abs(ya - predicted))),
    }


def fit_power_law(difficulties: Sequence[float],
                  reach: Sequence[float]) -> Dict[str, float]:
    """Fit ``Reach = A0 (1-D)**-k`` on the narrowing coordinate.

    The regressor is ``u = -ln(1-D)`` and the response is ``ln Reach``, so the
    fitted slope *is* ``k`` with no conversion. Fitting instead on ``ln(1-D)``
    gives ``-k`` and someone always forgets the sign; doing it this way makes the
    printed number the number in the paper.
    """
    u = [-math.log(1.0 - d) for d in difficulties]
    y = [math.log(r) for r in reach]
    fit = _linear_fit(u, y)
    fit["a0"] = math.exp(fit["intercept"])
    return fit


def fit_exponential_in_d(difficulties: Sequence[float],
                         reach: Sequence[float]) -> Dict[str, float]:
    """Fit ``Reach = A0 exp(alpha D)``. The form the brief asked for."""
    y = [math.log(r) for r in reach]
    fit = _linear_fit(list(difficulties), y)
    fit["a0"] = math.exp(fit["intercept"])
    return fit


#: The low and high accuracy levels whose parameter ratio defines the apparent
#: width of a transition. 0.1 and 0.9 are the conventional decade/90% pair.
WIDTH_LEVELS = (0.1, 0.9)


def transition_width(law: ScalingLaw, low: float = WIDTH_LEVELS[0],
                     high: float = WIDTH_LEVELS[1]) -> float:
    """Decades of parameters over which *any* depth-``m`` task goes low -> high.

    Independent of ``m``, and that is the result. Derivation: at depth ``m`` the
    crossing is ``N = (m*A/x)**(1/alpha)`` for ``x = -ln(level)``, so ``m``
    cancels from the ratio ``N(high)/N(low)``::

        W = ln( ln(1/low) / ln(1/high) ) / (alpha * ln 10)

    At Chinchilla's ``alpha = 0.34`` this is 3.94 decades. A transition that wide
    cannot look like a step unless you sample it coarsely, which is the section
    below.
    """
    if not 0.0 < low < high < 1.0:
        raise ValueError("need 0 < low < high < 1")
    return math.log(math.log(1.0 / low) / math.log(1.0 / high)) / (
        law.exponent * math.log(10.0)
    )


def slope_at_criterion(law: ScalingLaw,
                       criterion: float = CRITERION) -> float:
    """``dA_m / d log10 N`` at the 50% point. Also independent of ``m``.

    ``ln A_m = -m*A*N**-alpha`` so ``d ln A_m/d ln N = alpha * (-ln criterion)``
    on the crossing, and multiplying by ``criterion * ln 10`` gives the slope in
    accuracy per decade. At ``alpha = 0.34``: 0.271 per decade. A "phase
    transition" with a slope of a quarter per decade is a gentle ramp.
    """
    return criterion * math.log(10.0) * law.exponent * math.log(1.0 / criterion)


def points_in_transition(width: float, spacing_decades: float) -> float:
    """How many model sizes land inside a ``width``-decade transition.

    The mirage in one line. At ten-per-decade spacing and a four-decade-wide
    transition you resolve four points across the whole rise, connect them with
    straight lines, and publish a step.
    """
    if spacing_decades <= 0.0:
        raise ValueError("spacing must be positive, got {!r}".format(spacing_decades))
    return width / spacing_decades


# ---------------------------------------------------------------------------
# 4. the analysis
# ---------------------------------------------------------------------------

#: The scale the brief nominates, and the one the console has to answer about
#: directly rather than in a footnote.
NOMINAL_CRITICAL = 2.0e10

#: Parameter sweep. The lower bound is where the Chinchilla fit is still a fit
#: (the paper's own smallest runs are ~70M); the upper bound is one decade past
#: the largest published model, so the 2-nine crossing is inside the frame
#: rather than an extrapolation the reader has to take on trust.
SWEEP = (1.0e8, 1.0e15)

#: Spacing at which frontier models are actually trained, in decades. A run at
#: 1B, 10B, 100B is the usual cadence; 0.5 covers the denser "3x" families.
TYPICAL_SPACINGS = (1.0, 0.5)


def analyse(law: ScalingLaw = CHINCHILLA) -> Dict[str, object]:
    """Everything numeric, no I/O. Tests call this; ``main`` renders it."""
    lo, hi = SWEEP
    grid = [lo * (hi / lo) ** (i / 400.0) for i in range(401)]

    rows: List[Dict[str, float]] = []
    for n in grid:
        loss = law.loss(n)
        fidelity = per_boundary_fidelity(loss, law.irreducible)
        rows.append({
            "params": n,
            "log10_params": math.log10(n),
            "loss": loss,
            "fidelity": fidelity,
            "resistance": resistance(fidelity),
            "nines": nines(resistance(fidelity)),
            "difficulty": semantic_difficulty(n),
            "reach": depth_reach(fidelity),
        })

    difficulties = [r["difficulty"] for r in rows]
    reach = [r["reach"] for r in rows]
    power = fit_power_law(difficulties, reach)
    exponential = fit_exponential_in_d(difficulties, reach)

    # --- the nines test: where does p = 1, 2, 3 actually fall? -------------
    #
    # Solved, not searched. A grid search reports the grid spacing as the
    # answer, which is the mistake ``_d50`` in run_experiments.py exists to
    # avoid.
    crossings = {}
    for target in (1.0, 2.0, 3.0):
        n_star = scale_for_nines(law, target)
        f_star = per_boundary_fidelity(law.loss(n_star), law.irreducible)
        crossings["p={:g}".format(target)] = {
            "params": n_star,
            "log10_params": math.log10(n_star),
            "fidelity": f_star,
            "resistance": resistance(f_star),
            "nines_check": nines(resistance(f_star)),
            "factor_above_nominal": n_star / NOMINAL_CRITICAL,
        }

    at_nominal = {"params": NOMINAL_CRITICAL}
    at_nominal["loss"] = law.loss(NOMINAL_CRITICAL)
    at_nominal["fidelity"] = per_boundary_fidelity(
        at_nominal["loss"], law.irreducible)
    at_nominal["difficulty"] = semantic_difficulty(NOMINAL_CRITICAL)
    at_nominal["resistance"] = resistance(at_nominal["fidelity"])
    at_nominal["nines"] = nines(at_nominal["resistance"])
    at_nominal["reach"] = depth_reach(at_nominal["fidelity"])

    # --- the task-depth family: where each benchmark "emerges" --------------
    #
    # The competing explanation, generated by the same model that generates the
    # fidelity curve. Nothing is added to make it appear.
    width = transition_width(law)
    family = []
    for depth in range(1, 13):
        n_star = emergence_scale(law, depth)
        f_star = per_boundary_fidelity(law.loss(n_star), law.irreducible)
        family.append({
            "depth": depth,
            "params": n_star,
            "log10_params": math.log10(n_star),
            "fidelity_at_crossing": f_star,
            "nines_at_crossing": nines(resistance(f_star)),
            # Predictions that do NOT depend on depth. Reported once per row so
            # that the invariance is visible in the table rather than asserted.
            "transition_width_decades": width,
            "slope_per_decade": slope_at_criterion(law),
        })

    # Which depth is the one that "emerges at 20B"? Reported because a reader
    # who has been told 20B is *the* threshold deserves to see that 20B is just
    # the depth their benchmark happened to have.
    nearest = min(family, key=lambda r: abs(math.log10(r["params"])
                                           - math.log10(NOMINAL_CRITICAL)))

    # --- the collapse, checked rather than claimed -------------------------
    #
    # A_m(N) == A_1(N / m**(1/alpha)) exactly. If this ever fails, the two
    # invariants above stop being true and the paper's C3 statement is wrong.
    collapse = []
    for depth in (2, 4, 8):
        for probe in (1.0e10, 1.0e11, 1.0e12):
            direct = task_accuracy(
                per_boundary_fidelity(law.loss(probe), law.irreducible), depth)
            shifted = task_accuracy(
                per_boundary_fidelity(
                    law.loss(probe / depth ** (1.0 / law.exponent)),
                    law.irreducible),
                1)
            collapse.append({
                "depth": depth,
                "params": probe,
                "direct": direct,
                "shifted": shifted,
                "abs_difference": abs(direct - shifted),
            })
    worst_collapse = max(c["abs_difference"] for c in collapse)

    # --- the invariant, and the number that could kill the mapping ----------
    k_over_p0 = power["slope"] / P.P0
    invariant = {
        "k_over_p0": k_over_p0,
        "alpha_ln10": law.exponent * math.log(10.0),
        "relative_difference": abs(k_over_p0 - law.exponent * math.log(10.0))
        / (law.exponent * math.log(10.0)),
        "implied_selectivity": math.exp(-k_over_p0),
        "interpretation": (
            "k/P0 = alpha*ln10 to machine precision; the mapping has exactly "
            "one free number, the implied selectivity s = exp(-k/P0)"
        ),
    }

    return {
        "law": {
            "name": law.name,
            "source": law.source,
            "irreducible": law.irreducible,
            "exponent": law.exponent,
        },
        "rows": rows,
        "power_law": power,
        "exponential_in_d": exponential,
        "crossings": crossings,
        "at_nominal": at_nominal,
        "family": family,
        "nearest_depth_at_nominal": nearest,
        "transition": {
            "width_decades": width,
            "slope_per_decade": slope_at_criterion(law),
            "points_at_spacing": {
                "{:g}".format(s): points_in_transition(width, s)
                for s in TYPICAL_SPACINGS
            },
            "depth_invariant": bool(worst_collapse < 1.0e-12),
            "worst_collapse_error": worst_collapse,
        },
        "invariant": invariant,
        "sweep": {"low": lo, "high": hi, "n": len(grid)},
    }


def robustness() -> Dict[str, object]:
    """Refit under both published laws. The conclusion has to survive both.

    The two exponents differ by 4.5x, so a claim that depends on which paper you
    cite is not a claim about the world. What survives: the power-law ``R**2``
    is 1 under both (and is therefore worthless as evidence), while ``k`` and
    the 2-nine scale move by orders of magnitude (and are therefore the only
    quantities carrying information).
    """
    out: Dict[str, object] = {}
    for law in LAWS:
        result = analyse(law)
        out[law.name] = {
            "exponent": law.exponent,
            "k": result["power_law"]["slope"],
            "r2_power": result["power_law"]["r2"],
            "r2_exponential": result["exponential_in_d"]["r2"],
            "implied_selectivity": result["invariant"]["implied_selectivity"],
            "p2_params": result["crossings"]["p=2"]["params"],
            "p_at_20B": result["at_nominal"]["nines"],
            "width_decades": result["transition"]["width_decades"],
        }
    return out


# ---------------------------------------------------------------------------
# 5. rendering
# ---------------------------------------------------------------------------

INK = "#1f2d3d"
ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"


def _style() -> None:
    """A figure that reads at 100% zoom and prints without colour."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 8.5,
        "axes.labelsize": 8.5,
        "axes.titlesize": 9,
        "legend.fontsize": 7.2,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "axes.linewidth": 0.7,
        "grid.linewidth": 0.4,
        "grid.alpha": 0.35,
        "savefig.dpi": 220,
        "figure.dpi": 110,
    })


def render(result: Dict[str, object] = None, path: str = FIGURE_PNG,
           law: ScalingLaw = CHINCHILLA) -> str:
    """The one figure. Four panels, each carrying one claim."""
    _style()
    import matplotlib.pyplot as plt

    if result is None:
        result = analyse(law)

    rows = result["rows"]
    n = np.array([r["params"] for r in rows])
    loss = np.array([r["loss"] for r in rows])
    fid = np.array([r["fidelity"] for r in rows])
    p = np.array([r["nines"] for r in rows])
    reach = np.array([r["reach"] for r in rows])
    difficulty = np.array([r["difficulty"] for r in rows])
    alpha = result["law"]["exponent"]

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.6), constrained_layout=True)

    # --- (a) the scaling law and the fidelity it implies -------------------
    ax = axes[0, 0]
    ax.plot(n, loss, color=INK, lw=1.5)
    ax.axhline(result["law"]["irreducible"], color=MUTED, ls=":", lw=1.0)
    ax.set_xscale("log")
    ax.set_xlim(n[0], n[-1])
    ax.set_xlabel("parameters $N$")
    ax.set_ylabel(r"loss $L(N)$  [nats/token]")
    ax.set_title(r"(a) Chinchilla fit, and the fidelity bridge")
    ax.grid(True, which="both", axis="x")
    ax2 = ax.twinx()
    ax2.plot(n, fid, color=ACCENT, lw=1.5)
    ax2.set_ylabel(r"$F=e^{-(L-E)}$", color=ACCENT)
    ax2.tick_params(axis="y", colors=ACCENT)
    ax2.set_ylim(0.0, 1.02)
    ax.axvline(NOMINAL_CRITICAL, color=COOL, ls="--", lw=0.9)
    ax.annotate("20B", xy=(NOMINAL_CRITICAL, loss[-1]),
                xytext=(5, 6), textcoords="offset points",
                color=COOL, fontsize=7)

    # --- (b) the nines curve: where p crosses 1, 2, 3 ---------------------
    ax = axes[0, 1]
    ax.plot(n, p, color=INK, lw=1.5)
    for level, label in ((1.0, r"$p=1$  (90%)"), (2.0, r"$p=2$  (99%)")):
        ax.axhline(level, color=MUTED, ls=":", lw=0.9)
        ax.text(0.015, level + 0.06, label, transform=ax.get_yaxis_transform(),
                color=MUTED, fontsize=7)
    ax.plot([NOMINAL_CRITICAL], [result["at_nominal"]["nines"]],
            marker="o", ms=5, color=COOL, zorder=5)
    ax.annotate("20B: $p={:.2f}$".format(result["at_nominal"]["nines"]),
                xy=(NOMINAL_CRITICAL, result["at_nominal"]["nines"]),
                xytext=(6, -16), textcoords="offset points",
                color=COOL, fontsize=7)
    p2 = result["crossings"]["p=2"]["params"]
    ax.plot([p2], [2.0], marker="v", ms=6, color=ACCENT, zorder=5)
    ax.annotate("$p=2$ at $10^{{{:.1f}}}$\n$={:.0f}\\times$ 20B".format(
        math.log10(p2), result["crossings"]["p=2"]["factor_above_nominal"]),
        xy=(p2, 2.0), xytext=(-8, 10), textcoords="offset points",
        ha="right", color=ACCENT, fontsize=7)
    ax.set_xscale("log")
    ax.set_xlim(n[0], n[-1])
    ax.set_xlabel("parameters $N$")
    ax.set_ylabel(r"nines $p=-\log_{10}(1-F)$")
    ax.set_title(r"(b) the 2-nine point is 1800$\times$ past 20B")
    ax.set_ylim(0, 3.2)
    ax.grid(True, which="both", axis="x")

    # --- (c) the strobe: a four-decade ramp sampled at decade spacing -----
    ax = axes[1, 0]
    depth = 8
    ax.plot(n, fid ** depth, color=INK, lw=1.4,
            label=r"smooth ($m={}$)".format(depth))
    # The "measured" points: frontier models live a decade apart.
    sampled = [10.0 ** e for e in range(8, 16)]
    sampled_vals = [
        task_accuracy(
            per_boundary_fidelity(law.loss(s), law.irreducible), depth)
        for s in sampled
    ]
    ax.plot(sampled, sampled_vals, color=ACCENT, lw=1.2, ls="--",
            marker="o", ms=4.5, label="sampled at decade spacing")
    ax.axhline(CRITERION, color=MUTED, ls=":", lw=0.9)
    ax.axvline(NOMINAL_CRITICAL, color=COOL, ls="--", lw=0.9)
    ax.set_xscale("log")
    ax.set_xlim(n[0], n[-1])
    ax.set_ylim(0, 1.0)
    ax.set_xlabel("parameters $N$")
    ax.set_ylabel(r"task accuracy $A_{m}=F^{m}$")
    ax.set_title("(c) a 3.94-decade ramp read at 1-decade spacing")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(True, which="both", axis="x")
    ax.text(0.97, 0.08,
            "width:  {:.2f} decades\nslope:  {:.3f} / decade\n"
            "points: {:.1f} per transition".format(
                result["transition"]["width_decades"],
                result["transition"]["slope_per_decade"],
                result["transition"]["points_at_spacing"]["1"]),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7,
            color=INK,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#d0d4da", lw=0.6))

    # --- (d) the fractal fit, and the exponential that fits worse ---------
    ax = axes[1, 1]
    u = -np.log(1.0 - difficulty)
    ax.plot(u, reach, color=INK, lw=3.0, alpha=0.28, label="model (exact)")
    k = result["power_law"]["slope"]
    a0 = result["power_law"]["a0"]
    ax.plot(u, a0 * np.exp(k * u), color=ACCENT, ls="--", lw=1.1,
            label=r"power in $u$:  $k={:.3f}$".format(k))
    beta = result["exponential_in_d"]["slope"]
    ax.plot(u, result["exponential_in_d"]["a0"] * np.exp(beta * difficulty),
            color=COOL, ls=":", lw=1.3,
            label=r"exp in $D$:  $\alpha={:.2f}$".format(beta))
    ax.set_yscale("log")
    ax.set_xlabel(r"narrowing $u=-\ln(1-D)$")
    ax.set_ylabel("reach (composed depth)")
    ax.set_title(r"(d) exact in $u$, convex in $D$")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(True, which="both")
    ax.text(0.97, 0.06,
            "$R^2_{{u}}={:.9f}$\n$R^2_{{D}}={:.4f}$".format(
                result["power_law"]["r2"],
                result["exponential_in_d"]["r2"]),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7,
            color=INK,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#d0d4da", lw=0.6))

    # No suptitle: in the paper the caption carries the title, and a heading
    # repeated inside the artwork is the kind of duplication that looks like a
    # figure pasted from a report rather than drawn for the page.
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 6. reports
# ---------------------------------------------------------------------------


def write_data(result: Dict[str, object]) -> None:
    with open(DATA_CSV, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["params", "log10_params", "loss", "fidelity",
                         "resistance", "nines", "difficulty", "reach"])
        for r in result["rows"]:
            writer.writerow([
                "{:.10g}".format(r["params"]),
                "{:.6f}".format(r["log10_params"]),
                "{:.8f}".format(r["loss"]),
                "{:.8f}".format(r["fidelity"]),
                "{:.8f}".format(r["resistance"]),
                "{:.8f}".format(r["nines"]),
                "{:.8f}".format(r["difficulty"]),
                "{:.8f}".format(r["reach"]),
            ])

    summary = {
        "law": result["law"],
        "power_law": result["power_law"],
        "exponential_in_d": result["exponential_in_d"],
        "crossings": result["crossings"],
        "at_nominal": result["at_nominal"],
        "family": result["family"],
        "nearest_depth_at_nominal": result["nearest_depth_at_nominal"],
        "transition": result["transition"],
        "invariant": result["invariant"],
        "robustness": robustness(),
    }
    with open(SUMMARY_JSON, "w") as handle:
        json.dump(summary, handle, indent=2, default=str)


def _sci(value: float, digits: int = 2) -> str:
    """``3.5e13``: a siunitx-parseable exponent with no sign noise.

    ``"{:.2e}".format`` emits ``3.54e+13``. ``\\num{3.54e+13}`` happens to parse,
    but the ``+`` is an implementation detail of Python's formatter leaking into
    a document, and one siunitx version taking offence would be one too many.
    """
    exponent = int(math.floor(math.log10(abs(value))))
    mantissa = value / 10.0 ** exponent
    if round(mantissa, digits) >= 10.0:
        exponent += 1
        mantissa = value / 10.0 ** exponent
    return "{:.{}f}e{}".format(mantissa, digits, exponent)


def write_paper_data(result: Dict[str, object],
                     figure: Optional[str] = None) -> None:
    """The section's numbers as LaTeX macros, and its figure.

    Same rule as ``run_experiments.write_topology_data``: a number that appears
    in the body is generated, never typed. Two consequences worth stating,
    because both have already caught an error in this package:

    * the macros are flat, alphanumeric names (``\\emWidth``), because TeX reads
      ``\\emWidth-3.9`` as a macro followed by text;
    * scientific notation is emitted as ``3.5e13`` and wrapped in ``\\num{}`` at
      the call site, so siunitx owns the typography and the data owns the digits.
    """
    os.makedirs(PAPER_DATA, exist_ok=True)

    def path(name: str) -> str:
        return os.path.join(PAPER_DATA, name)

    power = result["power_law"]
    exponential = result["exponential_in_d"]
    at = result["at_nominal"]
    crossings = result["crossings"]
    transition = result["transition"]
    nearest = result["nearest_depth_at_nominal"]
    invariant = result["invariant"]

    def macro(name: str, value: str) -> str:
        return "\\newcommand{\\" + name + "}{" + value + "}"

    macros = ["% generated by experiments/emergence_experiment.py -- do not edit"]
    macros.append(macro("emK", "{:.4f}".format(power["slope"])))
    macros.append(macro("emKstderr", "{:.1e}".format(power["stderr"])))
    macros.append(macro("emAlpha", "{:.4f}".format(exponential["slope"])))
    macros.append(macro("emAlphastderr", "{:.3f}".format(exponential["stderr"])))
    macros.append(macro("emAzero", "{:.4f}".format(power["a0"])))
    macros.append(macro("emRtwoU", "{:.9f}".format(power["r2"])))
    macros.append(macro("emRtwoD", "{:.4f}".format(exponential["r2"])))
    macros.append(macro("emSelectivity", "{:.3f}".format(
        invariant["implied_selectivity"])))
    macros.append(macro("emKoverPzero", "{:.5f}".format(invariant["k_over_p0"])))
    macros.append(macro("emWidth", "{:.2f}".format(transition["width_decades"])))
    macros.append(macro("emSlope", "{:.3f}".format(transition["slope_per_decade"])))
    macros.append(macro("emPointsperdecade", "{:.1f}".format(
        transition["points_at_spacing"]["1"])))
    macros.append(macro("emFidTwenty", "{:.3f}".format(at["fidelity"])))
    macros.append(macro("emResTwenty", "{:.3f}".format(at["resistance"])))
    macros.append(macro("emNinesTwenty", "{:.2f}".format(at["nines"])))
    macros.append(macro("emPtwoParams", _sci(crossings["p=2"]["params"])))
    macros.append(macro("emPtweFactor", "{:.0f}".format(
        crossings["p=2"]["factor_above_nominal"])))
    macros.append(macro("emPoneParams", _sci(crossings["p=1"]["params"])))
    macros.append(macro("emPoneFactor", "{:.1f}".format(
        crossings["p=1"]["factor_above_nominal"])))
    macros.append(macro("emNearestDepth", "{:d}".format(nearest["depth"])))
    macros.append(macro("emNearestScale", "{:.1f}".format(
        nearest["params"] / 1.0e9)))
    macros.append(macro("emCollapseError", "{:.0e}".format(
        transition["worst_collapse_error"])))
    macros.append(macro("emExponentRatio", "{:.1f}".format(
        result["law"]["exponent"] / 0.076)))
    with open(path("emergence_macros.tex"), "w") as handle:
        handle.write("\n".join(macros) + "\n")

    # The depth family. Eight rows is what the argument needs: enough for the
    # invariance of the last two columns to be visible, not so many that the
    # table becomes the section.
    lines = [
        "% generated by experiments/emergence_experiment.py -- do not edit",
        "\\begin{tabular}{@{}rrrrr@{}}",
        "\\toprule",
        "$m$ & $N$ at 50\\% & $F$ there & $p$ there & width (dex) \\\\",
        "\\midrule",
    ]
    for entry in result["family"]:
        if entry["depth"] > 8:
            break
        lines.append(
            "${}$ & ${:.2f}\\times10^{{{}}}$ & ${:.4f}$ & ${:.3f}$ & ${:.2f}$ \\\\"
            .format(
                entry["depth"],
                entry["params"] / 10.0 ** int(math.floor(math.log10(entry["params"]))),
                int(math.floor(math.log10(entry["params"]))),
                entry["fidelity_at_crossing"],
                entry["nines_at_crossing"],
                entry["transition_width_decades"],
            )
        )
    lines += ["\\bottomrule", "\\end{tabular}"]
    with open(path("emergence_table.tex"), "w") as handle:
        handle.write("\n".join(lines) + "\n")

    # The curve behind panel (b), so the figure is reproducible from the data
    # directory alone rather than from the PNG.
    os.makedirs(os.path.join(HERE, "paper", "figures"), exist_ok=True)
    with open(path("emergence.csv"), "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["log10_params", "loss", "fidelity", "resistance",
                         "nines", "reach"])
        for row in result["rows"]:
            writer.writerow([
                "{:.6f}".format(row["log10_params"]),
                "{:.6f}".format(row["loss"]),
                "{:.6f}".format(row["fidelity"]),
                "{:.6f}".format(row["resistance"]),
                "{:.6f}".format(row["nines"]),
                "{:.6f}".format(row["reach"]),
            ])

    if figure is not None and os.path.exists(figure):
        import shutil
        shutil.copyfile(figure, PAPER_FIGURE)


def report(result: Dict[str, object]) -> None:
    """The console output. Ordered as the four sub-claims, not as the code."""
    law = result["law"]
    power = result["power_law"]
    exponential = result["exponential_in_d"]
    at = result["at_nominal"]
    crossings = result["crossings"]
    inv = result["invariant"]
    transition = result["transition"]

    print("=" * 78)
    print("AI EMERGENCE AS A FRACTAL INTERFACE")
    print("=" * 78)
    print("  law        {:<10} {}".format(law["name"], law["source"]))
    print("  sweep      {:.0e} .. {:.0e} parameters".format(
        result["sweep"]["low"], result["sweep"]["high"]))
    print("  criterion  task passes at probability {:.2f}".format(CRITERION))
    print("  bridge     F = exp(-(L - E))   [modelling choice, see docstring]")

    print()
    print("-" * 78)
    print("C1  does scale buy fidelity?        yes, smoothly and slowly")
    print("-" * 78)
    print("  {:<12} {:>9} {:>9} {:>8} {:>10}".format(
        "parameters", "loss", "F", "nines", "reach"))
    for probe in (1e8, 1e9, 2e10, 1e11, 1e12, 1e13, 1e14):
        sample_loss = law["irreducible"] + CHINCHILLA.coefficient * probe ** -CHINCHILLA.exponent
        f = per_boundary_fidelity(sample_loss, law["irreducible"])
        print("  {:<12.0e} {:>9.4f} {:>9.4f} {:>8.3f} {:>10.2f}".format(
            probe, sample_loss, f, nines(resistance(f)), depth_reach(f)))
    print("  fidelity climbs from 0.46 to 0.99 across seven decades: no cliff,")
    print("  no saturation, no special scale.")

    print()
    print("-" * 78)
    print("C2  does the fractal law fit?        yes -- and the fit is not evidence")
    print("-" * 78)
    print("  Reach = A0 (1-D)^-k")
    print("    k            = {:.6f} +/- {:.6f}".format(power["slope"], power["stderr"]))
    print("    A0           = {:.6f}".format(power["a0"]))
    print("    R^2          = {:.10f}".format(power["r2"]))
    print("    max residual = {:.2e}".format(power["max_residual"]))
    print("  Reach = A0 exp(alpha D)")
    print("    alpha        = {:.6f} +/- {:.6f}".format(
        exponential["slope"], exponential["stderr"]))
    print("    R^2          = {:.6f}".format(exponential["r2"]))
    print("  -> the power law fits to machine precision, the exponential does")
    print("     not, reproducing Experiment E on LLM scaling data.")
    print()
    print("  why R^2=1 carries no information: Reach and u are both affine in")
    print("  ln N, so the regression is an identity. Repeated under Kaplan's")
    print("  law, whose exponent is 4.5x smaller:")
    rob = robustness()
    for name, entry in rob.items():
        print("    {:<10} alpha={:.3f}  k={:.4f}  R^2(u)={:.10f}  R^2(D)={:.4f}"
              .format(name, entry["exponent"], entry["k"], entry["r2_power"],
                      entry["r2_exponential"]))
    print("  -> R^2 is 1 under both. Any monotone loss law passes this test.")
    print("     R^2(D) is also identical under both ({:.4f}), because that fit"
          .format(rob["Chinchilla"]["r2_exponential"]))
    print("     depends only on the shape of the saturation map D(N), not on the")
    print("     scaling exponent. So *neither* R^2 is evidence for or against")
    print("     the fractal law on LLM data. The falsifiable content is k, and")
    print("     through it the selectivity:")
    print("       k/P0          = {:.6f}".format(inv["k_over_p0"]))
    print("       alpha ln 10   = {:.6f}".format(inv["alpha_ln10"]))
    print("       relative diff = {:.1e}".format(inv["relative_difference"]))
    print("       implied s     = exp(-k/P0) = {:.4f}".format(
        inv["implied_selectivity"]))
    print("     A measurement of s elsewhere disagreeing with {:.2f} kills the"
          .format(inv["implied_selectivity"]))
    print("     mapping. A good R^2 would not have confirmed it.")

    print()
    print("-" * 78)
    print("C3  is there a critical scale?       only as a step, and it is not at p")
    print("-" * 78)
    print("  {:<6} {:>12} {:>10} {:>8} {:>13}".format(
        "depth", "N at 50%", "F there", "nines", "width (dex)"))
    for entry in result["family"]:
        print("  m={:<4} {:>12.3e} {:>10.4f} {:>8.3f} {:>13.3f}".format(
            entry["depth"], entry["params"], entry["fidelity_at_crossing"],
            entry["nines_at_crossing"], entry["transition_width_decades"]))
    nearest = result["nearest_depth_at_nominal"]
    print("  the depth crossing at 20B is m={} (it crosses at {:.1f}B)".format(
        nearest["depth"], nearest["params"] / 1e9))
    print()
    print("  the width column is the same for every depth, and that is the")
    print("  result: composing boundaries translates the curve, it does not")
    print("  steepen it. Verified algebraically, A_m(N) = A_1(N/m^(1/alpha)):")
    print("    worst collapse error = {:.1e}  (invariant: {})".format(
        transition["worst_collapse_error"], transition["depth_invariant"]))
    print("  transition width {:.3f} decades; slope at 50% {:.3f} per decade."
          .format(transition["width_decades"], transition["slope_per_decade"]))
    print("  So a 50-signal task and a 5-step task differ by a horizontal")
    print("  shift, not by a change of shape. Frontier models are trained")
    print("  roughly every 1 decade, so:")
    for spacing, count in sorted(
            transition["points_at_spacing"].items(), key=lambda kv: -float(kv[0])):
        print("    spacing {:.1f} dex -> {:.1f} models inside the transition"
              .format(float(spacing), count))
    print("  -> four points across a four-decade ramp, joined by straight")
    print("     lines, is a step. That is the mirage, and it is arithmetic.")

    print()
    print("-" * 78)
    print("C4  is the critical scale where p crosses 2?   no, by ~1800x")
    print("-" * 78)
    print("  {:<8} {:>14} {:>12} {:>9} {:>12}".format(
        "level", "N", "F", "nines", "vs 20B"))
    for label, entry in crossings.items():
        print("  {:<8} {:>14.3e} {:>12.6f} {:>9.4f} {:>11.0f}x".format(
            label, entry["params"], entry["fidelity"], entry["nines_check"],
            entry["factor_above_nominal"]))
    print("  at 20B: F={:.4f}  R={:.4f}  p={:.3f}  the story needs p>=2".format(
        at["fidelity"], at["resistance"], at["nines"]))
    print("  -> 20B sits at {:.2f} nines. The '99% per boundary' story needs"
          .format(at["nines"]))
    print("     {:.0f}x more parameters, and is not what happens at 20B."
          .format(crossings["p=2"]["factor_above_nominal"]))
    print()
    print("  the charitable reading: the brief's 20B is within 2x of the p=1")
    print("  crossing ({:.0f}B). One nine, not two. If the intended threshold was"
          .format(crossings["p=1"]["params"] / 1e9))
    print("  90% per boundary, the observation is right and only the number was")
    print("  rounded up a decade.")

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print("  hypothesis as proposed:")
    print('    "AI emergence is not magic; it is the fractal law projected onto')
    print('     the parameter axis, and it fires when fidelity crosses 99%."')
    print()
    print("  what the numbers license, in one sentence:")
    print("    Emergence is not magic -- it is a smooth power law read through a")
    print("    threshold metric -- but the threshold is the benchmark's depth and")
    print("    sampling cadence, not an integer number of nines of fidelity.")
    print()
    print("  survives:      Reach is exponential in narrowing (k={:.2f}) and the"
          .format(power["slope"]))
    print("                 exponent is fixed by the scaling law, k/P0 = alpha ln10.")
    print("  does not:      p=2 at 20B. Off by {:.0f}x."
          .format(crossings["p=2"]["factor_above_nominal"]))
    print("  neither:       the ~20B 'critical point' is real and reproducible --")
    print("                 for a five-step task at 50%. Change m and it moves.")
    print("  untested:      the loss->fidelity bridge. Every number above sits on")
    print("                 it, and no scaling-law data can test it.")


def main() -> int:
    result = analyse()
    report(result)
    path = render(result)
    write_data(result)
    write_paper_data(result, path)

    print()
    print("=" * 78)
    print("WROTE")
    print("=" * 78)
    print("  {}".format(os.path.relpath(path, ROOT)))
    print("  {}".format(os.path.relpath(DATA_CSV, ROOT)))
    print("  {}".format(os.path.relpath(SUMMARY_JSON, ROOT)))
    print("  {}".format(os.path.relpath(os.path.join(PAPER_DATA, "emergence_macros.tex"), ROOT)))
    print("  {}".format(os.path.relpath(os.path.join(PAPER_DATA, "emergence_table.tex"), ROOT)))
    print("  {}".format(os.path.relpath(PAPER_FIGURE, ROOT)))
    print()
    print("Reproduce with:  python3 -m experiments.emergence_experiment")
    print("No network access. No commit. No push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
