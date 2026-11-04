"""Fractal interface evolution: every crossing narrows the next gap and widens the reach.

An interface is not a medium
----------------------------
The instinct is to explain an interface with the stuff between the two sides --
air carries sound, copper carries current. That explanation is circular. Air is
not the substrate of a conversation; air is itself a layer, bounded and defined
by the layer above it, and a conversation crosses it only because a language
fits through a very narrow opening in it. A medium is just the next layer up,
mistaken for a floor because it is where we happened to stop looking.

So this module models an interface as a *gap*: what has to be true for two layers
to reach each other, and how much reach that buys.

The fractal law
---------------
Crossing a gap does not delete it. It closes the old opening and opens a new one
**of the same shape**, one octave in:

    air          crossed by language        -> a small tribe is connected
    space/time   crossed by writing         -> an industrial society is connected
    semantics    crossed by schema+witness  -> the digital and physical worlds

Each line has the same form: a narrow condition, a large reach, and a new gap
inside the thing that was just connected. The opening narrows -- difficulty
climbs toward 1.0 -- while the reach explodes. That is the invariant this module
enforces, and it is why the third row is not the end of the story but the point
at which the interface stops being a feature and becomes infrastructure.

What is measured, and what is declared
--------------------------------------
``span``, ``difficulty`` and ``amplification`` are declared by the caller. There
is no sociometer in this package; the numbers are a model, exactly like the
energy figures elsewhere in it. What is *not* declared is everything this module
does with them:

* ``difficulty`` must climb across generations, and cannot reach 1.0, because a
  gap that can never be crossed is not an interface but a wall.
* ``amplification`` must climb, because each generation demands a stricter
  condition and pays for it with a larger reach. A chain where the third crossing
  buys less than the second is not a fractal; it is three unrelated claims.
* ``span`` is clamped at :data:`SPAN_HORIZON`, because "infinite" cannot be
  multiplied, compared, or printed. A number you cannot compute is a slogan.

The invariant that catches the most nonsense is the last one: after crossing, a
sub-interface is spawned whose span equals the newly reached span. If a caller's
numbers do not do that, the chain is not self-similar and the engine refuses to
call it evolution.

Why re-crossing is refused
--------------------------
Crossing an interface twice would double its span, and a caller in a loop would
manufacture unbounded reach without ever satisfying a new condition. Reach is
not a counter. An interface that is already crossed must be *superseded* by its
sub-interface, not re-crossed.

Zero third-party dependencies: standard library, plus the package's verdict
lattice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .double_entry import FAIL, INVALID, PASS

__all__ = [
    "DEFAULT_AMPLIFICATION",
    "FractalEvolver",
    "Interface",
    "InterfaceError",
    "Layer",
    "NARROW_THRESHOLD",
    "NESTING_RATIO",
    "SPAN_HORIZON",
    "VERDICTS",
    "format_span",
]

#: The three verdicts are imported from the package's own lattice rather than
#: restated. Two spellings of PASS that could drift apart is the bug the lattice
#: exists to prevent; a fourth value here would be a private arithmetic.
VERDICTS = (PASS, FAIL, INVALID)

#: A gap whose condition is easier than this is not a boundary. Anyone can cross
#: open space; the thing worth naming is the opening that only one narrow
#: condition fits through. The threshold is the module's one policy dial.
NARROW_THRESHOLD = 0.8

#: How much a crossing multiplies the connected span, when the interface does not
#: say otherwise. One hundred is the default the spec names; it is a default and
#: not a law, because the demonstration's own ladder is 50x, 10,000x, 1,000,000x.
#: Hardcoding 100 would have produced 2 -> 200 -> 20,000 and contradicted it.
DEFAULT_AMPLIFICATION = 100.0

#: Each generation closes half of the remaining room to impossibility, so
#: difficulty converges toward 1.0 without ever arriving. A gap that arrives is a
#: wall, and a wall is not an interface.
NESTING_RATIO = 0.5

#: The reach at which an interface stops being a feature and becomes
#: infrastructure. Finite on purpose: ``float("inf")`` cannot be multiplied,
#: compared, or printed, and a quantity that breaks arithmetic is not a
#: measurement.
SPAN_HORIZON = 1.0e12

#: Worst-wins, same ranking as the rest of the package. A contradiction outranks
#: an omission, and neither is ever promoted past a witness.
_SEVERITY = {PASS: 0, INVALID: 2, FAIL: 3}

#: Strings that name a wish rather than a mechanism. A placeholder cannot be
#: measured, so it cannot be wrong, which makes it an unsuitable witness rather
#: than a weak one.
PLACEHOLDER_CONDITIONS = frozenset(
    {
        "",
        "ai",
        "ai_powered",
        "blockchain",
        "direct",
        "generic",
        "magic",
        "none",
        "obviously",
        "trust_me",
        "unknown",
        "vibes",
    }
)


class InterfaceError(ValueError):
    """The chain, the request, or the interface cannot be honoured as given."""


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def format_span(span: float) -> str:
    """A span as a human reads it: grouped below 10^7, scientific above."""
    if span != span or span in (float("inf"), float("-inf")):
        return "unbounded"
    if span >= 1.0e7:
        return "{:.0e}".format(span).replace("e+", "e")
    if float(span).is_integer():
        return "{:,}".format(int(span))
    return "{:,.2f}".format(span)


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    """One level of organisation, on either side of a gap.

    Deliberately thin. A layer is not a component and not a medium: it is a
    *scale of description* that some other layer bounds. All the structure that
    matters lives on the :class:`Interface` between two of them, because the
    interface is where the engineering actually is.
    """

    key: str
    name: str
    domain: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise InterfaceError("a layer needs a key")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "domain": self.domain,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# interfaces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Interface:
    """A gap between two layers, and the record of everything done to it.

    Frozen, and that is load-bearing. Crossing changes span, difficulty and the
    set of sub-interfaces; an object anyone can mutate is an object whose history
    cannot be reconstructed. Changes go through :meth:`evolve`, which returns a
    new interface, so a chain is a list of values rather than a list of moments
    that have passed.

    Attributes:
        condition: the narrow thing that has to hold to cross. ``""`` means the
            shape of the next gap is known but its content is not -- an honest
            state that the engine reports rather than papering over.
        span: how much can connect once the condition holds, in whatever unit the
            caller is counting. Not comparable across domains; comparable across
            generations of one chain, which is what the fractal law is about.
        difficulty: how narrow the condition is, in ``[0, 1]``. Rises with each
            generation and never reaches 1.0.
        amplification: what crossing multiplies the span by. Must exceed 1.0; a
            crossing that connects less than the thing it replaces is not a
            crossing.
    """

    source: str
    target: str
    condition: str
    span: float
    difficulty: float
    condition_met: bool = False
    amplification: float = DEFAULT_AMPLIFICATION
    generation: int = 0
    sub_interfaces: Tuple["Interface", ...] = ()
    parent: Optional[Tuple[str, str, int]] = None
    crossed_by: Optional[str] = None
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.source or not self.target:
            raise InterfaceError("an interface needs two layers")
        if self.source == self.target:
            raise InterfaceError(
                "an interface needs two different layers; got {!r} twice".format(
                    self.source
                )
            )
        span = _positive(self.span)
        if span is None:
            raise InterfaceError(
                "span must be a finite number greater than zero, got {!r}".format(
                    self.span
                )
            )
        difficulty = _unit(self.difficulty)
        if difficulty is None:
            raise InterfaceError(
                "difficulty must be in [0, 1], got {!r}".format(self.difficulty)
            )
        amplification = _positive(self.amplification)
        if amplification is None or amplification <= 1.0:
            raise InterfaceError(
                "amplification must exceed 1.0; {!r} would make a crossing "
                "connect less than the gap it closes".format(self.amplification)
            )
        if not isinstance(self.generation, int) or isinstance(self.generation, bool):
            raise InterfaceError("generation must be an integer")
        if self.generation < 0:
            raise InterfaceError("generation cannot be negative")
        if not isinstance(self.sub_interfaces, tuple):
            raise InterfaceError(
                "sub_interfaces must be a tuple; a list would be mutable and this "
                "object is meant to be a value"
            )

    # -- identity ---------------------------------------------------------

    @property
    def key(self) -> Tuple[str, str, int]:
        """Unordered pair plus generation: the identity of one rung of the ladder."""
        first, second = sorted((self.source, self.target))
        return (first, second, self.generation)

    @property
    def named(self) -> bool:
        """Whether the condition is a real condition or a placeholder for one."""
        return bool(self.condition.strip())

    @property
    def is_crossed(self) -> bool:
        return self.condition_met

    # -- traversal --------------------------------------------------------

    def walk(self) -> Iterator["Interface"]:
        """This interface, then every sub-interface, depth first."""
        yield self
        for child in self.sub_interfaces:
            yield from child.walk()

    def frontier(self) -> List["Interface"]:
        """The uncrossed gaps: where the chain currently ends.

        A crossed interface is not a frontier even if it has no children yet,
        because the next gap exists whether or not anyone has named it.
        """
        return [node for node in self.walk() if not node.is_crossed]

    def depth(self) -> int:
        """How many generations below this one (0 for a leaf)."""
        if not self.sub_interfaces:
            return 0
        return 1 + max(child.depth() for child in self.sub_interfaces)

    # -- evolution --------------------------------------------------------

    def evolve(self, **changes: Any) -> "Interface":
        """A copy with ``changes`` applied. The only way an interface changes."""
        return replace(self, **changes)

    def as_dict(self, depth: int = 8) -> Dict[str, Any]:
        """A JSON-ready view. ``depth`` bounds the recursion for huge chains."""
        payload: Dict[str, Any] = {
            "key": list(self.key),
            "source": self.source,
            "target": self.target,
            "condition": self.condition,
            "span": self.span,
            "difficulty": round(self.difficulty, 6),
            "condition_met": self.condition_met,
            "amplification": self.amplification,
            "generation": self.generation,
            "parent": list(self.parent) if self.parent else None,
            "crossed_by": self.crossed_by,
            "rationale": self.rationale,
        }
        if depth > 0:
            payload["sub_interfaces"] = [
                child.as_dict(depth - 1) for child in self.sub_interfaces
            ]
        else:
            payload["sub_interfaces"] = "…"
        return payload


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _positive(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number <= 0.0:
        return None
    return number


def _unit(value: Any) -> Optional[float]:
    number = _positive(value) if value != 0 else 0.0
    if number is None:
        return None
    if number < 0.0 or number > 1.0:
        return None
    return number


def _next_difficulty(difficulty: float, ratio: float) -> float:
    """Close half the remaining room. Never returns 1.0, which would be a wall."""
    candidate = difficulty + (1.0 - difficulty) * ratio
    ceiling = 1.0 - 1.0e-9
    return min(candidate, ceiling)


def _fault(kind: str, verdict: Optional[str], detail: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "verdict": verdict,
        "decisive": verdict is not None,
        "detail": detail,
    }


def _worst(faults: Sequence[Mapping[str, Any]]) -> str:
    winner = PASS
    best = -1
    for fault in faults:
        verdict = fault.get("verdict")
        if verdict is None:
            continue
        rank = _SEVERITY.get(verdict)
        if rank is None:
            raise InterfaceError("unknown verdict {!r}".format(verdict))
        if rank > best:
            best = rank
            winner = verdict
    return winner


def _read_implementation(raw: Any) -> Tuple[str, str, Optional[bool]]:
    """Normalise a condition implementation into (mechanism, evidence, satisfies).

    Accepts a mapping or a bare string. A bare string is a mechanism with no
    evidence, which is a claim and not a witness -- it parses, and then fails the
    evidence check rather than crashing.
    """
    if raw is None:
        return "", "", None
    if isinstance(raw, str):
        return raw.strip(), "", None
    if isinstance(raw, Mapping):
        mechanism = _text(raw.get("mechanism") or raw.get("condition"))
        evidence = _text(raw.get("evidence") or raw.get("witness"))
        satisfies = raw.get("satisfies")
        if satisfies is not None and not isinstance(satisfies, bool):
            raise InterfaceError(
                "satisfies must be a bool, got {!r}".format(type(satisfies).__name__)
            )
        return mechanism, evidence, satisfies
    raise InterfaceError(
        "a condition implementation must be a mapping or a string, got {!r}".format(
            type(raw).__name__
        )
    )


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value).strip()


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# the evolver
# ---------------------------------------------------------------------------


class FractalEvolver:
    """Runs the ladder: cross a gap, widen the reach, inherit a narrower gap.

    Args:
        layers: the layers interfaces may span. Optional; interfaces naming an
            unknown layer are refused, so a typo cannot invent a domain.
        membrane: anything exposing ``append_call(tool, verdict, **fields)``.
            Duck-typed like everywhere else in the package; its absence is
            recorded on the result rather than raised.
        narrow_threshold: the difficulty below which a gap is not a boundary.
        nesting_ratio: how much of the remaining room each generation closes.
        span_horizon: the reach at which an interface becomes infrastructure.

    There is exactly one door upward. :meth:`cross_interface` is the only method
    that raises ``condition_met``, multiplies a span, or spawns a sub-interface;
    nothing else in the class mutates a chain. A model whose values can be set
    from three places has no invariant, only a convention.
    """

    def __init__(
        self,
        layers: Optional[Iterable[Layer]] = None,
        membrane: Any = None,
        narrow_threshold: float = NARROW_THRESHOLD,
        nesting_ratio: float = NESTING_RATIO,
        span_horizon: float = SPAN_HORIZON,
    ) -> None:
        threshold = _unit(narrow_threshold)
        if threshold is None or not 0.0 < threshold < 1.0:
            raise InterfaceError(
                "narrow_threshold must be strictly inside (0, 1); {!r} would make "
                "every gap a boundary or none of them".format(narrow_threshold)
            )
        ratio = _unit(nesting_ratio)
        if ratio is None or not 0.0 < ratio < 1.0:
            raise InterfaceError(
                "nesting_ratio must be strictly inside (0, 1); {!r} would leave "
                "the next generation identical to this one, or reach a wall in "
                "one step".format(nesting_ratio)
            )
        horizon = _positive(span_horizon)
        if horizon is None:
            raise InterfaceError("span_horizon must be a positive number")
        if membrane is not None and not callable(getattr(membrane, "append_call", None)):
            raise InterfaceError(
                "membrane must expose append_call(), got {!r}".format(
                    type(membrane).__name__
                )
            )

        self._layers: Dict[str, Layer] = {}
        self._roots: Dict[Tuple[str, str, int], Interface] = {}
        self._history: List[Dict[str, Any]] = []
        self._membrane = membrane
        self._threshold = threshold
        self._ratio = ratio
        self._horizon = horizon
        for layer in layers or ():
            self.add_layer(layer)

    # -- layers -----------------------------------------------------------

    def add_layer(self, layer: Layer) -> Layer:
        if not isinstance(layer, Layer):
            raise InterfaceError(
                "a layer must be a Layer, got {!r}".format(type(layer).__name__)
            )
        if layer.key in self._layers:
            raise InterfaceError(
                "layer {!r} is already registered; replacing it would change "
                "every interface that spans it".format(layer.key)
            )
        self._layers[layer.key] = layer
        return layer

    def layer(self, key: str) -> Layer:
        try:
            return self._layers[key]
        except KeyError:
            raise InterfaceError(
                "unknown layer {!r}; known: {}".format(
                    key, ", ".join(sorted(self._layers)) or "(none)"
                )
            )

    # -- the ladder -------------------------------------------------------

    def seed(self, interface: Interface) -> Interface:
        """Register the first gap of a chain.

        A seed is a root: it has no parent and generation 0. Adding a second root
        for the same pair is refused, because two independent chains for one pair
        would each double the span and neither would be the truth.
        """
        if not isinstance(interface, Interface):
            raise InterfaceError(
                "a seed must be an Interface, got {!r}".format(
                    type(interface).__name__
                )
            )
        for key in (interface.source, interface.target):
            if self._layers and key not in self._layers:
                raise InterfaceError(
                    "interface spans unknown layer {!r}".format(key)
                )
        if interface.generation != 0:
            raise InterfaceError(
                "a seed is generation 0, not {}; generations are assigned by "
                "crossing, never declared".format(interface.generation)
            )
        if interface.parent is not None:
            raise InterfaceError("a seed cannot have a parent")
        if interface.key in self._roots:
            raise InterfaceError(
                "{} already has a seed; an interface that is already crossed must "
                "be superseded by its sub-interface, not re-seeded".format(
                    list(interface.key)
                )
            )
        self._roots[interface.key] = interface
        return interface

    def chains(self) -> List[Interface]:
        return [self._roots[key] for key in sorted(self._roots)]

    def chain(self, source: Optional[str] = None, target: Optional[str] = None) -> List[Interface]:
        """Every rung, oldest first. Optionally filtered to one pair."""
        rungs: List[Interface] = []
        for root in self.chains():
            for node in root.walk():
                if source and source not in (node.source, node.target):
                    continue
                if target and target not in (node.source, node.target):
                    continue
                rungs.append(node)
        rungs.sort(key=lambda node: (node.generation, node.key))
        return rungs

    def frontier(self) -> List[Interface]:
        """The gaps the chain currently ends at, across every root."""
        nodes: List[Interface] = []
        for root in self.chains():
            nodes.extend(root.frontier())
        nodes.sort(key=lambda node: node.generation)
        return nodes

    def _locate(self, target: Any) -> Tuple[Tuple[str, str, int], Interface]:
        """Find the live copy of an interface: (root key, node).

        Interfaces are values, so a caller may hold a stale copy after a
        crossing. The lookup is by identity of the rung, not by object, and the
        current value is what gets evolved -- otherwise a stale handle would
        silently undo a crossing that already happened.
        """
        key = _key_of(target)
        for root_key, root in self._roots.items():
            if root.key == key:
                return root_key, root
            for node in root.walk():
                if node.key == key:
                    return root_key, node
        raise InterfaceError(
            "no interface {} in this chain; known gaps: {}".format(
                list(key),
                ", ".join(str(list(node.key)) for node in self.frontier()) or "(none)",
            )
        )

    def _replace_node(self, root_key: Tuple[str, str, int], updated: Interface) -> None:
        """Rewrite one rung inside its tree, keeping every other value as it was."""
        def rebuild(current: Interface) -> Optional[Interface]:
            if current.key == updated.key:
                return updated
            children: List[Interface] = []
            changed = False
            for child in current.sub_interfaces:
                candidate = rebuild(child)
                if candidate is None:
                    children.append(child)
                else:
                    children.append(candidate)
                    changed = True
            if not changed:
                return None
            return current.evolve(sub_interfaces=tuple(children))

        result = rebuild(self._roots[root_key])
        if result is None:
            raise InterfaceError(
                "interface {} vanished from its chain mid-update".format(
                    list(updated.key)
                )
            )
        self._roots[root_key] = result

    # -- 1. the crossing --------------------------------------------------

    def cross_interface(
        self,
        interface: Any,
        condition_implementation: Any,
        next_condition: Optional[str] = None,
        amplification: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Try to cross a gap. This is the only door upward.

        PASS requires all three: a mechanism that is not a placeholder, evidence
        that names an artefact, and a gap narrow enough to be a boundary. On PASS
        the span is multiplied, the condition is marked met, and a sub-interface
        of the same shape is spawned one generation deeper.

        FAIL means the gap was measured and the claim refuted: the condition was
        not satisfied, or the gap was too wide to be a boundary at all. INVALID
        means nothing was offered that could be refuted. The distinction matters
        because "I brought the wrong witness" and "I brought no witness" call for
        different work.
        """
        root, live = self._locate(interface)
        if live.condition_met:
            raise InterfaceError(
                "{} is already crossed by {!r}. Reach is not a counter: re-crossing "
                "would multiply the span without satisfying a new condition. Cross "
                "its sub-interface instead.".format(
                    list(live.key), live.crossed_by
                )
            )
        mechanism, evidence, satisfies = _read_implementation(condition_implementation)

        faults: List[Dict[str, Any]] = []
        lowered = mechanism.strip().lower()
        if not lowered:
            faults.append(
                _fault(
                    "NO_IMPLEMENTATION",
                    INVALID,
                    "no condition implementation was offered. The gap is not "
                    "broken; it was never attempted.",
                )
            )
        elif lowered in PLACEHOLDER_CONDITIONS:
            faults.append(
                _fault(
                    "PLACEHOLDER_CONDITION",
                    INVALID,
                    "{!r} is not a condition. It cannot be measured, so it cannot "
                    "be wrong, which is what makes it an unsuitable witness rather "
                    "than a weak one.".format(mechanism),
                )
            )
        if not evidence:
            faults.append(
                _fault(
                    "NO_EVIDENCE",
                    INVALID,
                    "no artefact is named. A mechanism says how the crossing would "
                    "work; evidence is what lets someone else check that it did.",
                )
            )
        if satisfies is False:
            faults.append(
                _fault(
                    "CONDITION_NOT_SATISFIED",
                    FAIL,
                    "the implementation reports that it does not satisfy {!r}. The "
                    "gap is intact.".format(live.condition or "the condition"),
                )
            )
        if live.difficulty <= self._threshold:
            faults.append(
                _fault(
                    "NOT_A_BOUNDARY",
                    FAIL,
                    "difficulty {:.2f} is at or below {:.2f}. A gap this easy is "
                    "open space, not an interface: anything crosses it, so it "
                    "narrows nothing and connects nobody in particular.".format(
                        live.difficulty, self._threshold
                    ),
                )
            )

        verdict = _worst(faults)
        if verdict != PASS:
            result = {
                "interface": list(live.key),
                "condition": live.condition,
                "verdict": verdict,
                "faults": faults,
                "difficulty": round(live.difficulty, 6),
                "generation": live.generation,
                "span_before": live.span,
                "span_after": live.span,
                "amplification": None,
                "sub_interface": None,
                "escalated": False,
                "condition_met": False,
                "message": "the gap is not broken",
            }
            self._record(result)
            return result

        multiplier = live.amplification if amplification is None else amplification
        checked = _positive(multiplier)
        if checked is None or checked <= 1.0:
            raise InterfaceError(
                "amplification must exceed 1.0, got {!r}".format(multiplier)
            )
        if checked < live.amplification:
            raise InterfaceError(
                "amplification {:.2f} is below this interface's {:.2f}; a crossing "
                "that connects less than the thing it replaces is not a crossing"
                .format(checked, live.amplification)
            )

        span_after = min(live.span * checked, self._horizon)
        crossed = live.evolve(
            condition_met=True,
            span=span_after,
            amplification=checked,
            crossed_by=mechanism,
        )

        child: Optional[Interface] = None
        spawn = next_condition if next_condition is not None else ""
        if isinstance(spawn, str):
            child = Interface(
                source=crossed.source,
                target=crossed.target,
                condition=spawn.strip(),
                span=span_after,
                difficulty=_next_difficulty(crossed.difficulty, self._ratio),
                generation=crossed.generation + 1,
                parent=crossed.key,
                amplification=crossed.amplification,
                rationale=(
                    "the gap inside the space just connected; same shape, "
                    "narrower opening"
                ),
            )
            crossed = crossed.evolve(sub_interfaces=crossed.sub_interfaces + (child,))
        else:
            raise InterfaceError("next_condition must be a string or None")

        escalated = span_after >= self._horizon
        result = {
            "interface": list(crossed.key),
            "condition": crossed.condition,
            "verdict": PASS,
            "faults": faults,
            "difficulty": round(crossed.difficulty, 6),
            "generation": crossed.generation,
            "span_before": live.span,
            "span_after": span_after,
            "amplification": checked,
            "sub_interface": child.as_dict(depth=0) if child else None,
            "escalated": escalated,
            "condition_met": True,
            "mechanism": mechanism,
            "evidence": evidence,
            "message": (
                "the gap is broken and the reach is {}. It is now infrastructure."
                .format(format_span(span_after))
                if escalated
                else "the gap is broken; the reach is {} and the next gap is "
                "{}".format(
                    format_span(span_after),
                    "narrower" if child and child.named else "not yet named",
                )
            ),
        }
        self._record(result, digest_payload={
            "interface": list(crossed.key),
            "mechanism": mechanism,
            "evidence": evidence,
        })
        self._install(root, crossed)
        return result

    def _install(self, root_key: Tuple[str, str, int], updated: Interface) -> None:
        self._replace_node(root_key, updated)

    # -- 2. reading the ladder --------------------------------------------

    def escalate(self, interface: Any) -> Dict[str, Any]:
        """Whether a crossed interface has become infrastructure, and why.

        Separate from :meth:`cross_interface` because "this is now
        infrastructure" is a claim about the shape of the chain, not a verdict
        about a crossing.

        The gate is reach saturation **and** self-similarity, not saturation and
        narrowness. Narrowness would be a guard that can never fire: any
        interface reachable here already passed the crossing gate, so re-testing
        it would be decoration dressed as an invariant. What can actually fail is
        coherence -- a chain that arrived from a file or another process may be
        saturated and malformed at once, and a saturated malformed chain is a
        large number rather than an operating system.
        """
        _, live = self._locate(interface)
        if not live.is_crossed:
            raise InterfaceError(
                "{} is not crossed, so it is a gap rather than infrastructure"
                .format(list(live.key))
            )
        at_horizon = live.span >= self._horizon
        coherent = self.invariants()["self_similar"]
        if at_horizon and coherent:
            reason = (
                "reach has saturated at the horizon ({}) and every generation on "
                "the way is self-similar".format(format_span(self._horizon))
            )
        elif at_horizon:
            reason = (
                "reach is saturated but the chain is not self-similar, so the "
                "saturation is a number rather than a foundation"
            )
        else:
            reason = "reach is {} of {}".format(
                format_span(live.span), format_span(self._horizon)
            )
        return {
            "interface": list(live.key),
            "span": live.span,
            "difficulty": round(live.difficulty, 6),
            "at_horizon": at_horizon,
            "chain_self_similar": coherent,
            "is_infrastructure": at_horizon and coherent,
            "reason": reason,
        }

    def invariants(self) -> Dict[str, Any]:
        """The fractal law, checked against the chain as it actually stands.

        Returns one boolean per property plus the evidence behind it, so a demo
        can print why it believes the ladder is self-similar rather than asserting
        that it is.
        """
        violations: List[str] = []
        pairs = 0
        for root in self.chains():
            for node in root.walk():
                for child in node.sub_interfaces:
                    pairs += 1
                    if child.difficulty <= node.difficulty:
                        violations.append(
                            "{} -> {}: difficulty did not rise ({:.4f} -> {:.4f})"
                            .format(node.key, child.key, node.difficulty, child.difficulty)
                        )
                    if child.span < node.span:
                        violations.append(
                            "{} -> {}: span shrank ({})".format(
                                node.key, child.key, format_span(child.span)
                            )
                        )
                    if child.difficulty >= 1.0:
                        violations.append(
                            "{}: difficulty reached 1.0, which is a wall".format(
                                child.key
                            )
                        )
                    if child.generation != node.generation + 1:
                        violations.append(
                            "{}: generation {} does not follow {}".format(
                                child.key, child.generation, node.generation
                            )
                        )
                    if not node.is_crossed:
                        violations.append(
                            "{}: has a sub-interface but was never crossed".format(
                                node.key
                            )
                        )
        return {
            "generations": pairs,
            "violations": violations,
            "self_similar": not violations,
        }

    @property
    def history(self) -> List[Dict[str, Any]]:
        return [dict(step) for step in self._history]

    # -- 3. plumbing -------------------------------------------------------

    def _record(
        self,
        result: Dict[str, Any],
        digest_payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        step = dict(result)
        step["step"] = len(self._history) + 1
        step["digest"] = _digest(
            digest_payload
            if digest_payload is not None
            else {"interface": result["interface"], "verdict": result["verdict"]}
        )
        self._history.append(step)
        if self._membrane is None:
            result["logged"] = False
            result["log_note"] = "no membrane attached: this crossing is unrecorded"
            return
        attempts = (
            lambda: self._membrane.append_call(
                tool="fractal_interfaces",
                verdict=result["verdict"],
                agent="fractal_interfaces",
                intent="cross {}".format(result["interface"]),
                witness=step["digest"],
            ),
            lambda: self._membrane.append_call(
                "fractal_interfaces", result["verdict"]
            ),
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
            "the membrane accepted none of this evolver's signature shapes; the "
            "crossing stands but is unrecorded"
        )

    # -- 4. rendering -----------------------------------------------------

    def render_chain(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        title: bool = True,
    ) -> str:
        """The ladder as a table: generation, condition, difficulty, reach.

        ``title=False`` drops the outer headings, for embedding under a caller's
        own section banner. Two titles stacked would be a formatting bug that
        every reader has to look past.
        """
        width = 78
        rungs = self.chain(source, target)
        lines: List[str] = []
        if title:
            lines.append("=" * width)
            lines.append("FRACTAL INTERFACE LADDER")
            lines.append("=" * width)
        if not rungs:
            lines.append("  (no interfaces: nothing has been named yet)")
            lines.append("=" * width)
            return "\n".join(lines)
        lines.append(
            "  {:<4} {:<26} {:>7} {:>12}  {}".format(
                "gen", "condition", "narrow", "reach", "x"
            )
        )
        lines.append("  " + "-" * (width - 2))
        for node in rungs:
            amplification = "{}x".format(format_span(node.amplification))
            lines.append(
                "  {:<4} {:<26} {:>7.3f} {:>12}  {}".format(
                    node.generation,
                    (node.condition or "(unnamed)")[:26],
                    node.difficulty,
                    format_span(node.span),
                    amplification,
                )
            )
        lines.append("")
        status = self.invariants()
        if status["self_similar"]:
            lines.append(
                "  self-similar across {} generation(s):".format(
                    status["generations"]
                )
            )
            lines.append(
                "  difficulty rises, reach explodes, opening narrows."
            )
        else:
            lines.append("  NOT self-similar:")
            for violation in status["violations"]:
                lines.append("    - " + violation)
        if title:
            lines.append("=" * width)
        return "\n".join(lines)

    def render_tree(self) -> str:
        """The nesting, as indentation. One root per chain."""
        lines: List[str] = []
        for root in self.chains():
            def walk(node: Interface, depth: int) -> None:
                mark = "x" if node.is_crossed else " "
                lines.append(
                    "  {}[{}] gen {}  {:<26} narrow {:.3f}  reach {}".format(
                        "  " * depth,
                        mark,
                        node.generation,
                        (node.condition or "(unnamed)")[:26],
                        node.difficulty,
                        format_span(node.span),
                    )
                )
                for child in node.sub_interfaces:
                    walk(child, depth + 1)

            walk(root, 0)
        return "\n".join(lines) if lines else "  (empty)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _key_of(interface: Any) -> Tuple[str, str, int]:
    """Accept an Interface, a dict, or a (source, target[, generation]) tuple."""
    if isinstance(interface, Interface):
        return interface.key
    if isinstance(interface, Mapping):
        key = interface.get("key")
        if isinstance(key, (list, tuple)) and len(key) == 3:
            return (str(key[0]), str(key[1]), int(key[2]))
        source, target = interface.get("source"), interface.get("target")
        if source and target:
            generation = interface.get("generation", 0)
            first, second = sorted((str(source), str(target)))
            return (first, second, int(generation))
        raise InterfaceError(
            "an interface mapping needs 'key' or 'source'/'target'; got {}".format(
                sorted(interface)
            )
        )
    if isinstance(interface, (list, tuple)) and len(interface) in (2, 3):
        first, second = sorted((str(interface[0]), str(interface[1])))
        generation = int(interface[2]) if len(interface) == 3 else 0
        return (first, second, generation)
    raise InterfaceError(
        "cannot read an interface from {!r}".format(type(interface).__name__)
    )
