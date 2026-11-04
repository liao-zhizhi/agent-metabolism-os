"""Tests for the fractal interface evolver.

The cases worth having are the ones where a plausible implementation would
produce a *ladder that looks like a story but is not a law*. Five decide whether
this module is an engineering model or a poster:

* Crossing must not delete the gap. If it did, the third generation would be
  free-floating and the fractal claim would be decoration.
* Difficulty must never reach 1.0. A gap that cannot be crossed is a wall, and a
  wall is the opposite of an interface.
* Reach must never become ``inf``. Infinity cannot be multiplied, compared, or
  printed, so it would quietly break the arithmetic the whole model rests on.
* A gap that anything can cross is not a boundary. Crediting it would let a
  caller multiply reach by satisfying nothing.
* Re-crossing must be refused. Otherwise a loop manufactures unbounded reach
  without ever satisfying a new condition.

Everything runs on the standard library with hand-built layers. No test asserts
anything about a real society, because nothing in this module can measure one.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism.double_entry import FAIL, INVALID, PASS  # noqa: E402
from agent_metabolism.interface_engine import (  # noqa: E402
    DEFAULT_AMPLIFICATION,
    EXPANSIVE,
    NARROW,
    NarrowCondition,
    REPLICATION_CEILING,
    magnitude_gap,
    NARROW_THRESHOLD,
    NESTING_RATIO,
    PLACEHOLDER_CONDITIONS,
    SPAN_HORIZON,
    FractalEvolver,
    Interface,
    InterfaceError,
    Layer,
    format_span,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _layers():
    return [
        Layer(key="Mind", name="A single mind", domain="interior"),
        Layer(key="World", name="The shared world", domain="exterior"),
    ]


def _evolver(**kwargs):
    return FractalEvolver(_layers(), **kwargs)


def _bare(**kwargs):
    """An evolver with no layer registry: any pair of keys is admissible."""
    return FractalEvolver(**kwargs)


def _air():
    return Interface(
        source="Mind", target="World", condition="language",
        span=2, difficulty=0.85, amplification=50.0,
    )


def _witness(mechanism="language", evidence="grammar: phonology + syntax", **extra):
    payload = {"mechanism": mechanism, "evidence": evidence}
    payload.update(extra)
    return payload


def _three_rungs():
    """The demo's ladder: 2 -> 100 -> 1,000,000 -> the horizon."""
    evolver = _evolver()
    evolver.seed(_air())
    first = evolver.cross_interface(
        ("Mind", "World", 0), _witness(), next_condition="writing"
    )
    second = evolver.cross_interface(
        ("Mind", "World", 1), _witness("writing", "press: 1450"),
        next_condition="semantics", amplification=10_000.0,
    )
    third = evolver.cross_interface(
        ("Mind", "World", 2), _witness("schema+witness", "sha256:aa"),
        amplification=1_000_000.0,
    )
    return evolver, first, second, third


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------


class TestLayers(unittest.TestCase):
    def test_a_layer_needs_a_key(self):
        with self.assertRaises(InterfaceError):
            Layer(key="", name="x")

    def test_a_duplicate_layer_is_refused(self):
        evolver = _evolver()
        with self.assertRaises(InterfaceError):
            evolver.add_layer(Layer(key="Mind", name="again"))

    def test_an_unknown_layer_lists_the_known_ones(self):
        with self.assertRaises(InterfaceError) as caught:
            _evolver().layer("Nope")
        self.assertIn("Mind", str(caught.exception))

    def test_a_layer_round_trips(self):
        layer = Layer(key="A", name="A", domain="d", description="x")
        self.assertEqual(Layer(**layer.as_dict()), layer)


# ---------------------------------------------------------------------------
# the interface as a value
# ---------------------------------------------------------------------------


class TestInterface(unittest.TestCase):
    def test_an_interface_needs_two_different_layers(self):
        with self.assertRaises(InterfaceError):
            Interface("A", "A", "c", span=1, difficulty=0.9)
        with self.assertRaises(InterfaceError):
            Interface("", "B", "c", span=1, difficulty=0.9)

    def test_span_must_be_finite_and_positive(self):
        for bad in (0, -1, float("inf"), float("nan"), "wide", True):
            with self.assertRaises(InterfaceError):
                Interface("A", "B", "c", span=bad, difficulty=0.9)

    def test_difficulty_must_be_a_unit_interval(self):
        for bad in (-0.1, 1.1, float("inf"), "hard", True):
            with self.assertRaises(InterfaceError):
                Interface("A", "B", "c", span=1, difficulty=bad)

    def test_difficulty_of_zero_is_allowed(self):
        """Open space is representable; it is just not a boundary."""
        self.assertEqual(Interface("A", "B", "c", span=1, difficulty=0.0).difficulty, 0.0)

    def test_amplification_must_exceed_one(self):
        for bad in (1.0, 0.5, 0.0, -2):
            with self.assertRaises(InterfaceError):
                Interface("A", "B", "c", span=1, difficulty=0.9, amplification=bad)

    def test_generation_must_be_a_non_negative_integer(self):
        for bad in (-1, 1.5, "0", True):
            with self.assertRaises(InterfaceError):
                Interface("A", "B", "c", span=1, difficulty=0.9, generation=bad)

    def test_sub_interfaces_must_be_a_tuple(self):
        """A list would be mutable, and this object is meant to be a value."""
        with self.assertRaises(InterfaceError):
            Interface("A", "B", "c", span=1, difficulty=0.9, sub_interfaces=[])

    def test_the_key_is_unordered_and_carries_the_generation(self):
        forward = Interface("A", "B", "c", span=1, difficulty=0.9)
        backward = Interface("B", "A", "c", span=1, difficulty=0.9)
        self.assertEqual(forward.key, backward.key)
        deeper = Interface("B", "A", "c", span=1, difficulty=0.9, generation=1)
        self.assertNotEqual(forward.key, deeper.key)

    def test_an_empty_condition_is_unnamed_not_absent(self):
        """The gap inside a freshly connected space exists before it is named."""
        self.assertFalse(Interface("A", "B", "", span=1, difficulty=0.9).named)
        self.assertTrue(Interface("A", "B", "x", span=1, difficulty=0.9).named)

    def test_evolve_returns_a_new_object(self):
        original = _air()
        copy = original.evolve(condition_met=True)
        self.assertIsNot(original, copy)
        self.assertFalse(original.condition_met)

    def test_walk_is_depth_first(self):
        child = Interface("A", "B", "two", span=10, difficulty=0.9, generation=1)
        root = Interface(
            "A", "B", "one", span=1, difficulty=0.8,
            sub_interfaces=(child,),
        )
        self.assertEqual([node.generation for node in root.walk()], [0, 1])

    def test_depth_counts_the_levels_below(self):
        child = Interface("A", "B", "two", span=10, difficulty=0.9, generation=1)
        root = Interface("A", "B", "one", span=1, difficulty=0.8, sub_interfaces=(child,))
        self.assertEqual(root.depth(), 1)
        self.assertEqual(child.depth(), 0)

    def test_frontier_is_the_uncrossed_gaps(self):
        child = Interface("A", "B", "two", span=10, difficulty=0.9, generation=1)
        root = Interface(
            "A", "B", "one", span=1, difficulty=0.8, condition_met=True,
            sub_interfaces=(child,),
        )
        self.assertEqual([node.generation for node in root.frontier()], [1])

    def test_an_interface_round_trips(self):
        payload = _air().as_dict()
        self.assertEqual(payload["key"], ["Mind", "World", 0])
        self.assertEqual(payload["condition"], "language")

    def test_the_dict_view_can_be_bounded(self):
        """A deep chain must not blow the recursion limit when serialised."""
        child = Interface("A", "B", "two", span=10, difficulty=0.9, generation=1)
        root = Interface("A", "B", "one", span=1, difficulty=0.8, sub_interfaces=(child,))
        self.assertEqual(root.as_dict(depth=0)["sub_interfaces"], "…")


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------


class TestSeeding(unittest.TestCase):
    def test_seed_accepts_a_generation_zero_gap(self):
        evolver = _evolver()
        self.assertIsNotNone(evolver.seed(_air()))

    def test_seed_refuses_a_declared_generation(self):
        """Generations are assigned by crossing, never declared."""
        decreed = Interface("Mind", "World", "c", span=1, difficulty=0.9, generation=1)
        with self.assertRaises(InterfaceError):
            _evolver().seed(decreed)

    def test_seed_refuses_a_parent(self):
        adopted = Interface(
            "Mind", "World", "c", span=1, difficulty=0.9,
            parent=("Mind", "World", 0),
        )
        with self.assertRaises(InterfaceError):
            _evolver().seed(adopted)

    def test_seed_refuses_a_second_root_for_one_pair(self):
        evolver = _evolver()
        evolver.seed(_air())
        with self.assertRaises(InterfaceError):
            evolver.seed(_air())

    def test_seed_refuses_an_unknown_layer(self):
        with self.assertRaises(InterfaceError):
            _evolver().seed(Interface("Mind", "Ghost", "c", span=1, difficulty=0.9))

    def test_seed_works_without_a_layer_registry(self):
        """A bare evolver is usable; layer checking is opt-in."""
        bare = FractalEvolver()
        self.assertIsNotNone(
            bare.seed(Interface("X", "Y", "c", span=1, difficulty=0.9))
        )


# ---------------------------------------------------------------------------
# the crossing
# ---------------------------------------------------------------------------


class TestCrossing(unittest.TestCase):
    def test_a_satisfied_narrow_condition_passes(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertEqual(result["verdict"], PASS)

    def test_the_span_is_multiplied(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertEqual(result["span_before"], 2)
        self.assertEqual(result["span_after"], 100.0)

    def test_the_condition_is_marked_met(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertTrue(evolver.chain()[0].condition_met)

    def test_crossing_spawns_a_narrower_sub_interface(self):
        """The law: no crossing without a new gap of the same shape."""
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), _witness(), next_condition="writing"
        )
        child = result["sub_interface"]
        self.assertEqual(child["generation"], 1)
        self.assertEqual(child["condition"], "writing")
        self.assertGreater(child["difficulty"], result["difficulty"])

    def test_the_sub_interface_inherits_the_reach(self):
        """It is the gap *inside* the space just connected, not a new space."""
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertEqual(result["sub_interface"]["span"], result["span_after"])

    def test_the_sub_interface_records_its_parent(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness())
        child = evolver.frontier()[0]
        self.assertEqual(child.parent, ("Mind", "World", 0))

    def test_the_next_condition_may_be_unnamed(self):
        """What is inside the newly connected space is often not yet known."""
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertEqual(evolver.frontier()[0].condition, "")
        self.assertFalse(evolver.frontier()[0].named)

    def test_no_implementation_is_invalid(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), None)
        self.assertEqual(result["verdict"], INVALID)
        self.assertIn("NO_IMPLEMENTATION", [f["kind"] for f in result["faults"]])

    def test_a_placeholder_condition_is_invalid(self):
        for name in ("", "magic", "ai", "blockchain", "trust_me"):
            evolver = _evolver()
            evolver.seed(_air())
            result = evolver.cross_interface(
                ("Mind", "World", 0), _witness(mechanism=name)
            )
            self.assertEqual(result["verdict"], INVALID, repr(name))

    def test_every_placeholder_is_rejected(self):
        for name in PLACEHOLDER_CONDITIONS:
            evolver = _evolver()
            evolver.seed(_air())
            result = evolver.cross_interface(
                ("Mind", "World", 0), _witness(mechanism=name)
            )
            self.assertEqual(result["verdict"], INVALID, repr(name))
            kinds = [f["kind"] for f in result["faults"]]
            expected = "NO_IMPLEMENTATION" if name == "" else "PLACEHOLDER_CONDITION"
            self.assertIn(expected, kinds, repr(name))

    def test_missing_evidence_is_invalid(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), {"mechanism": "language"}
        )
        self.assertEqual(result["verdict"], INVALID)
        self.assertIn("NO_EVIDENCE", [f["kind"] for f in result["faults"]])

    def test_a_boolean_is_not_evidence(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), {"mechanism": "language", "evidence": True}
        )
        self.assertIn("NO_EVIDENCE", [f["kind"] for f in result["faults"]])

    def test_an_unsatisfied_condition_is_a_fault(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), _witness(satisfies=False)
        )
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("CONDITION_NOT_SATISFIED", [f["kind"] for f in result["faults"]])

    def test_satisfies_true_is_accepted(self):
        evolver = _evolver()
        evolver.seed(_air())
        self.assertEqual(
            evolver.cross_interface(("Mind", "World", 0), _witness(satisfies=True))["verdict"],
            PASS,
        )

    def test_a_non_boolean_satisfies_is_refused(self):
        evolver = _evolver()
        evolver.seed(_air())
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(
                ("Mind", "World", 0), _witness(satisfies="yes")
            )

    def test_a_gap_anything_can_cross_is_not_a_boundary(self):
        """Crediting it would let reach multiply by satisfying nothing."""
        evolver = _bare()
        evolver.seed(Interface("A", "B", "walking", span=2, difficulty=0.30))
        result = evolver.cross_interface(
            ("A", "B", 0), {"mechanism": "walking", "evidence": "legs"}
        )
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("NOT_A_BOUNDARY", [f["kind"] for f in result["faults"]])

    def test_the_threshold_is_the_boundary_between_gap_and_open_space(self):
        evolver = _bare()
        evolver.seed(Interface("A", "B", "c", span=2, difficulty=NARROW_THRESHOLD))
        result = evolver.cross_interface(
            ("A", "B", 0), {"mechanism": "c", "evidence": "e"}
        )
        self.assertEqual(result["verdict"], FAIL)

    def test_the_threshold_is_configurable(self):
        lax = _bare(narrow_threshold=0.2)
        lax.seed(Interface("A", "B", "c", span=2, difficulty=0.3))
        self.assertEqual(
            lax.cross_interface(("A", "B", 0), {"mechanism": "c", "evidence": "e"})["verdict"],
            PASS,
        )

    def test_an_out_of_range_threshold_is_refused(self):
        for bad in (0.0, 1.0, -0.1, 1.5, "high"):
            with self.assertRaises(InterfaceError):
                _evolver(narrow_threshold=bad)

    def test_a_fault_outranks_an_omission(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), {"mechanism": "magic", "evidence": "", "satisfies": False}
        )
        self.assertEqual(result["verdict"], FAIL)

    def test_a_bare_string_is_a_mechanism_without_evidence(self):
        """It parses, then fails the evidence check rather than crashing."""
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), "language")
        self.assertEqual(result["verdict"], INVALID)
        self.assertIn("NO_EVIDENCE", [f["kind"] for f in result["faults"]])

    def test_a_non_mapping_implementation_is_refused(self):
        evolver = _evolver()
        evolver.seed(_air())
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(("Mind", "World", 0), 42)

    def test_a_failed_crossing_changes_nothing(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), None)
        self.assertEqual(result["span_after"], result["span_before"])
        self.assertFalse(result["condition_met"])
        self.assertFalse(evolver.chain()[0].condition_met)
        self.assertEqual(evolver.chain()[0].sub_interfaces, ())

    def test_a_failed_crossing_spawns_nothing(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), None)
        self.assertEqual(len(evolver.frontier()), 1)


# ---------------------------------------------------------------------------
# the only door upward
# ---------------------------------------------------------------------------


class TestNoFreeReach(unittest.TestCase):
    def test_re_crossing_is_refused(self):
        """Reach is not a counter; a loop must not manufacture it."""
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness())
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(("Mind", "World", 0), _witness())

    def test_re_crossing_through_a_stale_handle_is_refused(self):
        """A caller holding the pre-crossing object must not undo the crossing."""
        evolver = _evolver()
        stale = _air()
        evolver.seed(stale)
        evolver.cross_interface(("Mind", "World", 0), _witness())
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(stale, _witness())

    def test_a_supersession_must_buy_more(self):
        """On an open gap the declared multiplier is a default; once crossed it
        is a claim, and a replacement that buys less is indistinguishable from
        the thing it replaces."""
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness("language", "g"))
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(
                ("Mind", "World", 0), _witness("writing", "p"), amplification=2.0
            )

    def test_a_first_crossing_may_buy_less_than_the_default(self):
        """A mechanism dearer than assumed is a fact, not a lie."""
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), _witness(), amplification=2.0
        )
        self.assertEqual(result["verdict"], PASS)
        self.assertEqual(result["span_after"], 4.0)
        self.assertIn("BELOW_NOMINAL", [f["kind"] for f in result["faults"]])
        nominal = next(f for f in result["faults"] if f["kind"] == "BELOW_NOMINAL")
        self.assertFalse(nominal["decisive"])

    def test_amplification_below_one_is_refused(self):
        evolver = _evolver()
        evolver.seed(_air())
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(("Mind", "World", 0), _witness(), amplification=0.5)

    def test_amplification_may_be_raised(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), _witness(), amplification=500.0
        )
        self.assertEqual(result["span_after"], 1000.0)

    def test_the_span_is_clamped_at_the_horizon(self):
        evolver = _bare()
        evolver.seed(Interface("A", "B", "c", span=1e11, difficulty=0.9))
        result = evolver.cross_interface(
            ("A", "B", 0), {"mechanism": "c", "evidence": "e"}, amplification=1e6
        )
        self.assertEqual(result["span_after"], SPAN_HORIZON)

    def test_the_span_never_becomes_infinity(self):
        evolver = _bare()
        evolver.seed(Interface("A", "B", "c", span=1e11, difficulty=0.9))
        result = evolver.cross_interface(
            ("A", "B", 0), {"mechanism": "c", "evidence": "e"}, amplification=1e6
        )
        self.assertTrue(result["span_after"] != float("inf"))
        self.assertTrue(result["span_after"] == result["span_after"])

    def test_saturation_is_reported_not_hidden(self):
        evolver = _bare()
        evolver.seed(Interface("A", "B", "c", span=1e11, difficulty=0.9))
        result = evolver.cross_interface(
            ("A", "B", 0), {"mechanism": "c", "evidence": "e"}, amplification=1e6
        )
        self.assertTrue(result["escalated"])

    def test_an_unknown_interface_is_refused(self):
        with self.assertRaises(InterfaceError):
            _evolver().cross_interface(("Mind", "World", 0), _witness())

    def test_a_callers_error_in_amplification_leaves_the_gap_open(self):
        evolver = _evolver()
        evolver.seed(_air())
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(("Mind", "World", 0), _witness(), amplification=0.1)
        self.assertFalse(evolver.chain()[0].condition_met)


# ---------------------------------------------------------------------------
# the fractal law
# ---------------------------------------------------------------------------


class TestTheLaw(unittest.TestCase):
    def test_difficulty_rises_with_every_generation(self):
        evolver, first, second, third = _three_rungs()
        values = [first["difficulty"], second["difficulty"], third["difficulty"]]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])))

    def test_difficulty_never_reaches_one(self):
        """A gap that arrives is a wall, and a wall is not an interface."""
        evolver = _bare()
        evolver.seed(Interface("A", "B", "c", span=1, difficulty=0.999999))
        result = evolver.cross_interface(
            ("A", "B", 0), {"mechanism": "c", "evidence": "e"}
        )
        self.assertLess(result["sub_interface"]["difficulty"], 1.0)

    def test_the_nesting_ratio_is_configurable(self):
        fast = _evolver(nesting_ratio=0.9)
        fast.seed(_air())
        result = fast.cross_interface(("Mind", "World", 0), _witness())
        self.assertGreater(result["sub_interface"]["difficulty"], 0.9 * 0.85 + 0.85 * 0)

    def test_an_out_of_range_ratio_is_refused(self):
        for bad in (0.0, 1.0, -0.5, 2.0):
            with self.assertRaises(InterfaceError):
                _evolver(nesting_ratio=bad)

    def test_the_default_ratio_is_the_documented_one(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), _witness())
        expected = 0.85 + (1.0 - 0.85) * NESTING_RATIO
        self.assertAlmostEqual(result["sub_interface"]["difficulty"], expected, places=9)

    def test_the_default_amplification_is_the_documented_one(self):
        self.assertEqual(
            Interface("A", "B", "c", span=1, difficulty=0.9).amplification,
            DEFAULT_AMPLIFICATION,
        )

    def test_the_ladder_is_self_similar(self):
        evolver, _, _, _ = _three_rungs()
        status = evolver.invariants()
        self.assertTrue(status["self_similar"], status["violations"])
        self.assertEqual(status["generations"], 3)

    def test_the_ladder_reaches_the_documented_numbers(self):
        _, first, second, third = _three_rungs()
        self.assertEqual(first["span_after"], 100.0)
        self.assertEqual(second["span_after"], 1_000_000.0)
        self.assertEqual(third["span_after"], SPAN_HORIZON)

    def test_reach_explodes_by_at_least_four_orders_each_rung(self):
        _, first, second, third = _three_rungs()
        self.assertGreaterEqual(second["span_after"] / first["span_after"], 1e4)
        self.assertGreaterEqual(third["span_after"] / second["span_after"], 1e4)

    def test_crossing_does_not_delete_the_gap(self):
        evolver, _, _, _ = _three_rungs()
        self.assertEqual(len(evolver.frontier()), 1)
        self.assertEqual(len(evolver.chain()), 4)

    def test_a_stalled_difficulty_is_caught(self):
        """The invariant as a second check on chains built elsewhere."""
        child = Interface(
            "E", "F", "two", span=1000, difficulty=0.90, generation=1,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=100, difficulty=0.90, condition_met=True,
            crossed_by="one", sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        status = evolver.invariants()
        self.assertFalse(status["self_similar"])
        self.assertTrue(any("difficulty did not rise" in v for v in status["violations"]))

    def test_a_shrinking_span_is_caught(self):
        child = Interface(
            "E", "F", "two", span=10, difficulty=0.95, generation=1,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=100, difficulty=0.90, condition_met=True,
            sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        self.assertTrue(
            any("span shrank" in v for v in evolver.invariants()["violations"])
        )

    def test_a_sub_interface_under_an_uncrossed_gap_is_caught(self):
        child = Interface(
            "E", "F", "two", span=1000, difficulty=0.95, generation=1,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=100, difficulty=0.90, condition_met=False,
            sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        self.assertTrue(
            any("never crossed" in v for v in evolver.invariants()["violations"])
        )

    def test_a_wall_is_caught(self):
        child = Interface(
            "E", "F", "two", span=1000, difficulty=1.0, generation=1,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=100, difficulty=0.90, condition_met=True,
            sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        self.assertTrue(any("wall" in v for v in evolver.invariants()["violations"]))

    def test_a_wrong_generation_number_is_caught(self):
        child = Interface(
            "E", "F", "two", span=1000, difficulty=0.95, generation=5,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=100, difficulty=0.90, condition_met=True,
            sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        self.assertTrue(
            any("generation" in v for v in evolver.invariants()["violations"])
        )


# ---------------------------------------------------------------------------
# infrastructure
# ---------------------------------------------------------------------------


class TestEscalation(unittest.TestCase):
    def test_a_saturated_self_similar_interface_is_infrastructure(self):
        evolver, _, _, _ = _three_rungs()
        verdict = evolver.escalate(("Mind", "World", 2))
        self.assertTrue(verdict["is_infrastructure"])
        self.assertTrue(verdict["at_horizon"])
        self.assertTrue(verdict["chain_self_similar"])

    def test_an_uncrossed_gap_is_not_infrastructure(self):
        evolver = _evolver()
        evolver.seed(_air())
        with self.assertRaises(InterfaceError):
            evolver.escalate(("Mind", "World", 0))

    def test_a_saturated_malformed_chain_is_not_infrastructure(self):
        """Saturation plus a broken law is a large number, not a foundation."""
        child = Interface(
            "E", "F", "two", span=SPAN_HORIZON, difficulty=0.90, generation=1,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=SPAN_HORIZON, difficulty=0.90, condition_met=True,
            sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        verdict = evolver.escalate(("E", "F", 0))
        self.assertFalse(verdict["is_infrastructure"])
        self.assertTrue(verdict["at_horizon"])
        self.assertFalse(verdict["chain_self_similar"])
        self.assertIn("not self-similar", verdict["reason"])

    def test_an_unsaturated_interface_is_not_infrastructure(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness())
        verdict = evolver.escalate(("Mind", "World", 0))
        self.assertFalse(verdict["is_infrastructure"])
        self.assertIn("of", verdict["reason"])


# ---------------------------------------------------------------------------
# reading the ladder
# ---------------------------------------------------------------------------


class TestReading(unittest.TestCase):
    def test_chain_without_a_filter_returns_every_rung(self):
        evolver, _, _, _ = _three_rungs()
        self.assertEqual(len(evolver.chain()), 4)

    def test_chain_can_be_filtered_to_one_pair(self):
        evolver, _, _, _ = _three_rungs()
        other = Interface("Mind", "Other", "x", span=1, difficulty=0.9)
        evolver.add_layer(Layer(key="Other", name="Other"))
        evolver.seed(other)
        self.assertEqual(len(evolver.chain("Mind", "World")), 4)

    def test_the_ladder_renders_ascii(self):
        evolver, _, _, _ = _three_rungs()
        rendered = evolver.render_chain()
        self.assertTrue(rendered.isascii())
        self.assertIn("FRACTAL INTERFACE LADDER", rendered)

    def test_the_ladder_says_when_it_is_not_self_similar(self):
        child = Interface(
            "E", "F", "two", span=1000, difficulty=0.90, generation=1,
            parent=("E", "F", 0),
        )
        root = Interface(
            "E", "F", "one", span=100, difficulty=0.90, condition_met=True,
            sub_interfaces=(child,),
        )
        evolver = FractalEvolver()
        evolver.seed(root)
        self.assertIn("NOT self-similar", evolver.render_chain())

    def test_an_empty_ladder_renders_without_crashing(self):
        self.assertIn("no interfaces", _evolver().render_chain())

    def test_the_tree_renders_ascii(self):
        evolver, _, _, _ = _three_rungs()
        rendered = evolver.render_tree()
        self.assertTrue(rendered.isascii())
        self.assertIn("gen 0", rendered)

    def test_the_tree_marks_crossed_and_open_gaps(self):
        evolver, _, _, _ = _three_rungs()
        rendered = evolver.render_tree()
        self.assertIn("[x]", rendered)
        self.assertIn("[ ]", rendered)

    def test_an_empty_tree_renders(self):
        self.assertIn("empty", _evolver().render_tree())

    def test_history_records_every_attempt(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), None)
        evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertEqual([step["step"] for step in evolver.history], [1, 2])

    def test_the_history_is_a_copy(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), None)
        evolver.history[0]["verdict"] = "TAMPERED"
        self.assertEqual(evolver.history[0]["verdict"], INVALID)

    def test_every_attempt_carries_a_digest(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertTrue(result["logged"] is False)
        self.assertTrue(evolver.history[-1]["digest"].startswith("sha256:"))


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


class TestLogging(unittest.TestCase):
    def test_a_membrane_receives_the_verdict(self):
        calls = []

        class Membrane:
            def append_call(self, tool, verdict, **fields):
                calls.append((tool, verdict))

        evolver = FractalEvolver(_layers(), membrane=Membrane())
        evolver.seed(_air())
        evolver.cross_interface(("Mind", "World", 0), _witness())
        self.assertEqual(calls, [("fractal_interfaces", PASS)])

    def test_absence_of_a_membrane_is_recorded(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), None)
        self.assertFalse(result["logged"])
        self.assertIn("unrecorded", result["log_note"])

    def test_a_minimal_membrane_still_accepts(self):
        seen = []

        class Minimal:
            def append_call(self, tool, verdict):
                seen.append(verdict)

        evolver = FractalEvolver(_layers(), membrane=Minimal())
        evolver.seed(_air())
        self.assertTrue(evolver.cross_interface(("Mind", "World", 0), None)["logged"])
        self.assertEqual(seen, [INVALID])

    def test_a_refusing_membrane_is_reported(self):
        class Refusing:
            def append_call(self, *args, **kwargs):
                raise RuntimeError("chain full")

        evolver = FractalEvolver(_layers(), membrane=Refusing())
        evolver.seed(_air())
        result = evolver.cross_interface(("Mind", "World", 0), None)
        self.assertFalse(result["logged"])
        self.assertIn("chain full", result["log_note"])

    def test_an_object_without_append_call_is_refused(self):
        with self.assertRaises(InterfaceError):
            FractalEvolver(_layers(), membrane=object())


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


class TestFormatSpan(unittest.TestCase):
    def test_small_spans_are_grouped(self):
        self.assertEqual(format_span(100), "100")
        self.assertEqual(format_span(1_000_000), "1,000,000")

    def test_large_spans_use_scientific_notation(self):
        self.assertEqual(format_span(1e12), "1e12")

    def test_a_fractional_span_keeps_two_places(self):
        self.assertEqual(format_span(2.5), "2.50")

    def test_infinity_is_named_rather_than_printed(self):
        self.assertEqual(format_span(float("inf")), "unbounded")
        self.assertEqual(format_span(float("nan")), "unbounded")


# ---------------------------------------------------------------------------
# narrow conditions
# ---------------------------------------------------------------------------


class TestNarrowCondition(unittest.TestCase):
    def test_a_condition_needs_a_name(self):
        with self.assertRaises(InterfaceError):
            NarrowCondition("", "m", "e", amplification=2.0)

    def test_amplification_must_exceed_one(self):
        for bad in (1.0, 0.5, 0.0, -1):
            with self.assertRaises(InterfaceError):
                NarrowCondition("n", "m", "e", amplification=bad)

    def test_cost_must_be_a_unit_interval(self):
        for bad in (-0.1, 1.1, "cheap"):
            with self.assertRaises(InterfaceError):
                NarrowCondition("n", "m", "e", amplification=2.0, cost=bad)

    def test_replicable_must_be_a_bool(self):
        with self.assertRaises(InterfaceError):
            NarrowCondition("n", "m", "e", amplification=2.0, replicable="yes")

    def test_satisfies_must_be_a_bool(self):
        with self.assertRaises(InterfaceError):
            NarrowCondition("n", "m", "e", amplification=2.0, satisfies="yes")

    def test_a_condition_round_trips(self):
        condition = NarrowCondition("n", "m", "e", 2.0, replicable=False, cost=0.9)
        self.assertEqual(condition.as_dict()["replicable"], False)
        self.assertEqual(condition.as_dict()["amplification"], 2.0)


class TestStatus(unittest.TestCase):
    """Verdict answers "did it hold"; status answers "how far did it carry"."""

    def _cross(self, amplification, replicable=True):
        evolver = _evolver()
        evolver.seed(_air())
        return evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("c", "mechanism", "artifact-1", amplification,
                            replicable=replicable, cost=0.5),
        )

    def test_a_small_amplification_is_narrow(self):
        result = self._cross(2.0)
        self.assertEqual(result["verdict"], PASS)
        self.assertEqual(result["status"], NARROW)

    def test_a_large_amplification_is_expansive(self):
        result = self._cross(10_000.0)
        self.assertEqual(result["status"], EXPANSIVE)

    def test_the_ceiling_is_the_boundary_between_them(self):
        self.assertEqual(
            self._cross(REPLICATION_CEILING)["status"], NARROW
        )
        self.assertEqual(
            self._cross(REPLICATION_CEILING * 1.01)["status"], EXPANSIVE
        )

    def test_an_unreplicable_mechanism_is_always_narrow(self):
        """Even a large amplification cannot make a laboratory non-local."""
        result = self._cross(5.0, replicable=False)
        self.assertEqual(result["status"], NARROW)

    def test_an_uncrossed_gap_has_no_status(self):
        evolver = _evolver()
        evolver.seed(_air())
        self.assertEqual(evolver.chain()[0].status, "")

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(InterfaceError):
            Interface("A", "B", "c", span=1, difficulty=0.9, status="MAYBE")

    def test_the_status_is_visible_on_the_interface(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("c", "m", "artifact-1", 1000.0),
        )
        self.assertEqual(evolver.chain()[0].status, EXPANSIVE)


class TestUnreplicableReach(unittest.TestCase):
    def test_a_lab_bound_mechanism_cannot_claim_scale(self):
        """The contradiction is the filing's, and it is decisive."""
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("cryostat", "m", "artifact-1", 10_000.0,
                            replicable=False),
        )
        self.assertEqual(result["verdict"], FAIL)
        kinds = [f["kind"] for f in result["faults"]]
        self.assertIn("UNREPLICABLE_REACH", kinds)

    def test_a_lab_bound_mechanism_may_claim_a_little(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("cryostat", "m", "artifact-1", 2.0, replicable=False),
        )
        self.assertEqual(result["verdict"], PASS)

    def test_an_undeclared_replicability_is_not_a_claim_of_scale(self):
        """Unspecified is not the same as false; the benefit of the doubt applies."""
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0), _witness(), amplification=10_000.0
        )
        self.assertEqual(result["verdict"], PASS)
        self.assertNotIn("UNREPLICABLE_REACH", [f["kind"] for f in result["faults"]])

    def test_the_ceiling_is_configurable(self):
        evolver = _evolver(replication_ceiling=100.0)
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("c", "m", "artifact-1", 5_000.0, replicable=False),
        )
        self.assertEqual(result["verdict"], FAIL)

    def test_an_out_of_range_ceiling_is_refused(self):
        for bad in (1.0, 0.5, -2):
            with self.assertRaises(InterfaceError):
                _evolver(replication_ceiling=bad)


class TestSupersession(unittest.TestCase):
    """Cryogenics crossed the gap first; a protein crossed it wider."""

    def _crossed(self):
        evolver = _evolver()
        evolver.seed(_air())
        evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("cryostat", "cooling", "lab-114", 2.0, replicable=False),
        )
        return evolver

    def test_a_different_mechanism_may_supersede(self):
        evolver = self._crossed()
        result = evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("protein", "scaffold", "3EML", 10_000.0),
        )
        self.assertEqual(result["verdict"], PASS)
        self.assertTrue(result["superseding"])

    def test_the_same_mechanism_is_still_refused(self):
        """Reach is not a counter, and a supersession is not a loophole."""
        evolver = self._crossed()
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(
                ("Mind", "World", 0),
                NarrowCondition("cryostat", "cooling", "lab-114", 500.0),
            )

    def test_a_supersession_that_buys_less_is_refused(self):
        evolver = self._crossed()
        with self.assertRaises(InterfaceError):
            evolver.cross_interface(
                ("Mind", "World", 0),
                NarrowCondition("worse", "other", "id-1", 1.5),
            )

    def test_reach_is_recomputed_from_the_origin_not_compounded(self):
        """Otherwise two mechanisms would multiply each other."""
        evolver = self._crossed()
        result = evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("protein", "scaffold", "3EML", 10_000.0),
        )
        self.assertEqual(result["origin_span"], 2.0)
        self.assertEqual(result["span_after"], 20_000.0)

    def test_a_supersession_replaces_the_next_gap(self):
        """The old interior belonged to a laboratory, not to a biosphere."""
        evolver = self._crossed()
        evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("protein", "scaffold", "3EML", 10_000.0),
            next_condition="biomimetic structure",
        )
        self.assertEqual(len(evolver.frontier()), 1)
        self.assertEqual(evolver.frontier()[0].condition, "biomimetic structure")

    def test_the_origin_span_survives_the_supersession(self):
        evolver = self._crossed()
        evolver.cross_interface(
            ("Mind", "World", 0),
            NarrowCondition("protein", "scaffold", "3EML", 10_000.0),
        )
        self.assertEqual(evolver.chain()[0].origin_span, 2.0)


class TestSloganEvidence(unittest.TestCase):
    def test_a_slogan_is_not_evidence(self):
        for slogan in ("it obviously works", "trust me", "common sense", "It works."):
            evolver = _evolver()
            evolver.seed(_air())
            result = evolver.cross_interface(
                ("Mind", "World", 0),
                {"mechanism": "language", "evidence": slogan},
            )
            self.assertEqual(result["verdict"], INVALID, slogan)
            self.assertIn(
                "ASSERTION_NOT_EVIDENCE",
                [f["kind"] for f in result["faults"]],
                slogan,
            )

    def test_a_locator_is_accepted(self):
        evolver = _evolver()
        evolver.seed(_air())
        result = evolver.cross_interface(
            ("Mind", "World", 0),
            {"mechanism": "language", "evidence": "lab notebook L-114"},
        )
        self.assertEqual(result["verdict"], PASS)

    def test_the_blacklist_is_exported_and_finite(self):
        """A closed set of slogans, not a quality test of evidence."""
        from agent_metabolism.interface_engine import PLACEHOLDER_EVIDENCE

        self.assertTrue(PLACEHOLDER_EVIDENCE)
        self.assertTrue(all(isinstance(item, str) for item in PLACEHOLDER_EVIDENCE))


# ---------------------------------------------------------------------------
# declared magnitudes
# ---------------------------------------------------------------------------


class TestMagnitudeGap(unittest.TestCase):
    def _layers(self, first, second, quantity="coherence_s"):
        return (
            Layer("A", "A", measured={quantity: first}),
            Layer("B", "B", measured={quantity: second}),
        )

    def test_the_ratio_is_twelve_decades_for_a_picosecond(self):
        quantum, macro = self._layers(1e-12, 1.0)
        ratio, decades = magnitude_gap(quantum, macro, "coherence_s")
        self.assertAlmostEqual(ratio, 1e12, delta=1.0)
        self.assertAlmostEqual(decades, 12.0, places=9)

    def test_the_ratio_is_direction_free(self):
        quantum, macro = self._layers(1e-12, 1.0)
        self.assertEqual(
            magnitude_gap(quantum, macro, "coherence_s"),
            magnitude_gap(macro, quantum, "coherence_s"),
        )

    def test_equal_quantities_have_no_gap(self):
        a, b = self._layers(1.0, 1.0)
        ratio, decades = magnitude_gap(a, b, "coherence_s")
        self.assertEqual((ratio, decades), (1.0, 0.0))

    def test_a_missing_quantity_is_an_error(self):
        a, b = self._layers(1.0, 2.0)
        with self.assertRaises(InterfaceError):
            magnitude_gap(a, b, "temperature_k")

    def test_a_zero_measurement_is_refused_at_construction(self):
        """A zero would make every ratio against it meaningless."""
        for bad in (0, -1, float("inf")):
            with self.assertRaises(InterfaceError):
                Layer("A", "A", measured={"coherence_s": bad})

    def test_a_layer_round_trips_with_measurements(self):
        layer = Layer("A", "A", measured={"coherence_s": 1e-12})
        self.assertEqual(Layer(**layer.as_dict()), layer)


# ---------------------------------------------------------------------------
# the promise
# ---------------------------------------------------------------------------


class TestNoThirdPartyImports(unittest.TestCase):
    def test_the_module_imports_only_the_standard_library_and_itself(self):
        import ast

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "agent_metabolism", "interface_engine.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        allowed = set(sys.stdlib_module_names) | {"agent_metabolism"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            offenders.extend(n for n in names if n not in allowed)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
