"""Tests for the bibliography audit.

The audit is a wrapper around a tool in another repository, so what is testable
here is the wrapper's honesty rather than the auditor's own behaviour (which
``agent-harness-audit`` tests itself):

* it must not report a clean bibliography when the auditor is missing;
* it must not turn "no witness" into "wrong", nor "wrong" into "no witness";
* a FAIL must fail the build;
* the appendix macros and table must be regenerable from the cached run, so the
  printed numbers cannot drift from the JSON the auditor produced;
* every entry the wrapper calls "located" must actually be INVALID, because an
  entry no agency can witness but that a human confirmed is a different claim
  from one an API confirmed.

Nothing here touches the network: the tests run against the committed cache and
synthetic payloads.

Run:  python3 -m unittest tests.test_citation_audit
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments import citation_audit_report as report  # noqa: E402


def _payload(*entries):
    return {"file": "references.bib", "entries": list(entries)}


def _entry(key, verdict, identifier="", kind="", agency="", cause="matched",
           title="T", reason=""):
    return {
        "key": key, "type": "article", "title": title,
        "identifier": identifier, "identifier_kind": kind,
        "verdict": verdict, "reason": reason or cause,
        "cause": cause, "actual_title": title if verdict == "PASS" else "",
        "agency": agency,
    }


class Tally(unittest.TestCase):
    """Counting must not round a failure into a success."""

    def test_counts_each_verdict_separately(self):
        records = report._records(_payload(
            _entry("a", "PASS", "10.1/x", "doi", "crossref"),
            _entry("b", "FAIL", "10.1/y", "doi", "crossref"),
            _entry("c", "INVALID", cause="no_identifier"),
        ))
        counts = report.tally(records)
        self.assertEqual(counts, {"PASS": 1, "FAIL": 1, "INVALID": 1})

    def test_agencies_count_only_witnessed_entries(self):
        records = report._records(_payload(
            _entry("a", "PASS", "10.1/x", "doi", "crossref"),
            _entry("b", "PASS", "10.1/y", "doi", "crossref"),
            _entry("c", "PASS", "2001.0001", "arxiv", "arxiv"),
            _entry("d", "INVALID", cause="no_identifier"),
        ))
        self.assertEqual(report.by_agency(records), {"crossref": 2, "arxiv": 1})


class MissingAuditor(unittest.TestCase):
    """A missing checker must stop the audit, not pass it."""

    def test_absent_auditor_is_an_error(self):
        with mock.patch.object(report, "AUDITOR", "/nonexistent/audit_bib.py"):
            with self.assertRaises(SystemExit):
                report.run_auditor(report.BIB)

    def test_availability_probe_is_path_based(self):
        with mock.patch.object(report, "AUDITOR", "/nonexistent/audit_bib.py"):
            self.assertFalse(report._auditor_available())


class Locators(unittest.TestCase):
    """A locator is not a witness, and the code must keep them apart."""

    def test_every_locator_is_an_invalid_entry(self):
        result = report.audit(refresh=False)
        for key in result["locators"]:
            record = next(r for r in result["current"] if r.key == key)
            self.assertEqual(record.verdict, "INVALID", key)

    def test_a_locator_that_becomes_witnessed_is_an_error(self):
        # If an agency ever does witness a book, the note in LOCATORS is stale
        # and silently preferring either answer would hide that.
        payload = _payload(_entry("mandelbrot1982", "PASS", "10.1/x", "doi",
                                  "crossref"),
                           _entry("simon1962", "INVALID",
                                  cause="no_identifier"))
        # The cache path is redirected, not just the network: a test that let
        # audit() write its synthetic payload into the committed cache would
        # corrupt the very file the appendix numbers come from.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(report, "CACHE", os.path.join(tmp, "cache.json")):
                with mock.patch.object(report, "run_auditor",
                                       side_effect=[payload, payload]):
                    with self.assertRaises(SystemExit):
                        report.audit(refresh=True)

    def test_locator_entries_are_listed_in_the_report(self):
        result = report.audit(refresh=False)
        with open(report.REPORT_MD, encoding="utf-8") as handle:
            text = handle.read()
        for key in result["locators"]:
            self.assertIn("`{}`".format(key), text)


class Cache(unittest.TestCase):
    """The appendix numbers must come from the auditor's JSON, not from prose."""

    def test_cache_is_present_and_parseable(self):
        self.assertTrue(os.path.exists(report.CACHE),
                        "run `python3 -m experiments.citation_audit_report`")
        with open(report.CACHE, encoding="utf-8") as handle:
            cache = json.load(handle)
        self.assertIn("current", cache)
        self.assertIn("prior", cache)

    def test_cached_run_matches_the_current_bibliography(self):
        # Key sets must agree: an entry added to references.bib without a
        # refresh means the appendix is describing a different bibliography.
        result = report.audit(refresh=False)
        with open(report.CACHE, encoding="utf-8") as handle:
            cache = json.load(handle)
        cached_keys = {e["key"] for e in cache["current"]["entries"]}
        self.assertEqual(cached_keys, {r.key for r in result["current"]})
        with open(report.BIB, encoding="utf-8") as handle:
            on_disk = set(re.findall(r"(?m)^@\w+\{([^,]+),", handle.read()))
        self.assertEqual(cached_keys, on_disk,
                         "references.bib changed; rerun the audit")

    def test_macros_are_current(self):
        result = report.audit(refresh=False)
        path = os.path.join(report.PAPER_DATA, "citation_audit_macros.tex")
        with open(path, encoding="utf-8") as handle:
            before = handle.read()
        report.write_macros(result)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(before, handle.read(),
                             "citation macros were stale; rerun the audit")

    def test_table_is_current(self):
        result = report.audit(refresh=False)
        path = os.path.join(report.PAPER_DATA, "citation_audit_table.tex")
        with open(path, encoding="utf-8") as handle:
            before = handle.read()
        report.write_table(result)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(before, handle.read(),
                             "citation table was stale; rerun the audit")

    def test_macro_numbers_match_the_cache(self):
        result = report.audit(refresh=False)
        path = os.path.join(report.PAPER_DATA, "citation_audit_macros.tex")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        macros = dict(re.findall(r"\\newcommand\{\\(\w+)\}\{(\d+)\}", text))
        self.assertEqual(int(macros["caTotal"]), len(result["current"]))
        self.assertEqual(int(macros["caPass"]), result["counts"]["PASS"])
        self.assertEqual(int(macros["caFail"]), result["counts"]["FAIL"])
        self.assertEqual(int(macros["caInvalid"]), result["counts"]["INVALID"])
        self.assertEqual(
            int(macros["caCrossrefCount"]) + int(macros["caArxivCount"]),
            result["counts"]["PASS"],
            "every PASS must be attributable to an agency")


class NoFailures(unittest.TestCase):
    """The build gate: a FAIL means a citation names the wrong paper."""

    def test_no_entry_fails(self):
        result = report.audit(refresh=False)
        self.assertEqual(result["counts"]["FAIL"], 0,
                         "an identifier resolves to a different work")

    def test_the_pre_audit_bibliography_had_no_identifiers(self):
        # The claim the appendix makes about the first run is a claim about a
        # file in this repository, so it is checked here rather than asserted.
        result = report.audit(refresh=False)
        self.assertEqual(result["prior_counts"]["PASS"], 0)
        self.assertEqual(result["prior_counts"]["INVALID"], len(result["prior"]))
        for record in result["prior"]:
            self.assertEqual(record.verdict, "INVALID", record.key)
            self.assertEqual(record.cause, "no_identifier", record.key)


class TableSafety(unittest.TestCase):
    """The generated table has to be LaTeX-legal before it is readable."""

    def test_breakable_keeps_the_token(self):
        token = "panitchayangkoon2010"
        broken = report._breakable(token)
        self.assertEqual(re.sub(r"\\allowbreak\{\}", "", broken), token)

    def test_breakable_inserts_seams(self):
        broken = report._breakable("10.1073/pnas.1005484107")
        self.assertIn(r"10.\allowbreak{}", broken)
        self.assertIn(r"pnas.\allowbreak{}", broken)
        self.assertIn(r"100548410", broken.replace(r"\allowbreak{}", ""))

    def test_escaping_leaves_math_alone(self):
        self.assertEqual(report._tex_escape("$10.1/x$"), "$10.1/x$")

    def test_underscores_are_escaped_outside_math(self):
        self.assertIn(r"\_", report._tex_escape("references_bib"))

    def test_control_characters_escaped(self):
        escaped = report._tex_escape("a & b % c # d < e > f | g")
        for piece in (r"\&", r"\%", r"\#", r"\textless{}", r"\textgreater{}",
                      r"\textbar{}"):
            self.assertIn(piece, escaped)

    def test_generated_table_has_no_bare_underscore(self):
        path = os.path.join(report.PAPER_DATA, "citation_audit_table.tex")
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.lstrip().startswith("%"):
                    continue  # the generator's own banner
                without_escapes = line.replace(r"\_", "")
                self.assertNotIn("_", without_escapes, line)


if __name__ == "__main__":
    unittest.main()
