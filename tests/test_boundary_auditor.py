"""Tests for the boundary auditor.

A boundary auditor is easy to write badly in one specific way: it becomes a
confidence engine. It says FAIL about things nobody measured and PASS about
things nobody checked, and both sentences sound authoritative. The cases below
are chosen to pin the opposite behaviour:

* A filing with only prose must be INVALID, never FAIL. "Nobody looked" and "we
  looked and it is wrong" are different findings; collapsing them turns every
  thin patent into a refutation, which is over-accusation by the auditor.
* A filing whose own numbers fall short must be FAIL, never INVALID. It measured
  itself and lost; that is the strongest finding available here and it would be
  wasted as "unaudited".
* An advisory note must be structurally unable to change a verdict. The
  reliability note is the test case: it must not be able to fail a filing.
* A known mechanism with correct arithmetic passes even if the auditor has never
  heard of it -- a novel mechanism is not an offence.
* A hypothetical filing is used for the PASS cases and is labelled as such. No
  test here asserts that any real physics claim is true, because nothing in this
  module could establish that.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism.double_entry import FAIL, INVALID, PASS  # noqa: E402
from agent_metabolism.boundary_auditor import (  # noqa: E402
    DEFAULT_INVENTIONS,
    DEFAULT_LAYERS,
    MIN_STRUCTURAL_PARAMETERS,
    BoundaryAuditor,
    BoundaryError,
    Invention,
    Layer,
    WitnessFault,
)


def _params(**overrides):
    base = {
        "chromophore_separation_A": 12.4,
        "scaffold_barrier_height_meV": 210.0,
        "debye_temperature_K": 220.0,
    }
    base.update(overrides)
    return base


def _witness(**overrides):
    base = {
        "mechanism": "protein_scaffold_shielding",
        "structural_parameters": _params(),
        "suppression_factor": 1e12,
        "coherence_time_s": 1.0,
        "operating_temperature_k": 300.0,
        "medium": "aqueous",
        "single_shot": True,
    }
    base.update(overrides)
    return base


def _filing(**overrides):
    base = {
        "identifier": "T",
        "title": "a test filing",
        "claim": "a claim",
        "witness": _witness(),
        "targets": ("Theory_Quantum", "Theory_Bio"),
    }
    base.update(overrides)
    return Invention(**base)


def _audit(invention, layer_a="Theory_Quantum", layer_b="Theory_Bio", **kwargs):
    return BoundaryAuditor(**kwargs).audit_boundary_crossing(
        layer_a, layer_b, invention
    )


def _kinds(result):
    return {fault["kind"] for fault in result["faults"]}


class TestTheGap(unittest.TestCase):
    """The boundary is a number before it is a verdict."""

    def test_the_default_gap_is_twelve_decades(self):
        boundary = BoundaryAuditor().boundary("Theory_Quantum", "Theory_Bio")
        self.assertAlmostEqual(boundary.decades, 12.0, places=6)
        self.assertAlmostEqual(boundary.gap_ratio, 1e12, delta=1e6)

    def test_the_gap_is_the_worse_demand_over_the_better_supply(self):
        """Not a within-layer ratio, which would be 1 by construction."""
        boundary = BoundaryAuditor().boundary("Theory_Quantum", "Theory_Bio")
        self.assertEqual(boundary.required_coherence_s, 1.0)
        self.assertEqual(boundary.intrinsic_coherence_s, 1e-12)

    def test_direction_does_not_change_the_gap(self):
        auditor = BoundaryAuditor()
        forward = auditor.boundary("Theory_Quantum", "Theory_Bio")
        backward = auditor.boundary("Theory_Bio", "Theory_Quantum")
        self.assertEqual(forward.gap_ratio, backward.gap_ratio)

    def test_a_layer_is_not_a_boundary_with_itself(self):
        with self.assertRaises(BoundaryError):
            BoundaryAuditor().boundary("Theory_Bio", "Theory_Bio")

    def test_an_unknown_layer_is_named_in_the_error(self):
        with self.assertRaises(BoundaryError) as caught:
            BoundaryAuditor().boundary("Theory_Quantum", "Theory_Vibes")
        self.assertIn("Theory_Vibes", str(caught.exception))

    def test_there_are_four_axes_and_they_are_distinct(self):
        boundary = BoundaryAuditor().boundary("Theory_Quantum", "Theory_Bio")
        keys = [axis.key for axis in boundary.axes]
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 4)
        self.assertIn("temperature", keys)

    def test_a_caller_can_supply_better_measurements(self):
        """The numbers are the verdict's foundation, so they must be replaceable."""
        better = dict(DEFAULT_LAYERS)
        better["Theory_Quantum"] = Layer(
            key="Theory_Quantum",
            name="a slower quantum layer",
            regime="quantum",
            temperature_k=300.0,
            required_coherence_s=1e-9,
            intrinsic_coherence_s=1e-9,
            medium="condensed phase",
            description="test",
        )
        boundary = BoundaryAuditor(layers=better).boundary(
            "Theory_Quantum", "Theory_Bio"
        )
        self.assertAlmostEqual(boundary.decades, 9.0, places=6)


class TestProseWithoutWitness(unittest.TestCase):
    """The filed patent: the right words, and nothing to check."""

    def test_the_default_filing_is_invalid(self):
        result = BoundaryAuditor().audit_boundary_crossing(
            "Theory_Quantum", "Theory_Bio", "Patent_BioQuantum"
        )
        self.assertEqual(result["verdict"], INVALID)
        self.assertFalse(result["closes_gap"])

    def test_a_domain_name_is_not_a_mechanism(self):
        result = BoundaryAuditor().audit_boundary_crossing(
            "Theory_Quantum", "Theory_Bio", "Patent_BioQuantum"
        )
        self.assertIn("NO_MECHANISM", _kinds(result))

    def test_every_missing_witness_is_named(self):
        result = BoundaryAuditor().audit_boundary_crossing(
            "Theory_Quantum", "Theory_Bio", "Patent_BioQuantum"
        )
        kinds = _kinds(result)
        for expected in (
            "NO_MECHANISM",
            "NO_STRUCTURE",
            "NO_SUPPRESSION",
            "NO_TEMPERATURE",
            "NO_MEDIUM",
        ):
            self.assertIn(expected, kinds)

    def test_an_empty_witness_is_invalid_and_says_so(self):
        result = _audit(_filing(witness={}))
        self.assertEqual(result["verdict"], INVALID)
        self.assertIn("NO_WITNESS", _kinds(result))

    def test_invalid_never_carries_a_decisive_fail(self):
        result = _audit(_filing(witness={}))
        self.assertEqual(result["verdict"], INVALID)
        self.assertNotEqual(result["verdict"], FAIL)

    def test_placeholder_mechanisms_are_all_rejected(self):
        for name in ("quantum", "QUANTUM_EFFECTS", "nano", "proprietary", "  "):
            result = _audit(_filing(witness={"mechanism": name}))
            self.assertIn(
                "NO_MECHANISM",
                _kinds(result),
                "{} should not count as a mechanism".format(name),
            )

    def test_the_invalid_reading_does_not_call_it_false(self):
        result = _audit(_filing(witness={}))
        self.assertIn("unaudited", result["reading"])
        self.assertNotIn("refuted", result["reading"])


class TestSelfRefutingFilings(unittest.TestCase):
    """Numbers present, and they lose. This is the strongest finding here."""

    def test_a_suppression_factor_short_of_the_gap_is_fail(self):
        result = _audit(_filing(witness=_witness(
            suppression_factor=1e3, coherence_time_s=1e-9
        )))
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("INSUFFICIENT_SUPPRESSION", _kinds(result))

    def test_the_shortfall_is_quantified_in_decades(self):
        result = _audit(_filing(witness=_witness(
            suppression_factor=1e3, coherence_time_s=1e-9
        )))
        detail = next(
            f["detail"] for f in result["faults"]
            if f["kind"] == "INSUFFICIENT_SUPPRESSION"
        )
        self.assertIn("9.0 decades", detail)

    def test_a_factor_of_one_is_protection_of_none(self):
        result = _audit(_filing(witness=_witness(
            suppression_factor=1.0, coherence_time_s=1e-12
        )))
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("NO_SUPPRESSION", _kinds(result))

    def test_arithmetic_that_does_not_multiply_out_is_fail(self):
        """1e-12 s x 1e12 is a second, not a microsecond."""
        result = _audit(_filing(witness=_witness(
            suppression_factor=1e12, coherence_time_s=1e-6
        )))
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("ARITHMETIC_INCONSISTENT", _kinds(result))

    def test_the_inconsistency_is_spelled_out(self):
        result = _audit(_filing(witness=_witness(coherence_time_s=1e-6)))
        detail = next(
            f["detail"] for f in result["faults"]
            if f["kind"] == "ARITHMETIC_INCONSISTENT"
        )
        self.assertIn("self", detail.replace("-", " "))

    def test_a_negative_structural_parameter_is_fail(self):
        result = _audit(_filing(witness=_witness(
            structural_parameters=_params(chromophore_separation_A=-3.0)
        )))
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("BAD_STRUCTURE", _kinds(result))

    def test_the_bool_is_not_a_number(self):
        """``True`` is 1 to Python and would read as a measurement."""
        result = _audit(_filing(witness=_witness(suppression_factor=True)))
        self.assertIn("NO_SUPPRESSION", _kinds(result))
        self.assertEqual(result["verdict"], INVALID)


class TestWrongLayer(unittest.TestCase):
    """The trap worth understanding: winning the argument, losing the target."""

    def test_cryogenic_coherence_does_not_cross(self):
        result = _audit(_filing(witness=_witness(
            operating_temperature_k=4.0,
            suppression_factor=1e13,
            coherence_time_s=10.0,
        )))
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("WRONG_TEMPERATURE", _kinds(result))

    def test_the_temperature_fault_explains_the_abandonment(self):
        result = _audit(_filing(witness=_witness(operating_temperature_k=4.0)))
        detail = next(
            f["detail"] for f in result["faults"]
            if f["kind"] == "WRONG_TEMPERATURE"
        )
        self.assertIn("left the layer it was addressing", detail)

    def test_room_temperature_is_a_band_not_a_point(self):
        for temperature in (295.0, 300.0, 305.0):
            result = _audit(_filing(witness=_witness(operating_temperature_k=temperature)))
            self.assertNotIn("WRONG_TEMPERATURE", _kinds(result))

    def test_outside_the_band_fails(self):
        for temperature in (294.9, 305.1, 310.0):
            result = _audit(_filing(witness=_witness(operating_temperature_k=temperature)))
            self.assertIn("WRONG_TEMPERATURE", _kinds(result))

    def test_a_vacuum_medium_does_not_cross(self):
        result = _audit(_filing(witness=_witness(medium="ultra-high vacuum")))
        self.assertEqual(result["verdict"], FAIL)
        self.assertIn("WRONG_MEDIUM", _kinds(result))

    def test_water_spellings_are_accepted(self):
        for medium in ("aqueous", "Water", "AQUEOUS_PHYSIOLOGICAL"):
            result = _audit(_filing(witness=_witness(medium=medium)))
            self.assertEqual(result["verdict"], PASS, medium)


class TestThePassPath(unittest.TestCase):
    """The hypothetical filing, and what its PASS is allowed to mean."""

    def test_a_complete_witness_passes(self):
        result = _audit(_filing())
        self.assertEqual(result["verdict"], PASS)
        self.assertTrue(result["closes_gap"])

    def test_a_pass_states_what_it_does_not_establish(self):
        result = _audit(_filing())
        self.assertIn("never establishes that the mechanism works", result["establishes"])

    def test_a_pass_does_not_call_the_invention_working(self):
        result = _audit(_filing())
        self.assertIn("not evidence that it will work", result["reading"])

    def test_an_unrecognised_mechanism_can_still_pass(self):
        """A novel mechanism is not an offence, only an unassessed one."""
        result = _audit(_filing(witness=_witness(mechanism="some_new_idea")))
        self.assertEqual(result["verdict"], PASS)
        self.assertFalse(result["mechanism_recognized"])
        self.assertIn("UNRECOGNISED_MECHANISM", _kinds(result))

    def test_the_unrecognised_note_is_advisory(self):
        result = _audit(_filing(witness=_witness(mechanism="some_new_idea")))
        note = next(
            f for f in result["faults"] if f["kind"] == "UNRECOGNISED_MECHANISM"
        )
        self.assertIsNone(note["verdict"])
        self.assertFalse(note["decisive"])

    def test_a_recognised_mechanism_is_reported(self):
        self.assertTrue(_audit(_filing())["mechanism_recognized"])

    def test_fewer_than_the_minimum_parameters_is_invalid(self):
        short = _params()
        short = {key: short[key] for key in list(short)[: MIN_STRUCTURAL_PARAMETERS - 1]}
        result = _audit(_filing(witness=_witness(structural_parameters=short)))
        self.assertEqual(result["verdict"], INVALID)
        self.assertIn("NO_STRUCTURE", _kinds(result))

    def test_non_numeric_parameters_do_not_count(self):
        result = _audit(_filing(witness=_witness(
            structural_parameters={
                "a": "large", "b": "small", "c": "moderate"
            }
        )))
        self.assertEqual(result["verdict"], INVALID)

    def test_a_dict_can_be_audited_without_wrapping_it_first(self):
        result = BoundaryAuditor().audit_boundary_crossing(
            "Theory_Quantum",
            "Theory_Bio",
            {
                "identifier": "ad-hoc",
                "claim": "a claim",
                "witness": _witness(),
            },
        )
        self.assertEqual(result["verdict"], PASS)

    def test_a_dict_without_a_claim_is_refused(self):
        with self.assertRaises(BoundaryError):
            BoundaryAuditor().audit_boundary_crossing(
                "Theory_Quantum", "Theory_Bio", {"identifier": "x"}
            )

    def test_an_unknown_invention_name_lists_the_known_ones(self):
        with self.assertRaises(BoundaryError) as caught:
            BoundaryAuditor().audit_boundary_crossing(
                "Theory_Quantum", "Theory_Bio", "Patent_DoesNotExist"
            )
        self.assertIn("Patent_BioQuantum", str(caught.exception))

    def test_a_bad_invention_type_is_refused(self):
        with self.assertRaises(BoundaryError):
            BoundaryAuditor().audit_boundary_crossing(
                "Theory_Quantum", "Theory_Bio", 3.14
            )


class TestAdvisoryNotes(unittest.TestCase):
    """A note that could change a verdict would not be a note."""

    def test_the_reliability_note_cannot_fail_a_passing_filing(self):
        witness = _witness()
        witness.pop("single_shot")
        result = _audit(_filing(witness=witness))
        self.assertEqual(result["verdict"], PASS)
        self.assertIn("RELIABILITY_UNSTATED", _kinds(result))

    def test_the_note_is_marked_advisory(self):
        witness = _witness()
        witness.pop("single_shot")
        result = _audit(_filing(witness=witness))
        note = next(f for f in result["faults"] if f["kind"] == "RELIABILITY_UNSTATED")
        self.assertFalse(note["decisive"])
        self.assertIsNone(note["verdict"])

    def test_a_targets_mismatch_is_a_note_not_a_verdict(self):
        result = _audit(
            _filing(targets=("Theory_Quantum", "Theory_Chemistry"))
        )
        self.assertIn("TARGETS_ANOTHER_BOUNDARY", _kinds(result))
        self.assertEqual(result["verdict"], PASS)

    def test_a_witness_fault_without_a_verdict_is_not_decisive(self):
        fault = WitnessFault(kind="NOTE", verdict=None, detail="x")
        self.assertFalse(fault.decisive)


class TestBlindBoundaries(unittest.TestCase):
    """The map describes the registry. It must say which registry."""

    def test_the_default_registry_leaves_every_axis_blind(self):
        rows = BoundaryAuditor().find_blind_boundaries(
            "Theory_Quantum", "Theory_Bio"
        )
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["status"] == "blind" for row in rows))

    def test_one_filing_crosses_the_axes_it_witnesses(self):
        auditor = BoundaryAuditor()
        auditor.add_invention(_filing(identifier="crosser"))
        rows = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
        self.assertTrue(all(row["status"] == "crossed" for row in rows))

    def test_an_attempt_that_falls_short_is_unproven_not_blind(self):
        """A contested axis is not a blind spot; conflating them loses the map."""
        auditor = BoundaryAuditor()
        auditor.add_invention(
            _filing(
                identifier="short",
                witness=_witness(suppression_factor=1e3, coherence_time_s=1e-9),
            )
        )
        rows = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
        lifetime = next(row for row in rows if row["axis"] == "coherence_lifetime")
        self.assertEqual(lifetime["status"], "unproven")
        self.assertTrue(lifetime["shortfall"])
        self.assertEqual(lifetime["attempts"], ["short"])

    def test_the_shortfall_is_named_in_the_row(self):
        auditor = BoundaryAuditor()
        auditor.add_invention(
            _filing(
                identifier="short",
                witness=_witness(suppression_factor=1e3, coherence_time_s=1e-9),
            )
        )
        rows = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
        lifetime = next(row for row in rows if row["axis"] == "coherence_lifetime")
        self.assertIn("short", lifetime["shortfall"][0])

    def test_an_empty_registry_makes_everything_blind(self):
        auditor = BoundaryAuditor(inventions={})
        rows = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
        self.assertTrue(all(row["status"] == "blind" for row in rows))

    def test_every_row_says_it_is_not_a_census(self):
        rows = BoundaryAuditor().find_blind_boundaries(
            "Theory_Quantum", "Theory_Bio"
        )
        for row in rows:
            self.assertIn("registry", row["registry"])

    def test_the_rendered_map_states_the_scope(self):
        auditor = BoundaryAuditor()
        rows = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
        rendered = " ".join(
            BoundaryAuditor.render_blind(
                rows, "Theory_Quantum", "Theory_Bio"
            ).split()
        )
        self.assertIn("not a census of the field", rendered)

    def test_a_boundary_needs_two_real_layers_to_map(self):
        with self.assertRaises(BoundaryError):
            BoundaryAuditor().find_blind_boundaries("Theory_Quantum", "Theory_Nope")


class TestWhatWasChecked(unittest.TestCase):
    """A PASS that lists nothing cannot be told apart from a bug."""

    def test_a_pass_reports_what_was_checked(self):
        result = _audit(_filing())
        labels = {check["check"] for check in result["checks"]}
        self.assertEqual(
            labels,
            {"mechanism", "structure", "suppression", "lifetime",
             "temperature", "environment", "reliability"},
        )

    def test_the_rows_are_derived_from_the_witness_not_the_verdict(self):
        """They must be able to disagree with the outcome, or they prove nothing."""
        result = _audit(_filing(witness=_witness(suppression_factor=1e3,
                                                 coherence_time_s=1e-9)))
        self.assertEqual(result["verdict"], FAIL)
        suppression = next(
            c for c in result["checks"] if c["check"] == "suppression"
        )
        self.assertIn("1e+03", suppression["result"])

    def test_an_unstated_field_says_so_rather_than_being_omitted(self):
        result = _audit(_filing(witness={}))
        for check in result["checks"]:
            self.assertTrue(check["result"])
        unstated = [c for c in result["checks"] if c["result"] == "not stated"]
        self.assertGreaterEqual(len(unstated), 4)

    def test_the_checks_appear_in_the_render(self):
        result = _audit(_filing())
        rendered = BoundaryAuditor.render_audit(result)
        self.assertIn("WHAT WAS CHECKED", rendered)
        self.assertIn("single-shot claimed", rendered)


class TestRendering(unittest.TestCase):
    def test_the_audit_renders_ascii_and_fits_the_page(self):
        result = BoundaryAuditor().audit_boundary_crossing(
            "Theory_Quantum", "Theory_Bio", "Patent_BioQuantum"
        )
        rendered = BoundaryAuditor.render_audit(result)
        self.assertTrue(rendered.isascii())
        for line in rendered.splitlines():
            self.assertLessEqual(len(line), 96, line)

    def test_every_decisive_fault_appears_in_the_render(self):
        result = BoundaryAuditor().audit_boundary_crossing(
            "Theory_Quantum", "Theory_Bio", "Patent_BioQuantum"
        )
        rendered = BoundaryAuditor.render_audit(result)
        for fault in result["faults"]:
            if fault["decisive"]:
                self.assertIn(fault["kind"], rendered)

    def test_the_render_carries_the_scope_sentence(self):
        result = _audit(_filing())
        rendered = " ".join(BoundaryAuditor.render_audit(result).split())
        self.assertIn("never establishes that the mechanism works", rendered)


class TestDigestAndLogging(unittest.TestCase):
    def test_the_digest_is_stable_for_the_same_witness(self):
        first = _audit(_filing())
        second = _audit(_filing())
        self.assertEqual(first["witness_digest"], second["witness_digest"])

    def test_editing_the_witness_changes_the_digest(self):
        first = _audit(_filing())
        second = _audit(_filing(witness=_witness(suppression_factor=1e12, coherence_time_s=1.0)))
        self.assertEqual(first["witness_digest"], second["witness_digest"])
        third = _audit(_filing(witness=_witness(
            structural_parameters=_params(chromophore_separation_A=13.0)
        )))
        self.assertNotEqual(first["witness_digest"], third["witness_digest"])

    def test_the_digest_has_the_package_shape(self):
        result = _audit(_filing())
        self.assertTrue(result["witness_digest"].startswith("sha256:"))
        self.assertEqual(len(result["witness_digest"]), len("sha256:") + 64)

    def test_a_membrane_receives_the_verdict(self):
        calls = []

        class Membrane:
            def append_call(self, tool, verdict, **fields):
                calls.append((tool, verdict))
                return len(calls)

        _audit(_filing(), membrane=Membrane())
        self.assertEqual(calls, [("boundary_auditor", PASS)])

    def test_the_result_records_that_it_was_logged(self):
        class Membrane:
            def append_call(self, tool, verdict, **fields):
                return 1

        self.assertTrue(_audit(_filing(), membrane=Membrane())["logged"])

    def test_a_missing_membrane_is_recorded_not_raised(self):
        result = _audit(_filing())
        self.assertFalse(result["logged"])
        self.assertIn("unrecorded", result["log_note"])

    def test_a_membrane_with_only_the_minimal_signature_still_accepts(self):
        """Widening fallback: an extra field must never lose the record."""
        seen = []

        class Minimal:
            def append_call(self, tool, verdict):
                seen.append(verdict)

        result = _audit(_filing(), membrane=Minimal())
        self.assertTrue(result["logged"])
        self.assertEqual(seen, [PASS])

    def test_a_refusing_membrane_is_reported_not_swallowed(self):
        class Refusing:
            def append_call(self, *args, **kwargs):
                raise RuntimeError("chain full")

        result = _audit(_filing(), membrane=Refusing())
        self.assertFalse(result["logged"])
        self.assertIn("chain full", result["log_note"])

    def test_an_object_without_append_call_is_refused(self):
        with self.assertRaises(BoundaryError):
            BoundaryAuditor(membrane=object())


class TestKnowledgeBase(unittest.TestCase):
    def test_the_default_layers_are_the_two_named_in_the_spec(self):
        self.assertIn("Theory_Quantum", DEFAULT_LAYERS)
        self.assertIn("Theory_Bio", DEFAULT_LAYERS)

    def test_the_filed_patent_is_in_the_default_registry(self):
        self.assertIn("Patent_BioQuantum", DEFAULT_INVENTIONS)

    def test_the_patent_names_the_boundary_it_targets(self):
        self.assertEqual(
            DEFAULT_INVENTIONS["Patent_BioQuantum"].targets,
            ("Theory_Quantum", "Theory_Bio"),
        )

    def test_the_quantum_layer_is_generous_to_itself(self):
        """A boundary audit that flattered the quantum side would make crossing
        look easy, so the stated lifetime is the *optimistic* end of the
        literature rather than the pessimistic one."""
        layer = DEFAULT_LAYERS["Theory_Quantum"]
        self.assertLessEqual(layer.intrinsic_coherence_s, 1e-12)

    def test_the_quantum_layer_carries_its_sources(self):
        self.assertTrue(DEFAULT_LAYERS["Theory_Quantum"].sources)

    def test_the_bio_layer_requires_continuity(self):
        self.assertGreaterEqual(DEFAULT_LAYERS["Theory_Bio"].required_coherence_s, 1.0)

    def test_adding_a_non_invention_is_refused(self):
        with self.assertRaises(BoundaryError):
            BoundaryAuditor().add_invention({"identifier": "x"})


class TestZeroDependencies(unittest.TestCase):
    def test_the_module_imports_only_the_standard_library_and_itself(self):
        """The package-wide check parses every module; this pins the new one.

        Kept local so a failure names the file that broke the promise instead of
        a list of offenders.
        """
        import ast

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "agent_metabolism", "boundary_auditor.py")
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
