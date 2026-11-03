#!/usr/bin/env python3
"""Demo: audit a claim that crosses the micro/macro boundary.

The claim: a protein scaffold wrapped around a chromophore makes a room-
temperature quantum battery. The question is not whether quantum biology is
respectable -- it is whether *this filing* supplies anything that could be
measured and found wrong.

The demo walks four filings through the same boundary, because the interesting
result is the three-way split and not any single verdict:

    no numbers at all          -> INVALID   nothing to examine
    numbers that fall short    -> FAIL      refuted by its own filing
    right physics, wrong layer -> FAIL      cooling to 4 K abandons the target
    a complete witness         -> PASS      falsifiable -- and still not "true"

The fourth one is invented for this demo. It is labelled as such everywhere it
appears, and the demo asserts that its PASS carries the scope sentence, because a
demo that ends with "and therefore the battery works" would be doing the exact
thing this module exists to catch.

Exit code 0 only if every self-check passes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_metabolism.boundary_auditor import (  # noqa: E402
    BoundaryAuditor,
    Invention,
)
from agent_metabolism.double_entry import FAIL, INVALID, PASS  # noqa: E402

WIDTH = 78


def _section(title: str) -> None:
    print()
    print("#" * WIDTH)
    print("# " + title)
    print("#" * WIDTH)


def _rule() -> None:
    print("  " + "-" * (WIDTH - 2))


class _RecordingMembrane:
    """A duck-typed membrane: enough to prove the auditor logs, nothing more.

    It accepts only the shape this auditor sends first, so the widening-signature
    fallback is exercised by the tests rather than relied on here.
    """

    def __init__(self) -> None:
        self.calls = []

    def append_call(self, tool, verdict, **fields):
        self.calls.append({"tool": tool, "verdict": verdict, **fields})
        return len(self.calls)


# ---------------------------------------------------------------------------
# the filings
# ---------------------------------------------------------------------------

#: As filed: prose, a mechanism named at the level of a field, and no number.
FILED = Invention(
    identifier="Patent_BioQuantum",
    title="A bio-quantum battery using protein-encapsulated chromophores",
    claim=(
        "A special protein scaffold encapsulates a chromophore and exploits "
        "quantum coherence to transport energy at room temperature, yielding a "
        "bio-compatible battery with near-lossless transfer."
    ),
    witness={"mechanism": "quantum_effect_generic"},
    targets=("Theory_Quantum", "Theory_Bio"),
)

#: Numbers present, and they say the gap is not closed. The filing refutes itself.
SHORT = Invention(
    identifier="Patent_BioQuantum_Short",
    title="Same claim, with numbers that fall short",
    claim=(
        "A scaffold suppresses decoherence by a factor of 1e3, extending the "
        "coherence lifetime to a nanosecond, which we describe as lossless."
    ),
    witness={
        "mechanism": "protein_scaffold_shielding",
        "structural_parameters": {
            "chromophore_separation_A": 9.0,
            "scaffold_barrier_height_meV": 60.0,
            "hydration_shell_thickness_A": 2.0,
            "debye_temperature_K": 120.0,
        },
        # 1e-12 s x 1e3 = 1e-9 s, so the arithmetic is internally consistent --
        # and still nine decades short of the 1 s the other layer needs.
        "suppression_factor": 1e3,
        "coherence_time_s": 1e-9,
        "operating_temperature_k": 300.0,
        "medium": "aqueous",
        "single_shot": True,
    },
    targets=("Theory_Quantum", "Theory_Bio"),
)

#: The case worth taking seriously. The coherence problem is solved outright --
#: by cryogenics, which is not where biology lives. Physically plausible, still
#: not a crossing, and the reason the boundary is four constraints and not one.
CRYOGENIC = Invention(
    identifier="Patent_BioQuantum_Cryo",
    title="Coherence solved by cooling, which leaves the layer it was aimed at",
    claim=(
        "A chromophore assembly reaches a ten-second coherence time in a "
        "cryogenic matrix, and is described as bio-compatible."
    ),
    witness={
        "mechanism": "cavity_polariton",
        "structural_parameters": {
            "cavity_length_nm": 240.0,
            "mirror_reflectivity": 0.9999,
            "chromophore_count": 12.0,
            "detuning_meV": 18.0,
        },
        "suppression_factor": 1e13,
        "coherence_time_s": 10.0,
        "operating_temperature_k": 4.0,
        "medium": "aqueous",
        "single_shot": True,
    },
    targets=("Theory_Quantum", "Theory_Bio"),
)

#: HYPOTHETICAL. Invented to exercise the PASS path. The numbers are plausible
#: shapes, not measurements, and PASS means only that the arithmetic closes.
HYPOTHETICAL = Invention(
    identifier="Patent_BioQuantum_Hypothetical",
    title="HYPOTHETICAL -- a complete witness, invented to show the pass path",
    claim=(
        "A rigid protein scaffold at 12.4 A chromophore separation with a 210 meV "
        "barrier detunes the bath modes enough to hold electronic coherence for "
        "one second at 300 K in water, one molecule at a time."
    ),
    witness={
        "mechanism": "protein_scaffold_shielding",
        "structural_parameters": {
            "chromophore_separation_A": 12.4,
            "scaffold_barrier_height_meV": 210.0,
            "vibrational_detuning_cm-1": 340.0,
            "hydration_shell_thickness_A": 3.8,
            "debye_temperature_K": 220.0,
        },
        "suppression_factor": 1e12,
        "coherence_time_s": 1.0,
        "operating_temperature_k": 300.0,
        "medium": "aqueous",
        "single_shot": True,
    },
    targets=("Theory_Quantum", "Theory_Bio"),
    notes=(
        "Illustrative only. Its PASS records that the filing is falsifiable and "
        "arithmetically sufficient; nothing in this demo tests the scaffold."
    ),
)


def _show(result) -> None:
    print(BoundaryAuditor.render_audit(result))
    print()


def main() -> int:
    membrane = _RecordingMembrane()
    auditor = BoundaryAuditor(membrane=membrane)

    _section("PHASE 1  The boundary is not an API, it is a gap")
    boundary = auditor.boundary("Theory_Quantum", "Theory_Bio")
    print("  Two layers, each internally consistent:")
    print()
    for layer in (boundary.layer_a, boundary.layer_b):
        print("    {}".format(layer.name))
        for chunk in _wrap(layer.description, WIDTH - 8):
            print("      " + chunk)
        print()
    print("  Between them: a requirement conflict, not a disagreement about facts.")
    print("  One side provides {:.3g} s of coherence; the other needs {:.3g} s.".format(
        boundary.intrinsic_coherence_s, boundary.required_coherence_s))
    print("  That is a factor of {:.0e} -- {:.0f} orders of magnitude.".format(
        boundary.gap_ratio, boundary.decades))
    print()
    print("  Nobody designed that gap, and no argument widens it. An invention")
    print("  either supplies {:.1f} decades of protection or it does not.".format(
        boundary.decades))

    _section("PHASE 2  Audit the filed patent: prose without a witness")
    print("  The filing names the right mechanism family -- a protein scaffold")
    print("  around a chromophore is exactly how this boundary would be attacked.")
    print("  It then supplies no structure, no factor, no temperature, no medium.")
    print()
    filed = auditor.audit_boundary_crossing("Theory_Quantum", "Theory_Bio", FILED)
    _show(filed)
    print("  Read the verdict as written: INVALID is not 'false'. Nobody looked,")
    print("  because the filing gives nobody anything to look at. Calling that a")
    print("  refutation would be the over-accusation this system is built to avoid.")

    _section("PHASE 3  Numbers present but short: the filing refutes itself")
    print("  Same scaffold, now with measurements -- and they land nine decades")
    print("  short. This is FAIL, not INVALID: something was examined, and the")
    print("  claim did not survive its own arithmetic.")
    print()
    short = auditor.audit_boundary_crossing("Theory_Quantum", "Theory_Bio", SHORT)
    _show(short)

    _section("PHASE 4  Right physics, wrong layer: the cryogenic trap")
    print("  This filing actually solves the coherence problem. Ten seconds, a")
    print("  factor of 1e13, internally consistent. It does it at 4 K.")
    print()
    print("  That is the failure mode worth understanding: the inventor wins the")
    print("  argument they were having and abandons the layer they were addressing.")
    print("  Water is ice, proteins do not fold, and the organism -- the thing that")
    print("  needed the battery -- is gone. Stronger coherence is not a crossing.")
    print()
    cryo = auditor.audit_boundary_crossing("Theory_Quantum", "Theory_Bio", CRYOGENIC)
    _show(cryo)

    _section("PHASE 5  HYPOTHETICAL: a complete witness")
    print("  Invented for this demo, and marked as such in its own title. It is")
    print("  here to show that the gate can open, since a gate that never opens is")
    print("  not a gate.")
    print()
    hypo = auditor.audit_boundary_crossing("Theory_Quantum", "Theory_Bio", HYPOTHETICAL)
    _show(hypo)
    print("  PASS here means the claim is falsifiable and the arithmetic closes.")
    print("  It does not mean the scaffold exists. The two sentences have to stay")
    print("  together, or this auditor becomes a machine for laundering invention")
    print("  into evidence -- which is the failure it was written to find.")

    _section("PHASE 6  The blind-boundary map")
    print("  Four axes, none of them addressed by the registry as loaded:")
    print()
    blind_before = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
    print(BoundaryAuditor.render_blind(blind_before, "Theory_Quantum", "Theory_Bio"))
    print()
    print("  Now register one filing that claims all four axes, and watch the map")
    print("  change. The map follows the registry, not reality -- which is exactly")
    print("  what a map should do, as long as it says so.")
    print()
    auditor.add_invention(HYPOTHETICAL)
    blind_after = auditor.find_blind_boundaries("Theory_Quantum", "Theory_Bio")
    for row in blind_after:
        print("    {:<24} {}".format(row["name"], row["status"].upper()))
    print()
    print("  Four crossed by one hypothetical filing. If the filing were withdrawn")
    print("  the axes would go blind again. A blind row is an invitation to look,")
    print("  never a claim that no one has.")

    _section("PHASE 7  What was recorded")
    print("  audits appended to the membrane: {}".format(len(membrane.calls)))
    for call in membrane.calls:
        print("    {:<10} {}".format(call["verdict"], call.get("intent", "")[:60]))
    print()
    print("  The witness digest pins the exact filing that was judged:")
    print("    {}".format(filed["witness_digest"]))
    print("  Re-digesting the same witness reproduces it; an edited filing does not.")

    _section("SUMMARY")
    checks = {
        "gap measured in decades": abs(boundary.decades - 12.0) < 0.01,
        "prose alone is INVALID": filed["verdict"] == INVALID,
        "and it is not called a refutation": not filed["closes_gap"],
        "the refusal names the missing structure": any(
            f["kind"] == "NO_STRUCTURE" for f in filed["faults"]
        ),
        "the refusal names the fake mechanism": any(
            f["kind"] == "NO_MECHANISM" and f["decisive"] for f in filed["faults"]
        ),
        "short numbers are FAIL, not INVALID": short["verdict"] == FAIL,
        "the shortfall is quantified": any(
            f["kind"] == "INSUFFICIENT_SUPPRESSION" for f in short["faults"]
        ),
        "cryogenic work is FAIL, not PASS": cryo["verdict"] == FAIL,
        "and the reason is the temperature": any(
            f["kind"] == "WRONG_TEMPERATURE" for f in cryo["faults"]
        ),
        "a complete witness passes": hypo["verdict"] == PASS
        and hypo["closes_gap"],
        "the pass carries its scope sentence": "never establishes that the "
        "mechanism works" in hypo["establishes"],
        "the hypothetical is labelled": "HYPOTHETICAL" in hypo["claim"]
        or "HYPOTHETICAL" in HYPOTHETICAL.title,
        "four axes were blind": all(r["status"] == "blind" for r in blind_before),
        "one filing crosses four axes": all(
            r["status"] == "crossed" for r in blind_after
        ),
        "registry scope is stated": "not a census of the field"
        in " ".join(
            BoundaryAuditor.render_blind(
                blind_before, "Theory_Quantum", "Theory_Bio"
            ).split()
        ),
        "every audit was logged": len(membrane.calls) == 4,
        "verdicts logged in order": [c["verdict"] for c in membrane.calls]
        == [INVALID, FAIL, FAIL, PASS],
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


def _wrap(text: str, width: int):
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


if __name__ == "__main__":
    sys.exit(main())
