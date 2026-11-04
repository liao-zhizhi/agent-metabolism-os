"""Tests for the self-audit: the paper graded by its own lattice.

Two kinds of test live here, and the difference matters.

The first kind checks the *auditor*: that the verdict vocabulary is exactly the
package's four values plus the escalation, that every anchor still matches exactly
once, that a drifted anchor escalates rather than passing quietly, and that the
generated artifacts agree with a fresh run. These are ordinary unit tests.

The second kind is the point of the exercise: the audit must be able to *fail*.
A checker that has never been shown failing is indistinguishable from a checker
that always passes, so several tests below inject a broken manuscript and a broken
disclosure and assert that the verdict changes. If those tests are removed the
audit still runs and still prints PASS, which is exactly the failure mode this
suite exists to prevent.

Run:  python3 -m unittest tests.test_paper_self_audit
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments import paper_self_audit as audit  # noqa: E402


def _manuscript() -> str:
    with open(audit.MAIN_TEX, encoding="utf-8") as handle:
        return handle.read()


class VerdictVocabulary(unittest.TestCase):
    """The audit must not invent a fifth value to make itself look better."""

    def test_four_plus_escalation(self):
        self.assertEqual(audit.VERDICTS,
                         ("PASS", "INVALID", "UNAVAILABLE", "BLIND"))

    def test_every_claim_uses_a_known_verdict(self):
        for claim in audit.CLAIMS:
            self.assertIn(claim.verdict, audit.VERDICTS, claim.id)

    def test_ids_are_unique(self):
        ids = [claim.id for claim in audit.CLAIMS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_scope_note_separates_pass_from_true(self):
        # The sentence the package insists on: a verdict without its scope is a
        # lie by citation.
        self.assertIn("does not mean", audit.SCOPE_NOTE)
        self.assertIn("not that the claim is false", audit.SCOPE_NOTE)

    def test_premise_sentence_is_the_requested_one(self):
        self.assertEqual(audit.PREMISE_SENTENCE,
                         "This is a philosophical premise, not a falsifiable "
                         "hypothesis.")


class Anchors(unittest.TestCase):
    """An audit whose anchors rot reports on a paper it never read."""

    def test_every_anchor_matches_exactly_once(self):
        tex = _manuscript()
        for claim in audit.CLAIMS:
            with self.subTest(claim=claim.id):
                self.assertEqual(len(re.findall(claim.anchor, tex)), 1,
                                 "anchor for {} matched {} times".format(
                                     claim.id,
                                     len(re.findall(claim.anchor, tex))))

    def test_pass_claims_declare_a_witness(self):
        for claim in audit.CLAIMS:
            if claim.verdict == audit.PASS:
                self.assertIsNotNone(claim.witness, claim.id)

    def test_unwitnessed_claims_declare_why(self):
        for claim in audit.CLAIMS:
            if claim.verdict in (audit.INVALID, audit.UNAVAILABLE):
                self.assertTrue(claim.channel.strip(), claim.id)
                self.assertTrue(claim.note.strip(), claim.id)

    def test_a_missing_anchor_escalates(self):
        claim = audit.Claim(id="X", claim="x", verdict=audit.PASS,
                            channel="c", anchor=r"a sentence that is not there",
                            witness=lambda: (True, "", "", ""))
        row = audit.grade(claim, _manuscript())
        self.assertTrue(row.escalated)
        self.assertEqual(row.verdict, "FAIL")
        self.assertIn("expected exactly 1", row.detail)

    def test_a_duplicated_anchor_escalates(self):
        claim = audit.Claim(id="X", claim="x", verdict=audit.BLIND,
                            channel="c", anchor=r"the")
        row = audit.grade(claim, _manuscript())
        self.assertTrue(row.escalated)


class Witnesses(unittest.TestCase):
    """A PASS must be earned by recomputation, not asserted."""

    def test_all_witnesses_pass_on_the_current_modules(self):
        for claim in audit.CLAIMS:
            if claim.verdict != audit.PASS:
                continue
            with self.subTest(claim=claim.id):
                ok, detail, measured, expected = claim.witness()
                self.assertTrue(ok, "{}: {} ({} vs {})".format(
                    claim.id, detail, measured, expected))

    def test_knee_witness_rejects_a_wrong_printed_value(self):
        # Inject the failure the witness exists to catch: a macro that prints a
        # knee which is not 1-1/L. The check must notice and say so.
        original = audit.NS_MACROS
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, "nested_macros.tex")
            with open(original, encoding="utf-8") as handle:
                text = handle.read()
            text = re.sub(r"\\newcommand\{\\nestedKnee\}\{[^}]*\}",
                          r"\\newcommand{\\nestedKnee}{0.99}", text)
            with open(broken, "w", encoding="utf-8") as handle:
                handle.write(text)
            audit.NS_MACROS = broken
            try:
                ok, _, _, _ = audit.witness_knee()
            finally:
                audit.NS_MACROS = original
        self.assertFalse(ok, "a knee of 0.99 must not pass as 1-1/L")

    def test_disclosed_assumption_does_not_escalate(self):
        # Every INVALID row is checked against the manuscript's own disclosure.
        # This is the assertion that the paper is allowed to state an assumption
        # -- provided it says so.
        tex = _manuscript()
        for claim in audit.CLAIMS:
            if claim.verdict != audit.INVALID:
                continue
            self.assertIsNotNone(claim.disclosure, claim.id)
            self.assertTrue(re.search(claim.disclosure, tex),
                            "{} is not disclosed in the manuscript".format(
                                claim.id))

    def test_an_undisclosed_assumption_escalates(self):
        claim = audit.Claim(id="X", claim="x", verdict=audit.INVALID,
                            channel="c", anchor=r"\\label\{eq:do\}",
                            disclosure=r"a disclosure that is not there")
        row = audit.grade(claim, _manuscript())
        self.assertTrue(row.escalated)
        self.assertIn("not disclosed", row.detail)


class BlindMap(unittest.TestCase):
    """The BLIND row is arithmetic on the registry, not an opinion."""

    def test_counts_come_from_the_registry(self):
        info = audit.blind_map()
        self.assertEqual(info["strata"], 5)
        self.assertEqual(info["edges"], 4)
        self.assertEqual(info["pairs"], 10)
        self.assertEqual(info["unwitnessed_count"], 6)

    def test_unwitnessed_pairs_are_the_non_adjacent_ones(self):
        names = [s.name for s in audit.topology.STRATA]
        for a, b in audit.blind_map()["unwitnessed"]:
            self.assertGreater(abs(names.index(a) - names.index(b)), 1,
                               "{}|{} is adjacent and should be witnessed".format(
                                   a, b))

    def test_exhaustiveness_is_not_claimable(self):
        self.assertIsNone(audit.blind_map()["exhaustive"])


class EndToEnd(unittest.TestCase):
    """The whole audit, and its artifacts, against a regenerated run."""

    def test_no_row_escalates(self):
        rows, _ = audit.audit()
        failed = [row.claim.id for row in rows if row.escalated]
        self.assertEqual(failed, [], "escalated: {}".format(failed))

    def test_tally_sums_to_the_claim_count(self):
        rows, _ = audit.audit()
        counts = audit.tally(rows)
        self.assertEqual(sum(counts.values()), len(rows))
        self.assertEqual(counts["FAIL"], 0)

    def test_all_four_verdicts_are_exercised(self):
        # A four-valued audit that only ever prints one value is a one-valued
        # audit with a longer name.
        rows, _ = audit.audit()
        used = {row.verdict for row in rows}
        for verdict in audit.VERDICTS:
            self.assertIn(verdict, used)

    def test_report_and_artifacts_are_current(self):
        # Stale artifacts are the quiet failure: the PDF would print numbers the
        # audit no longer believes. Regenerate and compare.
        with tempfile.TemporaryDirectory() as tmp:
            targets = (audit.REPORT_MD, audit.PAPER_DATA + "/self_audit_macros.tex",
                       audit.PAPER_DATA + "/self_audit_table.tex")
            before = {}
            for path in targets:
                with open(path, encoding="utf-8") as handle:
                    before[path] = handle.read()
            rows, info = audit.audit()
            audit.write_report(rows, info)
            audit.write_paper_artifacts(rows)
            for path in targets:
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(before[path], handle.read(),
                                     "{} was stale; rerun the audit".format(
                                         os.path.basename(path)))

    def test_table_macros_latex_legal(self):
        # A macro ending in a digit would be read as \saPass + "2" by TeX.
        path = os.path.join(audit.PAPER_DATA, "self_audit_macros.tex")
        with open(path, encoding="utf-8") as handle:
            names = re.findall(r"\\newcommand\{\\([A-Za-z]+)\}", handle.read())
        self.assertTrue(names)
        for name in names:
            self.assertFalse(name[-1].isdigit(), name)

    def test_command_line_check_mode_returns_success(self):
        result = subprocess.run(
            [sys.executable, "-m", "experiments.paper_self_audit", "--check"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SELF-AUDIT", result.stdout)
        self.assertIn("No commit. No push.", result.stdout)


if __name__ == "__main__":
    unittest.main()
