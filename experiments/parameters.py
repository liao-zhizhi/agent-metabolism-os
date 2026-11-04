"""Every number this experiment uses, and where it came from.

A fit is only as honest as this table. The distinction enforced here is the one
a referee will press on:

``literature``
    a measured or estimated quantity with a citation. It could be wrong; it is
    not invented here.
``derived``
    computed from other anchors by a stated rule. Reproducible, not observed.
``policy``
    a constant of the *software*, not a fact about nature. Changing it changes
    the model's opinions and nothing physical.
``assumed``
    a modelling choice with no external support. Declared so a reader can
    discount it.

Mixing these four into one column of numbers is how a simulation starts reading
as a measurement. Every anchor below is tagged, and the paper's parameter table
is generated from this file rather than typed by hand.

The one number in here that most deserves scepticism is ``T2_FMO_300K``. The
long-lived electronic beats reported for FMO at 77 K were later shown to be
largely vibrational at physiological temperature, so the honest statement is
that at 300 K the scaffold does **not** buy a longer electronic coherence time.
That is not a problem for this paper -- it is the paper's point. The bio path
buys reach without buying coherence, and any model that awards reach in
proportion to coherence time gets the biology exactly backwards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass(frozen=True)
class Anchor:
    """A named constant with its provenance, so no number arrives unattributed."""

    key: str
    value: float
    unit: str
    kind: str
    source: str

    def __post_init__(self) -> None:
        if self.kind not in ("literature", "derived", "policy", "assumed"):
            raise ValueError("unknown provenance {!r}".format(self.kind))

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


# ---------------------------------------------------------------------------
# the layers
# ---------------------------------------------------------------------------

#: Electronic dephasing of photosynthetic pigments in bulk solvent at 300 K.
#: Order of magnitude, not a single measurement: reported values span roughly
#: 50 fs to a few ps depending on pigment and environment.
T2_BULK_300K = Anchor(
    "T2_bulk_300K", 1.0e-12, "s", "literature",
    "2D electronic spectroscopy of pigment-protein complexes, 300 K; "
    "order of magnitude from the 50 fs - 2 ps spread in the literature",
)

#: Coherence time of a superconducting qubit in a dilution refrigerator.
#: Reported T2 values sit at 0.1-1 ms; 1 ms is the optimistic end and is used
#: here because the claim under test is the *upper bound* of the artificial path.
T2_CRYO = Anchor(
    "T2_cryo", 1.0e-3, "s", "literature",
    "superconducting transmon T2 ~ 0.1-1 ms at ~10 mK",
)

#: Electronic coherence of FMO at physiological temperature. The 77 K beats do
#: not survive; the 300 K oscillations that remain are assigned to vibrational,
#: not electronic, coherence. So the scaffold buys no coherence-time advantage.
T2_FMO_300K = Anchor(
    "T2_FMO_300K", 1.0e-12, "s", "literature",
    "no electronic-coherence advantage over bulk at 300 K; the long-lived 77 K "
    "beats vanish at physiological temperature (Duan et al., PNAS 2017)",
)

#: Excitation-trapping quantum yield of the FMO complex. 'Nearly 100%' is the
#: usual phrasing; 0.98 is the lower end of the reported range and is the value
#: used, so the paper does not lean on the best case.
ETA_FMO = Anchor(
    "eta_FMO", 0.98, "fraction", "literature",
    "excitation trapping efficiency of FMO, reported 0.95-0.99",
)


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------

#: The macro/quantum coherence gap is 12 decades (1 s vs 1 ps). The difficulty
#: scale is fixed by requiring that this *one real gap* sits at difficulty 0.99,
#: which is the value the model was specified with. That leaves no free scale
#: parameter: P0 is determined, not chosen.
POTENTIAL_DECADES = 12.0
DIFFICULTY_AT_REFERENCE = 0.99
P0 = POTENTIAL_DECADES / -math.log(1.0 - DIFFICULTY_AT_REFERENCE)

#: Independent constraints per decade of potential. One per decade is a modelling
#: choice with no external support; the paper reports it as such and the fit's
#: *shape* does not depend on its value, only the slope's magnitude does.
GAMMA = 1.0

#: Span of the un-crossed gap: the number of sites where the two layers can
#: coexist at all. Carried over from the interface engine's demo, where it is 10.
SPAN0 = 10.0


# ---------------------------------------------------------------------------
# reach anchors
# ---------------------------------------------------------------------------

#: The software refuses an un-replicable mechanism that claims more than 10x, so
#: 10 is the largest reach the artificial path can be expressed at. It is a
#: policy constant, and it is also a fair summary: a cryostat buys one order of
#: magnitude, not nine.
REACH_ART = Anchor(
    "reach_artificial", 10.0, "x", "policy",
    "interface_engine.REPLICATION_CEILING: an un-replicable mechanism cannot "
    "claim scale, so 10x is the model's ceiling for the cryostat",
)

#: A modelled, not measured, estimate: the biomimetic condition holds across
#: roughly four orders of magnitude more sites than a laboratory does. Section V
#: sweeps this from 1e2 to 1e6 and shows the conclusion does not depend on it.
REACH_BIO = Anchor(
    "reach_biomimetic", 1.0e4, "x", "assumed",
    "modelling choice: the scaffold condition holds over ~4 orders more sites "
    "than a laboratory; sensitivity-swept in the paper",
)


# ---------------------------------------------------------------------------
# the tolerance model (experiment C)
# ---------------------------------------------------------------------------

#: Achieved fraction of the narrow condition. A protein that folds correctly 95%
#: of the time has slack 0.95. The mean is the fidelity of the mechanism.
SLACK_MEAN = 0.95

#: Instance-to-instance variation in achieved slack, expressed as a fraction of
#: the *remaining* room rather than as an absolute constant:
#:
#:     sigma(mu) = KAPPA * (1 - mu)
#:
#: An absolute sigma would be unphysical at high fidelity -- a mechanism centred
#: at 0.999 with sigma 0.01 would exceed slack 1.0 in 46% of instances, which is
#: not a noisy mechanism but a broken definition. Tying the noise to the room
#: left keeps every draw inside [0, 1] while still letting a 99.9% mechanism be
#: resolved near 0.999, which is exactly where the boundary question lives.
SLACK_NOISE_FRACTION = 0.1

#: Fidelity tiers used to show that the failure boundary *moves* with fidelity.
#: If the boundary were a property of difficulty alone, these three curves would
#: coincide; they do not, and that is the result.
FIDELITY_TIERS = (0.90, 0.95, 0.999)

#: Selectivity of each mechanism's narrow condition -- the fraction of sites at
#: which the condition holds. It is *not* the same quantity as the boundary
#: fidelity F of ``topology.py``, and conflating the two is the easiest available
#: mistake in this paper: high F means the boundary is cheap to cross, while high
#: selectivity means the condition is easy to satisfy and therefore buys little
#: reach. They move in opposite directions, which is the whole counterintuitive
#: result of Experiment A.
#:
#: Cold machine: a dilution refrigerator is a nearly perfect condition -- it
#: really does deliver millisecond coherence -- so its selectivity is close to 1
#: and its exponent k is therefore tiny. Reach barely responds to narrowing.
#: What it lacks is multiplicity: one laboratory, one machine.
SELECTIVITY_ART = Anchor(
    "selectivity_artificial", 0.999, "fraction", "assumed",
    "a cryostat's condition is satisfied in essentially every site it is built "
    "at -- there is one site, and it is built there",
)

#: Protein scaffold: 0.98 is the FMO trapping efficiency, which is a *measured*
#: routing fidelity and is reused here as the selectivity of the scaffold
#: condition. The reuse is a modelling choice, flagged in the paper.
#:
#: Its exponent is 20x the cryostat's, which is why the lower-fidelity mechanism
#: wins at high difficulty. Nothing here is paradoxical once selectivity and
#: boundary fidelity are kept apart.
SELECTIVITY_BIO = Anchor(
    "selectivity_biomimetic", 0.98, "fraction", "literature",
    "FMO excitation-trapping efficiency 0.95-0.99, taken at 0.98; reused as "
    "the scaffold condition's site-wise success rate",
)


def slack_sigma(mean: float, fraction: float = SLACK_NOISE_FRACTION) -> float:
    """Noise on the achieved slack, bounded by the room left to perfect."""
    return max(fraction * (1.0 - mean), 1.0e-9)


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------

SEED = 20261104

#: Potentials are sampled log-uniformly over this range, and the bounds are not
#: arbitrary. The lower bound is 5 rather than 1 because the engine refuses
#: difficulty <= 0.8 as NOT_A_BOUNDARY, which corresponds to P < 4.19 decades.
#: The upper bound is 40 because 10^40 decades underflows ``exp(-P/P0)`` to
#: exactly 1.0 in float64, and a difficulty of exactly 1.0 is a wall with no
#: inverse -- that is the representational boundary of Section V, and it is
#: measured there rather than accidentally sampled here.
POTENTIAL_RANGE = (5.0, 40.0)

#: Slack is sampled log-uniformly. The lower bound is 0.45 rather than 0.5 because
#: only slack below ~0.53 at P=40 pushes reach past the 1e12 horizon, and the
#: censoring that follows is a real property of the model worth measuring.
SLACK_RANGE = (0.45, 0.99)


ANCHORS: List[Anchor] = [
    T2_BULK_300K, T2_CRYO, T2_FMO_300K, ETA_FMO,
    REACH_ART, REACH_BIO, SELECTIVITY_ART, SELECTIVITY_BIO,
]


def anchor_table() -> List[Dict[str, object]]:
    """The anchors plus the derived constants, as rows a table can consume."""
    rows = [a.as_dict() for a in ANCHORS]
    rows.extend([
        Anchor("P0", P0, "decades", "derived",
               "12 / ln(100): the scale that puts the 12-decade gap at 0.99"
               ).as_dict(),
        Anchor("gamma", GAMMA, "constraints/decade", "assumed",
               "one independent constraint per decade of potential").as_dict(),
        Anchor("span0", SPAN0, "sites", "policy",
               "span of the un-crossed gap in the interface engine").as_dict(),
        Anchor("slack_mean", SLACK_MEAN, "fraction", "assumed",
               "achieved fidelity of the narrow condition").as_dict(),
        Anchor("slack_noise", SLACK_NOISE_FRACTION, "fraction of room", "assumed",
               "sigma = 0.1 * (1 - fidelity)").as_dict(),
    ])
    return rows


if __name__ == "__main__":
    width = max(len(r["key"]) for r in anchor_table())
    print("{:<{w}}  {:>12}  {:<20}  {}".format("anchor", "value", "unit", "kind",
                                               w=width))
    print("-" * 96)
    for row in anchor_table():
        print("{:<{w}}  {:>12.4g}  {:<20}  {}".format(
            str(row["key"]), float(row["value"]), str(row["unit"]),
            str(row["kind"]), w=width))
