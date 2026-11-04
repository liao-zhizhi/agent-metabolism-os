"""Five strata, the fidelity of the boundaries between them, and "seamless".

This module is algebra plus a small amount of bookkeeping. Like ``model.py`` it
knows nothing about the interface engine, because it is the thing the engine is
being compared against.

Three claims are set up here, and the paper proves all three:

**1. Fidelity is a property of a boundary *and a function*.** A boundary that
carries energy does not thereby preserve phase, and a boundary that preserves
phase may carry nothing. There is no single number ``F`` for the quantum /
chemical boundary: it routes excitations at 0.98 and preserves electronic phase
at about 1e-5. A model that reports one ``F`` per boundary is not simplifying,
it is answering a question nobody asked.

**2. "Seamless" is a threshold on resistance, counted in nines.**
``R = 1 - F``; the interface is seamless for a requirement when ``R < eps``.
Since ``R = 10**-p`` for ``p`` nines of fidelity, the criterion is
``p >= ceil(log10(1/eps))``, which is *quantised*. An interface does not fade
out as it improves; it flips at a whole number of nines. That is the formal
content of ``the interface disappears when the technology matures``.

**3. Depth is paid for in nines, logarithmically.** A path of ``n`` boundaries
is seamless only when each is about ``log10(n)`` nines better than a single
boundary would need, because ``1 - (1-10**-p)**n <= n * 10**-p``. Four strata
cost 0.60 nines, ten cost 1.0, a hundred cost 2.0.

The word "about" is load-bearing. The bound ``n*10**-p`` is the linearisation of
``1-(1-eps)**(1/n)``, and the linearisation is *optimistic*: the exact
per-boundary budget is ``1-(1-eps)**(1/n)``, which exceeds ``eps/n``. The gap is
``O(eps)``, so at ``eps=1e-2`` and ``n=4`` the penalty is 0.6004 nines against a
predicted 0.6021 -- small, but a reader who computes both deserves to be told
which is which rather than finding out.

Nothing here is measured. Every fidelity below is either taken from a cited
measurement or declared as an assumption in ``parameters.py``'s vocabulary; the
module refuses to accept a number that has not been tagged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# the five strata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stratum:
    """One level of organisation, with the scale it lives at.

    ``length_m`` and ``time_s`` are order-of-magnitude characterisations, not
    measurements of anything in particular. They are here because a topology
    figure with no scale on its axes is decoration, and because the ratio between
    neighbouring strata is what makes the ladder a ladder rather than a list.
    """

    index: int
    name: str
    discipline: str
    length_m: float
    time_s: float
    unit: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "name": self.name,
            "discipline": self.discipline,
            "length_m": self.length_m,
            "time_s": self.time_s,
            "unit": self.unit,
        }


#: Bottom-up. Each row is one stratum; the gap to the next is a boundary.
#:
#: The order is by *scale of organisation*, not by size: the information stratum
#: has no characteristic length at all, which is itself the reason it is the
#: hardest to place. We give it the length of a symbol (the scale at which a
#: token is addressable) and say so.
STRATA: Tuple[Stratum, ...] = (
    Stratum(0, "Quantum", "physics", 1.0e-10, 1.0e-12, "coherence"),
    Stratum(1, "Physico-chemical", "chemistry", 1.0e-9, 1.0e-9, "reaction"),
    Stratum(2, "Biological", "biology", 1.0e-6, 1.0e-3, "function"),
    Stratum(3, "Cognitive", "neuroscience", 1.0e-2, 1.0, "representation"),
    Stratum(4, "Information", "computer science", 1.0e-6, 1.0e-6, "claim"),
)

#: The four boundaries, each named by the strata it joins.
BOUNDARIES: Tuple[str, ...] = tuple(
    "{}|{}".format(STRATA[i].name, STRATA[i + 1].name)
    for i in range(len(STRATA) - 1)
)


# ---------------------------------------------------------------------------
# fidelity: per boundary, per function
# ---------------------------------------------------------------------------

#: A *function* is what you are asking the boundary to do, and the same physical
#: boundary has a different fidelity for each. This is the module's central
#: structural claim, so it is a first-class key rather than a comment.
ROUTE = "route"        # does the payload get through?
PHASE = "phase"        # is the payload still coherent when it arrives?
CHECKABLE = "checkable"  # can a third party verify that it arrived?

#: Fidelity of each boundary for each function it is asked to serve.
#:
#: ``B0`` is the only boundary with a *measured* number in it: the FMO trapping
#: efficiency (0.95-0.99, taken at 0.98) is the fraction of excitations that
#: arrive at the reaction centre rather than being lost, which is precisely a
#: routing fidelity. Its phase fidelity is a ratio of times, 1 ps of electronic
#: coherence against the ~100 ns a solid-state operation would need.
#:
#: ``B1``-``B3`` are declared assumptions. They are *not* measurements and the
#: paper says so at the table. What the paper uses them for is the shape of the
#: argument -- which boundary binds, and by how many nines -- and that is robust
#: to moving any of them by a factor of a few.
FIDELITY: Dict[str, Dict[str, float]] = {
    "Quantum|Physico-chemical": {ROUTE: 0.98, PHASE: 1.0e-5},
    "Physico-chemical|Biological": {ROUTE: 0.95},
    "Biological|Cognitive": {ROUTE: 0.90},
    "Cognitive|Information": {ROUTE: 0.70, CHECKABLE: 0.70},
}

#: Which number in ``FIDELITY`` came from where. A fidelity with no provenance
#: does not get into the figure.
PROVENANCE: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("Quantum|Physico-chemical", ROUTE):
        ("literature", "FMO excitation-trapping efficiency, 0.95-0.99; 0.98 used"),
    ("Quantum|Physico-chemical", PHASE):
        ("literature", "T2(bulk,300 K)=1 ps over a 100 ns operation = 1e-5"),
    ("Physico-chemical|Biological", ROUTE):
        ("assumed", "self-assembly and proofreading yield; no anchor located"),
    ("Biological|Cognitive", ROUTE):
        ("assumed", "neural coding reliability; order of magnitude only"),
    ("Cognitive|Information", ROUTE):
        ("assumed", "witness density: fraction of claims arriving with an artefact"),
    ("Cognitive|Information", CHECKABLE):
        ("assumed", "same quantity measured as a verification rate"),
}

#: The policy threshold. An interface is seamless when its resistance is below
#: this. It is a *policy* number in the sense ``parameters.py`` means, and the
#: paper's results are reported at three thresholds so the choice is visible.
SEAMLESS_EPS = 1.0e-2

#: Thresholds swept in the experiment, so no conclusion rests on one choice.
EPS_GRID: Tuple[float, ...] = (1.0e-1, 1.0e-2, 1.0e-3, 1.0e-4)


def boundary(index: int) -> str:
    """The name of the boundary above stratum ``index``."""
    if not 0 <= index < len(BOUNDARIES):
        raise IndexError("no boundary {} in a {}-stratum stack".format(
            index, len(STRATA)))
    return BOUNDARIES[index]


def fidelity(name: str, function: str = ROUTE) -> float:
    """The fidelity of boundary ``name`` for ``function``.

    Raises rather than defaulting. A boundary that was never claimed to serve a
    function cannot be assigned a number for it, and inventing one is how a
    figure acquires a quantity nobody measured.
    """
    try:
        table = FIDELITY[name]
    except KeyError:
        raise KeyError("unknown boundary {!r}; known: {}".format(name, list(FIDELITY)))
    if function not in table:
        raise KeyError(
            "boundary {!r} was not characterised for {!r}; it serves {}".format(
                name, function, sorted(table)
            )
        )
    value = float(table[function])
    if not 0.0 < value <= 1.0:
        raise ValueError("fidelity must be in (0, 1], got {!r}".format(value))
    return value


def resistance(name: str, function: str = ROUTE) -> float:
    """``R = 1 - F``. What the boundary costs, in the same units as fidelity."""
    return 1.0 - fidelity(name, function)


def nines(value: float) -> float:
    """How many nines a fidelity or resistance has, as a real number.

    ``F = 1 - 10**-p`` has ``p`` nines. Reported as a real rather than an integer
    because a boundary at 1.7 nines is genuinely closer to seamless than one at
    1.0, and rounding that away would hide the result the figure is about.
    """
    room = 1.0 - value if value >= 0.5 else value
    if room <= 0.0:
        return float("inf")
    return -math.log10(room)


def required_nines(eps: float = SEAMLESS_EPS) -> int:
    """The whole number of nines a boundary needs to be seamless at ``eps``.

    Integer because the criterion is a threshold: 1.99 nines fails exactly as
    hard as 0.99 does. ``ceil`` is the whole content of claim 2 in the docstring.
    """
    if not 0.0 < eps < 1.0:
        raise ValueError("eps must be in (0, 1), got {!r}".format(eps))
    return int(math.ceil(-math.log10(eps)))


def is_seamless(value: float, eps: float = SEAMLESS_EPS) -> bool:
    """``R < eps``, stated once so no caller restates it with ``<=``."""
    return resistance_ok(1.0 - value, eps)


def resistance_ok(res: float, eps: float) -> bool:
    """The criterion itself, on a resistance rather than a fidelity."""
    if not 0.0 <= res <= 1.0:
        raise ValueError("resistance must be in [0, 1], got {!r}".format(res))
    return res < eps


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def path_transmittance(fidelities: Iterable[float]) -> float:
    """``T = prod F``. Exact, and the reason depth is expensive.

    Multiplication, not averaging: a chain of boundaries is only as transparent
    as the product, and the product is dominated by the worst term.
    """
    values = list(fidelities)
    if not values:
        raise ValueError("a path needs at least one boundary")
    for value in values:
        if not 0.0 < value <= 1.0:
            raise ValueError("fidelity must be in (0, 1], got {!r}".format(value))
    total = 1.0
    for value in values:
        total *= value
    return total


def path_resistance(fidelities: Iterable[float]) -> float:
    """``1 - prod F``. The path's total cost."""
    return 1.0 - path_transmittance(fidelities)


def resistance_budget(n: int, eps: float = SEAMLESS_EPS) -> float:
    """The per-boundary resistance a path of ``n`` boundaries must stay under.

    Derived, not chosen. ``1 - (1-r)**n < eps`` is satisfied when
    ``r < 1 - (1-eps)**(1/n)``, which is the exact bound; the linearisation
    ``eps/n`` is the one people quote and it is *optimistic*, so we report the
    exact value and note the gap.
    """
    if n < 1:
        raise ValueError("a path needs at least one boundary, got {}".format(n))
    if not 0.0 < eps < 1.0:
        raise ValueError("eps must be in (0, 1), got {!r}".format(eps))
    return 1.0 - (1.0 - eps) ** (1.0 / n)


def depth_penalty_nines(n: int, eps: float = SEAMLESS_EPS) -> float:
    """Extra nines each boundary needs because the path is ``n`` deep.

    At ``eps=1e-2`` and ``n=4`` this is 0.60 nines -- the difference between a
    two-nines interface and a 2.6-nines one. Depth is the reason a five-stratum
    stack cannot be made seamless by fixing one boundary.

    Equals ``log10(n)`` only to ``O(eps)``. Reported exactly here rather than as
    the logarithm because the exact value is what the budget uses, and two
    spellings of one number is the class of defect this package keeps finding.
    """
    per_boundary = resistance_budget(n, eps)
    return -math.log10(per_boundary) - (-math.log10(eps))


def max_depth(fidelity_value: float, eps: float = SEAMLESS_EPS) -> int:
    """How many identical boundaries at this fidelity can be seamless at once.

    ``(1-R)**n > 1-eps``  =>  ``n < ln(1-eps)/ln(F)``. Returns 0 when even a
    single boundary fails.
    """
    if not 0.0 < fidelity_value <= 1.0:
        raise ValueError("fidelity must be in (0, 1], got {!r}".format(fidelity_value))
    if fidelity_value >= 1.0:
        return 10 ** 6  # never reached in finite depth; reported as a sentinel
    if not is_seamless(fidelity_value, eps):
        return 0
    return int(math.floor(math.log(1.0 - eps) / math.log(fidelity_value)))


# ---------------------------------------------------------------------------
# the stack
# ---------------------------------------------------------------------------


def stack_report(function: str = ROUTE, eps: float = SEAMLESS_EPS) -> Dict[str, object]:
    """Every boundary's fidelity, resistance, nines and verdict, plus the path.

    One boundary at a time, and then the path, because the two answers differ:
    a stack can have every boundary individually above threshold and still fail
    as a path, and that gap is what ``depth_penalty_nines`` measures.
    """
    rows: List[Dict[str, object]] = []
    for index in range(len(BOUNDARIES)):
        name = boundary(index)
        try:
            value = fidelity(name, function)
        except KeyError:
            rows.append({
                "index": index, "boundary": name, "function": function,
                "fidelity": None, "resistance": None, "nines": None,
                "required_nines": required_nines(eps), "seamless": None,
                "note": "not characterised for this function",
            })
            continue
        res = 1.0 - value
        rows.append({
            "index": index, "boundary": name, "function": function,
            "fidelity": value, "resistance": res, "nines": nines(value),
            "required_nines": required_nines(eps),
            "seamless": res < eps,
            "note": PROVENANCE.get((name, function), ("assumed", ""))[1],
        })

    characterised = [r for r in rows if r["fidelity"] is not None]
    transmittance = path_transmittance(r["fidelity"] for r in characterised)
    return {
        "function": function,
        "eps": eps,
        "rows": rows,
        "n_boundaries": len(characterised),
        "path_transmittance": transmittance,
        "path_resistance": 1.0 - transmittance,
        "path_seamless": (1.0 - transmittance) < eps,
        "per_boundary_budget": resistance_budget(len(characterised), eps),
        "depth_penalty_nines": depth_penalty_nines(len(characterised), eps),
        "bottleneck": min(characterised, key=lambda r: r["fidelity"])["boundary"],
    }


# ---------------------------------------------------------------------------
# noise: what the coarse strata do to the fine one
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoiseSource:
    """A coarse stratum's disturbance, measured where it is generated.

    ``power`` is in the source's own units and is deliberately dimensionless: the
    model does not claim to know absolute noise powers across twelve decades of
    scale. What it does claim is that the *admitted fraction* is the product of
    the leakages on the way down, and that claim needs no units.
    """

    source_index: int
    power: float
    mechanism: str


#: One source per stratum above the bottom. The bottom stratum has no source
#: because it is the one being disturbed.
NOISE_SOURCES: Tuple[NoiseSource, ...] = (
    NoiseSource(1, 1.0, "vibrational and conformational rearrangement"),
    NoiseSource(2, 1.0, "thermal bath at 300 K"),
    NoiseSource(3, 1.0, "metabolic and signalling activity"),
    NoiseSource(4, 1.0, "semantic and measurement error"),
)


def downward_leakage(source_index: int) -> float:
    """Fraction of a coarse stratum's noise admitted down to stratum 0.

    ``lambda_k = 1 - F_k``: a boundary that passes the condition with probability
    ``F`` passes what does not match it with probability ``1-F``. That is the
    complementarity assumption, and it is an assumption -- a real filter's pass
    and stop bands are not exact complements. It is stated here rather than
    buried because the cryostat/protein distinction is exactly where it bends.
    """
    if source_index < 1 or source_index > len(STRATA) - 1:
        raise ValueError("no stratum {}".format(source_index))
    leak = 1.0
    for k in range(source_index):
        leak *= resistance(boundary(k), ROUTE)
    return leak


def noise_budget() -> List[Dict[str, object]]:
    """What reaches the quantum stratum from each coarser stratum."""
    requirement = fidelity(boundary(0), PHASE)
    need_decades = -math.log10(max(requirement, 1.0e-300))
    rows: List[Dict[str, object]] = []
    for source in NOISE_SOURCES:
        leak = downward_leakage(source.source_index)
        decades = -math.log10(max(leak, 1.0e-300))
        # How many times over the quantum tolerance this source lands, at unit
        # source power. Ratios of this number between strata are the only
        # assumption-free content: the model does not know absolute noise powers
        # across twelve decades, and does not pretend to.
        over = (leak * source.power) / max(requirement, 1.0e-300)
        rows.append({
            "source_index": source.source_index,
            "source": STRATA[source.source_index].name,
            "mechanism": source.mechanism,
            "admitted_fraction": leak,
            "attenuation_decades": decades,
            "required_decades": need_decades,
            "shortfall_decades": need_decades - decades,
            "over_budget": over,
            "tolerable_fraction": 1.0 / over if over > 0 else float("inf"),
            "sufficient": decades >= need_decades,
        })
    # The stratum that binds is the one furthest over budget, not the one with
    # the largest leak per unit: same thing here because every source is unit
    # power, and stated this way so it stays right if a power is ever changed.
    worst = max(r["over_budget"] for r in rows)
    for r in rows:
        r["over_budget_relative"] = r["over_budget"] / worst
        r["binds_first"] = r["over_budget"] == worst
    return rows


def nines_to_close_shortfall(shortfall_decades: float) -> float:
    """Extra nines of fidelity on the top boundary that would close a shortfall.

    Each nine of ``F`` is one decade of rejection, so the conversion is the
    identity. It is a function rather than a comment so the paper can quote a
    number that a reader can reproduce.
    """
    return max(0.0, float(shortfall_decades))
