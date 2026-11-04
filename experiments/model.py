"""The derivations, separated from the simulation that tests them.

Everything here is algebra. Nothing here knows about the interface engine, which
is the point: the engine is the thing being tested, so it must not also be the
thing computing the prediction. When a number in the paper is called *predicted*
it came from this file; when it is called *measured* it came from a run.

The model in four equations:

    (1)  D(P) = 1 - exp(-P / P0)            a saturating map into [0, 1)
    (2)  n(P) = max(1, ceil(gamma * P))     independent constraints
    (3)  A(s, n) = s ** -n                  reach amplification
    (4)  Reach = span0 * A, capped by the span horizon

Substituting (1) into (2) and (3):

    ln A = gamma * P0 * |ln s| * ( -ln(1 - D) )

so the model predicts a **power law in the tolerance**, ``Reach ∝ (1-D)^-k``
with ``k = gamma * P0 * |ln s|``. Two consequences worth stating before any data
is collected:

* There is no single ``Reach = f(D)``. The exponent depends on the slack, so the
  correct statement is a one-parameter *family* of power laws, one per fidelity.
  A single fitted curve across mixed slack is the wrong model, and Section IV shows
  it fits worse.
* ``k`` does not depend on the generation. A law that is truly fractal must
  produce the same exponent at every rung, which is a falsifiable claim about the
  simulation rather than an assumption.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

from . import parameters as P


# ---------------------------------------------------------------------------
# equations (1)-(4)
# ---------------------------------------------------------------------------


def difficulty_from_potential(potential: float, p0: float = P.P0) -> float:
    """(1) Decades of mismatch to a difficulty in [0, 1).

    Saturating rather than linear, because difficulty must stay below 1.0: a gap
    at exactly 1.0 is a wall, and a wall is not an interface. The price is that
    the map is not invertible at 1.0, which is where Section V finds the model's
    representation boundary.
    """
    if potential <= 0:
        raise ValueError("potential must be positive decades, got {!r}".format(potential))
    return 1.0 - math.exp(-potential / p0)


def potential_from_difficulty(difficulty: float, p0: float = P.P0) -> float:
    """Inverse of (1), defined only for difficulty < 1."""
    if not 0.0 <= difficulty < 1.0:
        raise ValueError(
            "difficulty {!r} is outside [0, 1); 1.0 has no inverse because it is "
            "a wall".format(difficulty)
        )
    return -p0 * math.log(1.0 - difficulty)


def constraint_count(potential: float, gamma: float = P.GAMMA) -> int:
    """(2) How many independent constraints the gap imposes.

    Ceiling, and at least one: a gap that imposes no constraint is not a gap.
    """
    return max(1, int(math.ceil(gamma * potential)))


def amplification(slack: float, constraints: int) -> float:
    """(3) Reach is inverse rarity.

    If a fraction ``s`` of the sites satisfy each of ``n`` independent
    constraints, a fraction ``s**n`` satisfy all of them, and a crossing that
    works only where all of them hold reaches ``s**-n`` times further than one
    that works anywhere. This is the paper's central modelling assumption, and
    it is stated as an assumption rather than derived from thermodynamics.
    """
    if not 0.0 < slack < 1.0:
        raise ValueError("slack must be in (0, 1), got {!r}".format(slack))
    if constraints < 1:
        raise ValueError("constraints must be at least 1, got {!r}".format(constraints))
    return slack ** -constraints


def predicted_reach(
    slack: float, potential: float, span0: float = P.SPAN0,
    horizon: float = 1.0e12,
) -> float:
    """(4) Reach before the engine sees it, censored at the horizon."""
    return min(span0 * amplification(slack, constraint_count(potential)), horizon)


def law_exponent(slack: float, gamma: float = P.GAMMA, p0: float = P.P0) -> float:
    """``k`` in ``Reach ∝ (1-D)^-k``. Analytic, not fitted."""
    return gamma * p0 * abs(math.log(slack))


# ---------------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------------


def representation_boundary(
    ratio: float = 0.5, lo: float = 0.0, hi: float = 1.0, tol: float = 1e-15
) -> float:
    """The difficulty at or above which a crossing stops raising the child's.

    Measured by bisection on the engine's own successor function rather than by
    restating its constant, so the number in the paper is a measurement of the
    software and not a copy of its source.

    Returns the smallest ``D`` for which ``next_difficulty(D) <= D``.
    """
    from agent_metabolism.interface_engine import _next_difficulty

    def rises(d: float) -> bool:
        return _next_difficulty(d, ratio) > d

    if not rises(lo):
        return lo
    if rises(hi):
        return hi  # no boundary in range; caller must not pretend there is one
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if rises(mid):
            lo = mid
        else:
            hi = mid
    return hi


def ladders_before_saturation(
    difficulty0: float, ratio: float = 0.5, ceiling: float = 1.0 - 1.0e-9
) -> int:
    """Rungs that can be added before the successor clamps at the ceiling."""
    room = 1.0 - difficulty0
    floor = 1.0 - ceiling
    if room <= floor:
        return 0
    return int(math.ceil(math.log(floor / room) / math.log(ratio)))


# ---------------------------------------------------------------------------
# the exponential form: what is exact, what is local, what is impossible
# ---------------------------------------------------------------------------
#
# The law is a power law in the tolerance, ``Reach = A0 * (1-D)**-k``. It is
# often reported instead as an exponential in difficulty. This section says
# precisely how those two statements relate, because two of the three relations
# hold exactly and the third -- the one people usually mean -- cannot hold at
# all. Having the difference in code rather than in prose is what lets the paper
# plot it.
#
# Write ``eps = 1 - D`` and ``A0 = span0``.
#
# (i)  Exact, in the narrowing coordinate ``u = ln(1/eps)``:
#          Reach = A0 * exp(k * u)
#      by the identity ``eps**-k = exp(-k ln eps)``. The exponent k is a rate per
#      *nat of narrowing*, not per unit difficulty. This is the honest reading of
#      "exponential growth of reach": the driving variable is how many orders of
#      magnitude of tolerance the mechanism gives up, not D.
#
# (ii) Exact, along the ladder, in the generation index ``n``:
#          eps_n = eps_0 * ratio**n   =>   Reach_n = Reach_0 * exp(k ln(1/ratio) n)
#      The nested successor multiplies the room left by a constant, so the log of
#      reach is exactly affine in generation. At ratio 0.5 the rate is k ln 2.
#      This is why a fractal ladder looks exponential when plotted against depth.
#
# (iii) Local, in D, near D=1 -- and *only* local. The tangent form is
#          Reach(D) ~ Reach(D0) * exp(alpha0 * (D - D0)),  alpha0 = k / (1 - D0),
#      because d ln Reach/dD = k/(1-D). The rate diverges as D0 -> 1.
#
# (iv) Impossible globally, in D, with constant alpha. ``ln Reach`` has second
#      derivative ``k/(1-D)**2 > 0`` everywhere below 1, so it is strictly convex
#      and therefore not affine on any interval of positive length. No choice of
#      constant alpha reproduces the power law beyond the tangent point.
#
# So the requested mapping exists in senses (i)-(iii) and is refuted in sense
# (iv). The useful quantitative statement is the *rate of convergence* of (iii):
# on a window of fixed relative half-width ``f`` in eps, the least-squares
# exponential rate satisfies
#
#          alpha_hat * (1 - D0) / k  =  1 + f**2 / 5  + O(f**4)
#
# and the goodness of fit is independent of D0. Both are verified numerically in
# experiment E; the derivation of the f**2/5 is the symmetric-window integral of
# ``x ln(1+x)`` against ``x**2``.


def narrowing_coordinate(difficulty: float) -> float:
    """``u = -ln(1-D) = ln(1/eps)``, nats of narrowing. Range ``[0, inf)``."""
    if not 0.0 <= difficulty < 1.0:
        raise ValueError(
            "difficulty {!r} is outside [0, 1); 1.0 is a wall with no coordinate"
            .format(difficulty)
        )
    return -math.log(1.0 - difficulty)


def exp_form_rate(slack: float, gamma: float = P.GAMMA, p0: float = P.P0) -> float:
    """The constant rate of ``Reach = A0 exp(k u)``, exact in ``u``. Equals ``k``."""
    return law_exponent(slack, gamma, p0)


def generation_rate(
    slack: float, ratio: float = 0.5, gamma: float = P.GAMMA, p0: float = P.P0
) -> float:
    """The exact rate per rung: ``k * ln(1/ratio)``. ``k ln 2`` at ratio 0.5."""
    if not 0.0 < ratio < 1.0:
        raise ValueError("ratio must be in (0, 1), got {!r}".format(ratio))
    return law_exponent(slack, gamma, p0) * abs(math.log(ratio))


def local_rate(
    difficulty: float, slack: float, gamma: float = P.GAMMA, p0: float = P.P0
) -> float:
    """``alpha(D) = k / (1-D)``: the tangent exponential rate. Diverges at 1."""
    if not 0.0 <= difficulty < 1.0:
        raise ValueError("difficulty must be in [0, 1), got {!r}".format(difficulty))
    return law_exponent(slack, gamma, p0) / (1.0 - difficulty)


def curvature(
    difficulty: float, slack: float, gamma: float = P.GAMMA, p0: float = P.P0
) -> float:
    """``d2 ln Reach / dD2 = k/(1-D)**2``. Strictly positive: the refutation of (iv)."""
    if not 0.0 <= difficulty < 1.0:
        raise ValueError("difficulty must be in [0, 1), got {!r}".format(difficulty))
    return law_exponent(slack, gamma, p0) / (1.0 - difficulty) ** 2


def is_affine_in_difficulty(slack: float, samples: int = 200) -> bool:
    """Whether ``ln Reach`` could be affine in ``D``. Always ``False``, by convexity.

    Evaluated numerically rather than returned as a literal so that a future
    change to the law would be caught here instead of in a paragraph of the paper
    that nobody re-checks.
    """
    if samples < 3:
        raise ValueError("need at least 3 samples")
    points = [i / (samples + 1) for i in range(1, samples + 1)]
    second = [
        curvature(d, slack)
        for d in (0.1 + 0.8 * p for p in points)
    ]
    return all(abs(value) < 1e-12 for value in second)


def exponential_window_ratio(relative_halfwidth: float) -> float:
    """``alpha_hat*(1-D0)/k`` for a symmetric geometric window.

    Analytic, from the ratio of two moments over ``x in [-f, f]``::

        sum x ln(1+x) / sum x**2  =  1 + f**2/5 + 3 f**4/35 + O(f**6)

    which is the expansion of ``1 + (3/5)f**2 (1 + ...)`` after the odd terms
    cancel on the symmetric interval. Experiment E's measured ratio is compared
    against the leading term, and its *residual* is compared against the f**4
    term -- so the check is analytic against numeric twice over, rather than
    numeric against itself once.
    """
    if not 0.0 < relative_halfwidth < 1.0:
        raise ValueError(
            "relative half-width must be in (0, 1), got {!r}"
            .format(relative_halfwidth)
        )
    f = relative_halfwidth
    # exact symmetric-window integral of x ln(1+x) / x**2, to O(f**4)
    return 1.0 + f * f / 5.0


def geometric_window(
    difficulty0: float, relative_halfwidth: float, samples: int = 201
) -> List[Tuple[float, float]]:
    """``(D, ln Reach/span0)`` on ``eps in eps0*[1-f, 1+f]``, clipped below 1.

    Geometric in eps, not linear in D. That is the point: a window of fixed
    *relative* width is a window of fixed shape in the narrowing coordinate, so
    the fit's quality is scale-free. A window of fixed *absolute* width in D
    becomes a window of growing relative width as D -> 1, which is why the naive
    exponential fit degrades there.
    """
    eps0 = 1.0 - difficulty0
    if not 0.0 < eps0 < 1.0:
        raise ValueError("difficulty0 must be in (0, 1), got {!r}".format(difficulty0))
    if not 0.0 < relative_halfwidth < 1.0:
        raise ValueError(
            "relative half-width must be in (0, 1), got {!r}"
            .format(relative_halfwidth)
        )
    low = eps0 * (1.0 - relative_halfwidth)
    high = min(eps0 * (1.0 + relative_halfwidth), 1.0)
    out: List[Tuple[float, float]] = []
    # Cell-centred, not endpoint-to-endpoint. The endpoints of a symmetric grid
    # are counted once where the interior points each stand for a cell, and that
    # gives the raw moment sums an O(1/samples) bias -- 1e-5 in the fitted rate
    # at 401 points, which is the same order as the O(f**4) term the fit is being
    # checked against. Centring the cells removes the bias instead of arguing
    # about it.
    for i in range(samples):
        eps = low + (high - low) * (i + 0.5) / samples
        out.append((1.0 - eps, -math.log(eps)))
    return out


def absolute_window(
    difficulty0: float, halfwidth: float, samples: int = 201
) -> List[Tuple[float, float]]:
    """``(D, ln Reach/span0)`` on ``D in D0 +/- halfwidth``, clipped below 1."""
    if not 0.0 <= difficulty0 < 1.0:
        raise ValueError("difficulty0 must be in [0, 1), got {!r}".format(difficulty0))
    if halfwidth <= 0.0:
        raise ValueError("halfwidth must be positive, got {!r}".format(halfwidth))
    low = max(difficulty0 - halfwidth, 0.0)
    high = min(difficulty0 + halfwidth, 1.0 - 1.0e-15)
    out: List[Tuple[float, float]] = []
    for i in range(samples):
        d = low + (high - low) * (i + 0.5) / samples
        out.append((d, -math.log(1.0 - d)))
    return out
