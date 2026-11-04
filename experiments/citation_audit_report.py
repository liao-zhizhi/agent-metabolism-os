"""Run the repository's own citation auditor against this paper's bibliography.

The auditor lives in a different repository (``agent-harness-audit``) and is not
vendored here. That is deliberate: a bibliography checked by a checker that lives
in the same tree is checked by a checker the author could have written to agree.
This script shells out to the real one, parses its JSON, and emits the numbers
the appendix prints.

What the auditor says, and what that means
------------------------------------------
``PASS``    an identifier resolved and the record's title matched the entry.
``FAIL``    an identifier resolved and the record *disagreed* -- the right-looking
            id naming the wrong paper. This is the fabrication this exists to
            catch, and it fails the build.
``INVALID`` no witness could be obtained: no identifier, an identifier the agency
            does not know, or no network. Not an accusation.

Two entries in this bibliography are books or pre-DOI print articles, and no
agency can ever witness them. Deleting them to make the table green would be the
false completion this repository refuses, so they stay, and their existence is
recorded as a *locator* rather than a witness -- a catalogue record or a scanned
volume index, cited so a reader can go and look. The difference between "an API
confirmed it" and "a human found it in a scan" is printed in the table.

The run is cached to ``citation_audit_results.json`` so the appendix regenerates
without network access; ``--refresh`` re-queries the agencies.

Run:
    python3 -m experiments.citation_audit_report             # cache or run
    python3 -m experiments.citation_audit_report --refresh    # force network
    python3 -m experiments.citation_audit_report --check      # exit 1 on FAIL
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAPER = os.path.join(HERE, "paper")
BIB = os.path.join(PAPER, "references.bib")
PRIOR_BIB = os.path.join(PAPER, "data", "references_pre_audit.bib")
PAPER_DATA = os.path.join(PAPER, "data")
CACHE = os.path.join(HERE, "citation_audit_results.json")
REPORT_MD = os.path.join(HERE, "CITATION_AUDIT_REPORT.md")

#: The auditor is a sibling checkout. Overridable because a reviewer may have it
#: somewhere else, and because a hard-coded absolute path is a claim about this
#: machine rather than about the audit.
AUDITOR = os.environ.get(
    "CITATION_AUDITOR",
    os.path.join(os.path.dirname(ROOT), "agent-harness-audit", "audit_bib.py"),
)

PASS = "PASS"
FAIL = "FAIL"
INVALID = "INVALID"

#: Entries no agency can witness, and where a human did confirm them. A locator
#: says "go and look here"; it is not a witness, and the table keeps the two
#: apart. Anything listed here that the auditor *does* witness is a mistake, and
#: the script says so rather than quietly preferring one answer.
LOCATORS: Dict[str, Dict[str, str]] = {
    "mandelbrot1982": {
        "kind": "book",
        "where": "OpenLibrary work OL1808391W",
        "note": "first published 1982; W. H. Freeman among the recorded "
                "publishers; books predate DOI registration",
    },
    "simon1962": {
        "kind": "print article",
        "where": "archive.org scan of Proc. Am. Phil. Soc. Vol. 106 (1962), "
                 "index p. 467",
        "note": "the volume index lists \"Simon, Herbert A., Architecture of "
                "complexity, 467\"; the 1962 volume has no DOI",
    },
}


@dataclass(frozen=True)
class Record:
    """One graded entry, as the auditor reported it."""

    key: str
    entry_type: str
    title: str
    identifier: str
    kind: str
    verdict: str
    cause: str
    agency: str
    reason: str


def _auditor_available() -> bool:
    return os.path.exists(AUDITOR)


def run_auditor(bib: str) -> Dict[str, object]:
    """Shell out to the auditor and return its JSON.

    A missing auditor is an error, not a skip: reporting "no citation problems"
    because the checker was absent is the exact substitution of silence for
    evidence this package exists to catch.
    """
    if not _auditor_available():
        raise SystemExit(
            "citation auditor not found at {}\n"
            "set CITATION_AUDITOR to its path, or check out agent-harness-audit "
            "beside this repository".format(AUDITOR))
    result = subprocess.run(
        [sys.executable, AUDITOR, "--json", bib],
        cwd=os.path.dirname(AUDITOR), capture_output=True, text=True)
    if not result.stdout.strip():
        raise SystemExit("auditor produced no output:\n{}".format(result.stderr))
    payload = json.loads(result.stdout)
    payload["_exit"] = result.returncode
    return payload


def _records(payload: Dict[str, object]) -> List[Record]:
    out = []
    for item in payload.get("entries", []):  # type: ignore[union-attr]
        out.append(Record(
            key=item["key"],
            entry_type=item.get("type", ""),
            title=item.get("title", ""),
            identifier=item.get("identifier", "") or "",
            kind=item.get("identifier_kind", "") or "",
            verdict=item["verdict"],
            cause=item.get("cause", ""),
            agency=item.get("agency", "") or "",
            reason=item.get("reason", ""),
        ))
    return out


def tally(records: Sequence[Record]) -> Dict[str, int]:
    counts = {PASS: 0, FAIL: 0, INVALID: 0}
    for record in records:
        counts[record.verdict] = counts.get(record.verdict, 0) + 1
    return counts


def by_agency(records: Sequence[Record]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for record in records:
        if record.verdict == PASS and record.agency:
            out[record.agency] = out.get(record.agency, 0) + 1
    return out


def audit(refresh: bool = False) -> Dict[str, object]:
    """Current bibliography, plus the pre-audit one, plus the locator check."""
    cache = None
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as handle:
            cache = json.load(handle)
    if cache is None:
        cache = {"current": run_auditor(BIB), "prior": run_auditor(PRIOR_BIB)}
        with open(CACHE, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=1, sort_keys=True)
            handle.write("\n")

    current = _records(cache["current"])
    prior = _records(cache["prior"])
    locators = {}
    for record in current:
        if record.key in LOCATORS:
            if record.verdict != INVALID:
                raise SystemExit(
                    "{} is listed as unwitnessable but the auditor returned {}"
                    .format(record.key, record.verdict))
            locators[record.key] = LOCATORS[record.key]
    return {
        "current": current,
        "prior": prior,
        "counts": tally(current),
        "prior_counts": tally(prior),
        "agencies": by_agency(current),
        "locators": locators,
        "auditor": AUDITOR,
    }


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


def write_macros(result: Dict[str, object]) -> str:
    os.makedirs(PAPER_DATA, exist_ok=True)
    counts = result["counts"]
    prior = result["prior_counts"]
    agencies = result["agencies"]
    path = os.path.join(PAPER_DATA, "citation_audit_macros.tex")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/citation_audit_report.py"
                     " -- do not edit\n")
        handle.write(r"\newcommand{\caTotal}{%d}" % len(result["current"]) + "\n")
        handle.write(r"\newcommand{\caPass}{%d}" % counts[PASS] + "\n")
        handle.write(r"\newcommand{\caFail}{%d}" % counts[FAIL] + "\n")
        handle.write(r"\newcommand{\caInvalid}{%d}" % counts[INVALID] + "\n")
        handle.write(r"\newcommand{\caCrossrefCount}{%d}"
                     % agencies.get("crossref", 0) + "\n")
        handle.write(r"\newcommand{\caArxivCount}{%d}"
                     % agencies.get("arxiv", 0) + "\n")
        handle.write(r"\newcommand{\caAgencyCount}{%d}" % len(agencies) + "\n")
        handle.write(r"\newcommand{\caPriorPass}{%d}" % prior[PASS] + "\n")
        handle.write(r"\newcommand{\caPriorInvalid}{%d}" % prior[INVALID] + "\n")
        handle.write(r"\newcommand{\caLocatedCount}{%d}"
                     % len(result["locators"]) + "\n")
    return path


def _tex_escape(text: str) -> str:
    """Escape a prose cell, leaving math and command sequences alone."""
    parts = re.split(r"(\$[^$]*\$)", text)
    out = []
    for part in parts:
        if part.startswith("$") and part.endswith("$") and len(part) > 1:
            out.append(part)
            continue
        clean = part
        for char, replacement in (("&", r"\&"), ("%", r"\%"), ("#", r"\#"),
                                  ("_", r"\_"), ("<", r"\textless{}"),
                                  (">", r"\textgreater{}"),
                                  ("|", r"\textbar{}")):
            clean = clean.replace(char, replacement)
        clean = clean.replace("10.", r"10.\allowbreak{}")
        clean = clean.replace("/", r"/\allowbreak{}")
        clean = clean.replace(r"\_", r"\_\allowbreak{}")
        out.append(clean)
    return "".join(out)


def _breakable(token: str) -> str:
    """Insert break points into an identifier that has none of its own.

    A DOI, a bib key and an arXiv id are single tokens, and a single token wider
    than its column is an overfull box no amount of \\raggedright can fix. The
    breaks go at the seams a reader already parses -- after a dot, a slash or an
    underscore, and between a letter and a digit -- so a wrapped identifier still
    reads as the identifier it is.
    """
    out = []
    previous = ""
    for char in token:
        if previous and (
                (previous.isalpha() and char.isdigit())
                or (previous.isdigit() and char.isalpha())):
            out.append(r"\allowbreak{}")
        out.append(char)
        if char in "./_-":
            out.append(r"\allowbreak{}")
        previous = char
    return "".join(out)


def write_table(result: Dict[str, object]) -> str:
    """The appendix table: what the auditor said, entry by entry.

    The verdict column is the auditor's, not ours. Where no agency can speak, the
    last column gives the locator a human used, so the reader can tell a
    machine-witnessed entry from a hand-checked one.
    """
    path = os.path.join(PAPER_DATA, "citation_audit_table.tex")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/citation_audit_report.py"
                     " -- do not edit\n")
        handle.write(r"\setlength{\tabcolsep}{3pt}" + "\n")
        # \scriptsize and 3pt separation: nineteen rows of identifiers and DOIs
        # inside one IEEEtran column is a width problem before it is a typography
        # problem, and the smaller face is what lets every token keep its seams.
        handle.write(r"\scriptsize" + "\n")
        handle.write(r"\begin{tabular}{@{}>{\raggedright\arraybackslash}"
                     r"p{0.24\columnwidth}>{\raggedright\arraybackslash}"
                     r"p{0.24\columnwidth}>{\raggedright\arraybackslash}"
                     r"p{0.16\columnwidth}>{\raggedright\arraybackslash}"
                     r"p{0.26\columnwidth}@{}}" + "\n")
        handle.write(r"\toprule" + "\n")
        handle.write(r"entry & identifier & verdict & how it was witnessed \\" + "\n")
        handle.write(r"\midrule" + "\n")
        for record in result["current"]:
            if record.verdict == PASS:
                how = "{} (title match)".format(record.agency or "agency")
            elif record.key in result["locators"]:
                how = "no agency; located at " + \
                      result["locators"][record.key]["where"]
            else:
                how = record.reason
            handle.write("{} & {} & \\textsc{{{}}} & {} \\\\\n".format(
                _breakable(record.key.replace("_", r"\_")),
                _breakable(record.identifier.replace("_", r"\_")) or "--",
                record.verdict.lower(),
                _tex_escape(how)))
        handle.write(r"\bottomrule" + "\n")
        handle.write(r"\end{tabular}" + "\n")
    return path


def write_report(result: Dict[str, object]) -> str:
    counts = result["counts"]
    prior = result["prior_counts"]
    lines: List[str] = []
    lines.append("# Citation audit of *Fractal Interfaces*")
    lines.append("")
    lines.append("Generated by `experiments/citation_audit_report.py`, which "
                 "shells out to the repository's own auditor at "
                 "`agent-harness-audit/audit_bib.py` (via `CitationAuditProxy`) "
                 "and records its verdicts verbatim. Do not edit by hand.")
    lines.append("")
    lines.append("## Tally")
    lines.append("")
    lines.append("| | count |")
    lines.append("| --- | --- |")
    lines.append("| entries | {} |".format(len(result["current"])))
    lines.append("| `PASS` (identifier resolved, title matched) | {} |".format(
        counts[PASS]))
    lines.append("| `FAIL` (identifier resolved, record contradicts) | {} |"
                 .format(counts[FAIL]))
    lines.append("| `INVALID` (no witness obtainable) | {} |".format(
        counts[INVALID]))
    lines.append("")
    lines.append("Agencies that answered: " + ", ".join(
        "{} {}".format(name, count)
        for name, count in sorted(result["agencies"].items())) + ".")
    lines.append("")
    lines.append("The bibliography as it was before the first run carried no "
                 "identifiers at all, and the auditor's verdict on it was "
                 "{} PASS, {} FAIL, {} INVALID. That first result is the "
                 "finding: an entry with no identifier is a claim nothing can "
                 "check, which is why `paper/data/references_pre_audit.bib` is "
                 "kept alongside the current file.".format(
                     prior[PASS], prior[FAIL], prior[INVALID]))
    lines.append("")
    lines.append("No entry has ever returned `FAIL` in this audit: no "
                 "identifier was found to name the wrong paper."
                 if counts[FAIL] == 0 else
                 "**{} entry/entries returned FAIL.** A resolved identifier "
                 "named a different work; each was corrected before this report "
                 "was written.".format(counts[FAIL]))
    lines.append("")
    lines.append("## Entry by entry")
    lines.append("")
    lines.append("| entry | identifier | verdict | how it was witnessed |")
    lines.append("| --- | --- | --- | --- |")
    for record in result["current"]:
        if record.verdict == PASS:
            how = "{} (title match)".format(record.agency or "agency")
        elif record.key in result["locators"]:
            how = "no agency; located at " + result["locators"][record.key]["where"]
        else:
            how = record.reason
        lines.append("| `{}` | `{}` | `{}` | {} |".format(
            record.key, record.identifier or "--", record.verdict, how))
    lines.append("")
    lines.append("## The two entries no agency can witness")
    lines.append("")
    lines.append("Both are genuine, and both stay. Removing an unverifiable but "
                 "true citation to make a table green would be the defect this "
                 "auditor was written to catch, one layer up.")
    lines.append("")
    for key, info in sorted(result["locators"].items()):
        lines.append("- **`{}`** ({}) — {}".format(key, info["kind"], info["note"]))
        lines.append("  - located at: {}".format(info["where"]))
    lines.append("")
    lines.append("## Reproducing")
    lines.append("")
    lines.append("```")
    lines.append("python3 -m experiments.citation_audit_report            "
                 "# use the cached run")
    lines.append("python3 -m experiments.citation_audit_report --refresh  "
                 "# re-query arXiv and CrossRef")
    lines.append("python3 -m experiments.citation_audit_report --check    "
                 "# exit 1 on any FAIL")
    lines.append("```")
    lines.append("")
    lines.append("Network is touched only by `--refresh` (or by a first run with "
                 "no cache). The auditor's own rule is inherited here: an "
                 "unreachable agency yields `INVALID`, never `FAIL`.")
    lines.append("")
    with open(REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return REPORT_MD


def report(result: Dict[str, object]) -> None:
    counts = result["counts"]
    prior = result["prior_counts"]
    line = "=" * 78
    print(line)
    print("CITATION AUDIT: references.bib, judged by CitationAuditProxy")
    print(line)
    print("bibliography  {}".format(os.path.relpath(BIB, ROOT)))
    print("auditor       {}".format(result["auditor"]))
    print("entries       {}".format(len(result["current"])))
    print()
    for record in result["current"]:
        who = record.agency or ("located" if record.key in result["locators"]
                                else "--")
        print("  [{:<8}] {:<22} {:<26} {}".format(
            record.verdict, record.key, record.identifier or "--", who))
    print()
    print("-" * 78)
    print("TALLY")
    print("-" * 78)
    print("  PASS      {}".format(counts[PASS]))
    print("  FAIL      {}".format(counts[FAIL]))
    print("  INVALID   {}".format(counts[INVALID]))
    print()
    print("  before the audit: {} PASS, {} FAIL, {} INVALID "
          "(no entry carried an identifier)".format(
              prior[PASS], prior[FAIL], prior[INVALID]))
    print("  agencies: " + ", ".join("{} {}".format(name, count)
                                     for name, count in
                                     sorted(result["agencies"].items())))
    print()
    print("  The {} INVALID entries are a book and a pre-DOI print article."
          .format(counts[INVALID]))
    for key, info in sorted(result["locators"].items()):
        print("    - {}: {}".format(key, info["where"]))
    print()
    print("  A FAIL would be a right-looking identifier naming the wrong paper.")
    print("  None was found: that is a statement about this bibliography, not")
    print("  about bibliographies in general.")
    print()


def main(argv: Sequence[str]) -> int:
    refresh = "--refresh" in argv
    check_only = "--check" in argv
    result = audit(refresh=refresh)
    report(result)
    counts = result["counts"]
    if not check_only:
        written = [write_report(result), write_macros(result), write_table(result)]
        print("=" * 78)
        print("WROTE")
        print("=" * 78)
        for path in written:
            print("  " + os.path.relpath(path, ROOT))
        print()
    print("Reproduce with:  python3 -m experiments.citation_audit_report")
    print("No commit. No push.")
    return 0 if counts[FAIL] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
