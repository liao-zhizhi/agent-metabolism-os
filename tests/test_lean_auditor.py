"""Tests for the Lean 4 proof auditor.

The cases that matter here are not "does it find `sorry`" -- that is a `grep`.
They are the ones where a naive scanner gives a *confidently wrong* answer:

* ``sorry`` inside a comment or a string literal is not a hole. A tool that
  accuses an innocent file gets turned off, which is the same as not having one.
* ``axiom`` inside a docstring is prose, not a declaration.
* ``opaque`` without a value is *not* ``axiom`` under another keyword, despite
  looking exactly like one: Lean requires the type to be inhabited, so it cannot
  manufacture a false proposition. It earns a note, never a FAIL. The test that
  pins this down is the one that would have shipped the false accusation.
* ``Classical.choice``, ``propext`` and ``Quot.sound`` are Lean's own axioms.
  Flagging them would fail almost every real proof.
* Lean accepts ``sorry`` and ``axiom`` with exit code 0, so a compiler PASS must
  never be allowed to upgrade a placeholder finding.

The last group runs a real Lean toolchain when one is present and skips
otherwise, so the suite is deterministic on a machine without Lean but still
checks the empirical claim where it can be checked.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_metabolism import (  # noqa: E402
    COMPILER,
    CUSTOM_AXIOM,
    HIDDEN_BODY,
    LEAN_FAIL,
    LEAN_INVALID,
    LEAN_PASS,
    PLACEHOLDER,
    CompileReport,
    LeanAuditorError,
    LeanProofAuditor,
    LeanToolchain,
    UnavailableCompiler,
    mask_source,
)

_SIDECAR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agent-harness-audit",
)
if os.path.isdir(_SIDECAR) and _SIDECAR not in sys.path:
    sys.path.insert(0, _SIDECAR)

try:
    from audit_sidecar.immune_state import ImmuneState
    from audit_sidecar.tools.membrane import MembraneGateway
    from audit_sidecar.verdict import Verdict

    _HAVE_SIDECAR = True
except ImportError:  # pragma: no cover - the package ships without the auditor
    _HAVE_SIDECAR = False


def _needs_sidecar(test):
    return unittest.skipUnless(_HAVE_SIDECAR, "harness-audit-sidecar not installed")(test)


ACCEPTED = CompileReport(status=LEAN_PASS, available=True, exit_code=0, reason="accepted")
REJECTED = CompileReport(
    status=LEAN_FAIL, available=True, exit_code=1, reason="Lean rejected the file"
)


class _StubCompiler:
    """A compiler witness with a fixed answer, so tests never need Lean."""

    def __init__(self, report=None, error=None):
        self.report = ACCEPTED if report is None else report
        self.error = error
        self.seen = []

    def check(self, source, *, theorems=()):
        self.seen.append((source, tuple(theorems)))
        if self.error is not None:
            raise self.error
        return self.report


def _audit(source, *, compiler=None, **kwargs):
    auditor = LeanProofAuditor(
        compiler=_StubCompiler() if compiler is None else compiler, **kwargs
    )
    return auditor.audit(source, claim=kwargs.pop("claim", ""))


class TestMasking(unittest.TestCase):
    """The scan must see code, not prose. Every case here is a false-positive."""

    def test_a_line_comment_is_not_code(self):
        source = "theorem t : True := trivial\n-- sorry, will finish later\n"
        result = _audit(source)
        self.assertEqual(result.placeholders, ())
        self.assertEqual(result.verdict, LEAN_PASS)

    def test_a_nested_block_comment_is_not_code(self):
        # Lean allows nesting, so a stripper without a depth counter would end
        # the comment at the first `-/` and read the remainder as live code.
        source = (
            "theorem t : True := trivial\n"
            "/- outer /- inner sorry -/ still a comment -- axiom x : False -/ \n"
        )
        result = _audit(source)
        self.assertEqual(result.placeholders, ())
        self.assertEqual(result.axioms, ())
        self.assertEqual(result.verdict, LEAN_PASS)

    def test_a_string_literal_is_not_code(self):
        source = 'def note : String := "sorry, axiom, admit"\ntheorem t : True := trivial\n'
        result = _audit(source)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.verdict, LEAN_PASS)

    def test_a_doc_comment_mentioning_axioms_is_not_a_declaration(self):
        source = "/-- This file introduces no axiom of any kind. -/\ntheorem t : True := trivial\n"
        self.assertEqual(_audit(source).axioms, ())

    def test_masking_preserves_length_and_line_positions(self):
        source = "a -- b\n/- c\nd -/\ne \"f\"\n"
        masked = mask_source(source)
        self.assertEqual(len(masked), len(source), "offsets must still map")
        self.assertEqual(
            [i for i, c in enumerate(masked) if c == "\n"],
            [i for i, c in enumerate(source) if c == "\n"],
            "line numbers must not shift",
        )
        # `a` survives; the `--` comment body is blanked; `e` after the block
        # comment survives, so the stripper closed it at the right place.
        self.assertEqual(masked[0], "a")
        self.assertNotIn("b", masked)
        self.assertIn("e", masked)
        self.assertNotIn("f", masked, "the string literal was blanked, as intended")

    def test_an_identifier_containing_a_keyword_is_not_a_placeholder(self):
        # `sorryAx` and `not_a_sorry` are names, not holes.
        source = "def sorryAx := 1\ndef not_a_sorry := 2\ntheorem t : True := trivial\n"
        self.assertEqual(_audit(source).placeholders, ())

    def test_an_apostrophe_in_an_identifier_does_not_open_a_string(self):
        # `'` is legal inside Lean names, so a lexer that treats it as a quote
        # delimiter would swallow the rest of the line and miss a real hole.
        source = "def h' := 1\ntheorem t : True := by sorry\n"
        result = _audit(source)
        self.assertEqual(len(result.placeholders), 1)
        self.assertEqual(result.placeholders[0].line, 2)


class TestPlaceholderWitness(unittest.TestCase):
    def test_sorry_is_fail(self):
        """A hole is a broken promise, not a gap in the auditor's knowledge.

        The agent stated the proof was complete; the artifact says otherwise.
        Both halves of that contradiction are visible, so this is a positive
        finding -- the same class as a smuggled axiom, and the same verdict.
        """
        result = _audit("theorem t : True := by sorry")
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertEqual(len(result.placeholders), 1)
        self.assertIn("sorry", result.placeholders[0].detail)
        self.assertIn("claims the proof is complete", result.placeholders[0].detail)

    def test_admit_is_fail(self):
        result = _audit("theorem t : True := by admit")
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertIn("admit", result.placeholders[0].detail)

    def test_both_cheats_share_one_verdict(self):
        """The ruling: same offence class, same verdict, same penalty.

        This is the invariant that keeps the immune thresholds from inverting,
        so it is asserted directly rather than left to the composition tests.
        """
        hole = _audit("theorem t : True := by sorry").verdict
        cheat = _audit("axiom a : True").verdict
        self.assertEqual(hole, cheat)

    def test_the_finding_points_at_the_token(self):
        source = "theorem a : True := trivial\ntheorem b : True := by sorry\n"
        finding = _audit(source).placeholders[0]
        self.assertEqual(finding.line, 2)
        self.assertEqual(finding.column, 24)
        self.assertEqual(finding.snippet, "theorem b : True := by sorry")

    def test_every_placeholder_is_reported_not_just_the_first(self):
        source = "theorem a : True := by sorry\ntheorem b : True := by admit\n"
        self.assertEqual(len(_audit(source).placeholders), 2)


class TestAxiomWitness(unittest.TestCase):
    def test_axiom_is_fail(self):
        result = _audit("axiom riemann : True\ntheorem t : True := riemann")
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertEqual(result.axioms[0].kind, CUSTOM_AXIOM)

    def test_a_bare_opaque_is_a_note_and_not_a_failure(self):
        """`opaque x : T` looks like `axiom x : T` and is not.

        Measured on Lean 4.33.1: ``opaque boom : False`` is rejected with
        "failed to synthesize 'Inhabited' or 'Nonempty'", because Lean fills the
        body with a default inhabitant. It therefore cannot prove a false
        proposition, and failing it would be a false accusation. This test exists
        because the first version of this auditor made exactly that accusation.
        """
        result = _audit("opaque magic : Nat\ntheorem t : True := trivial")
        self.assertEqual(result.verdict, LEAN_PASS)
        self.assertEqual(result.axioms, ())
        hidden = [f for f in result.findings if f.kind == HIDDEN_BODY]
        self.assertEqual(len(hidden), 1)
        self.assertIsNone(hidden[0].verdict, "advisory findings carry no verdict")

    def test_an_advisory_finding_can_never_move_a_verdict(self):
        # The invariant behind `verdict=None`: notes do not accumulate into an
        # accusation no matter how many of them a file has.
        source = "\n".join("opaque hidden_{} : Nat".format(i) for i in range(5))
        result = _audit(source + "\ntheorem t : True := trivial")
        self.assertEqual(len([f for f in result.findings if f.kind == HIDDEN_BODY]), 5)
        self.assertEqual(result.verdict, LEAN_PASS)

    def test_an_opaque_with_a_value_is_not_reported(self):
        source = "opaque answer : Nat := 42\ntheorem t : True := trivial"
        result = _audit(source)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.verdict, LEAN_PASS)

    def test_a_later_definition_does_not_make_an_opaque_look_supplied(self):
        # The `:=` below belongs to `other`, not to the bare `opaque`.
        source = "opaque hole : Nat\ndef other : Nat := 1"
        hidden = [f for f in _audit(source).findings if f.kind == HIDDEN_BODY]
        self.assertEqual(len(hidden), 1)

    def test_the_print_axioms_command_is_not_a_declaration(self):
        source = "#print axioms some_theorem\ntheorem t : True := trivial"
        self.assertEqual(_audit(source).axioms, ())


class TestVerdictComposition(unittest.TestCase):
    """Worst-wins over witnesses, and no witness may loosen another."""

    def test_a_clean_file_that_compiles_is_pass(self):
        result = _audit("theorem t : True := trivial")
        self.assertEqual(result.verdict, LEAN_PASS)
        self.assertTrue(result.passed)

    def test_a_clean_scan_without_a_compiler_is_invalid(self):
        """The claim this test protects: no compiler, no verification.

        A regex cannot distinguish a proof from a file with a syntax error, so
        a clean scan with no toolchain must not reach PASS.
        """
        result = _audit(
            "theorem t : True := trivial", compiler=UnavailableCompiler("no lean")
        )
        self.assertEqual(result.verdict, LEAN_INVALID)
        self.assertFalse(result.compile.measured)
        self.assertIn("no physical witness", result.reason)

    def test_placeholders_alone_are_fail(self):
        self.assertEqual(_audit("theorem t : True := by sorry").verdict, LEAN_FAIL)

    def test_axioms_alone_are_fail(self):
        self.assertEqual(_audit("axiom a : True").verdict, LEAN_FAIL)

    def test_both_are_fail_because_the_worse_verdict_wins(self):
        result = _audit("axiom a : True\ntheorem t : True := by sorry")
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertEqual(len(result.placeholders), 1)
        self.assertEqual(len(result.axioms), 1)

    def test_a_compiler_pass_cannot_upgrade_a_placeholder(self):
        """The measured reason the compiler is not the only witness.

        Lean returns 0 for `sorry`, so trusting the exit code would report a
        hole as a complete proof.
        """
        result = _audit(
            "theorem t : True := by sorry", compiler=_StubCompiler(ACCEPTED)
        )
        self.assertEqual(result.compile.status, LEAN_PASS)
        self.assertEqual(result.compile.exit_code, 0)
        self.assertEqual(result.verdict, LEAN_FAIL)

    def test_a_compiler_failure_is_fail(self):
        result = _audit("theorem t : True := nope", compiler=_StubCompiler(REJECTED))
        self.assertEqual(result.verdict, LEAN_FAIL)

    def test_a_lazy_compiler_cannot_rescue_an_axiom(self):
        result = _audit("axiom a : True", compiler=_StubCompiler(ACCEPTED))
        self.assertEqual(result.verdict, LEAN_FAIL)

    def test_an_empty_submission_is_refused(self):
        with self.assertRaises(LeanAuditorError):
            _audit("   \n\t\n")

    def test_a_non_string_submission_is_refused(self):
        with self.assertRaises(TypeError):
            _audit(b"theorem t : True := trivial")  # type: ignore[arg-type]


class TestCompilerWitness(unittest.TestCase):
    def test_print_axioms_names_a_smuggled_axiom(self):
        report = CompileReport(
            status=LEAN_PASS,
            available=True,
            exit_code=0,
            axioms=(("riemann_proved", ("riemann_hypothesis",)),),
            reason="accepted",
        )
        result = _audit("theorem riemann_proved : True := trivial", compiler=_StubCompiler(report))
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertIs(result.findings[0].kind, CUSTOM_AXIOM)
        self.assertIn("riemann_hypothesis", result.findings[0].detail)

    def test_sorry_ax_is_caught_even_when_the_word_never_appears(self):
        """The text says nothing; the axiom report is what catches it.

        This is the case a lexical scanner cannot see at all: the hole lives in
        an imported lemma, and only `#print axioms` knows.
        """
        report = CompileReport(
            status=LEAN_PASS,
            available=True,
            exit_code=0,
            axioms=(("t", ("sorryAx",)),),
            reason="accepted",
        )
        result = _audit("theorem t : True := trivial", compiler=_StubCompiler(report))
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertEqual(result.findings[0].kind, PLACEHOLDER)
        self.assertIn("sorryAx", result.findings[0].detail)

    def test_leans_own_axioms_are_not_evidence_of_cheating(self):
        """A false positive here would fail almost every real proof."""
        report = CompileReport(
            status=LEAN_PASS,
            available=True,
            exit_code=0,
            axioms=(("t", ("Classical.choice", "propext", "Quot.sound")),),
            reason="accepted",
        )
        result = _audit("theorem t : True := trivial", compiler=_StubCompiler(report))
        self.assertEqual(result.verdict, LEAN_PASS)
        self.assertEqual(result.findings, ())

    def test_a_dotted_standard_axiom_is_also_whitelisted(self):
        report = CompileReport(
            status=LEAN_PASS,
            available=True,
            exit_code=0,
            axioms=(("t", ("propext",)), ("u", ())),
            reason="accepted",
        )
        self.assertEqual(
            _audit("theorem t : True := trivial", compiler=_StubCompiler(report)).verdict,
            LEAN_PASS,
        )

    def test_a_broken_witness_does_not_accuse(self):
        # An exception is not a verdict. Treating it as FAIL would let a broken
        # toolchain condemn a correct proof.
        result = _audit(
            "theorem t : True := trivial", compiler=_StubCompiler(error=OSError("boom"))
        )
        self.assertEqual(result.verdict, LEAN_INVALID)
        self.assertIn("failed to run", result.reason)

    def test_theorem_names_are_read_from_the_file(self):
        compiler = _StubCompiler()
        _audit("theorem alpha : True := trivial\nlemma beta : True := trivial", compiler=compiler)
        self.assertEqual(compiler.seen[0][1], ("alpha", "beta"))

    def test_a_named_theorem_does_not_have_to_come_from_the_file(self):
        compiler = _StubCompiler()
        LeanProofAuditor(compiler=compiler).audit(
            "theorem t : True := trivial", theorem_names=["main"]
        )
        self.assertEqual(compiler.seen[0][1], ("main",))


class _RecordingMembrane:
    """Records what would be appended, without a ledger."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def append_call(self, tool, verdict, **fields):
        if self.error is not None:
            raise self.error
        self.calls.append((tool, verdict, fields))
        return {"seq": len(self.calls), "tool": tool, "verdict": verdict}


class TestTrail(unittest.TestCase):
    def test_no_membrane_is_reported_rather_than_hidden(self):
        result = _audit("theorem t : True := trivial")
        self.assertFalse(result.logged)
        self.assertIn("no membrane attached", result.log_error)
        # The verdict still stands; only the record is missing.
        self.assertEqual(result.verdict, LEAN_PASS)

    def test_the_verdict_and_findings_reach_the_membrane(self):
        membrane = _RecordingMembrane()
        result = LeanProofAuditor(membrane, compiler=_StubCompiler()).audit(
            "theorem t : True := by sorry", claim="proved RH"
        )
        tool, verdict, fields = membrane.calls[0]
        self.assertEqual(tool, "lean.audit")
        self.assertEqual(verdict, LEAN_FAIL)
        self.assertEqual(fields["claim"], "proved RH")
        self.assertEqual(len(fields["findings"]), 1)
        self.assertTrue(result.logged)

    def test_the_recorded_digest_is_the_digest_of_the_submitted_text(self):
        membrane = _RecordingMembrane()
        source = "theorem t : True := trivial"
        result = LeanProofAuditor(membrane, compiler=_StubCompiler()).audit(source)
        expected = "sha256:" + hashlib.sha256(source.encode()).hexdigest()
        self.assertEqual(result.digest, expected)
        self.assertEqual(membrane.calls[0][2]["digest"], expected)

    def test_a_refused_record_does_not_erase_the_verdict(self):
        membrane = _RecordingMembrane(error=RuntimeError("ledger is broken"))
        result = LeanProofAuditor(membrane, compiler=_StubCompiler()).audit(
            "axiom a : True"
        )
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertFalse(result.logged)
        self.assertIn("could not be written", result.log_error)

    def test_a_narrow_membrane_signature_still_gets_the_verdict(self):
        class Narrow:
            def __init__(self):
                self.calls = []

            def append_call(self, tool, verdict, *, tokens_used=None, elapsed_ms=None,
                            capability=None, digest=None):
                # No **extra: the rich payload above must not lose the verdict.
                self.calls.append((tool, verdict, digest))
                return {"seq": 1}

        narrow = Narrow()
        result = LeanProofAuditor(narrow, compiler=_StubCompiler()).audit(
            "theorem t : True := by sorry"
        )
        self.assertTrue(result.logged, "the verdict must survive a narrow signature")
        self.assertEqual(narrow.calls[0][1], LEAN_FAIL)
        self.assertIsNotNone(result.log_note, "the degradation must be named")

    @_needs_sidecar
    def test_two_incomplete_proofs_inflame_the_immune_layer(self):
        """The reason the verdict was changed, asserted end to end.

        Before the ruling a `sorry` produced INVALID, and the immune thresholds
        are asymmetric (one INVALID, two FAILs), so a file full of holes inflamed
        the system faster than a smuggled axiom did. That inversion is what this
        test forbids from coming back.
        """
        with tempfile.TemporaryDirectory() as directory:
            immune = ImmuneState()
            gateway = MembraneGateway(
                os.path.join(directory, "trace.jsonl"), immune_state=immune
            )
            auditor = LeanProofAuditor(
                gateway,
                compiler=_StubCompiler(),
                observer=lambda audit: immune.record_verdict(Verdict(audit.verdict)),
            )

            first = auditor.audit("theorem t : True := by sorry")
            self.assertEqual(first.verdict, "FAIL")
            self.assertEqual(immune.state, "NORMAL", "one failure is a normal day")

            second = auditor.audit("axiom a : True")
            self.assertEqual(second.verdict, "FAIL")
            self.assertEqual(immune.state, "INFLAMED")
            self.assertTrue(immune.is_inflamed())

    @_needs_sidecar
    def test_a_hole_and_an_axiom_inflame_equally_fast(self):
        """No asymmetry may remain between the two offences."""
        def sessions(source):
            immune = ImmuneState()
            auditor = LeanProofAuditor(
                compiler=_StubCompiler(),
                observer=lambda audit: immune.record_verdict(Verdict(audit.verdict)),
            )
            for _ in range(2):
                auditor.audit(source)
            return immune.state

        self.assertEqual(
            sessions("theorem t : True := by sorry"),
            sessions("axiom a : True"),
            "leaving a hole must not hurt differently from smuggling an axiom",
        )

    @_needs_sidecar
    def test_the_chain_verifies_after_an_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = MembraneGateway(os.path.join(directory, "trace.jsonl"))
            auditor = LeanProofAuditor(gateway, compiler=_StubCompiler())
            auditor.audit("theorem t : True := by sorry", claim="proved RH")
            auditor.audit("axiom a : True")
            self.assertEqual(gateway.verify()["status"], "PASS")
            entries = [e for e in gateway.ledger.entries() if e.get("record_type") == "call"]
            self.assertEqual([e["verdict"] for e in entries], [LEAN_FAIL, LEAN_FAIL])
            self.assertTrue(all(e["tool"] == "lean.audit" for e in entries))

    def test_a_penalty_is_only_charged_when_asked(self):
        class Ledger:
            def __init__(self):
                self.charged = []

            def record_violation(self, kind):
                self.charged.append(kind)
                return type("V", (), {"penalty": 50.0})()

        quiet = Ledger()
        LeanProofAuditor(compiler=_StubCompiler(), penalty_ledger=quiet).audit(
            "axiom a : True"
        )
        self.assertEqual(quiet.charged, [], "charging by default is not the auditor's call")

        loud = Ledger()
        result = LeanProofAuditor(
            compiler=_StubCompiler(), penalty_ledger=loud, charge_penalty=True
        ).audit("axiom a : True")
        self.assertEqual(loud.charged, ["FALSE_COMPLETION"])
        self.assertEqual(result.penalty_charged, 50.0)

    def test_an_incomplete_proof_is_fined_as_a_false_completion(self):
        """This test used to assert the opposite, and was wrong.

        It encoded the retracted rule ("a hole means we cannot tell, so it is not
        a lie"). Under the ruling, a hole next to a claim of completion *is* the
        lie, so it is charged exactly like a smuggled axiom.
        """

        class Ledger:
            def __init__(self):
                self.charged = []

            def record_violation(self, kind):
                self.charged.append(kind)
                return type("V", (), {"penalty": 50.0})()

        ledger = Ledger()
        result = LeanProofAuditor(
            compiler=_StubCompiler(), penalty_ledger=ledger, charge_penalty=True
        ).audit("theorem t : True := by sorry")
        self.assertEqual(ledger.charged, ["FALSE_COMPLETION"])
        self.assertEqual(result.penalty_charged, 50.0)


class TestRealLeanToolchain(unittest.TestCase):
    """Runs the actual compiler where one exists, and skips where it does not."""

    def setUp(self):
        toolchain = LeanToolchain()
        if toolchain.resolved_binary() is None:
            self.skipTest("no Lean toolchain on this machine")
        self.toolchain = toolchain

    def test_lean_accepts_a_sorry_proof_with_exit_zero(self):
        """The measurement that makes exit-code-only checking unsound."""
        report = self.toolchain.check("theorem t : (1:Nat) = 1 := by sorry")
        self.assertEqual(report.status, LEAN_PASS)
        self.assertEqual(report.exit_code, 0)
        self.assertTrue(report.warnings, "Lean warns, but does not fail")

    def test_print_axioms_exposes_the_placeholder_axiom(self):
        report = self.toolchain.check(
            "theorem t : (1:Nat) = 1 := by sorry", theorems=("t",)
        )
        self.assertEqual(report.axiom_map()["t"], ("sorryAx",))

    def test_a_smuggled_axiom_compiles_cleanly(self):
        source = "axiom fake : True\ntheorem t : True := fake"
        report = self.toolchain.check(source, theorems=("t",))
        self.assertEqual(report.status, LEAN_PASS)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.axiom_map()["t"], ("fake",),)
        self.assertEqual(report.warnings, (), "Lean says nothing at all")

    def test_an_honest_proof_reports_no_axioms(self):
        report = self.toolchain.check("theorem t : (1:Nat) = 1 := rfl", theorems=("t",))
        self.assertEqual(report.status, LEAN_PASS)
        self.assertEqual(report.axiom_map()["t"], ())

    def test_opaque_cannot_be_used_to_state_a_falsehood(self):
        """The measurement that stopped `opaque` from being classified a cheat."""
        report = self.toolchain.check("opaque boom : False")
        self.assertEqual(report.status, LEAN_FAIL)
        self.assertIn("nhabited", report.stdout + report.stderr)

    def test_a_bare_opaque_of_an_inhabited_type_compiles(self):
        report = self.toolchain.check("opaque magic : Nat")
        self.assertEqual(report.status, LEAN_PASS)

    def test_a_syntax_error_is_a_failure(self):
        report = self.toolchain.check("theorem t : := ")
        self.assertEqual(report.status, LEAN_FAIL)
        self.assertNotEqual(report.exit_code, 0)

    def test_the_end_to_end_case(self):
        auditor = LeanProofAuditor()
        result = auditor.audit(
            "axiom riemann : True\ntheorem riemann_proved : True := riemann\n"
            "theorem spare : True := by sorry",
            claim="solved the Riemann hypothesis",
        )
        self.assertEqual(result.verdict, LEAN_FAIL)
        self.assertTrue(result.compile.measured)
        self.assertEqual(result.compile.status, LEAN_PASS, "the compiler was happy")
        self.assertTrue(result.placeholders)
        self.assertTrue(result.axioms)


if __name__ == "__main__":
    unittest.main()
