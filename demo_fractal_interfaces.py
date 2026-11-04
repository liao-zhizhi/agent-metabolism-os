#!/usr/bin/env python3
"""Demo: three fractal transitions, and the invariant that makes them one story.

The correction this demo is built around: do not explain an interface with a
medium. Air is not the substrate of a conversation -- air is a layer, bounded by
the layer above it, and a conversation crosses it only because a language fits
through a very narrow opening in it. Every "medium" is just the next layer up,
mistaken for a floor because that is where the search stopped.

So an interface is modelled as a gap, and crossing it does not delete the gap. It
closes the old opening and opens a new one of the same shape, one octave in:

    generation 0   air          crossed by language        -> a small tribe
    generation 1   space/time   crossed by writing         -> an industrial society
    generation 2   semantics    crossed by schema+witness  -> the digital world

Each row has the same form. The opening narrows, the reach explodes, and the next
gap lives inside the thing that was just connected. That is what makes the three
one story instead of three claims, and the demo asserts it rather than narrating
it: `invariants()` recomputes the law against the chain as it actually stands.

Two honest limits, both printed rather than hidden:

* The reaches are declared, not measured. There is no sociometer in this package.
  What is computed is the *relationship* between generations.
* The third reach saturates at a finite horizon instead of becoming infinity.
  "Infinite" cannot be multiplied, compared, or printed, and a quantity that
  breaks arithmetic is a slogan rather than a measurement.

Exit code 0 only if every self-check passes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_metabolism.double_entry import FAIL, INVALID, PASS  # noqa: E402
from agent_metabolism.interface_engine import (  # noqa: E402
    SPAN_HORIZON,
    FractalEvolver,
    Interface,
    InterfaceError,
    Layer,
    format_span,
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


def ladder_row(result, label):
    """Print one crossing as the quantities that moved."""
    arrow = "{} -> {}".format(
        format_span(result["span_before"]), format_span(result["span_after"])
    )
    print(
        "  {:<10} {:<20} {:>7} {:>11}  {}".format(
            label,
            result["condition"][:20],
            "{:.3f}".format(result["difficulty"]),
            "{:.0f}x".format(result["amplification"]),
            arrow,
        )
    )


# ---------------------------------------------------------------------------
# the demo
# ---------------------------------------------------------------------------


def main():
    print()
    rule()
    print("FRACTAL INTERFACE EVOLVER")
    print("a medium is the next layer up, mistaken for a floor")
    rule()

    evolver = FractalEvolver(
        [
            Layer(
                key="Mind",
                name="A single mind",
                domain="interior",
                description="everything one person can hold at once",
            ),
            Layer(
                key="World",
                name="The shared world",
                domain="exterior",
                description="everything that exists outside a single skull",
            ),
        ]
    )

    # -- generation 0 ----------------------------------------------------
    section(0, "THE FIRST GAP: AIR")

    air = Interface(
        source="Mind",
        target="World",
        condition="language",
        span=2,
        difficulty=0.85,
        amplification=50.0,
        rationale=(
            "air is not the substrate of a conversation; it is a layer, and a "
            "shared language is the narrow opening that lets anything through it"
        ),
    )
    evolver.seed(air)

    field("layers", "Mind <-> World")
    field("gap", "air")
    field("narrow condition", air.condition)
    field("difficulty", "{:.3f}".format(air.difficulty))
    field("reach", "{} (two speakers)".format(format_span(air.span)))
    print()
    say(
        "Two people who share a grammar connect. Three who do not share one are as "
        "far apart as two strangers on different continents -- the air is not what "
        "separated them."
    )
    print()

    gen0 = evolver.cross_interface(
        air,
        {"mechanism": "language", "evidence": "grammar: shared phonology + syntax"},
        next_condition="writing",
    )
    print("  {:<10} {:<20} {:>7} {:>11}  {}".format(
        "rung", "condition", "narrow", "span x", "reach"))
    print("  " + "-" * (WIDTH - 2))
    ladder_row(gen0, "gen 0")
    print()
    field("verdict", gen0["verdict"])
    field("reach", "{}".format(format_span(gen0["span_after"])))
    field("spawned", "gen 1, condition {!r}".format(gen0["sub_interface"]["condition"]))
    field("its difficulty", "{:.3f} (up from {:.3f})".format(
        gen0["sub_interface"]["difficulty"], gen0["difficulty"]))
    print()
    say(
        "The crossing did not delete the gap. It closed the old opening and opened "
        "a narrower one inside the space it connected: distance and time now "
        "separate the speakers that language just joined."
    )

    # -- generation 1 ----------------------------------------------------
    section(1, "THE GAP INSIDE: SPACE/TIME")

    gen1 = evolver.cross_interface(
        ("Mind", "World", 1),
        {"mechanism": "writing", "evidence": "press: movable type, 1450"},
        next_condition="semantics",
        amplification=10000.0,
    )
    ladder_row(gen1, "gen 1")
    print()
    field("verdict", gen1["verdict"])
    field("reach", format_span(gen1["span_after"]))
    field("difficulty", "{:.4f} (was {:.3f})".format(
        gen1["difficulty"], gen0["difficulty"]))
    field("spawned", "gen 2, condition {!r}".format(gen1["sub_interface"]["condition"]))
    print()
    say(
        "Writing is a harsher condition than speech: it demands a durable medium, "
        "a reader who is not present, and a code that survives both. It buys a "
        "reach ten thousand times larger. The opening narrowed and the reach "
        "exploded -- the same trade, one octave down."
    )

    # -- generation 2 ----------------------------------------------------
    section(2, "THE GAP INSIDE THAT: SEMANTICS")

    gen2 = evolver.cross_interface(
        ("Mind", "World", 2),
        {
            "mechanism": "structured schema + physical witness",
            "evidence": "sha256 of the written blob + a commit that names it",
        },
        amplification=1_000_000.0,
    )
    ladder_row(gen2, "gen 2")
    print()
    field("verdict", gen2["verdict"])
    field("reach", format_span(gen2["span_after"]))
    field("difficulty", "{:.4f}".format(gen2["difficulty"]))
    field("saturated", gen2["escalated"])
    print()

    escalation = evolver.escalate(("Mind", "World", 2))
    field("is infrastructure", escalation["is_infrastructure"])
    field("why", escalation["reason"])
    print()
    rule("-")
    print("  接口已升级为基础基础设施（AI OS）")
    print("  the interface has become the base infrastructure (AI OS)")
    rule("-")
    print()
    say(
        "A schema says what a value means; a physical witness makes that meaning "
        "checkable by a party that was not present. Together they are the harshest "
        "condition in this chain -- and the only one that connects a mind to a "
        "machine that has no mind at all."
    )
    print()
    say(
        "Reach saturates at {}. Not infinity: an unbounded number cannot be "
        "multiplied, compared, or printed, and a quantity that breaks arithmetic "
        "is a slogan. The horizon is where this engine stops representing reach -- "
        "which is precisely the point at which the interface stops being a feature "
        "and becomes the floor everything else stands on.".format(
            format_span(SPAN_HORIZON)
        )
    )

    # -- the ladder ------------------------------------------------------
    section(3, "THE LADDER")

    print(evolver.render_chain(title=False))
    print()
    say("The nesting, as indentation. 'x' marks a crossed gap, ' ' an open one:")
    print()
    print(evolver.render_tree())
    print()

    status = evolver.invariants()
    field("generations checked", status["generations"])
    field("self-similar", status["self_similar"])
    if status["violations"]:
        for violation in status["violations"]:
            print("    - " + violation)

    # -- what the engine refuses -----------------------------------------
    section(4, "WHAT THE ENGINE REFUSES")

    strict = FractalEvolver()
    open_gap = Interface("A", "B", "walking", span=2, difficulty=0.30)
    strict.seed(open_gap)
    easy = strict.cross_interface(
        open_gap, {"mechanism": "walking", "evidence": "legs"}
    )
    field("a gap of difficulty 0.30", easy["verdict"])
    say(
        "A gap anything can cross narrows nothing and connects nobody in "
        "particular. It is open space, not an interface, and the engine says so "
        "instead of crediting the crossing."
    )
    print()

    narrow_gap = Interface("C", "D", "ritual", span=4, difficulty=0.88)
    strict.seed(narrow_gap)
    naive = strict.cross_interface(
        narrow_gap, {"mechanism": "magic", "evidence": "it felt right"}
    )
    field("'magic' as a condition", naive["verdict"])
    field("why", "a placeholder cannot be measured, so it cannot be wrong")
    print()

    print("  and a crossing that would connect less than it replaces:")
    for attempt, label in ((0.5, "amplification 0.5"), (2.0, "amplification 2.0")):
        try:
            strict.cross_interface(
                narrow_gap, {"mechanism": "ritual", "evidence": "rite: spring"},
                amplification=attempt,
            )
            print("    {:<20} accepted (unexpected)".format(label))
        except InterfaceError as exc:
            print("    {:<20} refused".format(label))
            for line in wrap(str(exc), WIDTH - 10):
                print("        " + line)
    print()

    say(
        "That last one is the interesting case. The engine makes a non-fractal "
        "chain unconstructible through its own door: amplification cannot fall, "
        "difficulty cannot stall, a span cannot shrink, and a sub-interface cannot "
        "appear under an uncrossed gap. So invariants() is not a hope -- it is a "
        "second check on chains that arrived from somewhere else, such as a file "
        "written by an older process."
    )
    print()

    broken = FractalEvolver()
    bad_child = Interface(
        "E", "F", "two", span=1000, difficulty=0.90, generation=1,
        parent=("E", "F", 0),
    )
    bad_root = Interface(
        "E", "F", "one", span=100, difficulty=0.90, condition_met=True,
        crossed_by="one", sub_interfaces=(bad_child,),
    )
    broken.seed(bad_root)
    say("A hand-built chain where generation 1 is no narrower than generation 0:")
    print()
    field("self-similar", broken.invariants()["self_similar"])
    for violation in broken.invariants()["violations"]:
        for index, line in enumerate(wrap(violation, WIDTH - 6)):
            print(("    - " if index == 0 else "      ") + line)

    # -- self-checks -----------------------------------------------------
    section(5, "SELF-CHECKS")

    rungs = evolver.chain()
    difficulties = [node.difficulty for node in rungs]
    spans = [node.span for node in rungs if node.is_crossed]
    refusals = []
    for call in (
        lambda: evolver.cross_interface(air, {"mechanism": "language", "evidence": "g"}),
        lambda: evolver.cross_interface(("Mind", "World", 1),
                                        {"mechanism": "writing", "evidence": "p"}),
    ):
        try:
            call()
        except InterfaceError as exc:
            refusals.append(str(exc))

    checks = {
        "level 1 reached exactly 100": gen0["span_after"] == 100.0,
        "level 1 multiplied by 50": gen0["amplification"] == 50.0,
        "level 2 reached one million": gen1["span_after"] == 1_000_000.0,
        "level 3 saturated the horizon": gen2["span_after"] == SPAN_HORIZON,
        "each crossing emitted PASS": [gen0["verdict"], gen1["verdict"],
                                       gen2["verdict"]] == [PASS, PASS, PASS],
        "difficulty rose monotonically": all(
            later > earlier for earlier, later in zip(difficulties, difficulties[1:])
        ),
        "difficulty never reached a wall": all(d < 1.0 for d in difficulties),
        "reach exploded monotonically": all(
            later >= earlier for earlier, later in zip(spans, spans[1:])
        ),
        "reach grew by four orders or more each rung": (
            gen1["span_after"] / gen0["span_after"] >= 1e4
            and gen2["span_after"] / gen1["span_after"] >= 1e4
        ),
        "a sub-interface was spawned each time": all(
            result["sub_interface"] is not None for result in (gen0, gen1, gen2)
        ),
        "the sub-interface is narrower": gen0["sub_interface"]["difficulty"]
        > gen0["difficulty"],
        "the sub-interface inherits the reach": gen0["sub_interface"]["span"]
        == gen0["span_after"],
        "crossing did not delete the gap": len(evolver.frontier()) == 1,
        "the next gap has no name yet": evolver.frontier()[0].condition == "",
        "the chain is self-similar": status["self_similar"],
        "three generations were checked": status["generations"] == 3,
        "level 3 is infrastructure": escalation["is_infrastructure"],
        "an easy gap is refused": easy["verdict"] == FAIL,
        "a placeholder is INVALID": naive["verdict"] == INVALID,
        "re-crossing is refused": len(refusals) == 2,
        "a broken chain is caught": not broken.invariants()["self_similar"],
        "infinite span was never produced": all(
            span == span and span != float("inf") for span in spans
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
