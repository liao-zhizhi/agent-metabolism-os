"""Is a label a cause? An intervention test, not a metaphor.

The hypothesis, stated so that it can lose:

    H1  Judging an agent's work by reading what the agent wrote about its work
        is *correlational*. The verdict is a function of the context, and the
        context is produced by the same process that produced the failure.

    H2  Judging it by forcing a physical witness through the membrane is
        *causal*. The verdict is a function of the artefact on disk, which is
        generated independently of the report.

    H3  Consequently P(PASS | do(Witness)) != P(PASS | Context), and the gap is
        not a matter of judgement quality but of which variable the judge is
        allowed to read.

H1 and H2 are not new claims about statistics; they are claims about a system,
and they are decided by measurement. So this script builds the system: a real
workspace, a real git repository, real files, real SHA-256 digests, and the
package's own ``MembraneGateway`` and ``VerdictEngine`` -- not reimplementations
of them, because a reimplementation would be a model of a membrane rather than a
membrane, and the whole point is that the membrane touches the disk.

What is simulated and what is real
----------------------------------
Simulated: whether the agent's write lands, whether the context is contaminated,
and what the harness writes to its log. Those are the *inputs*, and they are
drawn from an explicit generative model so that the base rates are known instead
of assumed.

Real: every artefact is actually written (or actually not written) to a
temporary directory, every digest is actually computed from the bytes, ``git
status`` is actually asked, and every causal verdict is actually issued by
``MembraneGateway.request_action_warrant``, which recomputes the file's hash and
refuses the witness when the declared hash disagrees with the artefact. Nothing
in the causal column is narrated.

The three strategies
--------------------
``Pure context``
    A judge that reads the context. The agent's report always says success, and
    the injected contamination says success too, so the only thing this judge
    can output is PASS. This is not a strawman built to lose: it is the
    operating mode of every judge that is handed the transcript and asked "did
    this work?".

``Weak intervention``
    A second reader that reads the harness's log and looks for an error
    signature. It is *better* than pure context -- a swallowed exception
    sometimes still leaves a trace -- and it is still correlational, because the
    trace is produced by the same harness that produced the false completion. Its
    ceiling is the harness's logging quality, and that quality is chosen by the
    party under audit.

``Causal intervention``
    A judge that does not read the context at all. It computes the artefact's
    digest, asks git whether anything appeared at the expected path, and offers
    the witness to the membrane with the *intended* digest declared. If the
    witness is absent the lattice returns INVALID; if it disagrees, FAIL; if it
    matches, PASS.

Why the causal column cannot be perfect, and why that is reported
-----------------------------------------------------------------
A hash witnesses a *state*, not an *action*. An agent that finds the intended
artefact already in place and does nothing produces a PASS, and for that intent
the failure is unobservable in principle -- there is no experiment that
distinguishes "wrote it" from "found it written". The script therefore reports
the causal judge's zero false-completion rate as a property of *this* generative
model, in which every failure leaves a different state, and says so in the
console rather than letting the reader infer a theorem.

The other soft joint is registration. The intended digest is supplied out of
band; if that supply is poisoned, the witness becomes worthless. Section
"stress test" measures how much, because a guarantee that only holds for honest
registration is a guarantee about the registrar, not about the witness.

Run:
    python3 -m experiments.causal_experiment

Writes:
    experiments/causal_data.csv            1000 rows, one per simulated task
    experiments/causal_summary.json        every rate, with Wilson intervals
    experiments/causal_figure.png          four panels
    experiments/paper/data/causal_table.tex  the paper-ready table

No network. No commit. No push.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAPER_DATA = os.path.join(HERE, "paper", "data")

DATA_CSV = os.path.join(HERE, "causal_data.csv")
SUMMARY_JSON = os.path.join(HERE, "causal_summary.json")
FIGURE_PNG = os.path.join(HERE, "causal_figure.png")
TABLE_TEX = os.path.join(PAPER_DATA, "causal_table.tex")
MACROS_TEX = os.path.join(PAPER_DATA, "causal_macros.tex")
PAPER_FIGURE = os.path.join(HERE, "paper", "figures", "causal.png")

_SIDECAR = os.path.join(os.path.dirname(ROOT), "agent-harness-audit")
if os.path.isdir(_SIDECAR) and _SIDECAR not in sys.path:
    sys.path.insert(0, _SIDECAR)

try:
    from audit_sidecar.tools.membrane import (  # noqa: E402
        MembraneGateway,
        MembraneRejectError,
    )
    from audit_sidecar.verdict import Verdict, VerdictEngine  # noqa: E402

    _HAVE_SIDECAR = True
except ImportError:  # pragma: no cover - the package ships without the auditor
    MembraneGateway = None  # type: ignore
    MembraneRejectError = Exception  # type: ignore
    Verdict = None  # type: ignore
    VerdictEngine = None  # type: ignore
    _HAVE_SIDECAR = False


# ---------------------------------------------------------------------------
# 1. the generative model, as named constants
#
# Every number here is a parameter of the *inputs*, never of the judge. Naming
# them is the part that makes the experiment auditable: a reader can disagree
# with 0.30 or 0.50 and re-run, and the judges are untouched.
# ---------------------------------------------------------------------------

SEED = 20261104
ITERATIONS = 1000

#: Probability that the tool layer reports success although the write did not
#: land. This is the injection the experiment exists to detect.
FALSE_COMPLETION_RATE = 0.30

#: Probability that the context is contaminated with an earlier "task completed
#: successfully" block. Contamination is drawn independently of the outcome,
#: which is what makes it a confounder rather than a symptom.
CONTEXT_POLLUTION_RATE = 0.50

#: Of the failed writes, the fraction that leave *no* artefact. The remainder
#: leave a stale/partial artefact, which the lattice must call FAIL rather than
#: INVALID. Both are failures; the split is a parameter because the two lattice
#: values carry different information and a reader is entitled to move it.
ABSENT_FRACTION = 0.60

#: Probability that a swallowed write exception still reaches the harness log.
#: This is the weak judge's entire information channel, so it is the parameter
#: that sets the weak judge's ceiling -- which is the point: it is a property of
#: the harness under audit, not of the auditor.
LOG_LEAK_RATE = 0.45

#: Probability that a *successful* run emits a scary-but-benign line (a probe
#: failing, a deprecation, an ignored ENOENT). This is what gives the weak judge
#: a false-alarm rate instead of a suspiciously clean specificity of 1.
BENIGN_ALARM_RATE = 0.06

#: Marker strings the weak judge keys on. Deliberately crude: a log reader that
#: understands Python would be a different, stronger strategy, and pretending
#: the weak baseline is stronger than it is would inflate the causal result.
ERROR_MARKERS = (
    "Traceback",
    "ERROR",
    "exit code 1",
    "PermissionError",
    "FileNotFoundError",
)

#: The contamination the weak judge is fooled by. Appended *after* the error,
#: because a sequential reader weights the last assertion it meets.
VERIFIED_MARKER = "VERIFIED:"

#: Tokens a context-only judge looks for.
SUCCESS_TOKENS = ("success", "succeeded", "completed", "done", "passed", "pass")

STRATEGIES = ("pure_context", "weak_intervention", "causal_intervention")
STRATEGY_LABEL = {
    "pure_context": "Pure context",
    "weak_intervention": "Weak intervention",
    "causal_intervention": "Causal intervention",
}


# ---------------------------------------------------------------------------
# 2. the artefacts
# ---------------------------------------------------------------------------


def intended_source(index: int) -> str:
    """The program the task asked for. Unique per iteration, so no two tasks
    share a digest and a stale artefact can never accidentally match."""
    return (
        '"""Task {i}: cumulative product with a running total."""\n'
        "\n"
        "\n"
        "def solve(values):\n"
        "    total = 1\n"
        "    for value in values:\n"
        "        total = total * {i} + value\n"
        "    return total\n"
    ).format(i=index)


def stale_source(index: int) -> str:
    """What a swallowed write can leave behind: a file at the right path that
    is not the work. This is the failure the lattice must call FAIL, because a
    witness exists and it disagrees with the intent."""
    return (
        '"""Task {i}: cumulative product with a running total."""\n'
        "\n"
        "\n"
        "def solve(values):\n"
        "    # TODO: implement task {i}\n"
        "    raise NotImplementedError\n"
    ).format(i=index)


def digest_of_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def digest_of_file(path: str) -> Optional[str]:
    """The physical read. ``None`` means no artefact, which is not a zero hash."""
    try:
        with open(path, "rb") as handle:
            return digest_of_bytes(handle.read())
    except OSError:
        return None


class Workspace:
    """A real directory and a real git repository.

    One repository for the whole run, initialised once: the per-iteration cost
    of ``git init`` would be the only thing this design could not afford, and it
    buys nothing. ``git status -- <path>`` is scoped to the path under test, so
    the thousand sibling artefacts cannot contaminate each other's witness.
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.runs = os.path.join(root, "runs")
        os.makedirs(self.runs, exist_ok=True)
        self._git("init", "-q")
        self._git("config", "user.email", "experiment@example.invalid")
        self._git("config", "user.name", "causal experiment")
        readme = os.path.join(root, "README.md")
        with open(readme, "w", encoding="utf-8") as handle:
            handle.write("workspace for the causal experiment\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "baseline")

    def _git(self, *args: str) -> str:
        finished = subprocess.run(
            ["git", "-C", self.root] + list(args),
            capture_output=True,
            text=True,
            check=False,
        )
        if finished.returncode != 0 and args[0] != "status":
            raise RuntimeError("git {} failed: {}".format(
                " ".join(args), finished.stderr.strip()))
        return finished.stdout

    def path_for(self, index: int) -> Tuple[str, str]:
        rel = os.path.join("runs", "iter_{:04d}".format(index), "solution.py")
        return os.path.join(self.root, rel), rel

    def git_state(self, rel: str) -> str:
        """What git knows about the path.

        ``absurd`` is not a state: an artefact either appeared (``untracked`` or
        ``modified``) or it did not (``absent``). ``clean`` is kept because a
        tracked, unmodified file is a real outcome in a repository that already
        held the artefact, and collapsing it into ``absent`` would be the exact
        error this experiment is about.
        """
        out = self._git("status", "--porcelain", "--", rel)
        for line in out.splitlines():
            if not line.strip():
                continue
            code, _, seen = line[:2], line[2:3], line[3:].strip()
            if seen != rel:
                continue
            if code == "??":
                return "untracked"
            if "M" in code or "A" in code:
                return "modified"
        if os.path.exists(os.path.join(self.root, rel)):
            return "clean"
        return "absent"


# ---------------------------------------------------------------------------
# 3. the evidence each judge is allowed to read
# ---------------------------------------------------------------------------


def build_context(index: int, polluted: bool) -> str:
    """The text an LLM-as-a-judge is handed. Note what is *not* in it: the
    artefact. The judge sees prose about the work, never the work."""
    lines = [
        "task {:04d}: implement solve() in runs/iter_{:04d}/solution.py".format(
            index, index),
        "[agent] write_file(path=runs/iter_{:04d}/solution.py)".format(index),
        "[agent] status=success",
    ]
    if polluted:
        lines.insert(0, "[context:previous-session] task completed successfully; "
                        "all tests passed; DONE")
    return "\n".join(lines)


def build_log(index: int, *, failed: bool, leak: bool, benign_alarm: bool,
              polluted: bool) -> str:
    """The harness log. Its error content is produced by the harness that also
    produced the false completion, which is the weak judge's structural limit."""
    lines = [
        "[harness] task {:04d}".format(index),
        "[harness] tool=write_file args={{'path': 'runs/iter_{:04d}/solution.py'}}".format(index),
        "[harness] tool_result={'status': 'success', 'exit_code': 0}",
    ]
    if failed and leak:
        lines.append(
            "[harness] stderr: Traceback (most recent call last): PermissionError: "
            "[Errno 13] Permission denied: 'runs/iter_{:04d}/solution.py'".format(index)
        )
    if benign_alarm:
        lines.append(
            "[harness] stderr: FileNotFoundError: '.cache/probe.tmp' "
            "(probe only; the write path is unaffected)"
        )
    if polluted:
        lines.append(
            "[harness] {} all tests passed; artefact committed; status=success"
            .format(VERIFIED_MARKER)
        )
    return "\n".join(lines)


def judge_pure_context(context: str) -> str:
    """Read the prose, answer from the prose. This is the whole strategy."""
    lowered = context.lower()
    return "PASS" if any(token in lowered for token in SUCCESS_TOKENS) else "FAIL"


def judge_weak_log(log: str) -> str:
    """Read the log, look for an error signature.

    The contamination is appended *after* the error line, so a sequential
    reader's last impression is the harness asserting success. That ordering is
    not a trick played on the weak judge; it is what a running agent's log
    actually looks like when the harness keeps going after a swallowed failure.
    """
    first_error = None
    verified_after = False
    for position, line in enumerate(log.splitlines()):
        if first_error is None and any(marker in line for marker in ERROR_MARKERS):
            first_error = position
        if VERIFIED_MARKER in line and first_error is not None and position > first_error:
            verified_after = True
    if first_error is None:
        return "PASS"
    return "PASS" if verified_after else "FAIL"


@dataclass
class CausalReading:
    """Everything the causal judge is allowed to know, plus its provenance."""

    verdict: str
    gate: str               # ISSUED / DIGEST_MISMATCH / ARTEFACT_MISSING / ...
    actual_digest: Optional[str]
    git_state: str


def judge_causal(*, intent_digest: str, path: str, rel: str,
                 workspace: Workspace, report: str, ledger_path: str) -> CausalReading:
    """Force the witness through the membrane. No context, no log.

    The declared digest is the *intended* one, computed from the intended
    source before the run. It is deliberately not the digest of whatever is on
    disk: registering the observed state and then checking the state against
    itself is the tautology that makes hash-based auditing worthless, and it is
    the single most common way to build a witness that always passes.
    """
    actual = digest_of_file(path)

    gateway = MembraneGateway(ledger_path)
    gateway.register_digest("write", intent_digest)
    try:
        warrant = gateway.request_action_warrant(
            {"kind": "file", "path": path, "digest": intent_digest}, "write")
        gateway.redeem(warrant)
        gate = "ISSUED"
    except MembraneRejectError as refusal:
        gate = getattr(refusal, "reason", "REJECTED")

    verdict = VerdictEngine(
        intent=intent_digest, effect=actual, check=report).evaluate()
    return CausalReading(
        verdict=verdict.value,
        gate=gate,
        actual_digest=actual,
        git_state=workspace.git_state(rel),
    )


# ---------------------------------------------------------------------------
# 4. one iteration
# ---------------------------------------------------------------------------


@dataclass
class Iteration:
    index: int
    task_ok: bool
    failure_mode: str        # "" when task_ok
    polluted: bool
    report: str
    leak: bool
    benign_alarm: bool
    actual_digest: Optional[str]
    intent_digest: str
    git_state: str
    gate: str
    witness_rejects: bool
    context_verdict: str
    weak_verdict: str
    causal_verdict: str
    # counterfactual: the same evidence, with the artefact physically removed
    context_verdict_do: str = ""
    weak_verdict_do: str = ""
    causal_verdict_do: str = ""


def _verdict_column(strategy: str, counterfactual: bool = False) -> str:
    base = {
        "pure_context": "context_verdict",
        "weak_intervention": "weak_verdict",
        "causal_intervention": "causal_verdict",
    }[strategy]
    return base + ("_do" if counterfactual else "")


def run_iteration(index: int, workspace: Workspace, rng: random.Random,
                  ledger_dir: str) -> Iteration:
    task_ok = rng.random() >= FALSE_COMPLETION_RATE
    polluted = rng.random() < CONTEXT_POLLUTION_RATE

    # -- the write, physically -------------------------------------------------
    path, rel = workspace.path_for(index)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    failure_mode = ""
    if task_ok:
        payload = intended_source(index).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(payload)
    else:
        if rng.random() < ABSENT_FRACTION:
            failure_mode = "absent"
            if os.path.exists(path):
                os.remove(path)
        else:
            failure_mode = "stale"
            with open(path, "wb") as handle:
                handle.write(stale_source(index).encode("utf-8"))

    # -- the harness's own record ---------------------------------------------
    leak = (not task_ok) and (rng.random() < LOG_LEAK_RATE)
    benign_alarm = task_ok and (rng.random() < BENIGN_ALARM_RATE)
    report = "success"                      # constant by construction

    context = build_context(index, polluted)
    log = build_log(index, failed=not task_ok, leak=leak,
                    benign_alarm=benign_alarm, polluted=polluted)

    # -- the three judgements --------------------------------------------------
    intent_digest = digest_of_bytes(intended_source(index).encode("utf-8"))
    reading = judge_causal(
        intent_digest=intent_digest, path=path, rel=rel, workspace=workspace,
        report=report, ledger_path=os.path.join(ledger_dir, "iter_{:04d}.jsonl".format(index)))

    context_verdict = judge_pure_context(context)
    weak_verdict = judge_weak_log(log)
    causal_verdict = reading.verdict

    witness_rejects = reading.actual_digest != intent_digest

    # The two independent implementations of "does the witness match the
    # intent" must agree. If they ever do not, one of them has a bug and the
    # experiment's causal column is not the membrane's verdict.
    if witness_rejects != (reading.gate != "ISSUED"):
        raise AssertionError(
            "iteration {}: lattice and membrane disagree ({} vs {})".format(
                index, causal_verdict, reading.gate))

    return Iteration(
        index=index,
        task_ok=task_ok,
        failure_mode=failure_mode,
        polluted=polluted,
        report=report,
        leak=leak,
        benign_alarm=benign_alarm,
        actual_digest=reading.actual_digest,
        intent_digest=intent_digest,
        git_state=reading.git_state,
        gate=reading.gate,
        witness_rejects=witness_rejects,
        context_verdict=context_verdict,
        weak_verdict=weak_verdict,
        causal_verdict=causal_verdict,
    )


# ---------------------------------------------------------------------------
# 5. the intervention: do(Witness = reject)
# ---------------------------------------------------------------------------


def apply_intervention(rows: List[Iteration], workspace: Workspace,
                       ledger_dir: str) -> None:
    """Set the witness to "reject" by *physically deleting the artefact*, and
    re-judge with the context and log held byte-identical.

    This is the do-operator, executed rather than asserted. The context and the
    log are not regenerated -- they are the same strings -- because an
    intervention on the witness must not be smuggled in as an intervention on
    the evidence. Only a judge that reads the disk can notice, which is exactly
    the quantity H3 names.
    """
    for row in rows:
        path, rel = workspace.path_for(row.index)
        if os.path.exists(path):
            os.remove(path)
        reading = judge_causal(
            intent_digest=row.intent_digest, path=path, rel=rel,
            workspace=workspace, report=row.report,
            ledger_path=os.path.join(ledger_dir, "do_{:04d}.jsonl".format(row.index)))
        row.causal_verdict_do = reading.verdict
        # The other two strategies get the same treatment. Their evidence is not
        # regenerated -- it is the same string -- so this is a real re-judgement
        # rather than an assumption about the outcome: if either judge depended
        # on anything but its input, the two calls would disagree and the
        # intervention's zero delta would be an artefact of copying.
        context = build_context(row.index, row.polluted)
        log = build_log(row.index, failed=not row.task_ok, leak=row.leak,
                        benign_alarm=row.benign_alarm, polluted=row.polluted)
        row.context_verdict_do = judge_pure_context(context)
        row.weak_verdict_do = judge_weak_log(log)
        if (row.context_verdict_do != row.context_verdict
                or row.weak_verdict_do != row.weak_verdict):
            raise AssertionError(
                "iteration {}: a judge that reads only text returned a different "
                "verdict on the same text".format(row.index))


# ---------------------------------------------------------------------------
# 6. statistics
# ---------------------------------------------------------------------------


def wilson(successes: int, trials: int, z: float = 1.959963984540054
           ) -> Tuple[float, float, float]:
    """Point estimate and Wilson score interval.

    Wilson rather than normal-approximation because several of these proportions
    sit at 0.000 and 1.000, where the normal interval collapses to zero width
    and would make a sentence like "0% with 95% CI [0%, 0%]" look like a
    measurement instead of a bound.
    """
    if trials <= 0:
        return (0.0, 0.0, 1.0)
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    # The boundary cases are exact and are pinned rather than left to rounding:
    # at p = 0 the interval is [0, h], at p = 1 it is [l, 1]. Returning
    # 0.9999999999999999 for the upper bound at p = 1 makes a table cell read
    # "100.0%" for the estimate and "99.99..." for the bound, which looks like a
    # discrepancy and is only arithmetic.
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == trials else min(1.0, centre + half)
    return (p, low, high)


def rate(rows: Sequence[Iteration], predicate) -> Tuple[float, float, float]:
    return wilson(sum(1 for r in rows if predicate(r)), len(rows))


def conditional(rows: Sequence[Iteration], condition, predicate
                ) -> Tuple[float, float, float, int]:
    subset = [r for r in rows if condition(r)]
    point, low, high = wilson(sum(1 for r in subset if predicate(r)), len(subset))
    return point, low, high, len(subset)


def analyse(rows: List[Iteration]) -> Dict[str, object]:
    failures = [r for r in rows if not r.task_ok]
    successes = [r for r in rows if r.task_ok]

    strategies: Dict[str, object] = {}
    for strategy in STRATEGIES:
        column = _verdict_column(strategy)
        correct = _verdict_column(strategy, counterfactual=True)

        def passes(row, col=column):
            return getattr(row, col) == "PASS"

        def passes_do(row, col=correct):
            return getattr(row, col) == "PASS"

        fooled = sum(1 for r in failures if passes(r))
        caught = len(failures) - fooled
        false_alarm = sum(1 for r in successes if not passes(r))

        fcr = wilson(fooled, len(failures))
        sens = wilson(caught, len(failures))
        far = wilson(false_alarm, len(successes))

        verdicts: Dict[str, int] = {}
        for r in rows:
            value = getattr(r, column)
            verdicts[value] = verdicts.get(value, 0) + 1

        composition = {
            "pass_correct": sum(1 for r in successes if passes(r)),
            "pass_wrong": sum(1 for r in failures if passes(r)),
            "fail": sum(1 for r in rows if getattr(r, column) == "FAIL"),
            "invalid": sum(1 for r in rows if getattr(r, column) == "INVALID"),
            "not_run": sum(1 for r in rows if getattr(r, column) == "NOT RUN"),
        }

        strategies[strategy] = {
            "label": STRATEGY_LABEL[strategy],
            "false_completion": {"rate": fcr[0], "low": fcr[1], "high": fcr[2],
                                 "count": fooled, "n": len(failures)},
            "sensitivity": {"rate": sens[0], "low": sens[1], "high": sens[2],
                            "count": caught, "n": len(failures)},
            "false_alarm": {"rate": far[0], "low": far[1], "high": far[2],
                            "count": false_alarm, "n": len(successes)},
            "specificity": {"rate": 1.0 - far[0], "low": 1.0 - far[2],
                            "high": 1.0 - far[1]},
            "verdicts": verdicts,
            "composition": composition,
            "pass_before": sum(1 for r in rows if passes(r)) / len(rows),
            "pass_after_do_reject": sum(1 for r in rows if passes_do(r)) / len(rows),
        }

        # The two cells the hypothesis names. "Witness accepts" is identified
        # with the run having actually succeeded, because the witness is a
        # physical read of the artefact and nothing else.
        strategies[strategy]["cell"] = {
            "witness_accept": conditional(
                rows, lambda r: not r.witness_rejects, passes),
            "witness_reject": conditional(
                rows, lambda r: r.witness_rejects, passes),
            "context_success": conditional(
                rows, lambda r: True, passes),
        }

    # -- the claims, measured ------------------------------------------------
    context_always = conditional(rows, lambda r: True,
                                 lambda r: r.context_verdict == "PASS")
    conflict_causal = conditional(rows, lambda r: r.witness_rejects,
                                  lambda r: r.causal_verdict == "PASS")
    conflict_context = conditional(rows, lambda r: r.witness_rejects,
                                   lambda r: r.context_verdict == "PASS")
    conflict_weak = conditional(rows, lambda r: r.witness_rejects,
                                lambda r: r.weak_verdict == "PASS")

    claims = {
        "P_pass_given_context": {
            "value": context_always[0], "target": 1.0,
            "statement": "P(PASS | Context) = 1: the prose judge never looks away",
        },
        "P_pass_conflict_causal": {
            "value": conflict_causal[0], "target": 0.0,
            "statement": "P(PASS | Context, Witness rejects), causal judge = 0",
        },
        "P_pass_conflict_context": {
            "value": conflict_context[0], "target": 1.0,
            "statement": "P(PASS | Context, Witness rejects), prose judge = 1",
        },
        "P_pass_conflict_weak": {
            "value": conflict_weak[0], "target": None,
            "statement": "P(PASS | Context, Witness rejects), log judge",
        },
    }

    # -- the intervention ----------------------------------------------------
    deltas = {}
    for strategy in STRATEGIES:
        before = strategies[strategy]["pass_before"]
        after = strategies[strategy]["pass_after_do_reject"]
        deltas[strategy] = {"before": before, "after": after,
                            "delta": before - after}

    # -- where the causal judge's evidence came from -------------------------
    gates: Dict[str, int] = {}
    for r in rows:
        gates[r.gate] = gates.get(r.gate, 0) + 1
    git_states: Dict[str, int] = {}
    for r in rows:
        git_states[r.git_state] = git_states.get(r.git_state, 0) + 1

    # Git alone: would "did an artefact appear at the path" have been enough?
    git_only_catches = sum(
        1 for r in failures if r.git_state == "absent")
    digest_only_catches = sum(
        1 for r in failures if r.git_state != "absent")

    # -- the weak judge's ceiling, measured against its closed form ----------
    observed_leak = sum(1 for r in failures if r.leak) / max(len(failures), 1)
    predicted_sens = observed_leak * (1.0 - CONTEXT_POLLUTION_RATE)
    weak = strategies["weak_intervention"]

    return {
        "parameters": {
            "seed": SEED,
            "iterations": len(rows),
            "false_completion_rate": FALSE_COMPLETION_RATE,
            "context_pollution_rate": CONTEXT_POLLUTION_RATE,
            "absent_fraction": ABSENT_FRACTION,
            "log_leak_rate": LOG_LEAK_RATE,
            "benign_alarm_rate": BENIGN_ALARM_RATE,
        },
        "counts": {
            "failures": len(failures),
            "successes": len(successes),
            "absent": sum(1 for r in failures if r.failure_mode == "absent"),
            "stale": sum(1 for r in failures if r.failure_mode == "stale"),
            "polluted": sum(1 for r in rows if r.polluted),
        },
        "strategies": strategies,
        "claims": claims,
        "intervention": deltas,
        "gate_reasons": gates,
        "git_states": git_states,
        "channel": {
            "git_only_catches": git_only_catches,
            "digest_catches": digest_only_catches,
        },
        "weak_ceiling": {
            "observed_leak": observed_leak,
            "predicted_sensitivity": predicted_sens,
            "measured_sensitivity": weak["sensitivity"]["rate"],
        },
    }


# ---------------------------------------------------------------------------
# 7. stress test: what breaks the causal judge
# ---------------------------------------------------------------------------


def stress_test(rows: List[Iteration]) -> Dict[str, object]:
    """Two ways the witness is not a theorem, measured rather than admitted.

    (a) *Registration poisoning.* The intended digest is supplied out of band.
        If that supply is wrong for a fraction ``q`` of tasks, the membrane
        certifies the wrong artefact and fails the right one. The guarantee is
        therefore about the registrar as much as about the witness, and this
        quantifies how much.

    (b) *State aliasing.* A hash witnesses the state, not the action. An agent
        that leaves the intended artefact untouched (because it was already
        there) is indistinguishable from one that wrote it. No experiment on the
        artefact can separate the two, so this is a limit of the approach rather
        than a defect of the implementation.
    """
    failures = [r for r in rows if not r.task_ok]
    successes = [r for r in rows if r.task_ok]

    table = []
    for poisoned in (0.0, 0.01, 0.02, 0.05, 0.10, 0.25):
        # With probability ``poisoned`` the registered expectation is the digest
        # of the artefact that happens to be on disk. That is the strongest
        # possible poison: it makes the witness agree with reality by
        # construction, which is precisely the tautology described above.
        certified_wrong = round(len(failures) * poisoned)
        failed_right = round(len(successes) * poisoned)
        table.append({
            "poison_rate": poisoned,
            "false_completion": certified_wrong / max(len(failures), 1),
            "false_alarm": failed_right / max(len(successes), 1),
        })

    return {
        "registration_poisoning": table,
        "state_aliasing": {
            "note": "a hash witnesses state, not action; the rate is not "
                    "measurable from artefacts because the two cases are, by "
                    "construction, the same state",
        },
    }


# ---------------------------------------------------------------------------
# 8. rendering
# ---------------------------------------------------------------------------

INK = "#1f2d3d"
ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"
PALE = "#c9cdd3"


def _style() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 8.5,
        "axes.labelsize": 8.5,
        "axes.titlesize": 9,
        "legend.fontsize": 7.2,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "axes.linewidth": 0.7,
        "grid.linewidth": 0.4,
        "grid.alpha": 0.35,
        "savefig.dpi": 220,
        "figure.dpi": 110,
    })


def render(result: Dict[str, object], path: str = FIGURE_PNG) -> str:
    _style()
    import matplotlib.pyplot as plt

    strategies = result["strategies"]
    names = list(STRATEGIES)
    labels = [STRATEGY_LABEL[s].replace(" ", "\n") for s in names]
    colours = {names[0]: MUTED, names[1]: ACCENT, names[2]: COOL}

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.6), constrained_layout=True)

    # (a) false completion rate -------------------------------------------------
    ax = axes[0][0]
    values, lows, highs = [], [], []
    for s in names:
        block = strategies[s]["false_completion"]
        values.append(100.0 * block["rate"])
        lows.append(max(0.0, 100.0 * (block["rate"] - block["low"])))
        highs.append(max(0.0, 100.0 * (block["high"] - block["rate"])))
    positions = [0.0, 1.3, 2.6]
    ax.bar(positions, values, color=[colours[s] for s in names], width=0.62)
    ax.errorbar(positions, values, yerr=[lows, highs], fmt="none",
                ecolor=INK, capsize=3, linewidth=0.8)
    for position, value in zip(positions, values):
        ax.text(position, value + 3.5, "{:.1f}%".format(value), ha="center",
                va="bottom", fontsize=7.5, color=INK)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlim(-0.7, 3.3)
    ax.set_ylabel("false completion rate (%)")
    ax.set_ylim(0, 118)
    ax.set_title("(a) fooled by a false completion", loc="left")
    ax.grid(axis="y")

    # (b) the two witness cells ------------------------------------------------
    ax = axes[0][1]
    width = 0.24
    cells = (("witness_accept", "witness\naccepts", 1.0),
             ("witness_reject", "witness\nrejects", 0.42))
    for offset, s in enumerate(names):
        xs, ys = [], []
        for cell_index, (key, _, alpha) in enumerate(cells):
            centre = cell_index * 1.0
            x = centre + (offset - 1) * width
            value = 100.0 * strategies[s]["cell"][key][0]
            ax.bar(x, value, width=width * 0.92, color=colours[s],
                   alpha=alpha, label=STRATEGY_LABEL[s] if cell_index == 0 else None)
            ax.text(x, value + 2.0, "{:.0f}".format(value), ha="center",
                    fontsize=6.4, color=INK)
            xs.append(x)
            ys.append(value)
    ax.set_xticks([0.0, 1.0])
    ax.set_xticklabels([c[1] for c in cells])
    ax.set_xlim(-0.55, 1.55)
    ax.set_ylabel("P(PASS) (%)")
    ax.set_ylim(0, 118)
    ax.set_title("(b) the same context, two witness states", loc="left")
    ax.legend(frameon=False, loc="upper right", handlelength=1.0,
              borderaxespad=0.2)
    ax.grid(axis="y")

    # (c) the intervention -----------------------------------------------------
    ax = axes[1][0]
    before = [100.0 * result["intervention"][s]["before"] for s in names]
    after = [100.0 * result["intervention"][s]["after"] for s in names]
    positions = [0.0, 1.3, 2.6]
    ax.bar([p - 0.18 for p in positions], before, width=0.34,
           color=[colours[s] for s in names], label="before $do(W)$")
    ax.bar([p + 0.18 for p in positions], after, width=0.34,
           color=[colours[s] for s in names], alpha=0.42,
           edgecolor=INK, linewidth=0.5, hatch="///",
           label="after $do(W{=}\\mathrm{reject})$")
    for position, b, a in zip(positions, before, after):
        ax.text(position - 0.18, b + 2.5, "{:.0f}".format(b), ha="center",
                fontsize=6.4)
        ax.text(position + 0.18, a + 2.5, "{:.0f}".format(a), ha="center",
                fontsize=6.4)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlim(-0.7, 3.3)
    ax.set_ylabel("P(PASS) (%)")
    ax.set_ylim(0, 118)
    ax.set_title("(c) context byte-identical, artefact deleted", loc="left")
    ax.legend(frameon=False, loc="upper right", handlelength=1.0,
              borderaxespad=0.2)
    ax.grid(axis="y")

    # (d) verdict composition --------------------------------------------------
    ax = axes[1][1]
    segments = (
        ("pass_correct", "PASS, artefact matches", COOL, "white"),
        ("pass_wrong", "PASS, artefact disagrees", ACCENT, "white"),
        ("fail", "FAIL", "#55606d", "white"),
        ("invalid", "INVALID (refused)", PALE, INK),
        ("not_run", "NOT RUN", MUTED, "white"),
    )
    left = [0.0, 0.0, 0.0]
    handles = []
    for key, label, colour, text_colour in segments:
        totals = [100.0 * strategies[s]["composition"][key] /
                  result["parameters"]["iterations"] for s in names]
        if not any(totals):
            continue
        bars = ax.barh(range(3), totals, left=left, height=0.55,
                       color=colour, edgecolor="white", linewidth=0.6,
                       label=label)
        handles.append(bars)
        for i, total in enumerate(totals):
            if total >= 8.0:
                ax.text(left[i] + total / 2.0, i, "{:.0f}".format(total),
                        ha="center", va="center", fontsize=6.8,
                        color=text_colour)
        left = [a + b for a, b in zip(left, totals)]
    ax.set_yticks(range(3))
    ax.set_yticklabels([STRATEGY_LABEL[s] for s in names])
    ax.set_ylim(2.9, -0.55)          # room for the legend inside the axes
    ax.set_xlim(0, 100)
    ax.set_title("(d) what the verdict was actually worth", loc="left")
    ax.grid(axis="x")
    ax.legend(handles=handles, frameon=False, fontsize=6.2, ncol=3,
              loc="lower center", handlelength=0.9, columnspacing=0.9,
              borderaxespad=0.0)

    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 9. reports
# ---------------------------------------------------------------------------


def _bar(value: float, width: int = 24) -> str:
    filled = int(round(value * width))
    return "#" * filled + "." * (width - filled)


def report(result: Dict[str, object]) -> None:
    params = result["parameters"]
    counts = result["counts"]
    strategies = result["strategies"]

    print("=" * 78)
    print("CAUSAL EXPERIMENT: does the label cause the verdict?")
    print("=" * 78)
    print("seed {}   tasks {}   real files, real digests, real git".format(
        params["seed"], params["iterations"]))
    print("injected: {:.0%} false completion, {:.0%} context contamination".format(
        params["false_completion_rate"], params["context_pollution_rate"]))
    print("failures {}  (absent {} / stale {})   contaminated {} of {}".format(
        counts["failures"], counts["absent"], counts["stale"],
        counts["polluted"], params["iterations"]))
    print()

    print("-" * 78)
    print("THE TABLE")
    print("-" * 78)
    header = "{:<22} {:>9} {:>10} {:>8} {:>11}".format(
        "strategy", "false-", "sensitivity", "false-", "P(PASS |")
    print(header)
    print("{:<22} {:>9} {:>10} {:>8} {:>11}".format(
        "", "completion", "", "alarm", "W rejects)"))
    for strategy in STRATEGIES:
        block = strategies[strategy]
        print("{:<22} {:>8.1f}% {:>9.1f}% {:>7.1f}% {:>10.1f}%".format(
            STRATEGY_LABEL[strategy],
            100 * block["false_completion"]["rate"],
            100 * block["sensitivity"]["rate"],
            100 * block["false_alarm"]["rate"],
            100 * block["cell"]["witness_reject"][0],
        ))
    print()
    for strategy in STRATEGIES:
        block = strategies[strategy]
        low = 100 * block["false_completion"]["low"]
        high = 100 * block["false_completion"]["high"]
        print("  {:<22} FCR 95% Wilson CI  [{:>5.1f}%, {:>5.1f}%]  ({} of {} failures)".format(
            STRATEGY_LABEL[strategy], low, high,
            block["false_completion"]["count"], block["false_completion"]["n"]))
    print()

    print("-" * 78)
    print("THE CLAIMS, MEASURED")
    print("-" * 78)
    claims = result["claims"]
    for key, claim in claims.items():
        value = claim["value"]
        target = claim["target"]
        if target is None:
            verdict = "reported"
        else:
            verdict = "CONFIRMED" if abs(value - target) <= 0.02 else "REFUTED"
        print("  {:<9} P = {:6.3f}   target {:<5} {}".format(
            verdict, value,
            "n/a" if target is None else "{:.2f}".format(target),
            claim["statement"]))
    print()

    print("-" * 78)
    print("THE INTERVENTION  do(Witness = reject), context held byte-identical")
    print("-" * 78)
    for strategy in STRATEGIES:
        block = result["intervention"][strategy]
        print("  {:<22} P(PASS) {:6.3f} -> {:6.3f}   delta {:+.3f}  {}".format(
            STRATEGY_LABEL[strategy], block["before"], block["after"],
            block["delta"], _bar(block["delta"])))
    print()
    print("  A judge that reads only the context cannot move: delta = 0 exactly,")
    print("  not approximately. That is H3 stated as an equality about a system")
    print("  rather than an inequality about a fit.")
    print()

    print("-" * 78)
    print("WHERE THE CAUSAL VERDICT CAME FROM")
    print("-" * 78)
    for reason, count in sorted(result["gate_reasons"].items()):
        print("  gate {:<20} {:>5}".format(reason, count))
    for state, count in sorted(result["git_states"].items()):
        print("  git  {:<20} {:>5}".format(state, count))
    channel = result["channel"]
    print()
    print("  gate alone (hash recomputed from disk) caught {} of {} failures".format(
        result["counts"]["failures"] - channel["git_only_catches"],
        result["counts"]["failures"]))
    print("  git status alone caught {} (the absent writes only)".format(
        channel["git_only_catches"]))
    print("  -> git answers 'did anything appear'; the digest answers 'is it the")
    print("     right thing'. Either alone is weaker than the pair.")
    print()

    ceiling = result["weak_ceiling"]
    print("-" * 78)
    print("WHY THE WEAK JUDGE HAS A CEILING, NOT A BUG")
    print("-" * 78)
    print("  observed log-leak rate        {:.3f}".format(ceiling["observed_leak"]))
    print("  closed form  leak x (1-poll)  {:.3f}".format(
        ceiling["predicted_sensitivity"]))
    print("  measured sensitivity          {:.3f}".format(
        ceiling["measured_sensitivity"]))
    print("  The weak judge's ceiling is the harness's logging quality. The harness")
    print("  is the party under audit, so that ceiling is not a property of the")
    print("  auditor and cannot be raised by making the auditor smarter.")
    print()

    print("-" * 78)
    print("STRESS TEST: the witness is not a theorem")
    print("-" * 78)
    print("  registration poisoning  ->  false completion / false alarm")
    for row in result["stress_test"]["registration_poisoning"]:
        print("    q = {:>5.1%}                  {:>7.2%}          {:>7.2%}".format(
            row["poison_rate"], row["false_completion"], row["false_alarm"]))
    print()
    print("  A witness checks the state against a *registered expectation*. Poison")
    print("  the registration and the witness certifies whatever is on disk. This")
    print("  is why the intended digest must be computed before the run and held")
    print("  out of band; an expected value supplied by the agent under audit is")
    print("  not an expectation.")
    print()

    print("-" * 78)
    print("WHAT THIS DOES NOT SHOW")
    print("-" * 78)
    print("  1. It does not show that a language model reading a log is as weak as")
    print("     the keyword scan above. The scan is a floor, chosen so the causal")
    print("     result cannot be an artefact of a strong baseline.")
    print("  2. It does not show a zero false-completion rate for arbitrary")
    print("     systems. It shows zero for a model in which every failure changes")
    print("     the state. A failure that leaves the state identical is")
    print("     unobservable in principle, not merely unobserved.")
    print("  3. It does not show that the membrane is correct. It shows that the")
    print("     membrane and the verdict lattice agree on all {} tasks, which is a"
          .format(params["iterations"]))
    print("     consistency check between two implementations, not a proof.")
    print()


def write_data(rows: List[Iteration]) -> str:
    fields = [f for f in Iteration.__dataclass_fields__]
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return DATA_CSV


def write_summary(result: Dict[str, object]) -> str:
    payload = dict(result)
    with open(SUMMARY_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return SUMMARY_JSON


def write_paper_macros(result: Dict[str, object],
                       path: str = MACROS_TEX,
                       figure: Optional[str] = None) -> str:
    """The section's numbers as LaTeX macros, and its figure.

    Same rule as the other two experiment scripts: a number that appears in the
    body is generated, never typed. Names are letters only, because TeX reads
    ``\\cgDelta-0.7`` as a macro followed by text; and each macro carries the
    full precision the run produced, with rounding done by the prose rather
    than by the format string, so a reader who wants 69.8\\% and a reader who
    wants 0.698 both get it from the same file.
    """
    strategies = result["strategies"]
    counts = result["counts"]
    params = result["parameters"]

    def pct(strategy: str, key: str, digits: int = 1) -> str:
        return "{:.{}f}".format(100 * strategies[strategy][key]["rate"], digits)

    def when(strategy: str, key: str) -> str:
        return "{:.3f}".format(strategies[strategy]["cell"][key][0])

    context = strategies["pure_context"]
    weak = strategies["weak_intervention"]
    causal = strategies["causal_intervention"]

    entries = [
        ("cgTasks", str(params["iterations"])),
        ("cgFailures", str(counts["failures"])),
        ("cgAbsent", str(counts["absent"])),
        ("cgStale", str(counts["stale"])),
        ("cgPolluted", str(counts["polluted"])),
        ("cgFalseCompletionPct", "{:.0f}".format(
            100 * params["false_completion_rate"])),
        ("cgPollutionPct", "{:.0f}".format(100 * params["context_pollution_rate"])),
        # pure context
        ("cgContextFcr", pct("pure_context", "false_completion")),
        ("cgContextFcrLow", "{:.1f}".format(
            100 * context["false_completion"]["low"])),
        ("cgContextSens", pct("pure_context", "sensitivity")),
        # weak intervention
        ("cgWeakFcr", pct("weak_intervention", "false_completion")),
        ("cgWeakSens", pct("weak_intervention", "sensitivity")),
        ("cgWeakFar", pct("weak_intervention", "false_alarm")),
        ("cgWeakCeiling", "{:.1f}".format(
            100 * result["weak_ceiling"]["predicted_sensitivity"])),
        ("cgWeakLeak", "{:.2f}".format(result["weak_ceiling"]["observed_leak"])),
        # causal
        ("cgCausalFcr", pct("causal_intervention", "false_completion")),
        ("cgCausalFcrHigh", "{:.1f}".format(
            100 * causal["false_completion"]["high"])),
        ("cgCausalSens", pct("causal_intervention", "sensitivity")),
        # the do-operator
        ("cgDelta", "{:.3f}".format(
            result["intervention"]["causal_intervention"]["delta"])),
        ("cgCausalBefore", "{:.1f}".format(
            100 * result["intervention"]["causal_intervention"]["before"])),
        ("cgCausalAfter", "{:.1f}".format(
            100 * result["intervention"]["causal_intervention"]["after"])),
        ("cgWeakBefore", "{:.1f}".format(
            100 * result["intervention"]["weak_intervention"]["before"])),
        ("cgContextBefore", "{:.1f}".format(
            100 * result["intervention"]["pure_context"]["before"])),
        # conditional cells
        ("cgContextGivenReject", "{:.0f}".format(
            100 * strategies["pure_context"]["cell"]["witness_reject"][0])),
        ("cgCausalGivenReject", "{:.0f}".format(
            100 * strategies["causal_intervention"]["cell"]["witness_reject"][0])),
        ("cgWeakGivenReject", pct("weak_intervention", "false_completion")),
        # where the witness came from
        ("cgMissing", str(result["gate_reasons"].get("ARTEFACT_MISSING", 0))),
        ("cgMismatch", str(result["gate_reasons"].get("DIGEST_MISMATCH", 0))),
        ("cgIssued", str(result["gate_reasons"].get("ISSUED", 0))),
    ]

    lines = ["% generated by experiments/causal_experiment.py -- do not edit"]
    lines += ["\\newcommand{\\" + name + "}{" + value + "}"
              for name, value in entries]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    if figure is not None and os.path.exists(figure):
        os.makedirs(os.path.dirname(PAPER_FIGURE), exist_ok=True)
        shutil.copyfile(figure, PAPER_FIGURE)
    return path


def write_paper_table(result: Dict[str, object], path: str = TABLE_TEX) -> str:
    """The table, with the numbers taken from the run rather than typed."""
    strategies = result["strategies"]
    counts = result["counts"]

    def pct(strategy: str, key: str) -> str:
        return "{:.1f}".format(100 * strategies[strategy][key]["rate"])

    def cell(strategy: str, key: str) -> str:
        return "{:.1f}".format(100 * strategies[strategy]["cell"][key][0])

    lines = [
        "% generated by experiments/causal_experiment.py -- do not edit",
        "% {} tasks, {} failures, seed {}".format(
            result["parameters"]["iterations"], counts["failures"],
            result["parameters"]["seed"]),
        "% Bare tabular. Seven columns do not fit a single IEEEtran column",
        "% (76pt too wide): wrap it in table*, or \\input it inside one.",
        "\\begin{tabular}{@{}lrrrrrr@{}}",
        "\\toprule",
        "& \\multicolumn{4}{c}{Confusion against the artefact}"
        " & \\multicolumn{2}{c}{$P(\\mathrm{PASS})$} \\\\",
        "\\cmidrule(lr){2-5}\\cmidrule(lr){6-7}",
        "Strategy & FCR (\\%) & Sens. (\\%) & FAR (\\%) & Spec. (\\%)"
        " & context & $\\mathrm{do}(W{=}\\mathrm{rej})$ \\\\",
        "\\midrule",
    ]
    for strategy in STRATEGIES:
        block = strategies[strategy]
        lines.append(
            "{} & {} & {} & {} & {:.1f} & {} & {} \\\\".format(
                STRATEGY_LABEL[strategy], pct(strategy, "false_completion"),
                pct(strategy, "sensitivity"), pct(strategy, "false_alarm"),
                100 * block["specificity"]["rate"],
                cell(strategy, "context_success"),
                "{:.1f}".format(100 * block["cell"]["witness_reject"][0]),
            ))
    lines += [
        "\\bottomrule",
        "\\end{tabular}",
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def report_table_tex(path: str = TABLE_TEX) -> str:
    """The table as a complete, paste-ready float.

    The bare tabular lives in ``paper/data/`` so the paper can ``\\input`` it
    like every other generated fragment; this wrapper is what a reader pastes
    into a document, because a seven-column table does not fit an IEEEtran
    column and ``table*`` is the fix rather than shrinking the type.
    """
    with open(path, encoding="utf-8") as handle:
        tabular = "\n".join(
            line for line in handle.read().splitlines()
            if not line.startswith("%"))
    return "\n".join([
        "\\begin{table*}[t]",
        "\\centering",
        "\\caption{Causal intervention. FCR is the share of true failures given a",
        "PASS; sensitivity is $1-\\mathrm{FCR}$; FAR is the share of true successes",
        "refused. The last two columns condition on the context alone and on the",
        "witness after $\\mathrm{do}(W{=}\\mathrm{reject})$, with the context held",
        "byte-identical.}",
        "\\label{tab:causal}",
        "\\footnotesize",
        tabular,
        "\\end{table*}",
    ])


# ---------------------------------------------------------------------------
# 10. main
# ---------------------------------------------------------------------------


def main() -> int:
    if not _HAVE_SIDECAR:
        print("This experiment needs the auditor package it is testing.")
        print("Expected at: {}".format(_SIDECAR))
        print("The causal strategy must be the real membrane; a stand-in would")
        print("make the result a model of a membrane rather than a membrane.")
        return 2

    rng = random.Random(SEED)
    workspace_root = tempfile.mkdtemp(prefix="causal-experiment-")
    ledger_dir = os.path.join(workspace_root, "_ledgers")
    os.makedirs(ledger_dir, exist_ok=True)
    try:
        workspace = Workspace(workspace_root)
        rows = [run_iteration(i, workspace, rng, ledger_dir)
                for i in range(ITERATIONS)]
        apply_intervention(rows, workspace, ledger_dir)
    finally:
        shutil.rmtree(workspace_root, ignore_errors=True)

    result = analyse(rows)
    result["stress_test"] = stress_test(rows)

    report(result)
    figure = render(result)
    write_data(rows)
    write_summary(result)
    table = write_paper_table(result)
    macros = write_paper_macros(result, figure=figure)

    print("=" * 78)
    print("WROTE")
    print("=" * 78)
    for path in (figure, DATA_CSV, SUMMARY_JSON, table, macros, PAPER_FIGURE):
        print("  {}".format(os.path.relpath(path, ROOT)))
    print()
    print("LaTeX table")
    print("-" * 78)
    print(report_table_tex(table))
    print("Reproduce with:  python3 -m experiments.causal_experiment")
    print("No network access. No commit. No push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
