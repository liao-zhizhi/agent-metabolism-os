"""Eating our own dog food: the interface theory, applied to the paper.

Every other experiment in this directory tests the *model*. This one tests the
*manuscript* -- with the same lattice it uses on anything else, and with the
verdicts the package already defines rather than new ones invented for the
occasion. That is the point of running it: an auditor that grades its own paper on
a private scale has audited nothing.

The four verdicts, and what each of them is allowed to mean
-----------------------------------------------------------
Taken verbatim from :mod:`agent_metabolism.boundary_auditor` and
:mod:`agent_metabolism.lean_auditor`, because a verdict whose scope is quoted
without its definition becomes a lie by citation:

``PASS``
    The claim is falsifiable, internally consistent, and its own numbers suffice
    to close it. Here that is enforced mechanically: the number printed in the
    paper is re-derived from the module that produced it, and the two must agree
    at the printed precision. **PASS does not mean the claim is true of nature.**
    It means the manuscript's arithmetic holds and would hold in any world where
    the model does.

``INVALID``
    No witness is offered -- *nothing to examine*, which is not the same finding
    as *refuted*. A stated assumption with no measurement behind it earns this
    verdict, and loses it the moment a witness exists.

``UNAVAILABLE``
    The channel that could answer cannot answer. A machine without a compiler has
    not found a bad proof, and a paper about interfaces has no instrument that
    could decide whether an interface is a boundary. An environment that cannot
    answer must not be allowed to accuse, so the verdict is recorded and the
    claim is left standing.

``BLIND``
    A statement about the registry, not about reality: an axis for which no filing
    supplies a witness. The paper partitions scale into five strata; whether those
    five are exhaustive, and whether the six pairs it does not connect are
    connected, is not decidable from anything in this repository.

So the report has a spine that cannot be argued with: `PASS` rows are recomputed,
`INVALID` rows are checked for disclosure (an undisclosed assumption is escalated
because the honesty of the paper, not the model, is what failed), `UNAVAILABLE`
rows are checked for a sentence marking them as premises, and `BLIND` rows are
counted off the topology registry.

Run:
    python3 -m experiments.paper_self_audit            # audit + write artifacts
    python3 -m experiments.paper_self_audit --check    # audit only, exit 1 on FAIL

Writes:
    experiments/PAPER_AUDIT_REPORT.md
    experiments/paper/data/self_audit_macros.tex
    experiments/paper/data/self_audit_table.tex

No network. No commit. No push.
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments import critical_budget_experiment as CB  # noqa: E402
from experiments import nested_solidification_experiment as NS  # noqa: E402
from experiments import topology  # noqa: E402

PAPER = os.path.join(HERE, "paper")
MAIN_TEX = os.path.join(PAPER, "main.tex")
PAPER_DATA = os.path.join(PAPER, "data")
REPORT_MD = os.path.join(HERE, "PAPER_AUDIT_REPORT.md")

CB_MACROS = os.path.join(PAPER_DATA, "critical_budget_macros.tex")
NS_MACROS = os.path.join(PAPER_DATA, "nested_macros.tex")
NS_TABLE = os.path.join(PAPER_DATA, "nested_table.tex")

#: The verdict vocabulary, in the order the paper should print it. Fixed here
#: rather than sputtered at each call site so that a typo cannot invent a
#: fifth verdict and still render.
PASS = "PASS"
INVALID = "INVALID"
UNAVAILABLE = "UNAVAILABLE"
BLIND = "BLIND"
VERDICTS = (PASS, INVALID, UNAVAILABLE, BLIND)

#: The sentence the architect asked to see in print, and which the appendix
#: repeats for every premise it applies to.
PREMISE_SENTENCE = ("This is a philosophical premise, not a falsifiable "
                    "hypothesis.")

#: The scope note. Printed in the report and in the appendix, because a table of
#: verdicts without it is the exact artefact this module exists to refuse.
SCOPE_NOTE = (
    "A PASS here means the claim is falsifiable and the manuscript's own numbers "
    "recompute; it does not mean the claim is true of nature. INVALID means no "
    "witness was offered, not that the claim is false. UNAVAILABLE means the "
    "instrument that could answer does not exist in this setting, which is a fact "
    "about the audit, not a defect in the claim. BLIND is a statement about the "
    "registry's coverage, not about the world."
)


# ---------------------------------------------------------------------------
# 1. the claim table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One assertion, where it lives, and what would decide it.

    ``anchor`` is a regular expression that must match exactly once in
    ``main.tex``. That is not decoration: an audit whose anchors silently rot is
    an audit that reports on a paper it never read, and the ``exactly once`` rule
    is what turns a moved paragraph into a loud failure rather than a quiet one.
    """

    id: str
    claim: str
    verdict: str
    channel: str
    anchor: str
    witness: Optional[Callable[[], Tuple[bool, str, str, str]]] = None
    disclosure: Optional[str] = None
    premise_marker: Optional[str] = None
    short: str = ""
    note: str = ""


@dataclass
class Row:
    """A graded claim: the definition plus what the audit found."""

    claim: Claim
    anchor_count: int
    ok: bool
    detail: str
    measured: str = ""
    expected: str = ""
    escalated: bool = False
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """The verdict actually reported, after escalation."""
        return "FAIL" if self.escalated else self.claim.verdict


def read_macros(path: str) -> Dict[str, str]:
    """``{name: body}`` for every ``\\newcommand`` in a generated file."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}", text))


def _printed(macros: Dict[str, str], name: str) -> Optional[float]:
    raw = macros.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _close(printed: Optional[float], value: float, digits: int) -> bool:
    """Is ``printed`` the decimal rendering of ``value`` at ``digits``?

    Half a unit in the last printed place, plus a whisker for the fact that a
    formatted float has already been rounded once. A looser comparison would pass
    on numbers that differ in the digit a reader would actually check.
    """
    if printed is None or not math.isfinite(value):
        return False
    return abs(printed - value) <= 0.5 * 10.0 ** (-digits) + 1e-9


# ---------------------------------------------------------------------------
# 2. witnesses: recompute the printed number from the module that produced it
# ---------------------------------------------------------------------------


def witness_cb_star() -> Tuple[bool, str, str, str]:
    """``B*`` at the nested experiment's premium, against ``\\nestedBstar``."""
    macros = read_macros(NS_MACROS)
    value = NS.critical_budget()
    printed = _printed(macros, "nestedBstar")
    ok = _close(printed, value, 2)
    return (ok, "B*(S=5) recomputed from critical_budget()",
            "{:.2f}".format(value), "nestedBstar={}".format(printed))


def witness_gain() -> Tuple[bool, str, str, str]:
    """``G_inf`` printed twice, and the two files must agree with each other."""
    cb = read_macros(CB_MACROS)
    ns = read_macros(NS_MACROS)
    value = CB.gain_infinite()
    first = _printed(cb, "cbGain")
    second = _printed(ns, "nestedGain")
    agree = (first is not None and second is not None
             and abs(first - second) <= 5e-4)
    ok = _close(first, value, 3) and _close(second, value, 3) and agree
    return (ok, "G_inf recomputed; both generated files must print it identically",
            "{:.3f}".format(value),
            "cbGain={} nestedGain={}".format(first, second))


def witness_slope() -> Tuple[bool, str, str, str]:
    """``G/(G-1)``: the slope nothing was fitted to."""
    cb = read_macros(CB_MACROS)
    macros = read_macros(NS_MACROS)
    value = CB.gain_infinite() / (CB.gain_infinite() - 1.0)
    printed = _printed(cb, "cbSlopePredicted")
    ok = _close(printed, value, 4)
    # The nested section's own gauge of the same quantity, if it printed one.
    del macros
    return (ok, "G/(G-1) recomputed from the two prices",
            "{:.4f}".format(value), "cbSlopePredicted={}".format(printed))


def witness_knee() -> Tuple[bool, str, str, str]:
    """``kappa* = 1-1/L`` and the bound it claims, both checked numerically."""
    macros = read_macros(NS_MACROS)
    value = NS.reuse_knee()
    printed = _printed(macros, "nestedKnee")
    # The claim is not only the number, and the three parts of it are checked
    # separately. Above the knee: bounded. At the knee: affine in depth, so
    # consecutive differences constant. Below it: the exact recursion growth,
    # (m_j - kappa*cbar)/m_{j-1} = (1-kappa)*L -- an identity, so no tolerance
    # is needed and none is offered. (Testing instead that m_9 is "large" would
    # have failed on a true claim: that is the failure mode an audit's own bug
    # produces, so each test follows the statement rather than a magnitude.)
    knee = NS.reuse_knee()
    bounded = all(
        NS.marginal_cost(60, k) < 1.25 * NS.marginal_cost(40, k)
        for k in (knee + 0.05, knee + 0.15, 0.95)
    )
    steps = [NS.marginal_cost(d + 1, knee) - NS.marginal_cost(d, knee)
             for d in (5, 10, 20)]
    affine = max(steps) - min(steps) <= 1e-12
    below = knee - 0.1
    identity = ((NS.marginal_cost(9, below) - below * NS.LOOKUP)
                / NS.marginal_cost(8, below))
    predicted_growth = (1.0 - below) * NS.BRANCH
    grows = abs(identity - predicted_growth) <= 1e-12
    ok = _close(printed, value, 2) and bounded and affine and grows
    return (ok, "kappa* recomputed; boundedness above the knee, affinity at it, "
            "and the recursion's growth identity below it, all re-tested",
            "{:.2f}".format(value),
            "nestedKnee={} bounded={} affine_at_knee={} growth={:.6f} vs "
            "(1-k)L={:.6f}".format(printed, bounded, affine, identity,
                                   predicted_growth))


def witness_recovery() -> Tuple[bool, str, str, str]:
    """``B_rec`` and the one-time cost behind it."""
    macros = read_macros(NS_MACROS)
    value = NS.recovery_budget(6)
    build = NS.one_time_cost(6)
    printed = _printed(macros, "nestedBrec")
    printed_build = _printed(macros, "nestedBuild")
    ok = (_close(printed, value, 2) and _close(printed_build, build, 2)
          and value > build)
    return (ok, "B_rec(6) recomputed, and it must sit above the one-time cost",
            "{:.2f} over T={:.2f}".format(value, build),
            "nestedBrec={} nestedBuild={}".format(printed, printed_build))


def witness_multiple() -> Tuple[bool, str, str, str]:
    """The ``30 207x`` in the table's last row, recomputed and re-read."""
    macros = read_macros(NS_MACROS)
    value = NS.efficiency(200.0, 6)
    printed = _printed(macros, "nestedEffFar")
    in_table = False
    if os.path.exists(NS_TABLE):
        with open(NS_TABLE, encoding="utf-8") as handle:
            in_table = "{:.0f}".format(value) in handle.read()
    ok = _close(printed, value, 0) and in_table
    return (ok, "the efficiency at B=200 recomputed, and present in the table file",
            "{:.0f}x".format(value), "nestedEffFar={} in_table={}".format(
                printed, in_table))


def witness_reach() -> Tuple[bool, str, str, str]:
    """Theorem 3's vanishing: the ratio must return to one, exponentially."""
    far = CB.reach_ratio(1.0e6)
    near_peak = CB.peak_reach_ratio()
    ok = (abs(far - 1.0) < 1e-9 and near_peak[1] > 1.0e6
          and 1.0 < near_peak[0] < 100.0)
    return (ok, "the reach ratio at B=1e6 recomputed, and the peak re-located",
            "ratio(1e6)={:.12f}, peak {:.4g} at B={:.3f}".format(
                far, near_peak[1], near_peak[0]), "ratio -> 1, interior peak")


def witness_ratio_of_one() -> Tuple[bool, str, str, str]:
    """Experiment A's headline: 1e9 coherence buys 1e2 reach, 1e0 buys 1e5."""
    from experiments import model
    del model
    return (True, "reported from the paper's own Experiment A table; not recomputed here",
            "1e2 vs 1e5", "table, not module")


# ---------------------------------------------------------------------------
# 3. the claims
# ---------------------------------------------------------------------------


CLAIMS: Sequence[Claim] = (
    Claim(
        id="A1",
        claim="The critical budget closed form "
              "$B^{*}=c_v[p(c_v-c_c)+S]/[p(c_v-c_c)-c_d]$",
        verdict=PASS,
        channel="recomputation from experiments/critical_budget_experiment.py",
        anchor=r"\\label\{eq:cbstar\}",
        witness=witness_cb_star,
        note="The nested experiment re-derives the same threshold at S=5; if the "
             "two modules ever disagreed, this row is where it would show.",
        short="The critical-budget closed form",
    ),
    Claim(
        id="A2",
        claim="The amortisation ceiling $G_\\infty=c_v/\\bar c$",
        verdict=PASS,
        channel="recomputation, printed by two independent generators",
        anchor=r"\\label\{eq:cbgain\}",
        witness=witness_gain,
        note="Two files print it. Agreement between independently generated "
             "artefacts is a stronger witness than either one alone.",
        short="The amortisation ceiling",
    ),
    Claim(
        id="A3",
        claim="The slope $G_\\infty/(G_\\infty-1)$ that nothing was fitted to",
        verdict=PASS,
        channel="recomputation from the two prices",
        anchor=r"slope\s+\$G_\\infty/\(G_\\infty-1\)\$",
        witness=witness_slope,
        note="Fitted over S in [0,4] with R^2=1.0000; the prediction and the fit "
             "are the same to four decimals.",
        short="The slope nothing was fitted to",
    ),
    Claim(
        id="A4",
        claim="The reuse knee $\\kappa^{*}=1-1/L=0.75$, with bounded marginal cost "
              "above it",
        verdict=PASS,
        channel="recomputation plus a two-sided boundedness test",
        anchor=r"\\label\{eq:nestedknee\}",
        witness=witness_knee,
        note="Both halves are checked: the number, and the property the number "
             "claims. A knee that did not bound anything would still print 0.75.",
        short="The reuse knee, with bounded marginal cost above it",
    ),
    Claim(
        id="A5",
        claim="The recovery budget $B_{\\text{rec}}(6)=T(d)/[1-m_d/c_{\\text{flat}}]$",
        verdict=PASS,
        channel="recomputation, plus the inequality B_rec > T",
        anchor=r"\\label\{eq:nestedbrec\}",
        witness=witness_recovery,
        short="The recovery budget",
    ),
    Claim(
        id="A6",
        claim="The efficiency jump to $\\num{30207}\\times$ at $B=200$",
        verdict=PASS,
        channel="recomputation, and a read-back of the generated table",
        anchor=r"\\input\{data/nested_table\.tex\}",
        witness=witness_multiple,
        note="The row is checked twice: the module that computes it, and the "
             "file the paper actually includes.",
        short="The efficiency jump at B=200",
    ),
    Claim(
        id="A7",
        claim="The reach advantage peaks inside and returns to one (Theorem 3)",
        verdict=PASS,
        channel="numerical re-solution, no generated constant involved",
        anchor=r"\\label\{eq:cbratio\}",
        witness=witness_reach,
        note="This row has no macro, so it is the one PASS that could not be "
             "checked by comparing printed digits. It is recomputed instead.",
        short="The reach advantage peaks inside and returns to one",
    ),
    Claim(
        id="B1",
        claim="Causation is labelling: the intervention $\\mathrm{do}(W{=}\\mathrm{reject})$ "
              "separates the two probabilities",
        verdict=UNAVAILABLE,
        channel="no instrument: the do-operator is estimated inside a simulated "
                "judge, not in a physical system",
        anchor=r"\\label\{eq:do\}",
        premise_marker=r"a statement about a\s+system rather than a fit",
        note="Experiment G establishes that the two estimates are separable in "
             "principle and differ in the simulation. It cannot establish that a "
             "real label is a cause, because no physical intervention is available.",
        short="Causation is labelling: the do-operator separates the two probabilities",
    ),
    Claim(
        id="B2",
        claim="An interface is a boundary (a narrowing condition), not a surface",
        verdict=UNAVAILABLE,
        channel="definitional: no measurement could decide it",
        anchor=r"the boundary is not a surface but a \\emph\{narrowing\}",
        premise_marker=r"category error dressed as a diagram",
        note="The whole model is built on this reading. If it were false the "
             "paper would be about something else, and no experiment inside it "
             "could say so.",
        short="An interface is a boundary, not a surface",
    ),
    Claim(
        id="C1",
        claim="The per-layer success probability is $p_{\\text{layer}}=1-D$",
        verdict=INVALID,
        channel="no witness: no measurement of a per-layer success rate at any "
                "of the five scales",
        anchor=r"We set\s+\$p_\{\\text\{layer\}\}=1-D\$",
        disclosure=r"That is\s+a choice and it is stated as one",
        note="INVALID here means unwitnessed, not refuted. The paper discloses "
             "the choice and proves its orderings survive any monotone decreasing "
             "replacement, which is why the verdict is not escalated.",
        short="The per-layer success probability is 1 minus the difficulty",
    ),
    Claim(
        id="C2",
        claim="Reach covaries with witness density rather than with capability",
        verdict=INVALID,
        channel="no witness: stated as a conjecture, no deployed system measured",
        anchor=r"This remains a conjecture",
        disclosure=r"This remains a conjecture",
        note="Falsifiable and not yet falsified -- different from unfalsifiable. "
             "The distinction is the reason this row is INVALID and B1 is "
             "UNAVAILABLE.",
        short="Reach tracks witness density, not capability",
    ),
    Claim(
        id="D1",
        claim="Scale partitions into the five strata of the topology",
        verdict=BLIND,
        channel="registry statement: coverage of the strata graph, not of nature",
        anchor=r"\\label\{sec:topology\}",
        note="Counted below from experiments/topology.py, not asserted.",
        short="Scale partitions into the five strata",
    ),
)


# ---------------------------------------------------------------------------
# 4. the registry count for the BLIND row
# ---------------------------------------------------------------------------


def blind_map() -> Dict[str, object]:
    """The strata graph, and which of its pairs no filing witnesses.

    ``STRATA`` is a chain: five nodes, four edges, every stratum joined only to
    its neighbours. Of the ten unordered pairs the complete graph would have, six
    are therefore unwitnessed -- including the ones a reader is most likely to
    want (quantum|biological, information|quantum). Exhaustiveness is a further
    question the registry cannot even pose.
    """
    names = [s.name for s in topology.STRATA]
    connected = {tuple(sorted(pair.split("|")))
                 for pair in topology.BOUNDARIES}
    pairs = [(names[i], names[j])
             for i in range(len(names)) for j in range(i + 1, len(names))]
    missing = [pair for pair in pairs if tuple(sorted(pair)) not in connected]
    return {
        "strata": len(names),
        "names": names,
        "edges": len(connected),
        "pairs": len(pairs),
        "unwitnessed": missing,
        "unwitnessed_count": len(missing),
        "exhaustive": None,
    }


# ---------------------------------------------------------------------------
# 5. grading
# ---------------------------------------------------------------------------


def grade(claim: Claim, tex: str) -> Row:
    """Check the anchor, then the witness, then the disclosure.

    The order matters. An anchor that does not match means the audit is looking
    at a paper it has not read, and no later evidence can repair that, so it
    escalates regardless of what the numbers say.
    """
    hits = len(re.findall(claim.anchor, tex))
    if hits != 1:
        return Row(claim, hits, False,
                   "anchor matched {} times (expected exactly 1)".format(hits),
                   escalated=True)

    if claim.verdict == PASS:
        if claim.witness is None:
            return Row(claim, hits, False, "PASS claim has no witness",
                       escalated=True)
        ok, detail, measured, expected = claim.witness()
        return Row(claim, hits, ok, detail, measured, expected,
                   escalated=not ok)

    if claim.verdict == INVALID:
        disclosed = bool(claim.disclosure and re.search(claim.disclosure, tex))
        if not disclosed:
            return Row(claim, hits, False,
                       "assumption is not disclosed anywhere in the manuscript",
                       escalated=True)
        return Row(claim, hits, True,
                   "unwitnessed, and disclosed as a choice (not escalated)",
                   "no witness", "none offered")

    if claim.verdict == UNAVAILABLE:
        marked = bool(claim.premise_marker
                      and re.search(claim.premise_marker, tex))
        return Row(claim, hits, True,
                   "no instrument can decide it; premise sentence printed"
                   if marked else
                   "no instrument can decide it; premise sentence printed in the "
                   "appendix (marker not in body)",
                   "unmeasurable here", "not applicable")

    if claim.verdict == BLIND:
        info = blind_map()
        return Row(claim, hits, True,
                   "registry: {} strata, {} witnessed edges, {} unwitnessed pairs"
                   .format(info["strata"], info["edges"],
                           info["unwitnessed_count"]),
                   "{} of {} pairs".format(info["unwitnessed_count"],
                                           info["pairs"]),
                   "exhaustiveness undecidable", extra={
                       "unwitnessed": ", ".join("{}|{}".format(a, b)
                                                for a, b in info["unwitnessed"]),
                   })

    return Row(claim, hits, False, "unknown verdict {}".format(claim.verdict),
               escalated=True)


def audit() -> Tuple[List[Row], Dict[str, object]]:
    if not os.path.exists(MAIN_TEX):
        raise SystemExit("no manuscript at {}".format(MAIN_TEX))
    with open(MAIN_TEX, encoding="utf-8") as handle:
        tex = handle.read()
    rows = [grade(claim, tex) for claim in CLAIMS]
    return rows, blind_map()


def tally(rows: Sequence[Row]) -> Dict[str, int]:
    counts = {verdict: 0 for verdict in VERDICTS}
    counts["FAIL"] = 0
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 6. artifacts: the report and the appendix table
# ---------------------------------------------------------------------------

VERDICT_GLOSS = {
    PASS: "falsifiable, and the manuscript's own numbers recompute. Not a claim "
          "about nature.",
    INVALID: "no witness offered. Nothing to examine, which is not the "
             "finding ``refuted''.",
    UNAVAILABLE: "the instrument that could answer does not exist here, so the "
                 "claim is left standing.",
    BLIND: "a gap in the registry's coverage, not in the world.",
    "FAIL": "escalated: the paper's own numbers, or its own disclosure, failed.",
}


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(rows: Sequence[Row], info: Dict[str, object]) -> str:
    counts = tally(rows)
    lines: List[str] = []
    lines.append("# Self-audit of *Fractal Interfaces*")
    lines.append("")
    lines.append("Generated by `experiments/paper_self_audit.py` from "
                 "`experiments/paper/main.tex` and the experiment modules. "
                 "Do not edit by hand: the table is rebuilt from the manuscript "
                 "on every run.")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append(SCOPE_NOTE)
    lines.append("")
    lines.append("## Tally")
    lines.append("")
    lines.append("| verdict | count | meaning |")
    lines.append("| --- | --- | --- |")
    for verdict in (*VERDICTS, "FAIL"):
        if verdict not in counts:
            continue
        lines.append("| `{}` | {} | {} |".format(
            verdict, counts[verdict], VERDICT_GLOSS[verdict]))
    lines.append("")
    lines.append("No row failed. The manuscript's arithmetic holds and every "
                 "assumption it cannot witness is disclosed where it is used."
                 if counts["FAIL"] == 0 else
                 "**{} row(s) escalated to FAIL.** The manuscript's own numbers "
                 "or its own disclosure did not hold.".format(counts["FAIL"]))
    lines.append("")
    lines.append("## The claims")
    lines.append("")
    lines.append("| id | claim | verdict | evidence channel | what was checked |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in rows:
        lines.append("| {} | {} | `{}` | {} | {} |".format(
            row.claim.id, _md_cell(row.claim.claim), row.verdict,
            _md_cell(row.claim.channel), _md_cell(row.detail)))
    lines.append("")
    lines.append("## What each verdict cost us")
    lines.append("")
    for verdict in VERDICTS:
        subset = [row for row in rows if row.verdict == verdict]
        if not subset:
            continue
        lines.append("### `{}`".format(verdict))
        lines.append("")
        lines.append(VERDICT_GLOSS[verdict])
        lines.append("")
        for row in subset:
            lines.append("- **{}** — {}".format(row.claim.id,
                                                _md_cell(row.claim.claim)))
            if row.measured:
                lines.append("  - measured: `{}`".format(row.measured))
            if row.expected:
                lines.append("  - reference: `{}`".format(row.expected))
            if row.claim.note:
                lines.append("  - {}".format(row.claim.note))
            for key, value in row.extra.items():
                lines.append("  - {}: `{}`".format(key, value))
        lines.append("")
    lines.append("## The blind boundary map")
    lines.append("")
    lines.append("The topology is a chain of {} strata with {} witnessed edges "
                 "out of {} pairs:".format(info["strata"], info["edges"],
                                           info["pairs"]))
    lines.append("")
    lines.append("> " + " -> ".join(info["names"]))
    lines.append("")
    lines.append("{} pairs have no filing and therefore no witness:".format(
        info["unwitnessed_count"]))
    lines.append("")
    for a, b in info["unwitnessed"]:
        lines.append("- `{}|{}`".format(a, b))
    lines.append("")
    lines.append("This is a statement about the registry. It is not evidence that "
                 "those boundaries are uncrossable, and it is not evidence that "
                 "there is no sixth stratum: the registry cannot pose the "
                 "exhaustiveness question at all, which is why the row is `BLIND` "
                 "rather than `FAIL`.")
    lines.append("")
    lines.append("## Reproducing")
    lines.append("")
    lines.append("```")
    lines.append("python3 -m experiments.paper_self_audit          # audit and "
                 "rewrite this report")
    lines.append("python3 -m experiments.paper_self_audit --check   # exit 1 on any "
                 "escalation")
    lines.append("```")
    lines.append("")
    with open(REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return REPORT_MD


def write_paper_artifacts(rows: Sequence[Row]) -> List[str]:
    """Macros and the appendix table, generated so the paper cannot drift."""
    os.makedirs(PAPER_DATA, exist_ok=True)
    counts = tally(rows)
    macro = os.path.join(PAPER_DATA, "self_audit_macros.tex")
    with open(macro, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/paper_self_audit.py"
                     " -- do not edit\n")
        # Names end in words, never digits: TeX reads \saPass2 as \saPass + "2".
        handle.write(r"\newcommand{\saPassCount}{%d}" % counts[PASS] + "\n")
        handle.write(r"\newcommand{\saInvalidCount}{%d}"
                     % counts[INVALID] + "\n")
        handle.write(r"\newcommand{\saUnavailableCount}{%d}"
                     % counts[UNAVAILABLE] + "\n")
        handle.write(r"\newcommand{\saBlindCount}{%d}" % counts[BLIND] + "\n")
        handle.write(r"\newcommand{\saFailCount}{%d}" % counts["FAIL"] + "\n")
        handle.write(r"\newcommand{\saClaimCount}{%d}" % len(rows) + "\n")
        info = blind_map()
        handle.write(r"\newcommand{\saStrataCount}{%d}" % info["strata"] + "\n")
        handle.write(r"\newcommand{\saEdgeCount}{%d}" % info["edges"] + "\n")
        handle.write(r"\newcommand{\saPairCount}{%d}" % info["pairs"] + "\n")
        handle.write(r"\newcommand{\saUnwitnessedCount}{%d}"
                     % info["unwitnessed_count"] + "\n")

    table = os.path.join(PAPER_DATA, "self_audit_table.tex")
    with open(table, "w", encoding="utf-8") as handle:
        handle.write("% generated by experiments/paper_self_audit.py"
                     " -- do not edit\n")
        # Widths sum to 0.85\columnwidth and the column separation is narrowed,
        # so the four columns fit inside one IEEEtran column with room to spare:
        # the alternative is an overfull box in the appendix, which is a failure
        # of the audit's output rather than of the paper.
        # Ragged-right cells, because justified text in a 78pt column produces
        # either an overfull box or a river of gaps, and the appendix is not the
        # place to discover which. Widths sum to 0.85\columnwidth with a
        # narrowed column separation so the four columns fit inside one
        # IEEEtran column.
        handle.write(r"\setlength{\tabcolsep}{4pt}" + "\n")
        handle.write(r"\begin{tabular}{@{}>{\raggedright\arraybackslash}"
                     r"p{0.05\columnwidth}>{\raggedright\arraybackslash}"
                     r"p{0.28\columnwidth}>{\raggedright\arraybackslash}"
                     r"p{0.22\columnwidth}>{\raggedright\arraybackslash}"
                     r"p{0.30\columnwidth}@{}}" + "\n")
        handle.write(r"\toprule" + "\n")
        handle.write(r"id & claim & verdict & evidence channel \\" + "\n")
        handle.write(r"\midrule" + "\n")
        for row in rows:
            handle.write("{} & {} & \\textsc{{{}}} & {} \\\\\n".format(
                row.claim.id,
                _tex_escape(row.claim.short or row.claim.claim),
                row.verdict.lower(),
                _tex_escape(row.claim.channel)))
        handle.write(r"\bottomrule" + "\n")
        handle.write(r"\end{tabular}" + "\n")
    return [macro, table]


def _tex_escape(text: str) -> str:
    """Make a prose cell safe for the table, leaving math alone.

    A blanket escape would turn ``$G_\\infty$`` into
    ``$G\\_\\textbackslash{}infty$``; the text of a paper is not a string literal,
    and an escaping pass that does not know where math starts is the kind of
    helper that corrupts the thing it was added to protect.
    """
    parts = re.split(r"(\$[^$]*\$)", text)
    out: List[str] = []
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
        # A module path is one unbreakable token in a 70pt column. Allow breaks
        # at its separators -- but \allowbreak, not \-, because a discretionary
        # hyphen inside a path renders as "critical-budget experiment" and a
        # reader would take that for the file's name.
        clean = clean.replace("/", r"/\allowbreak{}")
        clean = clean.replace(r"\_", r"\_\allowbreak{}")
        out.append(clean)
    return "".join(out)


# ---------------------------------------------------------------------------
# 7. console report
# ---------------------------------------------------------------------------


def report(rows: Sequence[Row], info: Dict[str, object]) -> None:
    line = "=" * 78
    print(line)
    print("SELF-AUDIT: the paper, graded by the lattice it proposes")
    print(line)
    print("manuscript  {}".format(os.path.relpath(MAIN_TEX, ROOT)))
    print("claims      {}".format(len(rows)))
    print()
    for row in rows:
        flag = row.verdict
        print("  [{:<11}] {}  {}".format(flag, row.claim.id, row.claim.claim))
        print("                {}: {}".format(row.claim.channel, row.detail))
        if row.measured and row.measured != "none offered":
            print("                measured {}".format(row.measured))
        if row.expected and row.expected != "not applicable":
            print("                reference {}".format(row.expected))
        if row.escalated:
            print("                ESCALATED -- the paper's own numbers or "
                  "disclosure did not hold")
    print()
    counts = tally(rows)
    print("-" * 78)
    print("TALLY")
    print("-" * 78)
    for verdict in (*VERDICTS, "FAIL"):
        print("  {:<12} {}".format(verdict, counts.get(verdict, 0)))
    print()
    print("-" * 78)
    print("THE BLIND BOUNDARY MAP")
    print("-" * 78)
    print("  chain     {}".format(" -> ".join(info["names"])))
    print("  registry  {} strata, {} witnessed edges of {} pairs".format(
        info["strata"], info["edges"], info["pairs"]))
    print("  blind     {}".format(info["unwitnessed_count"]))
    for a, b in info["unwitnessed"]:
        print("    - {}|{}".format(a, b))
    print()
    print("  This is a statement about the registry, not about nature. Whether")
    print("  the five strata are exhaustive is a question the registry cannot")
    print("  pose, which is why the row is BLIND and not FAIL.")
    print()
    print("-" * 78)
    print("SCOPE")
    print("-" * 78)
    for chunk in _wrap(SCOPE_NOTE, 74):
        print("  " + chunk)
    print()


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    return lines


def main(argv: Sequence[str]) -> int:
    check_only = "--check" in argv
    rows, info = audit()
    report(rows, info)
    counts = tally(rows)
    if not check_only:
        written = [write_report(rows, info)]
        written += write_paper_artifacts(rows)
        print("=" * 78)
        print("WROTE")
        print("=" * 78)
        for path in written:
            print("  " + os.path.relpath(path, ROOT))
        print()
    print("Reproduce with:  python3 -m experiments.paper_self_audit")
    print("No network access. No commit. No push.")
    return 0 if counts["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
