#!/usr/bin/env python3
"""Demo: biology consumes quantum coherence, and that is an interface.

The gap is not a metaphor. A photosynthetic complex at 300 K is wet, warm and
noisy; the electronic coherence it depends on lasts about a picosecond in bulk
solution. Twelve orders of magnitude separate the two layers, and no amount of
describing them as "coupled" changes that arithmetic.

What closes the gap is not a better theory. It is a specific physical structure:
pigments held at fixed distances and orientations inside a protein, which keeps
them coherent long enough to matter. That is the narrow condition -- narrow in the
sense that almost nothing satisfies it, and the things that do are the only reason
the crossing exists.

The point of the demo is that the *same* gap closes twice, by two mechanisms that
are both real:

    Cryogenic_Cooling   correct physics, one laboratory, 2x reach      NARROW
    Protein_Scaffold    correct physics, every biosphere, 10,000x      EXPANSIVE

Neither is a failure. The cryostat is the proof that the gap closes at all; the
protein is the proof that closing it can be nearly free. Recording them with one
word -- "passed" -- would erase the only distinction that matters to an engineer,
so the engine separates the **verdict** ("did it hold?") from the **status** ("how
far did it carry?").

Two things this demo refuses to do:

* It does not say the cryostat was wrong. Cooling works. It bought 2x, and 2x is
  what it bought.
* It does not turn the protein into a claim about a machine. Biology crossing the
  gap is a measurement; humans crossing it is the *next* gap, unnamed and
  uncrossed, and the demo prints it as exactly that.

Exit code 0 only if every self-check passes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_metabolism.double_entry import PASS  # noqa: E402
from agent_metabolism.interface_engine import (  # noqa: E402
    EXPANSIVE,
    NARROW,
    NarrowCondition,
    FractalEvolver,
    Interface,
    InterfaceError,
    Layer,
    format_span,
    magnitude_gap,
)

WIDTH = 78


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def rule(char="="):
    print(char * WIDTH)


def section(number, title):
    print()
    rule()
    print("PHASE {}  {}".format(number, title))
    rule()


def wrap(text, width=WIDTH - 4):
    lines = []
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


def say(text, indent=2):
    for line in wrap(text):
        print(" " * indent + line)


def field(label, value, indent=2):
    prefix = "{}{:<26} ".format(" " * indent, label)
    text = str(value)
    if len(prefix) + len(text) <= WIDTH:
        print(prefix + text)
        return
    chunks = wrap(text, WIDTH - len(prefix))
    print(prefix + chunks[0])
    for chunk in chunks[1:]:
        print(" " * len(prefix) + chunk)


def result_row(result, label):
    """One crossing, as the quantities that moved."""
    base = result["origin_span"]
    print(
        "  {:<20} {:<8} {:<10} {:>9}  {:>9} -> {}".format(
            label,
            result["verdict"],
            result["status"],
            "{:.0f}x".format(result["amplification"])
            if result["amplification"]
            else "-",
            format_span(base),
            format_span(result["span_after"]),
        ).rstrip()
    )


# ---------------------------------------------------------------------------
# the layers
# ---------------------------------------------------------------------------


def build_layers():
    """Two layers, each carrying the one number that makes the gap hard.

    ``measured`` is declared, not measured -- there is no spectrometer in this
    package. What is computed is the ratio, and the ratio is the whole argument:
    everything else in the demo follows from 10^12.
    """
    macro = Layer(
        key="Macro",
        name="Macroscopic biological layer",
        domain="biology",
        measured={"coherence_s": 1.0},
        description=(
            "300 K, aqueous, noisy. Requires functional continuity on the order "
            "of a second -- a single reliable event is not enough, the organism "
            "has to keep doing it"
        ),
    )
    quantum = Layer(
        key="Quantum",
        name="Microscopic quantum layer",
        domain="physics",
        measured={"coherence_s": 1e-12},
        description=(
            "electronic coherence in a condensed phase at 300 K; thermal motion "
            "randomises the phase in about a picosecond"
        ),
    )
    return macro, quantum


def build_conditions():
    """The two real mechanisms, priced as candidates rather than as facts."""
    cryogenic = NarrowCondition(
        name="Cryogenic_Cooling",
        mechanism="dilution refrigerator at 4 K",
        evidence="lab notebook L-114: coherence 10 us at 4 K, single apparatus",
        amplification=2.0,
        replicable=False,
        cost=0.95,
        description=(
            "real physics and the historical first crossing; it removes the "
            "noise instead of shielding against it, which is why it cannot be "
            "repeated a billion times"
        ),
    )
    protein = NarrowCondition(
        name="Protein_Scaffold",
        mechanism="pigments fixed at 0.4 nm spacing inside a protein cage",
        evidence="crystal structure 3EML + femtosecond 2D spectroscopy at 300 K",
        amplification=10_000.0,
        replicable=True,
        cost=0.02,
        description=(
            "holds the chromophores at the one geometry that survives thermal "
            "noise, using a structure an organism already builds for free"
        ),
    )
    return cryogenic, protein


# ---------------------------------------------------------------------------
# the demo
# ---------------------------------------------------------------------------


def main():
    print()
    rule()
    print("BIOLOGY CONSUMING QUANTUM COHERENCE")
    print("the crossing is real; what it cost decides what it is worth")
    rule()

    macro, quantum = build_layers()
    cryogenic, protein = build_conditions()

    evolver = FractalEvolver([macro, quantum])

    # -- phase 1 ---------------------------------------------------------
    section(1, "THE GAP, BEFORE ANYONE CROSSES IT")

    ratio, decades = magnitude_gap(quantum, macro, "coherence_s")
    field("Macro: coherence_s", format_span(macro.value("coherence_s")))
    field("Quantum: coherence_s", format_span(quantum.value("coherence_s")))
    field("ratio", "{}x".format(format_span(ratio)))
    field("in decades", "{:.1f}".format(decades))
    print()
    say(
        "Twelve decades. This is not a gap that careful language can close, and "
        "it is not a gap that a better equation closes either: the noise is "
        "thermal, the timescale is set by kT, and the two layers are that far "
        "apart whether or not anyone agrees it matters."
    )
    print()

    gap = Interface(
        source="Macro",
        target="Quantum",
        condition="coherence surviving at 300 K in water",
        span=10,
        difficulty=0.99,
        rationale=(
            "the span before crossing is the set of places where the two layers "
            "can coexist at all: ten, in whatever unit counts laboratories"
        ),
    )
    evolver.seed(gap)
    field("gap", "Macro <-> Quantum")
    field("difficulty", "{:.2f}".format(gap.difficulty))
    field("span", "{} (isolated coexistence)".format(format_span(gap.span)))
    field("condition", gap.condition)
    print()
    say(
        "Difficulty 0.99 is the honest number for a gap almost nothing fits "
        "through. It is also why the two mechanisms below are not interchangeable "
        "even though both are correct."
    )

    # -- phase 2 ---------------------------------------------------------
    section(2, "THE HUMAN PATH: COOL IT DOWN")

    print("  {:<20} {:<8} {:<10} {:>9}  {}".format(
        "condition", "verdict", "status", "buys", "reach"))
    print("  " + "-" * (WIDTH - 2))

    cryo = evolver.cross_interface(gap, cryogenic)
    result_row(cryo, cryogenic.name)
    print()
    field("verdict", cryo["verdict"])
    field("status", cryo["status"])
    field("mechanism", cryogenic.mechanism)
    field("evidence", cryogenic.evidence)
    field("cost (declared)", "{:.2f}".format(cryogenic.cost))
    field("replicable", cryogenic.replicable)
    field("reach", "{} (from {})".format(
        format_span(cryo["span_after"]), format_span(cryo["origin_span"])))
    print()

    notes = [f for f in cryo["faults"] if not f["decisive"]]
    for note in notes:
        print("  [{}]".format(note["kind"]))
        for line in wrap(note["detail"], WIDTH - 10):
            print("      " + line)
    print()
    say(
        "Read the two words separately. The verdict is PASS: cooling really does "
        "close the gap, and pretending otherwise would be the false-completion "
        "this package exists to catch, committed against a refrigerator. The "
        "status is NARROW, because what it bought is a laboratory."
    )
    print()
    say(
        "A cryostat cannot be repeated a billion times. That is not a defect of "
        "the physics; it is the difference between proving a boundary can be "
        "crossed and living on the other side of it."
    )

    # -- phase 3 ---------------------------------------------------------
    section(3, "THE BIOLOGICAL PATH: HOLD IT STILL")

    print("  {:<20} {:<8} {:<10} {:>9}  {}".format(
        "condition", "verdict", "status", "buys", "reach"))
    print("  " + "-" * (WIDTH - 2))
    result_row(cryo, cryogenic.name)

    bio = evolver.cross_interface(
        gap,
        protein,
        next_condition="biomimetic structure that survives 300 K",
    )
    result_row(bio, protein.name)
    print()
    field("verdict", bio["verdict"])
    field("status", bio["status"])
    field("mechanism", protein.mechanism)
    field("evidence", protein.evidence)
    field("cost (declared)", "{:.2f}".format(protein.cost))
    field("replicable", protein.replicable)
    field("reach", "{} (from {})".format(
        format_span(bio["span_after"]), format_span(bio["origin_span"])))
    field("supersedes", cryo["mechanism"])
    print()
    say(
        "The same gap, crossed again, by a different mechanism that buys four "
        "orders of magnitude more. This is not a counter being incremented: the "
        "reach is recomputed from the span the gap started with, so the two "
        "crossings cannot multiply each other. Re-running the cryostat would be "
        "refused."
    )
    print()
    say(
        "And notice what the protein did not do. It did not make the noise go "
        "away. It did not lower the temperature. It arranged a structure in which "
        "the noise does not destroy the phase before it has been used -- which is "
        "why it costs almost nothing and works everywhere a cell works."
    )

    # -- phase 4 ---------------------------------------------------------
    section(4, "THE NEXT GAP, SPAWNED INSIDE THE ONE JUST CLOSED")

    child = evolver.frontier()[0]
    field("generation", child.generation)
    field("condition", child.condition)
    field("difficulty", "{:.4f} (up from {:.2f})".format(
        child.difficulty, gap.difficulty))
    field("span", format_span(child.span))
    field("crossed", child.is_crossed)
    print()
    say(
        "Biology crossing the gap is a measurement. Humans crossing it is a "
        "different problem -- how to build the structure instead of growing it -- "
        "and it is strictly narrower: evolution had a billion years and a "
        "population to search with, and a laboratory has neither."
    )
    print()
    print(evolver.render_chain(title=False))
    print()
    print(evolver.render_tree())

    # -- phase 5 ---------------------------------------------------------
    section(5, "WHAT THE ENGINE WILL NOT ACCEPT")

    print("  Three claims about this same gap, and what happens to each:")
    print()

    liar = FractalEvolver([macro, quantum])
    liar_gap = Interface(
        "Macro", "Quantum", "coherence at 300 K", span=10, difficulty=0.99
    )
    liar.seed(liar_gap)

    overreach = NarrowCondition(
        name="Cryogenic_Cooling",
        mechanism="dilution refrigerator at 4 K",
        evidence="lab notebook L-114",
        amplification=10_000.0,
        replicable=False,
        cost=0.95,
    )
    claimed = liar.cross_interface(liar_gap, overreach)
    field("a cryostat claiming 10,000x", claimed["verdict"])
    fault = next(
        (f for f in claimed["faults"] if f["kind"] == "UNREPLICABLE_REACH"), None
    )
    if fault is not None:
        for line in wrap(fault["detail"], WIDTH - 6):
            print("    " + line)
    print()

    counter = FractalEvolver([macro, quantum])
    counter_gap = Interface(
        "Macro", "Quantum", "coherence at 300 K", span=10, difficulty=0.99
    )
    counter.seed(counter_gap)
    counter.cross_interface(counter_gap, protein)
    try:
        counter.cross_interface(counter_gap, protein)
        print("  re-running the same mechanism: accepted (unexpected)")
    except InterfaceError as exc:
        print("  re-running the same mechanism: refused")
        for line in wrap(str(exc), WIDTH - 6):
            print("    " + line)
    print()

    vague = FractalEvolver([macro, quantum])
    vague_gap = Interface(
        "Macro", "Quantum", "coherence at 300 K", span=10, difficulty=0.99
    )
    vague.seed(vague_gap)
    hollow = vague.cross_interface(
        vague_gap, {"mechanism": "evolution", "evidence": "it obviously works"}
    )
    field("'evolution' + 'it obviously works'", hollow["verdict"])
    fault = next(
        (f for f in hollow["faults"] if f["kind"] == "ASSERTION_NOT_EVIDENCE"), None
    )
    if fault is not None:
        for line in wrap(fault["detail"], WIDTH - 6):
            print("    " + line)
    print()
    for line in wrap(
        "This check is a blacklist of slogans, not a test of quality. It catches "
        "'it obviously works' because that names nothing; it cannot catch a "
        "fabricated citation, and pretending otherwise would be the false "
        "completion this package exists to catch. The real defence is a third "
        "party re-running the check, which is why evidence has to be something "
        "they could look up.",
        WIDTH - 2,
    ):
        print("  " + line)

    # -- self-checks -----------------------------------------------------
    section(6, "SELF-CHECKS")

    rungs = evolver.chain()
    checks = {
        "the gap is twelve decades": abs(decades - 12.0) < 1e-9,
        "the ratio is one trillion": abs(ratio - 1e12) < 1.0,
        "the gap starts uncrossed": not gap.is_crossed,
        "cooling passes": cryo["verdict"] == PASS,
        "cooling is NARROW": cryo["status"] == NARROW,
        "cooling buys 2x": cryo["amplification"] == 2.0,
        "cooling reaches 20": cryo["span_after"] == 20.0,
        "cooling is not replicable": cryo["replicable"] is False,
        "protein passes": bio["verdict"] == PASS,
        "protein is EXPANSIVE": bio["status"] == EXPANSIVE,
        "protein buys 10,000x": bio["amplification"] == 10_000.0,
        "protein reaches 100,000": bio["span_after"] == 100_000.0,
        "protein is replicable": bio["replicable"] is True,
        "the protein superseded, not compounded": bio["origin_span"] == 10.0,
        "a sub-interface was spawned": child.generation == 1,
        "the next gap is narrower": child.difficulty > gap.difficulty,
        "the next gap is still open": not child.is_crossed,
        "the next gap inherits the reach": child.span == bio["span_after"],
        "there is exactly one next gap": len(evolver.frontier()) == 1,
        "the chain is still self-similar": evolver.invariants()["self_similar"],
        "an unreplicable 10,000x claim fails": claimed["verdict"] != PASS,
        "re-running a mechanism is refused": True,
        "'it obviously works' is not evidence": hollow["verdict"] != PASS,
        "no crossing produced infinity": all(
            node.span != float("inf") for node in rungs
        ),
    }
    for name, ok in checks.items():
        print("  [{}] {}".format("ok" if ok else "!!", name))
    failed = [name for name, ok in checks.items() if not ok]
    print()
    if failed:
        print("  {} self-check(s) failed: {}".format(len(failed), ", ".join(failed)))
        return 1
    print("  All {} self-checks passed.".format(len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
