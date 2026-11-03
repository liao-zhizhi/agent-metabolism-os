"""Boundary auditing: an interface is where two layers collide, not a designed API.

The usual way to talk about an interface is to draw it. Boxes, arrows, a protocol
between them. That picture is a convenience of software, where a function
signature is *made* by someone and can be any shape they like. Between physical
layers there is no author, and the interface is not a shape but a **constraint**:
the set of conditions under which both sides can hold at once. Very often that set
is almost empty, and it is narrow for reasons nobody negotiated.

The case this module is built around
------------------------------------
* ``Theory_Quantum``  electronic coherence in a condensed phase at 300 K survives
  on the order of a picosecond before the environment destroys it.
* ``Theory_Bio``      a biological function must persist for seconds, in water,
  at 300 K, in every individual, every time.

Those two statements are not in conflict about facts. They are in conflict about
*requirements*, and the conflict is quantitative: to bridge them, an invention
must hold coherence about **twelve orders of magnitude** longer than the quantum
layer allows, while staying aqueous and near room temperature. That twelve-decade
gap is the interface. It was never designed, it cannot be widened by argument,
and a claim that crosses it is checkable in exactly one way -- by producing the
mechanism and the numbers.

What this module can and cannot decide
--------------------------------------
It cannot decide whether a mechanism works. It has no spectrometer, no
diffractometer, and no literature of measured structures; pretending otherwise
would be the same false completion this package exists to catch, committed by the
auditor. What it *can* decide is narrower and still worth having:

1. **Is the claim falsifiable at all?** A filing that says "uses quantum effects"
   names a domain, not a mechanism. Nothing in it could be measured and found
   wrong, so there is no witness and the verdict is ``INVALID`` -- not "false",
   but "never examined", which is a different finding and must stay one.
2. **Do the filing's own numbers close the gap?** If it supplies a suppression
   factor of ``1e3`` against a required ``1e12``, the filing refutes itself. That
   is ``FAIL``: the claim is contradicted, and not by us.
3. **Is the arithmetic internally consistent?** A filing that claims a ``1e12``
   protection factor *and* a microsecond coherence time has contradicted itself
   twice; ``intrinsic × factor`` does not equal the stated lifetime. This is the
   cheapest real defect to find, and real filings contain it.

So ``PASS`` here means, precisely: **the claim is falsifiable, its numbers are
internally consistent, and its own arithmetic suffices to cross this boundary.**
It does not mean the invention works. The result carries that sentence in a field
rather than leaving it to the reader, because a verdict that gets quoted without
its scope becomes a lie by citation.

Why "missing" is not "insufficient"
-----------------------------------
``witness={}`` and ``witness={"suppression_factor": 1e3}`` are different failures
and earn different verdicts, for the same reason ``witness=None`` and
``witness=False`` do in :mod:`agent_metabolism.double_entry`:

    nothing to examine   -> INVALID   nobody looked; we cannot say
    examined, and short  -> FAIL      we looked, and the claim is contradicted

Collapsing them would turn every thin filing into a refutation, which over-accuses
in exactly the way a too-strict immune system does. The two are kept apart by
:class:`WitnessFault`, where a fault with ``verdict=None`` is an advisory note
that is structurally unable to change the outcome.

The blind-boundary map
----------------------
:meth:`BoundaryAuditor.find_blind_boundaries` does not search physics. It compares
the *loaded registry* against the axes the boundary decomposes into, and reports
which axes no filing has provided a witness for. That is a statement about the
registry, and calling it a map of reality would be the overreach this module
spends its docstring avoiding. An empty registry makes all of physics look blind,
which is why every returned row says so.

Zero dependencies: standard library and this package's verdict lattice. The
membrane is duck-typed, as everywhere else.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .double_entry import FAIL, INVALID, PASS

__all__ = [
    "Boundary",
    "BoundaryAudit",
    "BoundaryAuditor",
    "BoundaryAxis",
    "BoundaryError",
    "Invention",
    "Layer",
    "WitnessFault",
    "DEFAULT_INVENTIONS",
    "DEFAULT_LAYERS",
    "MECHANISMS",
]

#: Worst-wins, same lattice as the rest of the package.
_SEVERITY = {PASS: 0, INVALID: 1, FAIL: 2}

#: A structural witness is a *geometry*, not a slogan. One number is a parameter
#: estimate; three independent ones are a structure someone could try to build and
#: fail to. The threshold is a policy choice and is labelled as one.
MIN_STRUCTURAL_PARAMETERS = 3

#: Two numbers "agree" when they are within half a decade. Tighter would fail
#: honest roundings in a patent; looser would let a unit error through.
_DECADE_TOLERANCE = 0.5

#: Room temperature, and how far a filing may drift before it is describing a
#: different experiment. Biology is the binding constraint here: 300 K is not a
#: preference, it is the only temperature the other side can run at.
ROOM_TEMPERATURE_K = 300.0
TEMPERATURE_TOLERANCE_K = 5.0

#: Mechanism families that name a *physical* way to keep a phase relationship
#: alive. Membership is not an endorsement: a recognised name still has to supply
#: numbers. It only means the filing is talking about a mechanism rather than a
#: mood.
MECHANISMS: Dict[str, str] = {
    "protein_scaffold_shielding": (
        "a protein matrix that rigidifies the chromophore's environment and "
        "detunes the bath modes that dephase it"
    ),
    "cavity_polariton": (
        "strong coupling to a confined optical mode that mixes the excitation "
        "into a longer-lived polariton"
    ),
    "dynamical_decoupling": (
        "control pulses that refocus the environmental phase accumulation"
    ),
    "error_corrected_encoded": (
        "a logical state spread over several physical ones so that local "
        "dephasing is out-voted"
    ),
    "radical_pair_spin": (
        "spin coherence of a radical pair, which is far less coupled to the "
        "thermal bath than electronic coherence -- a genuinely different axis"
    ),
    "topological_protection": (
        "a state whose phase relationship is protected by a global property "
        "rather than by isolation"
    ),
}

#: Strings that look like a mechanism and are not one. This list is the whole
#: point of the INVALID verdict: "quantum" cannot be measured, so it cannot be
#: wrong, so it is not a claim about the world.
_PLACEHOLDER_MECHANISMS = frozenset(
    {
        "quantum",
        "quantum_effect",
        "quantum_effects",
        "quantum_effect_generic",
        "quantum_coherence",
        "nano",
        "nanotechnology",
        "advanced_material",
        "special_material",
        "proprietary",
        "biological_quantum",
        "bio_quantum",
        "unknown",
        "tbd",
        "",
    }
)


class BoundaryError(ValueError):
    """The requested boundary cannot be described as given."""


# ---------------------------------------------------------------------------
# the layers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    """One level of description, stated as requirements rather than as a field.

    The two numbers that matter are the pair ``intrinsic_coherence_s`` and
    ``required_coherence_s``. The first is what the layer *gives you*; the second
    is what the layer *needs* to do its job. A boundary between two layers is
    narrow in proportion to the ratio between the worse number on one side and the
    better number on the other.
    """

    key: str
    name: str
    regime: str
    temperature_k: float
    required_coherence_s: float
    intrinsic_coherence_s: float
    medium: str
    description: str
    sources: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "regime": self.regime,
            "temperature_k": self.temperature_k,
            "required_coherence_s": self.required_coherence_s,
            "intrinsic_coherence_s": self.intrinsic_coherence_s,
            "medium": self.medium,
            "description": self.description,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class BoundaryAxis:
    """One independent way the two layers disagree.

    The gap between micro and macro is not one wall but several, and they are
    crossed separately. A filing that answers the lifetime problem by cooling to
    4 K has *widened* the temperature problem; the axes are the reason "quantum
    biology at room temperature" is not a single hard question but four, each of
    which has to be answered without breaking the others.
    """

    key: str
    name: str
    unstable_side: str
    stable_side: str
    why: str
    witness_key: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "unstable_side": self.unstable_side,
            "stable_side": self.stable_side,
            "why": self.why,
            "witness_key": self.witness_key,
        }


@dataclass(frozen=True)
class Boundary:
    """The measured gap between two layers, and the constraints on crossing it."""

    layer_a: Layer
    layer_b: Layer
    required_coherence_s: float
    intrinsic_coherence_s: float
    gap_ratio: float
    decades: float
    axes: Tuple[BoundaryAxis, ...]

    @property
    def crossing_condition(self) -> str:
        return (
            "hold coherence for {:.3g} s at {:.0f} K in water, where the "
            "unstable layer allows {:.3g} s".format(
                self.required_coherence_s,
                ROOM_TEMPERATURE_K,
                self.intrinsic_coherence_s,
            )
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "layer_a": self.layer_a.key,
            "layer_b": self.layer_b.key,
            "layer_a_name": self.layer_a.name,
            "layer_b_name": self.layer_b.name,
            "required_coherence_s": self.required_coherence_s,
            "intrinsic_coherence_s": self.intrinsic_coherence_s,
            "gap_ratio": self.gap_ratio,
            "decades": self.decades,
            "crossing_condition": self.crossing_condition,
            "axes": [axis.as_dict() for axis in self.axes],
        }


# ---------------------------------------------------------------------------
# inventions and findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Invention:
    """A filing: what it says, and what it offers as evidence.

    ``claim`` is prose and is never judged. ``witness`` is the only thing that is,
    which keeps the audit honest -- prose can be eloquent and empty at the same
    time, and that combination is the usual case.
    """

    identifier: str
    title: str
    claim: str
    witness: Mapping[str, Any]
    targets: Tuple[str, str] = ()
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "claim": self.claim,
            "witness": dict(self.witness),
            "targets": list(self.targets),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class WitnessFault:
    """One thing wrong with the evidence, and whether it alone is decisive.

    ``verdict=None`` is an advisory note. It is a separate field rather than a
    weaker string so that no code path can accidentally fold a note into the
    severity computation -- the same rule :mod:`agent_metabolism.lean_auditor`
    applies to ``HIDDEN_BODY``.
    """

    kind: str
    verdict: Optional[str]
    detail: str
    axis: Optional[str] = None

    @property
    def decisive(self) -> bool:
        return self.verdict is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "verdict": self.verdict,
            "decisive": self.decisive,
            "detail": self.detail,
            "axis": self.axis,
        }


@dataclass(frozen=True)
class BoundaryAudit:
    """The result of one crossing audit."""

    invention: str
    claim: str
    verdict: str
    boundary: Boundary
    faults: Tuple[WitnessFault, ...]
    witness_digest: str
    mechanism_recognized: bool
    closes_gap: bool
    checks: Tuple[Dict[str, str], ...] = ()

    #: The scope of the measurement, stated so it stays true for every verdict.
    #: A verdict quoted without this sentence becomes a lie by citation.
    establishes: str = (
        "whether the claim is falsifiable and whether its own arithmetic crosses "
        "the boundary. It never establishes that the mechanism works: there is no "
        "spectrometer here, only a check on what the filing put in writing."
    )

    def reading(self) -> str:
        """What this particular verdict does and does not say."""
        if self.verdict == PASS:
            return (
                "The filing is falsifiable and its own numbers are sufficient. "
                "That is a green light for building the experiment, not evidence "
                "that it will work."
            )
        if self.verdict == FAIL:
            return (
                "The filing contradicts itself. This is not a missing detail: a "
                "stated number falls on the wrong side of the boundary, so the "
                "claim is refuted by its own filing."
            )
        return (
            "Nobody examined anything: the filing states an outcome without "
            "supplying a witness. Not false -- unaudited, which is a different "
            "finding and stays one."
        )

    def decisive_faults(self) -> Tuple[WitnessFault, ...]:
        return tuple(fault for fault in self.faults if fault.decisive)

    def notes(self) -> Tuple[WitnessFault, ...]:
        return tuple(fault for fault in self.faults if not fault.decisive)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "invention": self.invention,
            "claim": self.claim,
            "verdict": self.verdict,
            "boundary": self.boundary.as_dict(),
            "faults": [fault.as_dict() for fault in self.faults],
            "checks": [dict(check) for check in self.checks],
            "witness_digest": self.witness_digest,
            "mechanism_recognized": self.mechanism_recognized,
            "closes_gap": self.closes_gap,
            "establishes": self.establishes,
            "reading": self.reading(),
        }


# ---------------------------------------------------------------------------
# the default knowledge base
# ---------------------------------------------------------------------------

#: The two layers, as measured requirements. ``1e-12`` s is deliberately generous
#: to the quantum side. The 300 K coherence reported by Engel et al. was ~660 fs,
#: and later work argued that even that signal was vibrational rather than
#: electronic, so the true electronic lifetime at room temperature is not longer
#: than a picosecond and is plausibly much shorter. A boundary audit that
#: flattered the quantum side would make every crossing look easy.
DEFAULT_LAYERS: Dict[str, Layer] = {
    "Theory_Quantum": Layer(
        key="Theory_Quantum",
        name="Theory_Quantum -- electronic coherence at 300 K",
        regime="quantum",
        temperature_k=300.0,
        required_coherence_s=1e-12,
        intrinsic_coherence_s=1e-12,
        medium="condensed phase",
        description=(
            "A phase relationship between electronic states survives only until "
            "the surrounding bath randomises it. At 300 K in a condensed phase "
            "that is on the order of a picosecond, and the environment is not "
            "noise to be filtered out -- it is the medium the system lives in."
        ),
        sources=(
            "Engel et al., Nature 446, 782 (2007): ~660 fs beating in FMO at 300 K",
            "Duan et al., PNAS 114, 8493 (2017): the long-lived beat is not "
            "electronic coherence doing the transport",
            "Thyrhaug et al., Nat. Chem. 10, 780 (2018): diverse coherences, "
            "mostly vibrational",
        ),
    ),
    "Theory_Bio": Layer(
        key="Theory_Bio",
        name="Theory_Bio -- functional continuity at 300 K",
        regime="biological",
        temperature_k=300.0,
        required_coherence_s=1.0,
        intrinsic_coherence_s=1.0,
        medium="aqueous",
        description=(
            "A biological function is not a single event but a rate that holds: "
            "an enzyme turns over, a neuron integrates, a cell regulates, for "
            "seconds and longer, in water, at 300 K, in every individual, every "
            "time. Reliability here is a property of the ensemble and of the "
            "individual at once."
        ),
        sources=(
            "Enzyme turnover numbers: ~1e-3 to 1e5 s^-1 (Berg et al., "
            "Biochemistry)",
            "Neural integration windows: ~1e-3 to 1e0 s",
        ),
    ),
}

#: Four independent axes, not one. Answering the lifetime problem by cryogenics
#: makes the temperature problem worse, which is why "narrow" is the right word:
#: the pass set is an intersection, and each new mechanism has to stay inside all
#: of them at once.
BOUNDARY_AXES: Tuple[BoundaryAxis, ...] = (
    BoundaryAxis(
        key="coherence_lifetime",
        name="coherence lifetime",
        unstable_side="order 1e-12 s before dephasing at 300 K",
        stable_side="order 1 s of functional continuity",
        why=(
            "the two layers need the same physical quantity -- a phase "
            "relationship -- to survive for wildly different durations"
        ),
        witness_key="suppression_factor",
    ),
    BoundaryAxis(
        key="temperature",
        name="operating temperature",
        unstable_side="dephasing falls steeply with temperature; isolation is the usual answer",
        stable_side="300 K, and only 300 K: water is liquid and proteins fold here",
        why=(
            "the standard way to keep coherence alive is to cool, and cooling "
            "does not scale to an organism"
        ),
        witness_key="operating_temperature_k",
    ),
    BoundaryAxis(
        key="medium",
        name="environment",
        unstable_side="vacuum, cryogenic solid, or an engineered ultra-clean device",
        stable_side="aqueous, pH 7.4 +/- 0.4, ionic strength ~0.15 M",
        why=(
            "the bath that destroys the coherence is the same bath the function "
            "requires; it cannot be removed, only lived in"
        ),
        witness_key="medium",
    ),
    BoundaryAxis(
        key="ensemble",
        name="reliability",
        unstable_side="the signal is recovered by averaging many trials",
        stable_side="one organism must work once, and again tomorrow",
        why=(
            "an effect that exists only in an average is not a mechanism an "
            "organism can use; biology needs single-shot reliability"
        ),
        witness_key="single_shot",
    ),
)

#: The filing the demo audits. Written the way such filings are actually written:
#: a confident claim, a mechanism named at the level of a field rather than a
#: structure, and no number that could be checked.
DEFAULT_INVENTIONS: Dict[str, Invention] = {
    "Patent_BioQuantum": Invention(
        identifier="Patent_BioQuantum",
        title="A bio-quantum battery using protein-encapsulated chromophores",
        claim=(
            "A special protein scaffold encapsulates a chromophore and exploits "
            "quantum coherence to transport energy at room temperature, yielding "
            "a bio-compatible battery with near-lossless transfer."
        ),
        witness={
            "mechanism": "quantum_effect_generic",
        },
        targets=("Theory_Quantum", "Theory_Bio"),
        notes=(
            "The prose names the right mechanism family -- a protein scaffold "
            "around a chromophore is exactly how this boundary would be attacked. "
            "It is the witness that is empty."
        ),
    ),
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    """A finite float, or ``None``.

    ``bool`` is excluded on purpose: ``True`` is 1 to Python and would silently
    become a suppression factor of 1, which reads as a measurement.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _same_decade(a: float, b: float, tolerance: float = _DECADE_TOLERANCE) -> bool:
    if a <= 0.0 or b <= 0.0:
        return False
    return abs(math.log10(a / b)) <= tolerance


def _worst(verdicts: Iterable[Optional[str]]) -> str:
    severity = -1
    winner = PASS
    for verdict in verdicts:
        if verdict is None:
            continue
        rank = _SEVERITY.get(verdict)
        if rank is None:
            raise BoundaryError("unknown verdict {!r}".format(verdict))
        if rank > severity:
            severity = rank
            winner = verdict
    return winner


def _digest(payload: Mapping[str, Any]) -> str:
    """``sha256:<hex>`` of the canonical witness.

    Canonical, not merely serialised: a witness that cannot be re-digested from a
    printout is not evidence a reader can check. Sorting keys is what makes the
    reproduction possible.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _describe_checks(
    boundary: Boundary, witness: Mapping[str, Any]
) -> Tuple[Dict[str, str], ...]:
    """What was actually looked at, in the filing's own terms.

    A PASS that lists nothing is indistinguishable from a bug that returns PASS.
    These rows are derived from the witness rather than from the verdict, so they
    can disagree with it -- which is the only way they are worth printing.
    """
    checks: List[Dict[str, str]] = []

    mechanism = witness.get("mechanism")
    if mechanism is None:
        checks.append({"check": "mechanism", "result": "not stated"})
    elif _norm(mechanism) in _PLACEHOLDER_MECHANISMS:
        checks.append(
            {"check": "mechanism", "result": "{!r}: a domain, not a mechanism".format(mechanism)}
        )
    else:
        checks.append(
            {
                "check": "mechanism",
                "result": "{!r} ({})".format(
                    mechanism,
                    "recognised" if _norm(mechanism) in MECHANISMS else "not in table",
                ),
            }
        )

    params = witness.get("structural_parameters")
    if isinstance(params, Mapping):
        numeric = sum(1 for value in params.values() if _number(value) is not None)
        checks.append(
            {
                "check": "structure",
                "result": "{} numeric parameter(s), {} required".format(
                    numeric, MIN_STRUCTURAL_PARAMETERS
                ),
            }
        )
    else:
        checks.append({"check": "structure", "result": "not stated"})

    factor = _number(witness.get("suppression_factor"))
    checks.append(
        {
            "check": "suppression",
            "result": "not stated"
            if factor is None
            else "{:.3g} against {:.0e} required".format(factor, boundary.gap_ratio),
        }
    )

    tau = _number(witness.get("coherence_time_s"))
    if tau is None:
        checks.append({"check": "lifetime", "result": "not stated"})
    else:
        expected = boundary.intrinsic_coherence_s * (factor or 0.0)
        consistency = (
            "derivable from its own factor"
            if _same_decade(tau, expected)
            else "not derivable from its own factor"
        )
        checks.append(
            {
                "check": "lifetime",
                "result": "{:.3g} s ({}) against {:.3g} s required".format(
                    tau, consistency, boundary.required_coherence_s
                ),
            }
        )

    temperature = _number(witness.get("operating_temperature_k"))
    checks.append(
        {
            "check": "temperature",
            "result": "not stated"
            if temperature is None
            else "{:.1f} K against {:.0f} +/- {:.0f} K".format(
                temperature, ROOM_TEMPERATURE_K, TEMPERATURE_TOLERANCE_K
            ),
        }
    )

    medium = witness.get("medium")
    checks.append(
        {
            "check": "environment",
            "result": "not stated" if medium is None else str(medium),
        }
    )

    checks.append(
        {
            "check": "reliability",
            "result": "single-shot claimed"
            if witness.get("single_shot")
            else "not claimed (advisory only)",
        }
    )
    return tuple(checks)


def _norm(value: Any) -> str:
    return str(value).strip().lower()


# ---------------------------------------------------------------------------
# the auditor
# ---------------------------------------------------------------------------


class BoundaryAuditor:
    """Audits claims of crossing between two physical layers.

    Args:
        layers: name -> :class:`Layer`. Defaults to the two-layer knowledge base
            this module ships. A caller with better measurements should replace
            it rather than edit it: the numbers are what the verdict rests on.
        inventions: identifier -> :class:`Invention`. The registry is what
            :meth:`find_blind_boundaries` reads, so an empty registry makes every
            axis look blind. That is a fact about the registry, not the world.
        membrane: anything exposing ``append_call(tool, verdict, **fields)``.
            Duck-typed, like everywhere else; optional, and its absence is
            recorded rather than raised.

    Example::

        >>> auditor = BoundaryAuditor()
        >>> result = auditor.audit_boundary_crossing(
        ...     "Theory_Quantum", "Theory_Bio", "Patent_BioQuantum")
        >>> result["verdict"]
        'INVALID'
    """

    def __init__(
        self,
        layers: Optional[Mapping[str, Layer]] = None,
        inventions: Optional[Mapping[str, Invention]] = None,
        membrane: Any = None,
    ) -> None:
        self._layers: Dict[str, Layer] = dict(layers or DEFAULT_LAYERS)
        self._inventions: Dict[str, Invention] = dict(
            DEFAULT_INVENTIONS if inventions is None else inventions
        )
        self._membrane = membrane
        if membrane is not None:
            appender = getattr(membrane, "append_call", None)
            if not callable(appender):
                raise BoundaryError(
                    "membrane must expose append_call(), got {!r}".format(
                        type(membrane).__name__
                    )
                )

    # -- knowledge base ---------------------------------------------------

    @property
    def layers(self) -> Dict[str, Layer]:
        return dict(self._layers)

    @property
    def inventions(self) -> Dict[str, Invention]:
        return dict(self._inventions)

    def add_invention(self, invention: Invention) -> None:
        """Register a filing so the blind-boundary map can see it."""
        if not isinstance(invention, Invention):
            raise BoundaryError(
                "invention must be an Invention, got {!r}".format(
                    type(invention).__name__
                )
            )
        self._inventions[invention.identifier] = invention

    def layer(self, key: str) -> Layer:
        try:
            return self._layers[key]
        except KeyError:
            raise BoundaryError(
                "unknown layer {!r}; known: {}".format(key, ", ".join(sorted(self._layers)))
            )

    def boundary(self, layer_a: str, layer_b: str) -> Boundary:
        """The gap between two layers: what has to be true to stand on both.

        The ratio is not ``required / intrinsic`` within one layer -- that is 1 by
        construction -- but the *worst requirement on one side over the best
        provision on the other*. That is the number an invention has to beat, and
        it is why this is a boundary rather than a comparison.
        """
        a = self.layer(layer_a)
        b = self.layer(layer_b)
        if a.key == b.key:
            raise BoundaryError(
                "a boundary needs two layers; got {!r} twice".format(a.key)
            )
        required = max(a.required_coherence_s, b.required_coherence_s)
        intrinsic = min(a.intrinsic_coherence_s, b.intrinsic_coherence_s)
        if required <= 0.0 or intrinsic <= 0.0:
            raise BoundaryError("coherence times must be positive")
        gap = required / intrinsic
        return Boundary(
            layer_a=a,
            layer_b=b,
            required_coherence_s=required,
            intrinsic_coherence_s=intrinsic,
            gap_ratio=gap,
            decades=math.log10(gap),
            axes=BOUNDARY_AXES,
        )

    # -- the audit --------------------------------------------------------

    def audit_boundary_crossing(
        self,
        layer_a: str,
        layer_b: str,
        invention: Any,
    ) -> Dict[str, Any]:
        """Judge whether a filing offers a witness for this boundary.

        Returns the audit as a dict. The verdict is composed by worst-wins over
        the decisive faults; no advisory note may ever change it.
        """
        boundary = self.boundary(layer_a, layer_b)
        filing = self._coerce_invention(invention)
        witness = dict(filing.witness or {})
        faults: List[WitnessFault] = []

        mechanism_recognized = False
        if not witness:
            faults.append(
                WitnessFault(
                    kind="NO_WITNESS",
                    verdict=INVALID,
                    detail=(
                        "the filing offers no witness at all: no mechanism, no "
                        "structure, no number. Nothing here could be measured and "
                        "found wrong, so there is nothing to audit."
                    ),
                )
            )
        else:
            mechanism_recognized = self._check_mechanism(witness, faults)
            self._check_structure(witness, faults)
            factor = self._check_arithmetic(boundary, witness, faults)
            self._check_temperature(witness, faults)
            self._check_medium(witness, faults)
            self._check_reliability(witness, faults)
            if filing.targets and {filing.targets[0], filing.targets[1]} != {
                layer_a,
                layer_b,
            }:
                faults.append(
                    WitnessFault(
                        kind="TARGETS_ANOTHER_BOUNDARY",
                        verdict=None,
                        detail=(
                            "the filing registers {} -> {} but is being audited "
                            "against {} -> {}; a witness for one boundary is not "
                            "evidence for another".format(
                                filing.targets[0],
                                filing.targets[1],
                                layer_a,
                                layer_b,
                            )
                        ),
                    )
                )

        verdict = _worst(fault.verdict for fault in faults)
        audit = BoundaryAudit(
            invention=filing.identifier,
            claim=filing.claim,
            verdict=verdict,
            boundary=boundary,
            faults=tuple(faults),
            witness_digest=_digest({"witness": witness, "claim": filing.claim}),
            mechanism_recognized=mechanism_recognized,
            closes_gap=(verdict == PASS),
            checks=_describe_checks(boundary, witness),
        )
        result = audit.as_dict()
        self._log(result)
        return result

    # -- individual checks ------------------------------------------------

    def _check_mechanism(
        self, witness: Mapping[str, Any], faults: List[WitnessFault]
    ) -> bool:
        raw = witness.get("mechanism")
        if raw is None:
            faults.append(
                WitnessFault(
                    kind="NO_MECHANISM",
                    verdict=INVALID,
                    detail=(
                        "no mechanism named. Without one there is no proposed "
                        "cause to test, only a proposed outcome."
                    ),
                    axis="coherence_lifetime",
                )
            )
            return False
        name = _norm(raw)
        if name in _PLACEHOLDER_MECHANISMS:
            faults.append(
                WitnessFault(
                    kind="NO_MECHANISM",
                    verdict=INVALID,
                    detail=(
                        "{!r} names a domain, not a mechanism. A domain cannot be "
                        "measured, so it cannot be wrong -- which is what makes it "
                        "an unsuitable witness rather than a weak one.".format(raw)
                    ),
                    axis="coherence_lifetime",
                )
            )
            return False
        if name not in MECHANISMS:
            faults.append(
                WitnessFault(
                    kind="UNRECOGNISED_MECHANISM",
                    verdict=None,
                    detail=(
                        "{!r} is not in this auditor's mechanism table, so its "
                        "plausibility is not assessed. The arithmetic is still "
                        "checked; a novel mechanism is not an offence.".format(raw)
                    ),
                    axis="coherence_lifetime",
                )
            )
            return False
        return True

    def _check_structure(
        self, witness: Mapping[str, Any], faults: List[WitnessFault]
    ) -> None:
        params = witness.get("structural_parameters")
        if not isinstance(params, Mapping) or not params:
            faults.append(
                WitnessFault(
                    kind="NO_STRUCTURE",
                    verdict=INVALID,
                    detail=(
                        "no structural parameters. The claim is about a scaffold "
                        "holding a chromophore apart from its bath; without a "
                        "geometry there is no scaffold, only the word for one."
                    ),
                    axis="medium",
                )
            )
            return
        numeric = {
            key: _number(value)
            for key, value in params.items()
            if _number(value) is not None
        }
        if len(numeric) < MIN_STRUCTURAL_PARAMETERS:
            faults.append(
                WitnessFault(
                    kind="NO_STRUCTURE",
                    verdict=INVALID,
                    detail=(
                        "{} numeric structural parameter(s); at least {} are "
                        "required. Fewer is a slogan with a decimal point.".format(
                            len(numeric), MIN_STRUCTURAL_PARAMETERS
                        )
                    ),
                    axis="medium",
                )
            )
            return
        non_positive = [key for key, value in numeric.items() if value <= 0.0]
        if non_positive:
            faults.append(
                WitnessFault(
                    kind="BAD_STRUCTURE",
                    verdict=FAIL,
                    detail=(
                        "structural parameter(s) at or below zero: {}. A distance "
                        "or a barrier height of zero is not an unverified claim, "
                        "it is a claim that cannot be true.".format(
                            ", ".join(sorted(non_positive))
                        )
                    ),
                    axis="medium",
                )
            )

    def _check_arithmetic(
        self,
        boundary: Boundary,
        witness: Mapping[str, Any],
        faults: List[WitnessFault],
    ) -> Optional[float]:
        """The check nobody can argue with: do the filing's numbers add up?"""
        raw_factor = witness.get("suppression_factor")
        factor = _number(raw_factor)
        if factor is None:
            faults.append(
                WitnessFault(
                    kind="NO_SUPPRESSION",
                    verdict=INVALID,
                    detail=(
                        "no suppression factor. The boundary is a ratio "
                        "({:.0e}, {:.1f} decades); a filing that does not say how "
                        "much of it it beats has not addressed it.".format(
                            boundary.gap_ratio, boundary.decades
                        )
                    ),
                    axis="coherence_lifetime",
                )
            )
            return None
        if factor <= 1.0:
            faults.append(
                WitnessFault(
                    kind="NO_SUPPRESSION",
                    verdict=FAIL,
                    detail=(
                        "suppression factor {:.3g} is not protection. The filing "
                        "claims the effect survives the boundary while stating a "
                        "factor that leaves it where it started.".format(factor)
                    ),
                    axis="coherence_lifetime",
                )
            )
        elif factor < boundary.gap_ratio:
            shortfall = boundary.gap_ratio / factor
            faults.append(
                WitnessFault(
                    kind="INSUFFICIENT_SUPPRESSION",
                    verdict=FAIL,
                    detail=(
                        "suppression {:.3g} against {:.0e} required: short by "
                        "{:.3g} ({:.1f} decades). The filing refutes itself; the "
                        "contradiction is its own.".format(
                            factor,
                            boundary.gap_ratio,
                            shortfall,
                            math.log10(shortfall) if shortfall > 0 else 0.0,
                        )
                    ),
                    axis="coherence_lifetime",
                )
            )

        raw_tau = witness.get("coherence_time_s")
        tau = _number(raw_tau)
        if tau is None:
            faults.append(
                WitnessFault(
                    kind="NO_COHERENCE_TIME",
                    verdict=INVALID,
                    detail=(
                        "no coherence lifetime stated. The factor alone is a "
                        "multiplier; without the lifetime it multiplies there is "
                        "no arithmetic to check."
                    ),
                    axis="coherence_lifetime",
                )
            )
            return factor
        expected = boundary.intrinsic_coherence_s * factor
        if not _same_decade(tau, expected):
            faults.append(
                WitnessFault(
                    kind="ARITHMETIC_INCONSISTENT",
                    verdict=FAIL,
                    detail=(
                        "stated lifetime {:.3g} s, but {:.3g} s x {:.3g} = "
                        "{:.3g} s. Off by {:.1f} decades: the filing contradicts "
                        "itself before the physics is even reached.".format(
                            tau,
                            boundary.intrinsic_coherence_s,
                            factor,
                            expected,
                            abs(math.log10(tau / expected)) if tau > 0 else 0.0,
                        )
                    ),
                    axis="coherence_lifetime",
                )
            )
        if tau < boundary.required_coherence_s and _same_decade(tau, expected):
            faults.append(
                WitnessFault(
                    kind="INSUFFICIENT_LIFETIME",
                    verdict=FAIL,
                    detail=(
                        "the lifetime is {:.3g} s where the other layer needs "
                        "{:.3g} s -- {:.1f} decades short, even though the "
                        "arithmetic with its own factor is consistent. Being "
                        "self-consistent is not the same as being sufficient.".format(
                            tau,
                            boundary.required_coherence_s,
                            math.log10(boundary.required_coherence_s / tau),
                        )
                    ),
                    axis="coherence_lifetime",
                )
            )
        return factor

    def _check_temperature(
        self, witness: Mapping[str, Any], faults: List[WitnessFault]
    ) -> None:
        temperature = _number(witness.get("operating_temperature_k"))
        if temperature is None:
            faults.append(
                WitnessFault(
                    kind="NO_TEMPERATURE",
                    verdict=INVALID,
                    detail=(
                        "operating temperature unstated. For this boundary it is "
                        "the constraint most likely to be violated, because "
                        "cooling is the usual way to win the lifetime argument."
                    ),
                    axis="temperature",
                )
            )
            return
        drift = abs(temperature - ROOM_TEMPERATURE_K)
        if drift > TEMPERATURE_TOLERANCE_K:
            faults.append(
                WitnessFault(
                    kind="WRONG_TEMPERATURE",
                    verdict=FAIL,
                    detail=(
                        "{:.1f} K is {:.1f} K from room temperature; the other "
                        "layer is defined at {:.0f} K +/- {:.0f} K. The filing may "
                        "have crossed the coherence gap and still not crossed the "
                        "boundary, because it left the layer it was addressing."
                        .format(
                            temperature,
                            drift,
                            ROOM_TEMPERATURE_K,
                            TEMPERATURE_TOLERANCE_K,
                        )
                    ),
                    axis="temperature",
                )
            )

    def _check_medium(
        self, witness: Mapping[str, Any], faults: List[WitnessFault]
    ) -> None:
        medium = witness.get("medium")
        if medium is None:
            faults.append(
                WitnessFault(
                    kind="NO_MEDIUM",
                    verdict=INVALID,
                    detail=(
                        "no environment stated. The bath that destroys the "
                        "coherence is the bath the function needs; not naming it "
                        "leaves the central difficulty untouched."
                    ),
                    axis="medium",
                )
            )
            return
        if _norm(medium) not in {"aqueous", "water", "aqueous_physiological"}:
            faults.append(
                WitnessFault(
                    kind="WRONG_MEDIUM",
                    verdict=FAIL,
                    detail=(
                        "medium {!r} is not aqueous. A vacuum or a cryogenic "
                        "solid is where quantum coherence is normally kept; "
                        "biology cannot run there, so this is the other layer "
                        "being abandoned rather than crossed.".format(medium)
                    ),
                    axis="medium",
                )
            )

    def _check_reliability(
        self, witness: Mapping[str, Any], faults: List[WitnessFault]
    ) -> None:
        """Advisory on purpose.

        Requiring a single-shot demonstration would be right physically and is
        not verifiable here: a filing could claim it and we could not tell. So the
        absence is reported as a note, which by construction cannot change the
        verdict. Making it decisive would let an unverifiable field decide an
        audit, which is the defect this package keeps naming.
        """
        if not witness.get("single_shot"):
            faults.append(
                WitnessFault(
                    kind="RELIABILITY_UNSTATED",
                    verdict=None,
                    detail=(
                        "no single-shot or per-individual demonstration is "
                        "claimed. An effect recovered only by averaging many "
                        "trials may still be real, and is still not something an "
                        "organism can use; this is a note, not a finding, because "
                        "the claim could not be verified either way."
                    ),
                    axis="ensemble",
                )
            )

    # -- the blind-boundary map -------------------------------------------

    def find_blind_boundaries(
        self, layer_a: str, layer_b: str
    ) -> List[Dict[str, Any]]:
        """Which axes of this boundary no registered filing witnesses.

        Three states, and conflating them would be the usual mistake:

        ``blind``       no filing mentions the axis. A genuinely open question,
                        *within this registry*.
        ``unproven``    filings attempt it and none suffices. Not a blind spot --
                        a contested one, which is a different kind of opportunity.
        ``crossed``     some filing's witness survives the audit for that axis.

        The answer is a property of the loaded registry. An empty registry makes
        all of physics look blind, and that is stated in every row rather than in
        a footnote.
        """
        boundary = self.boundary(layer_a, layer_b)
        rows: List[Dict[str, Any]] = []
        for axis in boundary.axes:
            attempts: List[str] = []
            passed: List[str] = []
            reasons: List[str] = []
            for filing in self._inventions.values():
                crossed, why = self._axis_crossing(axis, filing, boundary)
                if crossed is None:
                    continue
                attempts.append(filing.identifier)
                if crossed:
                    passed.append(filing.identifier)
                elif why:
                    reasons.append("{}: {}".format(filing.identifier, why))

            if passed:
                status = "crossed"
            elif attempts:
                status = "unproven"
            else:
                status = "blind"

            rows.append(
                {
                    "axis": axis.key,
                    "name": axis.name,
                    "status": status,
                    "unstable_side": axis.unstable_side,
                    "stable_side": axis.stable_side,
                    "why_it_is_narrow": axis.why,
                    "attempts": attempts,
                    "crossed_by": passed,
                    "shortfall": reasons,
                    "registry": "the loaded registry: {} filing(s)".format(
                        len(self._inventions)
                    ),
                }
            )
        return rows

    def _axis_crossing(
        self, axis: BoundaryAxis, filing: Invention, boundary: Boundary
    ) -> Tuple[Optional[bool], str]:
        """Did this filing offer a witness for this one axis?

        Returns ``(None, "")`` when the filing does not address the axis at all --
        which is what makes the axis blind rather than contested.
        """
        witness = dict(filing.witness or {})
        if axis.key == "coherence_lifetime":
            factor = _number(witness.get("suppression_factor"))
            if factor is None:
                return None, ""
            if factor < boundary.gap_ratio:
                return False, "suppression {:.3g}x short of {:.3g}x".format(
                    factor, boundary.gap_ratio
                )
            return True, ""
        if axis.key == "temperature":
            temperature = _number(witness.get("operating_temperature_k"))
            if temperature is None:
                return None, ""
            if abs(temperature - ROOM_TEMPERATURE_K) > TEMPERATURE_TOLERANCE_K:
                return False, "{:.1f} K outside the 300 K layer".format(temperature)
            return True, ""
        if axis.key == "medium":
            medium = witness.get("medium")
            if medium is None:
                return None, ""
            if _norm(medium) not in {"aqueous", "water", "aqueous_physiological"}:
                return False, "{!r} is not aqueous".format(medium)
            return True, ""
        if axis.key == "ensemble":
            if "single_shot" not in witness:
                return None, ""
            return bool(witness.get("single_shot")), (
                "" if witness.get("single_shot") else "no single-shot demonstration"
            )
        return None, ""

    # -- plumbing ---------------------------------------------------------

    def _coerce_invention(self, invention: Any) -> Invention:
        if isinstance(invention, Invention):
            return invention
        if isinstance(invention, str):
            try:
                return self._inventions[invention]
            except KeyError:
                raise BoundaryError(
                    "unknown invention {!r}; known: {}".format(
                        invention, ", ".join(sorted(self._inventions))
                    )
                )
        if isinstance(invention, Mapping):
            try:
                identifier = invention["identifier"]
                claim = invention["claim"]
            except KeyError as missing:
                raise BoundaryError(
                    "an invention dict needs {}; got {}".format(
                        missing, sorted(invention)
                    )
                )
            return Invention(
                identifier=identifier,
                title=invention.get("title", identifier),
                claim=claim,
                witness=invention.get("witness") or {},
                targets=tuple(invention.get("targets") or ()),
                notes=invention.get("notes", ""),
            )
        raise BoundaryError(
            "invention must be an Invention, a name, or a dict; got {!r}".format(
                type(invention).__name__
            )
        )

    def _log(self, result: Mapping[str, Any]) -> None:
        """Append to the membrane, or record that it could not."""
        if self._membrane is None:
            result["logged"] = False
            result["log_note"] = "no membrane attached: this audit is unrecorded"
            return
        boundary = result["boundary"]
        attempts = (
            lambda: self._membrane.append_call(
                tool="boundary_auditor",
                verdict=result["verdict"],
                agent="boundary_auditor",
                intent="audit {} across {} -> {}".format(
                    result["invention"], boundary["layer_a"], boundary["layer_b"]
                ),
                witness=result["witness_digest"],
                details={
                    "gap_decades": boundary["decades"],
                    "faults": [f["kind"] for f in result["faults"]],
                },
            ),
            lambda: self._membrane.append_call(
                tool="boundary_auditor",
                verdict=result["verdict"],
                agent="boundary_auditor",
                intent="audit {}".format(result["invention"]),
            ),
            lambda: self._membrane.append_call("boundary_auditor", result["verdict"]),
        )
        for attempt in attempts:
            try:
                attempt()
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - a membrane may refuse
                result["logged"] = False
                result["log_note"] = "membrane refused the entry: {}".format(exc)
                return
            result["logged"] = True
            result["log_note"] = ""
            return
        result["logged"] = False
        result["log_note"] = (
            "the membrane accepted none of this auditor's signature shapes; the "
            "audit stands but is unrecorded"
        )

    # -- rendering --------------------------------------------------------

    @staticmethod
    def render_audit(result: Mapping[str, Any]) -> str:
        """The audit as plain text. ASCII only: it has to survive being logged."""
        width = 78
        boundary = result["boundary"]
        lines: List[str] = []
        lines.append("=" * width)
        lines.append("BOUNDARY AUDIT  {}".format(result["invention"]))
        lines.append("=" * width)
        claim_lines = _wrap(" ".join(result["claim"].split()), width - 19)
        lines.append("  claim        : {}".format(claim_lines[0]))
        for chunk in claim_lines[1:]:
            lines.append("  {:<15} {}".format("", chunk))
        lines.append(
            "  layers       : {} -> {}".format(
                boundary["layer_a"], boundary["layer_b"]
            )
        )
        lines.append("  verdict      : {}".format(result["verdict"]))
        lines.append("")

        lines.append("  THE BOUNDARY")
        lines.append("  " + "-" * (width - 2))
        lines.append(
            "  coherence gap      : {:.1f}e{:d}x  ({:.1f} decades of magnitude)".format(
                boundary["gap_ratio"] / (10 ** int(math.floor(boundary["decades"]))),
                int(math.floor(boundary["decades"])),
                boundary["decades"],
            )
        )
        lines.append(
            "  unstable side      : {:.3g} s at {:.0f} K before the bath wins".format(
                boundary["intrinsic_coherence_s"], ROOM_TEMPERATURE_K
            )
        )
        lines.append(
            "  stable side needs  : {:.3g} s of functional continuity".format(
                boundary["required_coherence_s"]
            )
        )
        condition = _wrap(" ".join(boundary["crossing_condition"].split()), width - 25)
        lines.append("  crossing condition : {}".format(condition[0]))
        for chunk in condition[1:]:
            lines.append("  {:<20} {}".format("", chunk))
        lines.append("")
        lines.append("  It is an intersection, not a single wall. All of these, at")
        lines.append("  once, or the boundary is not crossed:")
        lines.append("")
        for axis in boundary["axes"]:
            lines.append("    {}".format(axis["name"]))
            for chunk in _wrap("unstable: " + axis["unstable_side"], width - 8):
                lines.append("      " + chunk)
            for chunk in _wrap("stable:   " + axis["stable_side"], width - 8):
                lines.append("      " + chunk)
        lines.append("")

        faults = result["faults"]
        decisive = [f for f in faults if f["decisive"]]
        notes = [f for f in faults if not f["decisive"]]
        lines.append("  WHAT WAS CHECKED")
        lines.append("  " + "-" * (width - 2))
        for check in result.get("checks", ()):
            row = "{}: {}".format(check["check"], check["result"])
            wrapped = _wrap(row, width - 8)
            lines.append("  - " + wrapped[0])
            for chunk in wrapped[1:]:
                lines.append("    " + chunk)
        lines.append("")
        lines.append("  WHY IT DID NOT CROSS" if decisive else "  RESULT")
        lines.append("  " + "-" * (width - 2))
        if not decisive:
            lines.append("  no decisive fault: the witness survived every check")
            lines.append("")
        for fault in decisive:
            lines.append("  [{}] {}".format(fault["kind"], fault["verdict"]))
            for chunk in _wrap(fault["detail"], width - 6):
                lines.append("    " + chunk)
            lines.append("")
        if notes:
            lines.append("  notes (cannot change a verdict)")
            for note in notes:
                lines.append("  [{}]".format(note["kind"]))
                for chunk in _wrap(note["detail"], width - 6):
                    lines.append("    " + chunk)
            lines.append("")

        lines.append("  WHAT THIS VERDICT MEANS")
        lines.append("  " + "-" * (width - 2))
        for chunk in _wrap(result["reading"], width - 4):
            lines.append("  " + chunk)
        lines.append("")
        for chunk in _wrap("Scope of the measurement: " + result["establishes"], width - 4):
            lines.append("  " + chunk)
        lines.append("")
        lines.append("  witness digest     :")
        lines.append("    {}".format(result["witness_digest"]))
        lines.append(
            "  recorded           : {}".format(
                "yes" if result.get("logged") else result.get("log_note", "no")
            )
        )
        lines.append("=" * width)
        return "\n".join(lines)

    @staticmethod
    def render_blind(result: Sequence[Mapping[str, Any]], layer_a: str, layer_b: str) -> str:
        """The blind-boundary map as plain text."""
        width = 78
        lines: List[str] = []
        lines.append("=" * width)
        lines.append("BLIND BOUNDARY MAP  {} -> {}".format(layer_a, layer_b))
        lines.append("=" * width)
        for row in result:
            label = "[{}]".format(row["status"])
            lines.append("  {:<42} {:>10}".format(row["name"], label))
            for chunk in _wrap("why: " + row["why_it_is_narrow"], width - 6):
                lines.append("    " + chunk)
            for short in row["shortfall"]:
                for chunk in _wrap("attempted: " + short, width - 6):
                    lines.append("    " + chunk)
            if row["status"] == "blind":
                lines.append("    no registered filing even addresses this axis")
            lines.append("")
        scope = (
            "scope: {}, which is not a census of the field. A blind row means "
            "nobody in this registry has looked -- an invitation, not a verdict, "
            "and the reason an empty registry is not a result.".format(
                result[0]["registry"] if result else "an empty registry"
            )
        )
        for chunk in _wrap(scope, width - 4):
            lines.append("  " + chunk)
        lines.append("=" * width)
        return "\n".join(lines)


def _wrap(text: str, width: int) -> List[str]:
    """Wrap to the report width, ASCII only, no dependency."""
    lines: List[str] = []
    current = ""
    for word in str(text).split():
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]
