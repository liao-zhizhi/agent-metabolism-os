#!/usr/bin/env python3
"""Search the literature for work that would collide with the CEL paper.

The paper claims that a *physical witness* between a planner and an actuator is
a different mechanism from a confidence gate. Before that claim is published, it
has to survive the question a referee asks first: who else has done this?

This script is the search, not the judgement. It queries arXiv and Semantic
Scholar with the paper's own keywords, keeps the records from 2023 onwards, and
writes them to ``lit_audit_results.json`` with the identifier, venue, date and
abstract of each hit. Reading that file and writing the comparison is a separate
step, deliberately: the search must be reproducible and the judgement must be
signed.

Every query is recorded with the number of hits it returned *before* the date
filter, because a keyword that returns nothing is a finding too -- it is the
difference between "nobody has done this" and "we did not look".

Run:
    python3 experiments/cel_paper/lit_audit.py            # network, writes JSON
    python3 experiments/cel_paper/lit_audit.py --offline  # re-read the cache
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "lit_audit_results.json")

UA = "cel-literature-audit/1.0 (academic citation check; contact: repository owner)"

# The six keywords the architect named, plus the two neighbour terms a referee
# would use for the same idea. Each is searched as a phrase.
QUERIES = [
    ("q1", '"Causal Execution Layer"'),
    ("q2", '"Safe VLA"'),
    ("q3", '"End-to-End Autonomous Driving"'),
    ("q4", '"World Model" robot manipulation'),
    ("q5", '"Physical Hallucination"'),
    ("q6", '"Metabolic Solidification"'),
    ("q7", '"safety filter" vision-language-action'),
    ("q8", '"runtime verification" robot manipulation'),
]

EARLIEST_YEAR = 2023

_ARXIV_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _fetch(url: str, attempts: int = 4, timeout: float = 30.0) -> Optional[bytes]:
    """GET with backoff. arXiv throttles hard and 429s look like failures."""
    delay = 3.0
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as problem:
            if problem.code in (429, 503):
                time.sleep(delay)
                delay *= 2.0
                continue
            sys.stderr.write("http {} for {}\n".format(problem.code, url))
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as problem:
            sys.stderr.write("network {} for {}\n".format(problem, url))
            time.sleep(delay)
            delay *= 2.0
    return None


def arxiv_search(query: str, limit: int = 20) -> List[Dict[str, str]]:
    """arXiv Atom API. Returns [] on failure; the caller records the failure."""
    url = (
        "http://export.arxiv.org/api/query?"
        + urllib.parse.urlencode(
            {
                "search_query": "all:{}".format(query),
                "start": 0,
                "max_results": limit,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
    )
    body = _fetch(url)
    if body is None:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    hits: List[Dict[str, str]] = []
    for entry in root.findall("a:entry", _ARXIV_NS):
        identifier = (entry.findtext("a:id", "", _ARXIV_NS) or "").rsplit("/", 1)[-1]
        published = entry.findtext("a:published", "", _ARXIV_NS) or ""
        hits.append(
            {
                "source": "arxiv",
                "id": identifier,
                "title": " ".join((entry.findtext("a:title", "", _ARXIV_NS) or "").split()),
                "summary": " ".join((entry.findtext("a:summary", "", _ARXIV_NS) or "").split()),
                "published": published[:10],
                "venue": (entry.findtext("arxiv:journal_ref", "", _ARXIV_NS) or "arXiv"),
                "url": "https://arxiv.org/abs/{}".format(identifier),
            }
        )
    return hits


def s2_search(query: str, limit: int = 20) -> List[Dict[str, str]]:
    """Semantic Scholar graph API, with the fields the comparison needs."""
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urllib.parse.urlencode(
            {
                "query": query,
                "limit": limit,
                "fields": "title,abstract,year,venue,externalIds,citationCount,publicationDate",
            }
        )
    )
    body = _fetch(url, attempts=3)
    if body is None:
        return []
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return []
    hits: List[Dict[str, str]] = []
    for paper in payload.get("data", []) or []:
        external = paper.get("externalIds") or {}
        hits.append(
            {
                "source": "s2",
                "id": external.get("ArXiv") or external.get("DOI") or paper.get("paperId", ""),
                "doi": external.get("DOI", ""),
                "title": " ".join((paper.get("title") or "").split()),
                "summary": " ".join((paper.get("abstract") or "").split()),
                "published": (paper.get("publicationDate") or str(paper.get("year") or ""))[:10],
                "venue": paper.get("venue") or "",
                "citations": paper.get("citationCount") or 0,
                "url": "https://www.semanticscholar.org/paper/{}".format(
                    paper.get("paperId", "")
                ),
            }
        )
    return hits


def year_of(hit: Dict[str, str]) -> int:
    text = hit.get("published", "")
    for token in text.replace("-", " ").split():
        if len(token) == 4 and token.isdigit():
            return int(token)
    return 0


def dedupe(hits: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """One record per title, preferring the copy that has an abstract."""
    best: Dict[str, Dict[str, str]] = {}
    for hit in hits:
        key = (hit.get("title") or "").casefold().strip(" .")
        if not key:
            continue
        current = best.get(key)
        if current is None or len(hit.get("summary") or "") > len(current.get("summary") or ""):
            best[key] = hit
    return list(best.values())


def run(offline: bool = False) -> int:
    if offline:
        if not os.path.exists(CACHE):
            raise SystemExit("no cache at {}; run without --offline first".format(CACHE))
        print("cache: {}".format(CACHE))
        return 0

    report: Dict[str, object] = {
        "earliest_year": EARLIEST_YEAR,
        "generated_by": "experiments/cel_paper/lit_audit.py",
        "queries": [],
    }
    failures: List[str] = []

    for name, query in QUERIES:
        record: Dict[str, object] = {"id": name, "query": query, "sources": {}}
        combined: List[Dict[str, str]] = []
        for source, search in (("arxiv", arxiv_search), ("semanticscholar", s2_search)):
            hits = search(query)
            if not hits:
                failures.append("{}:{}".format(name, source))
            record["sources"][source] = {
                "returned": len(hits),
                "kept": 0,
            }
            combined.extend(hits)
            time.sleep(3.0 if source == "arxiv" else 1.5)
        kept = [hit for hit in dedupe(combined) if year_of(hit) >= EARLIEST_YEAR]
        kept.sort(key=lambda hit: (year_of(hit), hit.get("citations", 0)), reverse=True)
        record["sources"]["merged_kept"] = len(kept)
        record["hits"] = kept
        report["queries"].append(record)
        print(
            "{:<3} {:<46} kept {:>3} of {:<3}".format(
                name, query, len(kept), len(combined)
            )
        )

    report["failures"] = failures
    with open(CACHE, "w") as handle:
        json.dump(report, handle, indent=1, ensure_ascii=False)
    print("\nwrote {}".format(CACHE))
    if failures:
        print("unanswered queries: {}".format(", ".join(failures)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="only report the cache")
    options = parser.parse_args(argv)
    return run(offline=options.offline)


if __name__ == "__main__":
    raise SystemExit(main())
