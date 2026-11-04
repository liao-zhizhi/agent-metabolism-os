#!/usr/bin/env python3
"""Search arXiv and CrossRef for work adjacent to this paper, and record the hits.

This is a retrieval tool, not a duplicate detector. It does not score
similarity: a number computed over titles measures word overlap, and word
overlap is exactly the thing a paper like this one is likely to fake. What it
does is run a fixed set of queries, keep the raw records, and write them to a
snapshot so that the audit in LITERATURE_AUDIT.md can be checked by rerunning
it rather than by trusting it.

Channels:
  arxiv     the arXiv API (no key, rate-limited to ~1 request / 3 s)
  crossref  the CrossRef works endpoint (no key, bibliographic query)

Semantic Scholar is deliberately absent: it returned HTTP 429 on every
unauthenticated attempt during the audit, and a channel that cannot answer is
recorded as unavailable rather than replaced by a guess.

Usage:
    python3 literature_search.py                    # both channels, snapshot
    python3 literature_search.py --channel arxiv
    python3 literature_search.py --channel crossref --rows 5
    python3 literature_search.py --out data/lit.json --quiet
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
USER_AGENT = ("literature-audit/1.0 (mailto:liaozhizhi@foxmail.com)")

# Each query is a claim in the paper that wants a prior-art check, not a
# keyword bag. The comment says which claim.
ARXIV_QUERIES = {
    # Claim: an interface can be modelled as a narrowing with a difficulty.
    "fractal_interface":
        'all:"fractal interface" OR all:"fractal interfaces"',
    # Claim: the fractal structure of the boundary, not of the data.
    "fractal_ml":
        'abs:fractal AND abs:"language model" AND abs:scaling',
    # Claim: apparent emergence is a metric artefact.
    "emergence_mirage":
        'ti:"emergent abilities" AND abs:mirage',
    "emergence_metric":
        'abs:"emergent abilities" AND abs:metric',
    # Claim: a witness beats a judgement about an agent's self-report.
    "agent_control":
        'ti:"AI control" OR abs:"untrusted model" AND abs:"monitoring"',
    "evaluation_integrity":
        'abs:sandbagging AND abs:"language model"',
    # Claim: reuse of a compiled result lowers marginal cost.
    "reuse_verification":
        'abs:"verification cost" AND abs:hierarchy',
    # Claim: a critical budget B* exists.
    "critical_budget":
        'abs:"critical budget" OR abs:"threshold budget"',
}

# CrossRef sees the journal record; arXiv does not. These are the same claims.
CROSSREF_QUERIES = {
    "fractal_interface":
        "fractal interfaces quantitative model cross-scale",
    "emergence_mirage":
        "emergent abilities mirage metric discontinuity",
    "reuse_verification":
        "compounding verification cost interface reuse hierarchy",
    "critical_budget":
        "critical budget reuse rate hierarchical verification",
}


def _get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read().decode("utf-8")


def search_arxiv(query: str, rows: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": rows,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    root = ET.fromstring(_get(ARXIV_API + "?" + params))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    records = []
    for entry in root.findall("a:entry", ns):
        records.append({
            "source": "arxiv",
            "id": (entry.find("a:id", ns).text or "").split("/abs/")[-1],
            "title": " ".join((entry.find("a:title", ns).text or "").split()),
            "date": (entry.find("a:published", ns).text or "")[:10],
            "authors": [a.find("a:name", ns).text
                        for a in entry.findall("a:author", ns)][:6],
            "abstract": " ".join(
                (entry.find("a:summary", ns).text or "").split())[:700],
        })
    return records


def search_crossref(query: str, rows: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "query.bibliographic": query,
        "rows": rows,
        "filter": "from-pub-date:2023-01-01",
        "select": "DOI,title,container-title,issued,author,type",
    })
    payload = json.loads(_get(CROSSREF_API + "?" + params))["message"]
    records = []
    for item in payload.get("items", []):
        issued = item.get("issued", {}).get("date-parts") or [[""]]
        records.append({
            "source": "crossref",
            "id": item.get("DOI", ""),
            "title": (item.get("title") or [""])[0],
            "date": str(issued[0][0]),
            "authors": [a.get("family", "") for a in item.get("author", [])][:6],
            "venue": (item.get("container-title") or [""])[0],
            "type": item.get("type", ""),
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("arxiv", "crossref", "both"),
                        default="both")
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--out", default="data/literature_search.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    snapshot = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "queries": {},
        "unavailable": {},
    }
    if args.channel in ("arxiv", "both"):
        for name, query in ARXIV_QUERIES.items():
            if not args.quiet:
                print("arxiv ::", name, "::", query)
            try:
                snapshot["queries"]["arxiv:" + name] = search_arxiv(
                    query, args.rows)
            except Exception as problem:  # noqa: BLE001
                snapshot["unavailable"]["arxiv:" + name] = str(problem)
            time.sleep(3.5)  # arXiv asks for >= 3 s between requests
    if args.channel in ("crossref", "both"):
        for name, query in CROSSREF_QUERIES.items():
            if not args.quiet:
                print("crossref ::", name, "::", query)
            try:
                snapshot["queries"]["crossref:" + name] = search_crossref(
                    query, args.rows)
            except Exception as problem:  # noqa: BLE001
                snapshot["unavailable"]["crossref:" + name] = str(problem)
            time.sleep(1.5)

    with open(args.out, "w") as handle:
        json.dump(snapshot, handle, indent=1, ensure_ascii=False)

    total = sum(len(v) for v in snapshot["queries"].values())
    print("records written to {} ({} records, {} blocked)".format(
        args.out, total, len(snapshot["unavailable"])))
    for channel, reason in snapshot["unavailable"].items():
        print("  blocked:", channel, "->", reason[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
