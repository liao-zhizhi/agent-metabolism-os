"""The three defects in ERRATA.md, pinned as behaviour rather than as prose.

These tests exist for one reason: the defects they cover were all *silent*. Each
one produced a record that looked fine, so nothing in the existing 594 tests
could catch it. A test written after the fact is worth exactly what it would have
been worth before, which is why each of these asserts the property that was
missing rather than the fix that supplied it.

The three:

    ERR-001  round(x, 6) turned any gap with 1-D < 5e-7 into a reported wall
    ERR-002  the successor clamp kept returning PASS after the ladder stopped
             being fractal
    ERR-003  the witness digest did not bind the difficulty, so two different
             claims shared one witness

The last one is the important one. A repository whose thesis is "a claim is only
as good as the witness that binds it" cannot ship a witness that does not bind.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism.interface_engine import (  # noqa: E402
    DIGEST_SCHEME,
    DIFFICULTY_CEILING,
    EXPANSIVE,
    NARROW,
    PASS,
    FractalEvolver,
    Interface,
    Layer,
    NarrowCondition,
    upgrade_witness,
    verify_witness,
    witness_digest,
    witness_payload,
)


def _evolver() -> FractalEvolver:
    return FractalEvolver(
        [
            Layer("Macro", "Macroscopic layer", "biology", measured={"t": 1.0}),
            Layer("Quantum", "Quantum layer", "physics", measured={"t": 1e-12}),
        ]
    )


def _gap(difficulty: float, span: float = 10.0) -> Interface:
    return Interface(
        "Macro", "Quantum", "coherence at 300 K in water",
        span=span, difficulty=difficulty, amplification=2.0,
    )


def _condition(amplification: float = 2.0) -> NarrowCondition:
    return NarrowCondition(
        name="Protein_Scaffold",
        mechanism="pigment cage fixed at 0.4 nm",
        evidence="structure P-1 with fs spectroscopy",
        amplification=amplification,
        replicable=False,
        cost=0.02,
    )


def _cross(difficulty: float, **kwargs):
    evolver = _evolver()
    gap = _gap(difficulty)
    evolver.seed(gap)
    result = evolver.cross_interface(gap, _condition(), **kwargs)
    return evolver, result


# ---------------------------------------------------------------------------
# ERR-001
# ---------------------------------------------------------------------------


class TestNoFalseWalls(unittest.TestCase):
    """A gap reported as difficulty 1.0 is a wall. Nothing else may be."""

    def test_a_hard_gap_is_reported_exactly(self):
        for exponent in (6, 7, 8, 9, 12):
            difficulty = 1.0 - 10.0 ** (-exponent)
            _, result = _cross(difficulty)
            self.assertLess(
                result["difficulty"], 1.0,
                "1-1e-{} was reported as a wall".format(exponent),
            )
            self.assertAlmostEqual(result["difficulty"], difficulty, places=15)

    def test_the_result_is_not_rounded(self):
        difficulty = 1.0 - 3.7e-9
        _, result = _cross(difficulty)
        self.assertAlmostEqual(result["difficulty"], difficulty, places=15)
        self.assertNotEqual(result["difficulty"], round(difficulty, 6))

    def test_as_dict_carries_full_precision(self):
        difficulty = 1.0 - 3.7e-9
        payload = _gap(difficulty).as_dict()
        self.assertAlmostEqual(payload["difficulty"], difficulty, places=15)

    def test_escalate_carries_full_precision(self):
        evolver, _ = _cross(1.0 - 3.7e-9, next_condition="inside")
        verdict = evolver.escalate(("Macro", "Quantum", 0))
        self.assertAlmostEqual(verdict["difficulty"], 1.0 - 3.7e-9, places=15)

    def test_the_renderer_does_not_show_a_false_wall(self):
        """Rendering is where rounding belongs, so it must spend it correctly."""
        evolver, _ = _cross(1.0 - 1e-9, next_condition="inside")
        rendered = evolver.render_chain()
        self.assertNotIn("1.000  ", rendered)
        self.assertIn("0.999999999", rendered)

    def test_a_mild_gap_still_renders_readably(self):
        evolver, _ = _cross(0.9)
        self.assertIn("0.900", evolver.render_chain())


# ---------------------------------------------------------------------------
# ERR-002
# ---------------------------------------------------------------------------


class TestTheLadderEndsHonestly(unittest.TestCase):
    """At the ceiling the ladder must say it ended, not grow a copy of itself."""

    def test_a_crossing_below_the_ceiling_still_spawns_a_rung(self):
        evolver, result = _cross(1.0 - 1e-8, next_condition="inside")
        self.assertFalse(result["terminal"])
        self.assertIsNotNone(result["sub_interface"])
        self.assertGreater(
            result["sub_interface"]["difficulty"], result["difficulty"]
        )

    def test_at_the_ceiling_no_rung_is_spawned(self):
        evolver, result = _cross(1.0 - 1e-9, next_condition="inside")
        self.assertTrue(result["terminal"])
        self.assertIsNone(result["sub_interface"])
        self.assertEqual(evolver.frontier(), [])

    def test_the_crossing_still_passes(self):
        """The crossing happened. Only the successor failed to be narrower."""
        _, result = _cross(1.0 - 1e-9, next_condition="inside")
        self.assertEqual(result["verdict"], PASS)

    def test_the_termination_is_reported_as_a_fault(self):
        _, result = _cross(1.0 - 1e-9, next_condition="inside")
        kinds = [fault["kind"] for fault in result["faults"]]
        self.assertIn("DIFFICULTY_SATURATED", kinds)
        fault = next(
            f for f in result["faults"] if f["kind"] == "DIFFICULTY_SATURATED"
        )
        self.assertIsNone(fault["verdict"], "a terminal ladder is not a failure")

    def test_the_chain_does_not_claim_to_be_broken(self):
        """No counterfeit rung means no violation, which is the point."""
        evolver, _ = _cross(1.0 - 1e-9, next_condition="inside")
        status = evolver.invariants()
        self.assertTrue(status["self_similar"], status["violations"])
        self.assertEqual(status["terminal_rungs"], 1)

    def test_a_healthy_chain_reports_no_terminal_rungs(self):
        evolver, _ = _cross(0.99, next_condition="inside")
        self.assertEqual(evolver.invariants()["terminal_rungs"], 0)

    def test_the_ceiling_constant_is_the_successor_floor(self):
        from agent_metabolism.interface_engine import _next_difficulty

        self.assertLessEqual(_next_difficulty(1.0, 0.5), DIFFICULTY_CEILING)
        self.assertEqual(_next_difficulty(DIFFICULTY_CEILING, 0.5), DIFFICULTY_CEILING)


# ---------------------------------------------------------------------------
# ERR-003
# ---------------------------------------------------------------------------


class TestTheWitnessBindsTheClaim(unittest.TestCase):
    """Two different claims must not share a witness."""

    def test_the_scheme_is_versioned(self):
        _, result = _cross(0.9)
        self.assertTrue(result["digest"].startswith(DIGEST_SCHEME + ":"))
        self.assertEqual(result["digest_scheme"], DIGEST_SCHEME)

    def test_v1_prefix_compatibility_is_preserved(self):
        """Anything testing ``startswith('sha256:')`` must keep working."""
        _, result = _cross(0.9)
        self.assertTrue(result["digest"].startswith("sha256:"))
        self.assertEqual(len(result["digest"]), len(DIGEST_SCHEME) + 1 + 64)

    def test_different_difficulties_get_different_digests(self):
        """The exact defect: these four once produced one digest."""
        digests = {}
        for difficulty in (0.50, 0.90, 0.99, 0.9999):
            _, result = _cross(difficulty)
            digests[difficulty] = result["digest"]
        self.assertEqual(
            len(set(digests.values())), len(digests),
            "distinct gaps collided onto one witness",
        )

    def test_the_same_claim_gets_the_same_digest(self):
        _, first = _cross(0.9)
        _, second = _cross(0.9)
        self.assertEqual(first["digest"], second["digest"])

    def test_the_payload_names_what_it_binds(self):
        _, result = _cross(0.9)
        payload = witness_payload(result)
        for field in ("difficulty", "span_before", "span_after", "status",
                      "mechanism", "evidence", "faults"):
            self.assertIn(field, payload)
        self.assertEqual(payload["v"], 2)

    def test_an_unbound_field_would_leave_a_hole(self):
        """Guard the fix rather than the symptom: dropping a field must show."""
        _, result = _cross(0.9)
        full = witness_payload(result)
        thin = {k: v for k, v in full.items() if k != "difficulty"}
        self.assertNotEqual(full, thin)
        self.assertNotEqual(witness_digest(result),
                            witness_digest({k: v for k, v in result.items()
                                            if k != "difficulty"}))


class TestVerifyWitness(unittest.TestCase):
    """A witness that is never re-checked is decoration."""

    def test_the_untouched_record_verifies(self):
        _, result = _cross(0.9)
        self.assertTrue(verify_witness(result))

    def test_editing_the_difficulty_breaks_it(self):
        _, result = _cross(0.9)
        tampered = dict(result)
        tampered["difficulty"] = 0.95
        self.assertFalse(verify_witness(tampered))

    def test_editing_a_span_breaks_it(self):
        _, result = _cross(0.9)
        tampered = dict(result)
        tampered["span_after"] = 999.0
        self.assertFalse(verify_witness(tampered))

    def test_editing_the_verdict_breaks_it(self):
        _, result = _cross(0.9)
        tampered = dict(result)
        tampered["verdict"] = "FAIL"
        self.assertFalse(verify_witness(tampered))

    def test_editing_the_status_breaks_it(self):
        _, result = _cross(0.9)
        tampered = dict(result)
        tampered["status"] = EXPANSIVE if result["status"] == NARROW else NARROW
        self.assertFalse(verify_witness(tampered))

    def test_a_record_without_a_digest_does_not_verify(self):
        _, result = _cross(0.9)
        stripped = {k: v for k, v in result.items() if k != "digest"}
        self.assertFalse(verify_witness(stripped))

    def test_every_recorded_step_verifies(self):
        evolver, _ = _cross(0.99, next_condition="inside")
        for step in evolver.history:
            self.assertTrue(verify_witness(step), step.get("step"))


class TestUpgradeWitness(unittest.TestCase):
    """Upgrading a v1 record freezes it; it does not resurrect its guarantee."""

    def _v1(self):
        return {
            "interface": ["Macro", "Quantum", 0],
            "verdict": PASS,
            "difficulty": 0.9,
            "span_before": 10.0,
            "span_after": 20.0,
            "amplification": 2.0,
            "status": NARROW,
            "mechanism": "pigment cage",
            "evidence": "structure P-1",
            "replicable": False,
            "digest": "sha256:" + "0" * 64,
        }

    def test_the_upgrade_moves_to_v2(self):
        upgraded = upgrade_witness(self._v1())
        self.assertTrue(upgraded["digest"].startswith(DIGEST_SCHEME + ":"))
        self.assertTrue(upgraded["upgraded"])

    def test_the_old_digest_is_recorded_not_discarded(self):
        upgraded = upgrade_witness(self._v1())
        self.assertEqual(upgraded["superseded_digest"], "sha256:" + "0" * 64)

    def test_the_upgraded_record_verifies(self):
        self.assertTrue(verify_witness(upgrade_witness(self._v1())))

    def test_editing_after_upgrade_breaks_it(self):
        upgraded = upgrade_witness(self._v1())
        upgraded["difficulty"] = 0.95
        self.assertFalse(verify_witness(upgraded))

    def test_swapping_the_provenance_breaks_it(self):
        """The old digest is part of the claim, so it cannot be rewritten."""
        upgraded = upgrade_witness(self._v1())
        upgraded["superseded_digest"] = "sha256:" + "1" * 64
        self.assertFalse(verify_witness(upgraded))

    def test_upgrading_twice_is_a_new_freeze_not_a_chain_of_trust(self):
        once = upgrade_witness(self._v1())
        twice = upgrade_witness(once)
        self.assertTrue(verify_witness(twice))
        self.assertEqual(twice["superseded_digest"], once["digest"])


if __name__ == "__main__":
    unittest.main()
