"""Formal QC for machine-written Lean 4 proofs.

Nobody on this side of the boundary can check a Riemann proof. That is not a
limitation to work around -- it is the reason a *formal* language is the right
substrate for machine claims in the first place: in Lean, a proof either
elaborates or it does not, and that judgement does not require understanding the
mathematics. This module audits what can be audited without knowing any:

1. **Placeholders.** ``sorry`` and ``admit`` are holes. A file containing one has
   no proof, whatever the agent says -- and that gap between the claim and the
   artifact is itself the finding, so a placeholder is FAIL, not INVALID. See
   the note on :data:`LEAN_SEVERITY` for why that distinction is not cosmetic.
2. **Smuggled axioms.** ``axiom`` declares a proposition true without proving
   it, which turns any theorem into a tautology of the author's choosing. Both
   offences are the same class: the claim of completion is contradicted by the
   code. They share a verdict and a penalty.

   Two near-misses were measured on Lean 4.33.1 and are *not* treated as cheats.
   ``constant`` is Lean 3 spelling and simply fails to elaborate. A bare
   ``opaque x : T`` looks like the same trick, but Lean requires ``T`` to be
   inhabited: ``opaque boom : False`` is rejected with "failed to synthesize
   'Inhabited' or 'Nonempty'", so it cannot manufacture a false proposition. What
   it does do is hide a body, so it is reported as a note -- advisory, and
   deliberately unable to change a verdict. Over-accusing here would be the same
   failure as missing a cheat, only louder.
3. **The compiler.** If a Lean toolchain is on the machine, the file is actually
   elaborated and the exit code read back -- plus ``#print axioms``, which is the
   witness that matters most (see below).

Why the compiler cannot be the only witness
-------------------------------------------
Measured on Lean 4.33.1, not assumed:

    theorem t : (1:Nat) = 1 := by sorry     ->  exit 0, warning "declaration uses `sorry`"
    axiom riemann : True
    theorem u : True := riemann             ->  exit 0, no output at all

Both cheats compile. A gate that trusts ``returncode == 0`` therefore reports a
sorry-riddled, axiom-smuggling file as a complete proof -- a false completion of
exactly the kind this whole project exists to catch, committed by the auditor
itself. So the verdict is composed by *worst-wins* over every witness, and no
witness may ever loosen another: a compiler PASS next to a placeholder finding
stays FAIL.

The converse rule matters just as much: **PASS requires that something actually
elaborated.** Two text witnesses agreeing that a file contains no `sorry` and no
`axiom` says nothing about whether it is a proof -- a file with a syntax error
satisfies both. So a clean scan with no toolchain available is reported as
INVALID, never PASS.

The compiler witness has a second half that is strictly better than a text scan:
``#print axioms thm`` reports the axioms a theorem actually depends on. ``sorry``
elaborates to the axiom ``sorryAx``, so this catches a hole even when the text is
obfuscated -- and it names a smuggled axiom by name. Three of the axioms it can
print are Lean's own (``Classical.choice``, ``propext``, ``Quot.sound``); flagging
those would accuse essentially every real proof, so they are whitelisted.

What this module does not do
----------------------------
* It does not prove anything, and it must not be described as verifying a
  theorem. It reports that no *hole* was found by these three witnesses, which is
  a far weaker claim -- the honest one.
* ``native_decide``, ``@[implemented_by]``, ``@[extern]`` and ``unsafe`` are
  trust-increasing escapes that are not detected. They are not cheats (Lean
  records them), but a rigorous audit would surface them.
* A theorem depending on a ``sorry`` in an *imported* file is invisible unless a
  ``lake`` project is set up; this auditor elaborates single files.
* A file needing Mathlib cannot be compiled without that environment. That is
  reported as ``UNAVAILABLE``, not ``FAIL``: an environment that cannot answer
  must not be allowed to accuse. The distinction is a narrow text heuristic on
  the compiler's own error output, and it is labelled as one.

Zero dependencies. The membrane is duck-typed, as everywhere else in this
package: ``dependencies = []`` is a promise the test suite checks by parsing
imports, not by trust.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "COMPILER",
    "CUSTOM_AXIOM",
    "HIDDEN_BODY",
    "LEAN_FAIL",
    "LEAN_INVALID",
    "LEAN_PASS",
    "LEAN_SEVERITY",
    "LEAN_VERDICTS",
    "PLACEHOLDER",
    "CompileReport",
    "LeanAudit",
    "LeanFinding",
    "LeanProofAuditor",
    "LeanToolchain",
    "UnavailableCompiler",
    "mask_source",
]

#: The three verdicts this auditor can reach. The strings match
#: ``audit_sidecar.verdict.Verdict`` by contract, so a caller may hand the result
#: straight to ``ImmuneState.record_verdict(Verdict(result.verdict))`` without
#: this package importing the auditor. ``NOT RUN`` is deliberately absent: an
#: audit always runs, so that value would be a lie here.
LEAN_PASS = "PASS"
LEAN_FAIL = "FAIL"
LEAN_INVALID = "INVALID"
LEAN_VERDICTS = (LEAN_PASS, LEAN_INVALID, LEAN_FAIL)

#: Worst-wins severity ordering. Both failure values are reachable and neither
#: is decorative: after the ruling that a placeholder is a broken promise rather
#: than a gap in our knowledge, INVALID now means exactly one thing -- *this
#: auditor could not establish anything*, because no physical witness was
#: collected. A verdict of INVALID is a statement about the audit, not about the
#: proof. Everything that is actually a judgement lands on FAIL.
LEAN_SEVERITY = {LEAN_PASS: 0, LEAN_INVALID: 1, LEAN_FAIL: 2}

#: Why a placeholder is FAIL and not INVALID.
#:
#: INVALID used to hold this slot, on the reasoning that an unfinished proof is
#: an absence of evidence. That misplaces the accusation. The offence is not
#: "we could not verify the mathematics" -- it is that the agent *stated* the
#: proof was complete while its own artifact contained a hole. The mismatch
#: between the claim and the code is a positive finding, and the auditor can see
#: both halves of it. Calling that INVALID would understate a contradiction the
#: auditor has direct evidence for, and it also produced a perverse incentive:
#: the immune layer inflames after one INVALID but two FAILs, so leaving holes
#: in a file would have hurt *more* than smuggling an axiom. Same offence class,
#: same verdict, same penalty.

#: Finding kinds, so a reader can tell which witness spoke.
PLACEHOLDER = "PLACEHOLDER"
CUSTOM_AXIOM = "CUSTOM_AXIOM"
COMPILER = "COMPILER"
#: Advisory only. A declaration whose body is hidden: worth a reviewer's eye, not
#: evidence of a hole, and never allowed to move a verdict.
HIDDEN_BODY = "HIDDEN_BODY"

#: Lean's own axioms. A theorem depending on these is normal; flagging them
#: would produce a false accusation against nearly every real proof, which is
#: how an audit gets turned off.
STANDARD_AXIOMS = frozenset({"Classical.choice", "propext", "Quot.sound"})

#: The axiom that ``sorry`` elaborates to. Its presence is proof of a hole even
#: if the word ``sorry`` never appears in the source.
SORRY_AXIOM = "sorryAx"

_PLACEHOLDER_RE = re.compile(r"\b(sorry|admit)\b")
#: ``axiom`` is the spelling the brief names.
_AXIOM_RE = re.compile(r"\baxiom\b")
#: ``opaque`` **without** a value is scanned because it hides a body, and a
#: reviewer should know. It is *not* an axiom: measured on Lean 4.33.1,
#: ``opaque boom : False`` fails with "failed to synthesize 'Inhabited' or
#: 'Nonempty'", because Lean fills the body with a default inhabitant. So it
#: cannot prove anything that was not already inhabited, and it is reported as an
#: advisory note rather than a FAIL. ``opaque x : T := v`` supplies a value and
#: hides nothing that was not already given, so it is not reported at all.
_OPAQUE_RE = re.compile(r"\bopaque\b")
#: Where one top-level command ends and the next begins, used to bound a
#: declaration so "has this `opaque` a body?" can be asked of the right text.
_DECL_START_RE = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t]*)*"
    r"(?:private[ \t]+|protected[ \t]+|noncomputable[ \t]+|unsafe[ \t]+|partial[ \t]+)*"
    r"(?:theorem|lemma|def|abbrev|axiom|opaque|instance|structure|inductive|class|"
    r"namespace|section|end|example|variable|universe|open|import|#)",
    re.MULTILINE,
)
_THEOREM_RE = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t]*)*(?:private[ \t]+|protected[ \t]+|noncomputable[ \t]+)*"
    r"(?:theorem|lemma)[ \t]+([^\s:({\[]+)",
    re.MULTILINE,
)

_AXIOM_LINE_RE = re.compile(r"^'([^']+)' depends on axioms: \[(.*)\]$")
_CLEAN_LINE_RE = re.compile(r"^'([^']+)' does not depend on any axioms$")

#: Compiler messages that mean "this environment cannot answer", as opposed to
#: "this proof is wrong". Narrow on purpose: every pattern here suppresses an
#: accusation, so widening the list weakens the gate.
_ENVIRONMENT_FAILURES = (
    "unknown module prefix",
    "object file",
    "failed to open file",
    "no such file or directory",
    "could not resolve",
    "unknown package",
)

_SNIPPET_LIMIT = 110


class LeanAuditorError(ValueError):
    """The auditor was asked to judge something it cannot describe as code."""


# ---------------------------------------------------------------------------
# source masking -- the difference between scanning code and scanning text
# ---------------------------------------------------------------------------


def _blank(characters: List[str], start: int, end: int) -> None:
    """Replace ``[start, end)`` with spaces, keeping newlines.

    Newlines are preserved so that an offset in the masked text is the same
    offset in the original: line and column numbers in a report must point at
    the real file, and a masking pass that shifted them would make every finding
    a small lie about where the problem is.
    """
    newline = "\n"
    for index in range(start, end):
        if characters[index] != newline:
            characters[index] = " "


def _char_literal_end(source: str, start: int) -> int:
    """End offset of a Lean character literal at ``start``, or ``start + 1``.

    ``'`` is also a legal character *inside* a Lean identifier (``foo'``), so
    treating every apostrophe as a delimiter would shred ordinary definitions.
    Only ``'x'`` and ``'\\n'`` shapes are taken as literals, which is the same
    decision Lean's own lexer makes.
    """
    index = start + 1
    if index < len(source) and source[index] == "\\":
        index += 2
        while index < len(source) and source[index] not in "'\n":
            index += 1
        return index + 1 if index < len(source) and source[index] == "'" else start + 1
    if index < len(source) and source[index] not in "'\n":
        index += 1
        if index < len(source) and source[index] == "'":
            return index + 1
    return start + 1


def mask_source(source: str) -> str:
    """Return ``source`` with comments and string literals blanked out.

    A regex over raw Lean text finds ``sorry`` in ``-- sorry, will do later`` and
    finds ``axiom`` in a docstring that merely *mentions* axioms. Both would be
    false accusations against innocent files, and a tool that cries wolf is worse
    than no tool. So the scan runs over a masked copy:

    * ``--`` line comments, to end of line
    * ``/- ... -/`` block comments, **nested** (Lean allows nesting, and a
      non-nesting stripper leaves the inner body looking like live code)
    * ``"..."`` string literals, with backslash escapes

    Length and newline positions are preserved, so offsets still map onto the
    original file.
    """
    characters = list(source)
    length = len(source)
    index = 0
    while index < length:
        pair = source[index:index + 2]
        character = source[index]

        if pair == "--":
            end = source.find("\n", index)
            if end == -1:
                end = length
            _blank(characters, index, end)
            index = end
        elif pair == "/-":
            depth = 1
            cursor = index + 2
            while cursor < length and depth:
                if source[cursor:cursor + 2] == "/-":
                    depth += 1
                    cursor += 2
                elif source[cursor:cursor + 2] == "-/":
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            _blank(characters, index, cursor)
            index = cursor
        elif character == '"':
            cursor = index + 1
            while cursor < length:
                if source[cursor] == "\\":
                    cursor += 2
                    continue
                if source[cursor] == '"':
                    cursor += 1
                    break
                cursor += 1
            _blank(characters, index, cursor)
            index = cursor
        elif character == "'":
            # Advanced past, not blanked: a character literal cannot contain a
            # keyword, so there is nothing in it to hide.
            index = _char_literal_end(source, index)
        else:
            index += 1
    return "".join(characters)


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeanFinding:
    """One thing a witness saw, where, and what it is worth.

    ``verdict`` is the severity this finding alone would justify. ``None`` means
    the witness said something *about* the file without judging it -- an
    advisory note, not a verdict. Keeping those out of the severity computation
    is what stops a warning from silently becoming a failure.
    """

    kind: str
    verdict: Optional[str]
    detail: str
    line: Optional[int] = None
    column: Optional[int] = None
    snippet: str = ""

    def position(self) -> str:
        if self.line is None:
            return "-"
        return "{}:{}".format(self.line, self.column) if self.column else str(self.line)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "verdict": self.verdict,
            "detail": self.detail,
            "line": self.line,
            "column": self.column,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class CompileReport:
    """What a physical Lean toolchain said, or why it could not say anything.

    ``status`` is ``PASS`` / ``FAIL`` / ``UNAVAILABLE``. ``UNAVAILABLE`` is a
    first-class answer rather than a failure: a machine without Lean has not
    discovered a bad proof, and reporting one would be inventing evidence.
    """

    status: str
    available: bool
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    axioms: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    warnings: Tuple[str, ...] = ()
    reason: str = ""
    duration_ms: float = 0.0
    command: Tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        return self.status != LEAN_INVALID

    def axiom_map(self) -> Dict[str, Tuple[str, ...]]:
        return {name: axioms for name, axioms in self.axioms}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "available": self.available,
            "exit_code": self.exit_code,
            "measured": self.measured,
            "axioms": {name: list(axioms) for name, axioms in self.axioms},
            "warnings": list(self.warnings),
            "reason": self.reason,
            "duration_ms": round(self.duration_ms, 1),
            "command": list(self.command),
        }


@dataclass(frozen=True)
class LeanAudit:
    """The result of one audit: a verdict, the evidence, and whether it was kept.

    ``logged`` is not decoration. An audit whose record could not be written is
    an audit nobody can prove happened, so the failure is reported as a field
    rather than raised: the finding still stands, and the hole is visible.
    """

    claim: str
    verdict: str
    findings: Tuple[LeanFinding, ...]
    compile: CompileReport
    digest: str
    theorems: Tuple[str, ...] = ()
    logged: bool = False
    record: Optional[Dict[str, Any]] = None
    log_error: Optional[str] = None
    log_note: Optional[str] = None
    penalty_charged: Optional[float] = None

    # -- convenience ------------------------------------------------------

    @property
    def passed(self) -> bool:
        return self.verdict == LEAN_PASS

    @property
    def placeholders(self) -> Tuple[LeanFinding, ...]:
        return tuple(f for f in self.findings if f.kind == PLACEHOLDER)

    @property
    def axioms(self) -> Tuple[LeanFinding, ...]:
        return tuple(f for f in self.findings if f.kind == CUSTOM_AXIOM)

    @property
    def reason(self) -> str:
        """One line naming the worst thing found, or that nothing was found."""
        judgments = [f for f in self.findings if f.verdict is not None]
        if not judgments:
            return "no placeholder, smuggled axiom or compiler objection found"
        worst = max(judgments, key=lambda f: LEAN_SEVERITY[f.verdict])
        return worst.detail

    def as_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "digest": self.digest,
            "theorems": list(self.theorems),
            "findings": [finding.as_dict() for finding in self.findings],
            "compile": self.compile.as_dict(),
            "logged": self.logged,
        }

    # -- human-readable ---------------------------------------------------

    def report(self) -> str:
        """A plain-text report. No colour, no box drawing: it must survive a log."""
        lines: List[str] = []
        width = 78
        lines.append("=" * width)
        lines.append("LEAN PROOF AUDIT")
        lines.append("=" * width)
        lines.append("  claim     : {}".format(self.claim or "(none stated)"))
        lines.append("  digest    : {}".format(self.digest))
        lines.append("  theorems  : {}".format(", ".join(self.theorems) or "(none found)"))
        lines.append("  verdict   : {}".format(self.verdict))
        lines.append("  reason    : {}".format(self.reason))
        lines.append("")

        lines.append("  witnesses")
        lines.append("  " + "-" * (width - 2))
        lexical = [f for f in self.findings if f.kind != COMPILER]
        if lexical:
            for finding in lexical:
                lines.append(
                    "  {} {:<13} line {:<5} {}".format(
                        _tag(finding.verdict or "note"),
                        finding.kind,
                        finding.position(),
                        finding.detail,
                    )
                )
                if finding.snippet:
                    lines.append("        | {}".format(finding.snippet))
        else:
            lines.append(
                "  {} {:<13} no placeholder and no axiom declaration".format(
                    _tag(LEAN_PASS), "LEXICAL"
                )
            )

        compile_line = self._compile_line()
        lines.append("  {}".format(compile_line))
        # Only trustworthy after a clean elaboration: on failure Lean substitutes
        # `sorry` for the broken declaration, so this output would report a
        # placeholder that is really just an error.
        if self.compile.axioms and self.compile.status == LEAN_PASS:
            for name, axioms in self.compile.axioms:
                if axioms:
                    lines.append("        '{}' depends on: {}".format(name, ", ".join(axioms)))
                else:
                    lines.append("        '{}' depends on no axioms".format(name))
        if self.compile.warnings:
            for warning in self.compile.warnings[:3]:
                lines.append("        warning: {}".format(warning))
        if self.compile.status == LEAN_PASS and self.verdict != LEAN_PASS:
            lines.append("")
            lines.append(
                "  NOTE: Lean accepted this file (exit 0) and the verdict is still"
            )
            lines.append(
                "        {}. `sorry` and `axiom` both elaborate cleanly, so an exit"
                .format(self.verdict)
            )
            lines.append(
                "        code of 0 is not evidence that a proof is complete."
            )
        lines.append("")

        lines.append("  trail")
        lines.append("  " + "-" * (width - 2))
        if self.logged:
            seq = (self.record or {}).get("seq")
            lines.append("  appended to the hash-chained ledger at seq {}".format(seq))
        else:
            lines.append("  NOT logged: {}".format(self.log_error or "no membrane attached"))
        if self.log_note:
            lines.append("  note: {}".format(self.log_note))
        if self.penalty_charged:
            lines.append("  penalty charged: {:.1f}".format(self.penalty_charged))
        lines.append("=" * width)
        return "\n".join(lines)

    def _compile_line(self) -> str:
        compile_ = self.compile
        if not compile_.measured:
            return "{} {:<13} {}".format(
                _tag("UNAVAILABLE"), "COMPILER", compile_.reason
            )
        return "{} {:<13} exit {} in {:.0f}ms".format(
            _tag(compile_.status),
            "COMPILER",
            compile_.exit_code,
            compile_.duration_ms,
        )


# ---------------------------------------------------------------------------
# the physical witness
# ---------------------------------------------------------------------------


class UnavailableCompiler:
    """The compiler witness when there is no compiler.

    Deliberately not a mock that returns ``PASS``. A simulated pass would be
    fabricated evidence -- the precise offence this auditor exists to detect --
    so the honest simulation is to report that the witness was not collected.
    """

    def __init__(self, reason: str = "no Lean toolchain available") -> None:
        self.reason = reason

    def check(self, source: str, *, theorems: Sequence[str] = ()) -> CompileReport:
        return CompileReport(
            status=LEAN_INVALID,
            available=False,
            reason="{}; the physical witness was not collected, which is not the "
                   "same as the file compiling".format(self.reason),
        )


class LeanToolchain:
    """Elaborate the file with a real Lean 4 binary and read the exit code.

    Also appends ``#print axioms <name>`` for each known declaration, because
    that output is a stronger witness than any text scan: it names the axioms a
    theorem actually depends on, and ``sorry`` appears there as ``sorryAx``.

    Every failure mode is converted into a report rather than an exception. An
    auditor that dies on a malformed proof reports *nothing*, which an attacker
    would find convenient.
    """

    def __init__(
        self,
        binary: Optional[str] = None,
        *,
        timeout: float = 60.0,
        max_output: int = 8000,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive, got {!r}".format(timeout))
        self._binary = binary
        self._timeout = timeout
        self._max_output = max_output

    @property
    def binary(self) -> Optional[str]:
        return self._binary

    def resolved_binary(self) -> Optional[str]:
        """Where the toolchain is, or ``None``. Resolved per call, not cached."""
        if self._binary:
            return self._binary if os.path.exists(self._binary) else None
        return shutil.which("lean")

    def check(self, source: str, *, theorems: Sequence[str] = ()) -> CompileReport:
        binary = self.resolved_binary()
        if binary is None:
            return UnavailableCompiler(
                "no `lean` executable found (looked for {!r} and on PATH)".format(
                    self._binary
                )
            ).check(source, theorems=theorems)

        probe = _with_axiom_probes(source, theorems)
        with tempfile.TemporaryDirectory(prefix="lean-audit-") as directory:
            target = os.path.join(directory, "Submission.lean")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(probe)
            command = (binary, target)
            started = _now()
            try:
                completed = subprocess.run(
                    list(command),
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired:
                return CompileReport(
                    status=LEAN_INVALID,
                    available=True,
                    reason="Lean did not finish within {:.0f}s; a timeout is not a "
                           "verdict".format(self._timeout),
                    command=command,
                )
            except OSError as problem:
                return CompileReport(
                    status=LEAN_INVALID,
                    available=True,
                    reason="the Lean binary could not be run: {}".format(problem),
                    command=command,
                )
            duration = (_now() - started) * 1000.0

        output = (completed.stdout or "") + (completed.stderr or "")
        # The scratch directory is an implementation detail on this machine, not
        # part of the evidence. Strip it so a permanent ledger entry names the
        # submission rather than a path that no longer exists.
        output = output.replace(directory + os.sep, "")
        output = output[: self._max_output]
        axioms = _parse_axioms(output)
        warnings = tuple(
            line.strip() for line in output.splitlines() if ": warning:" in line
        )

        if completed.returncode != 0:
            environmental = _looks_environmental(output)
            return CompileReport(
                status=LEAN_INVALID if environmental else LEAN_FAIL,
                available=True,
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                axioms=axioms,
                warnings=warnings,
                reason=(
                    "the toolchain could not resolve the file's dependencies "
                    "({}), so this run cannot judge the proof".format(
                        _first_error_line(output)
                    )
                    if environmental
                    else "Lean rejected the file: {}".format(_first_error_line(output))
                ),
                duration_ms=duration,
                command=command,
            )

        return CompileReport(
            status=LEAN_PASS,
            available=True,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            axioms=axioms,
            warnings=warnings,
            reason="Lean accepted the file with exit code 0",
            duration_ms=duration,
            command=command,
        )


def _with_axiom_probes(source: str, theorems: Sequence[str]) -> str:
    """Append ``#print axioms`` for each declaration, if any were found."""
    if not theorems:
        return source
    separator = "" if source.endswith("\n") else "\n"
    probes = "".join(
        "\n#print axioms {}\n".format(name) for name in theorems if name
    )
    return source + separator + probes


def _parse_axioms(output: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Read ``'name' depends on axioms: [..]`` pairs out of Lean's stdout."""
    found: List[Tuple[str, Tuple[str, ...]]] = []
    for line in output.splitlines():
        stripped = line.strip()
        match = _AXIOM_LINE_RE.match(stripped)
        if match:
            names = tuple(
                part.strip() for part in match.group(2).split(",") if part.strip()
            )
            found.append((match.group(1), names))
            continue
        clean = _CLEAN_LINE_RE.match(stripped)
        if clean:
            found.append((clean.group(1), ()))
    return tuple(found)


def _looks_environmental(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _ENVIRONMENT_FAILURES)


def _first_error_line(output: str) -> str:
    """The first line mentioning an error, else the first non-empty line."""
    for line in output.splitlines():
        if "error" in line.lower():
            return line.strip()[:_SNIPPET_LIMIT]
    for line in output.splitlines():
        if line.strip():
            return line.strip()[:_SNIPPET_LIMIT]
    return "no output"


def _now() -> float:
    """Monotonic seconds, for a duration that must not jump with the wall clock."""
    return time.monotonic()


# ---------------------------------------------------------------------------
# the auditor
# ---------------------------------------------------------------------------


class LeanProofAuditor:
    """Audit a Lean 4 file for the two ways a machine fakes a proof.

    Args:
        membrane: anything exposing ``append_call(tool, verdict, **fields)``. In
            practice ``audit_sidecar.tools.membrane.MembraneGateway``. Duck-typed
            so this package keeps ``dependencies = []``. When ``None`` the audit
            still runs and reports ``logged=False`` -- a documented hole, not a
            silent default.
        compiler: a witness with ``check(source, theorems=...)``. Defaults to
            :class:`LeanToolchain`, which looks for ``lean`` on PATH and reports
            ``UNAVAILABLE`` when there is none.
        agent: the name recorded in the ledger for the submitting party.
        charge_penalty: also record a ``FALSE_COMPLETION`` against an attached
            penalty ledger on FAIL. Off by default, because charging a fine is
            the caller's policy, not something an auditor should assume.
        penalty_ledger: anything with ``record_violation(type)``.

    Example::

        >>> auditor = LeanProofAuditor()          # membrane-less scan
        >>> result = auditor.audit("theorem t : True := by sorry")
        >>> result.verdict
        'FAIL'
    """

    #: Tool name recorded in the ledger.
    TOOL = "lean.audit"

    def __init__(
        self,
        membrane: Any = None,
        *,
        compiler: Any = None,
        agent: str = "lean-agent",
        charge_penalty: bool = False,
        penalty_ledger: Any = None,
        observer: Any = None,
    ) -> None:
        if membrane is not None:
            appender = getattr(membrane, "append_call", None)
            if not callable(appender):
                raise TypeError(
                    "membrane must expose append_call(), got {!r}".format(
                        type(membrane).__name__
                    )
                )
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent must be a non-empty str")
        if observer is not None and not callable(observer):
            raise TypeError(
                "observer must be callable(audit), got {!r}".format(
                    type(observer).__name__
                )
            )

        self._membrane = membrane
        self._compiler = LeanToolchain() if compiler is None else compiler
        if not callable(getattr(self._compiler, "check", None)):
            raise TypeError(
                "compiler must expose check(source, theorems=...), got {!r}".format(
                    type(self._compiler).__name__
                )
            )
        self._agent = agent
        self._charge = bool(charge_penalty)
        self._penalty = penalty_ledger
        self._observer = observer
        self._audits: List[LeanAudit] = []

    # -- read-only view ---------------------------------------------------

    @property
    def agent(self) -> str:
        return self._agent

    @property
    def compiler(self) -> Any:
        return self._compiler

    @property
    def history(self) -> Tuple[LeanAudit, ...]:
        return tuple(self._audits)

    # -- the audit --------------------------------------------------------

    def audit(
        self,
        source: str,
        *,
        claim: str = "",
        theorem_names: Optional[Sequence[str]] = None,
    ) -> LeanAudit:
        """Judge one submission. Never raises because the proof is bad.

        Args:
            source: the Lean 4 text, as the agent submitted it.
            claim: what the agent said about it, recorded for the report.
            theorem_names: which declarations to ask ``#print axioms`` about.
                When omitted, ``theorem`` / ``lemma`` declarations are found by
                scanning -- the names still come from the file, so a submission
                cannot nominate which theorem gets checked.

        Raises:
            TypeError: ``source`` is not text.
            ValueError: ``source`` is empty, which proves nothing.
        """
        if not isinstance(source, str):
            raise TypeError(
                "source must be a str, got {!r}".format(type(source).__name__)
            )
        if not source.strip():
            raise LeanAuditorError(
                "an empty submission proves nothing; refusing to score it PASS by "
                "default"
            )

        masked = mask_source(source)
        findings: List[LeanFinding] = []
        findings.extend(self._lexical(source, masked))
        theorems = tuple(theorem_names) if theorem_names is not None else _declarations(masked)
        compile_ = self._compile(source, theorems)
        findings.extend(self._from_compiler(compile_))

        verdict = _worst(findings)
        digest = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
        result = LeanAudit(
            claim=claim.strip(),
            verdict=verdict,
            findings=tuple(findings),
            compile=compile_,
            digest=digest,
            theorems=theorems,
        )
        result = self._log(result, source)
        result = self._observe(result)
        self._audits.append(result)
        return result

    # -- witnesses --------------------------------------------------------

    def _lexical(self, source: str, masked: str) -> List[LeanFinding]:
        """Scan the masked text, which is code rather than prose."""
        findings: List[LeanFinding] = []
        for match in _PLACEHOLDER_RE.finditer(masked):
            word = match.group(1)
            findings.append(
                _finding(
                    source,
                    match.start(),
                    kind=PLACEHOLDER,
                    verdict=LEAN_FAIL,
                    detail=(
                        "the agent claims the proof is complete, but the code "
                        "contains the unproved placeholder `{}`".format(word)
                    ),
                )
            )
        for match in _AXIOM_RE.finditer(masked):
            findings.append(
                _finding(
                    source,
                    match.start(),
                    kind=CUSTOM_AXIOM,
                    verdict=LEAN_FAIL,
                    detail=(
                        "`axiom` introduces an assumption the author chose to be "
                        "true, which makes the surrounding theorem prove nothing "
                        "about the mathematics"
                    ),
                )
            )
        for match in _OPAQUE_RE.finditer(masked):
            if _opaque_has_body(masked, match.start()):
                continue
            findings.append(
                _finding(
                    source,
                    match.start(),
                    kind=HIDDEN_BODY,
                    verdict=None,  # advisory: this is not a cheat
                    detail=(
                        "`opaque` without a value hides the term behind the name. "
                        "It is not an axiom -- Lean requires the type to be "
                        "inhabited, so it cannot produce a false proposition -- "
                        "but nothing can be learned about the value either"
                    ),
                )
            )
        return findings

    def _compile(self, source: str, theorems: Sequence[str]) -> CompileReport:
        try:
            report = self._compiler.check(source, theorems=theorems)
        except Exception as problem:  # a broken witness is not a verdict
            return CompileReport(
                status=LEAN_INVALID,
                available=False,
                reason="the compiler witness failed to run: {}".format(problem),
            )
        if not isinstance(report, CompileReport):
            raise TypeError(
                "compiler.check must return a CompileReport, got {!r}".format(
                    type(report).__name__
                )
            )
        return report

    def _from_compiler(self, report: CompileReport) -> List[LeanFinding]:
        """Turn the physical witness into findings -- and only into findings.

        Three things this deliberately does *not* do:

        * It does not emit PASS as a finding. A pass is the absence of an
          objection, and manufacturing a positive entry would let it look like
          evidence against a FAIL found lexically.
        * It does not trust ``returncode == 0`` as a proof of completeness. Lean
          accepts ``sorry`` and ``axiom`` with exit 0; that measurement is in the
          module docstring.
        * It does not treat an unavailable toolchain as a healthy one. A clean
          scan with no compiler reaches INVALID, not PASS: a regex cannot tell a
          proof from a file with a syntax error, so PASS requires that something
          actually elaborated. Without a toolchain this auditor can only report
          "no hole found in the text", which is a different and weaker claim.
        """
        findings: List[LeanFinding] = []
        if not report.measured:
            # No physical witness means no verification, whatever the text looks
            # like. A regex cannot tell a proof from a file with a syntax error,
            # so letting a clean scan reach PASS would grade a non-compiling
            # stub as a theorem. INVALID is the lattice's name for exactly this:
            # a statement about the audit, not about the proof.
            return [
                LeanFinding(
                    kind=COMPILER,
                    verdict=LEAN_INVALID,
                    detail="no physical witness: {}".format(report.reason),
                )
            ]

        if report.status == LEAN_FAIL:
            findings.append(
                LeanFinding(
                    kind=COMPILER,
                    verdict=LEAN_FAIL,
                    detail="the file does not elaborate: {}".format(report.reason),
                )
            )
            # Stop here. When elaboration fails, Lean replaces the failing
            # declaration's body with `sorry`, so the axiom report then describes
            # the error recovery rather than the proof. Measured: a file with an
            # unknown identifier prints "'t' depends on axioms: [sorryAx]".
            # Reading that as a placeholder would invent a finding, so the
            # compiler's own FAIL is the whole story for this submission.
            return findings

        # ``#print axioms`` is the strongest evidence available: it names the
        # dependencies of the theorem itself, whatever the text looks like.
        for name, axioms in report.axioms:
            smuggled = tuple(
                axiom for axiom in axioms if not _is_standard(axiom)
            )
            for axiom in smuggled:
                if _is_sorry(axiom):
                    findings.append(
                        LeanFinding(
                            kind=PLACEHOLDER,
                            verdict=LEAN_FAIL,
                            detail=(
                                "`#print axioms {}` reports a dependency on `{}`: the "
                                "proof is closed by the placeholder axiom, so it is "
                                "not a proof".format(name, axiom)
                            ),
                        )
                    )
                else:
                    findings.append(
                        LeanFinding(
                            kind=CUSTOM_AXIOM,
                            verdict=LEAN_FAIL,
                            detail=(
                                "`#print axioms {}` reports a dependency on the "
                                "non-standard axiom `{}`".format(name, axiom)
                            ),
                        )
                    )
        return findings

    # -- the trail --------------------------------------------------------

    def _log(self, result: LeanAudit, source: str) -> LeanAudit:
        """Append the verdict to the membrane's chain, or say that it was not."""
        charged: Optional[float] = None
        if self._charge and result.verdict == LEAN_FAIL and self._penalty is not None:
            try:
                violation = self._penalty.record_violation("FALSE_COMPLETION")
                charged = violation.penalty
            except Exception:  # a broken ledger must not erase the verdict
                charged = None

        if self._membrane is None:
            return _replace(
                result,
                logged=False,
                log_error="no membrane attached: this verification is unrecorded",
                penalty_charged=charged,
            )

        payload: Dict[str, Any] = {
            "agent": self._agent,
            "claim": result.claim,
            "theorems": list(result.theorems),
            "findings": [finding.as_dict() for finding in result.findings],
            "finding_count": len(result.findings),
            "compiler_status": result.compile.status,
            "compiler_measured": result.compile.measured,
            "compiler_exit_code": result.compile.exit_code,
            "compiler_axioms": {
                name: list(axioms) for name, axioms in result.compile.axioms
            },
            "reason": result.reason,
            "source_bytes": len(source.encode("utf-8")),
        }
        if charged is not None:
            payload["penalty_charged"] = charged

        # A verdict about work that was actually witnessed, so this is a call
        # record -- not a denial. The distinction F1 established: a deny means
        # "the gate held before anything ran", and here the analysis did run.
        #
        # Three attempts, widest first. A membrane with a narrower signature is
        # still a valid membrane, and an extra field must never be the reason a
        # verdict goes unrecorded: degrading to `append_call(tool, verdict)` is
        # worse evidence than the full payload, but it is evidence, and the
        # degradation is named in the report instead of being silent.
        attempts = (
            ("full", lambda: self._membrane.append_call(
                self.TOOL, result.verdict, digest=result.digest, **payload)),
            ("extra", lambda: self._membrane.append_call(
                self.TOOL, result.verdict, extra=payload)),
            ("minimal", lambda: self._membrane.append_call(
                self.TOOL, result.verdict)),
        )
        last_error: Optional[Exception] = None
        for mode, attempt in attempts:
            try:
                record = attempt()
            except Exception as problem:  # try the next, narrower contract
                last_error = problem
                continue
            return _replace(
                result,
                logged=True,
                record=record if isinstance(record, Mapping) else None,
                log_error=None,
                log_note=(
                    "the membrane accepted only the verdict; this ledger's signature "
                    "dropped the evidence payload".format()
                    if mode == "minimal" else None
                ),
                penalty_charged=charged,
            )

        return _replace(
            result,
            logged=False,
            log_error="the record could not be written: {}".format(last_error),
            penalty_charged=charged,
        )

    def _observe(self, result: LeanAudit) -> LeanAudit:
        """Hand the result to the caller's hook, if one is attached.

        This is how the immune layer learns about a verdict. It cannot be done
        from here: ``MembraneGateway`` exposes its ledger "for verification
        only" and is explicitly *not* a route to the immune state, because a
        record is not a sensor. So the bridge is an explicit callback rather
        than a guess about the membrane's internals -- and the callback is the
        caller's, which keeps this package free of the auditor's enum.

        A failing hook must not erase the verdict: the finding stands, and the
        failure is attached as a note.
        """
        if self._observer is None:
            return result
        try:
            self._observer(result)
        except Exception as problem:
            note = "observer failed: {}".format(problem)
            merged = "{}; {}".format(result.log_note, note) if result.log_note else note
            return _replace(result, log_note=merged)
        return result

    def __repr__(self) -> str:
        return "LeanProofAuditor(agent={!r}, compiler={}, membrane={})".format(
            self._agent,
            type(self._compiler).__name__,
            "attached" if self._membrane is not None else "absent",
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tag(status: str) -> str:
    """``[PASS]`` padded to a fixed column so the report lines up."""
    return "[{}]".format(status).ljust(13)


def _opaque_has_body(masked: str, start: int) -> bool:
    """True when the ``opaque`` at ``start`` supplies a value with ``:=``.

    The declaration is bounded by the next top-level command so a ``:=`` in a
    later definition cannot be mistaken for this one's body. Heuristic, and the
    failing direction is a false positive only for a type that itself contains
    ``:=`` (a default binder argument), which the report then shows in full for
    a human to dismiss.
    """
    line_end = masked.find("\n", start)
    if line_end == -1:
        line_end = len(masked)
    following = _DECL_START_RE.search(masked, line_end)
    end = following.start() if following else len(masked)
    return ":=" in masked[start:end]


def _declarations(masked: str) -> Tuple[str, ...]:
    """Every ``theorem`` / ``lemma`` name the file declares."""
    names: List[str] = []
    for match in _THEOREM_RE.finditer(masked):
        name = match.group(1)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _is_standard(axiom: str) -> bool:
    """True for the three axioms Lean itself rests on."""
    return axiom in STANDARD_AXIOMS or axiom.rsplit(".", 1)[-1] in STANDARD_AXIOMS


def _is_sorry(axiom: str) -> bool:
    return axiom.rsplit(".", 1)[-1] == SORRY_AXIOM


def _worst(findings: Sequence[LeanFinding]) -> str:
    """Compose one verdict by worst-wins; PASS when nothing objected."""
    verdict = LEAN_PASS
    for finding in findings:
        if finding.verdict is None:
            continue
        if LEAN_SEVERITY[finding.verdict] > LEAN_SEVERITY[verdict]:
            verdict = finding.verdict
    return verdict


def _finding(source: str, offset: int, *, kind: str, verdict: str, detail: str) -> LeanFinding:
    """Build a finding with the line, column and source line attached."""
    line = source.count("\n", 0, offset) + 1
    column = offset - source.rfind("\n", 0, offset)
    snippet = _line_at(source, line)
    return LeanFinding(
        kind=kind,
        verdict=verdict,
        detail=detail,
        line=line,
        column=column,
        snippet=snippet,
    )


def _line_at(source: str, line: int) -> str:
    lines = source.splitlines()
    if not 1 <= line <= len(lines):
        return ""
    text = lines[line - 1].strip()
    return text if len(text) <= _SNIPPET_LIMIT else text[:_SNIPPET_LIMIT - 3] + "..."


def _replace(result: LeanAudit, **changes: Any) -> LeanAudit:
    """A copy with fields replaced, keeping the frozen record honest."""
    return replace(result, **changes)
