"""The bio-quantum narrative, pinned so it cannot drift.

A demo is a claim with a plot. If the engine's rules change and the demo still
prints the same triumphant table, the demo has become a press release. These
tests run the demo and read the numbers back, so a rule change that quietly
breaks the story fails here rather than in a screenshot.

The specific numbers are the argument:

    10 -> 20          the cryostat: a real crossing, a laboratory of reach
    10 -> 100,000     the protein: the same crossing, four orders wider
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_bio_quantum_fractal as demo  # noqa: E402
from agent_metabolism.double_entry import PASS  # noqa: E402
from agent_metabolism.interface_engine import (  # noqa: E402
    EXPANSIVE,
    NARROW,
    FractalEvolver,
    Interface,
    magnitude_gap,
)


def run_demo():
    """Run the demo, capture what it printed, and report its exit code."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = demo.main()
    return code, buffer.getvalue()


class TestTheGap(unittest.TestCase):
    def test_the_coherence_gap_is_twelve_decades(self):
        macro, quantum = demo.build_layers()
        ratio, decades = magnitude_gap(quantum, macro, "coherence_s")
        self.assertAlmostEqual(ratio, 1e12, delta=1.0)
        self.assertAlmostEqual(decades, 12.0, places=9)

    def test_the_macro_layer_wants_a_second(self):
        macro, _ = demo.build_layers()
        self.assertEqual(macro.value("coherence_s"), 1.0)

    def test_the_quantum_layer_gets_a_picosecond(self):
        _, quantum = demo.build_layers()
        self.assertEqual(quantum.value("coherence_s"), 1e-12)


class TestTheTwoPaths(unittest.TestCase):
    """The cryostat and the protein are both correct, and not interchangeable."""

    def setUp(self):
        self.macro, self.quantum = demo.build_layers()
        self.cryogenic, self.protein = demo.build_conditions()
        self.evolver = FractalEvolver([self.macro, self.quantum])
        self.gap = Interface(
            "Macro", "Quantum", "coherence at 300 K", span=10, difficulty=0.99
        )
        self.evolver.seed(self.gap)

    def test_the_gap_starts_at_difficulty_099(self):
        self.assertEqual(self.gap.difficulty, 0.99)
        self.assertEqual(self.gap.span, 10.0)

    def test_cooling_passes_and_is_narrow(self):
        result = self.evolver.cross_interface(self.gap, self.cryogenic)
        self.assertEqual(result["verdict"], PASS)
        self.assertEqual(result["status"], NARROW)
        self.assertEqual(result["amplification"], 2.0)
        self.assertEqual(result["span_after"], 20.0)

    def test_cooling_is_not_replicable(self):
        self.assertFalse(self.cryogenic.replicable)
        self.assertTrue(self.cryogenic.cost > 0.5)

    def test_the_protein_passes_and_is_expansive(self):
        self.evolver.cross_interface(self.gap, self.cryogenic)
        result = self.evolver.cross_interface(self.gap, self.protein)
        self.assertEqual(result["verdict"], PASS)
        self.assertEqual(result["status"], EXPANSIVE)
        self.assertEqual(result["amplification"], 10_000.0)
        self.assertEqual(result["span_after"], 100_000.0)

    def test_the_protein_is_cheap_and_replicable(self):
        self.assertTrue(self.protein.replicable)
        self.assertTrue(self.protein.cost < 0.05)

    def test_the_protein_does_not_compound_on_the_cryostat(self):
        """100,000 is 10 x 10,000, not 20 x 10,000."""
        self.evolver.cross_interface(self.gap, self.cryogenic)
        result = self.evolver.cross_interface(self.gap, self.protein)
        self.assertEqual(result["origin_span"], 10.0)

    def test_the_next_gap_is_the_one_the_story_names(self):
        self.evolver.cross_interface(self.gap, self.cryogenic)
        self.evolver.cross_interface(
            self.gap, self.protein, next_condition="biomimetic structure"
        )
        frontier = self.evolver.frontier()
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0].generation, 1)
        self.assertGreater(frontier[0].difficulty, self.gap.difficulty)
        self.assertFalse(frontier[0].is_crossed)


class TestTheDemoRuns(unittest.TestCase):
    def test_the_demo_passes_its_own_checks(self):
        code, output = run_demo()
        self.assertEqual(code, 0, output[-2000:])
        self.assertIn("All", output)

    def test_the_demo_prints_the_gap(self):
        _, output = run_demo()
        self.assertIn("1e12x", output)
        self.assertIn("12.0", output)

    def test_the_demo_prints_both_paths(self):
        _, output = run_demo()
        self.assertIn("Cryogenic_Cooling", output)
        self.assertIn("Protein_Scaffold", output)
        self.assertIn("NARROW", output)
        self.assertIn("EXPANSIVE", output)

    def test_the_demo_stays_within_a_terminal(self):
        _, output = run_demo()
        too_wide = [line for line in output.splitlines() if len(line) > 78]
        self.assertEqual(too_wide, [], too_wide[:3])

    def test_the_demo_never_prints_a_non_finite_number(self):
        """``inf`` or ``nan`` in a reach column means an overflow was formatted."""
        _, output = run_demo()
        leaked = re.findall(r"\b(?:inf|nan)\b", output.lower())
        self.assertEqual(leaked, [])
