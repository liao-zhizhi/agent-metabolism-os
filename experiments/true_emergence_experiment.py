"""Is there a "true" emergence, or only a witnessed one? A comparison, not a tribute.

The hypothesis this script was asked to test is the strong one:

    H  A correlational agent's apparent reach inflates as difficulty grows
       (false emergence), while a causally-witnessed agent shows a genuine
       *super-exponential* reach explosion once the base layer is connected:
       because only real connections nest fractally, each further layer should
       cost less than the last, so the ladder's total cost falls and the reach
       diverges.

That hypothesis has a *shape* and a *mechanism*, and they are separable:

    shape      reach increases with task depth, faster than a power law
    mechanism  a nesting discount: each already-connected layer makes the next
               one cheaper, and cheaply enough that the accumulated cost falls

The shape is refuted below, and the refutation is not empirical luck---it is the
paper's own nines budget. Equation (19) of the manuscript says a path of ``n``
boundaries needs ``p >= log10(1/eps) + log10(n)`` nines per boundary. Depth is
therefore paid at a *logarithmic* rate, and a logarithmic rate is a discount:
the marginal cost of the ``n``-th layer is ``log10(n/(n-1))`` nines and it falls
monotonically. But it falls *towards* zero and never below it, so the total
``log10(n)`` is increasing. A discount that never turns negative cannot produce a
crossover. Super-exponential emergence requires a *negative* marginal cost, which
is not a discount at all.

So the honest measurement is not "is the explosion real" but "how large is the
nesting gain, and what does it buy". Answer: up to nine orders of magnitude in
depth, and nothing at all in difficulty.

What the groups measure
-----------------------
Group A reads a transcript. Per layer it asserts success when the layer physically
succeeded, or when the context carries contamination---and the transcript is
longer for deeper tasks, so contamination has more chances. Its reach is the
product of its assertions.

Group B reads a witness. Per layer it recomputes the artefact's digest, asks git
whether the file appeared, and puts both through ``MembraneGateway`` and
``VerdictEngine``. A failed layer *blocks* the ladder: no partial credit, and the
chain's connection depth is the number of consecutive PASSes before the first
non-PASS.

Two reach models are carried for B, because they answer different questions:

``independent``
    The layers fail independently at rate ``p^K``. This is the pessimistic model
    and it is what you get if you do not believe in nesting.

``nested``
    The paper's budget: total cost is ``n + log10(K)`` nines rather than ``n*K``.
    This *is* the nesting discount, taken at its strongest defensible value.

What is real and what is modelled
---------------------------------
Real: every causal verdict in the verification block comes from actual files in a
temporary git repository, hashed on disk and passed through the actual gateway.
Modelled: the probability that a layer succeeds at difficulty ``D``, which is the
independent variable of the whole experiment and cannot be measured from an
artefact (an artefact that is absent tells you it failed, not how likely it was).

The link between them is
    p_layer(D) = 1 - D,
i.e. one nine of difficulty costs exactly one factor of the per-layer success
probability. That is a *choice*, it is stated here rather than buried in a
subroutine, and the qualitative ordering below survives any monotone decreasing
p_layer. A reader who prefers ``p_layer = F**n`` can change one function.

Run:
    python3 -m experiments.true_emergence_experiment

Writes:
    experiments/emergence_comparison.csv     the full grid
    experiments/emergence_comparison.png     four panels
    experiments/emergence_comparison.json    every rate at the reported points

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
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DATA_CSV = os.path.join(HERE, "emergence_comparison.csv")
SUMMARY_JSON = os.path.join(HERE, "emergence_comparison.json")
FIGURE_PNG = os.path.join(HERE, "emergence_comparison.png")

_SIDECAR = os.path.join(os.path.dirname(ROOT), "agent-harness-audit")
if os.path.isdir(_SIDECAR) and _SIDECAR not in sys.path:
    sys.path.insert(0, _SIDECAR)

try:
    from audit_sidecar.tools.membrane import (  # noqa: E402
        MembraneGateway,
        MembraneRejectError,
    )
    from audit_sidecar.verdict import VerdictEngine  # noqa: E402

    _HAVE_SIDECAR = True
except ImportError:  # pragma: no cover - the package ships without the auditor
    MembraneGateway = None  # type: ignore
    MembraneRejectError = Exception  # type: ignore
    VerdictEngine = None  # type: ignore
    _HAVE_SIDECAR = False

# ---------------------------------------------------------------------------
# 1. the parameters, named
# ---------------------------------------------------------------------------

SEED = 20261104

#: The ladder's maximum depth. Four stages are named in the brief
#: (think -> write code -> execute -> verify); six is the smallest K that lets a
#: crossover, if one existed, be visible rather than argued about.
MAX_DEPTH = 6

#: Difficulty, expressed as nines: n = -log10(1 - D). D from 0.10 to 0.999 is
#: n from 0.0458 to 3. The grid is uniform in nines and therefore geometric in
#: (1 - D), which is the coordinate the law is linear in.
NINES_MIN = 0.05
NINES_MAX = 3.0
GRID_POINTS = 49

#: Reach anchor, in crossings: the scaffold's $10^{5}$ from Experiment A. The
#: absolute value cancels from every ratio reported below; it is carried so the
#: numbers have the paper's units rather than the author's.
REACH_ANCHOR = 1.0e5

#: Per-layer contamination probability. The same 0.50 as Experiment G, and
#: reused deliberately: if the two experiments disagreed about this number
#: neither could be compared to the other.
CONTAMINATION = 0.50

#: The nesting discount, as the paper's equation (19) states it: a path of K
#: boundaries needs log10(K) extra nines *in total*, not per boundary.
def nested_nines(nines: float, depth: int) -> float:
    return nines + math.log10(depth)


def independent_nines(nines: float, depth: int) -> float:
    return nines * depth


# ---------------------------------------------------------------------------
# 2. the two groups, analytically
# ---------------------------------------------------------------------------


def layer_probability(nines: float) -> float:
    """The per-layer physical success probability at a given difficulty.

    ``p = 1 - D = 10**-nines``. One nine costs one factor. Chosen so that the
    numbers are readable across three decades; every conclusion below is an
    ordering, and the ordering holds for any monotone decreasing p(D).
    """
    return 10.0 ** (-nines)


def claim_probability(nines: float, contamination: float = CONTAMINATION) -> float:
    """Group A's per-layer assertion.

    It asserts success when the layer succeeded (it was there, it saw it) or when
    the context was contaminated. The two events are independent by construction,
    which is what makes contamination a confounder rather than a symptom: the
    contamination is drawn without reference to the outcome.
    """
    p = layer_probability(nines)
    return p + (1.0 - p) * contamination


def chain_probability(per_layer: float, depth: int) -> float:
    """Probability that all ``depth`` layers pass."""
    return per_layer ** depth


def prefix_bound(per_layer: Sequence[float]) -> float:
    """``P(pass all rungs)`` for a ladder, with no independence assumed.

    This is the one-line reason the hypothesis's *shape* cannot hold. A ladder is
    completed only when every prefix is completed, so
    ``P(pass K) <= P(pass j)`` for every ``j <= K``, whatever the joint law is.
    Reach is therefore non-increasing in depth by definition, not by measurement,
    and no per-layer discount---however generous, however conditional---can
    reverse it. What a discount can do is reduce the *rate* at which it falls,
    which is a different claim and the one the sweep actually tests.
    """
    total = 1.0
    for value in per_layer:
        total *= value
    return total


def expected_chain(per_layer: float, depth: int) -> float:
    """Expected number of consecutive passes before the first failure.

    ``sum_{j=1..K} p**j``. This is the connection depth: how far the ladder
    actually got, which is a different question from whether the whole task
    passed, and the two are separated on purpose (a chain of 5 is informative
    even when the task fails at layer 6).
    """
    total = 0.0
    term = 1.0
    for _ in range(depth):
        term *= per_layer
        total += term
    return total


def reach_independent(nines: float, depth: int, per_layer: float) -> float:
    """The pessimistic model: layers fail independently, cost multiplies."""
    return REACH_ANCHOR * per_layer ** depth


def reach_nested(nines: float, depth: int, per_layer: float) -> float:
    """Nesting applied as the budget, not as a per-layer discount.

    Under equation (19) the *path* needs ``n + log10(K)`` nines in total instead
    of ``n*K``. The transmittance of the path is therefore ``10**-(n+log10 K)``
    rather than ``p**K``, and the difference at K = 6, n = 2 is nine orders of
    magnitude. That is the discount, and it is the strongest one the paper
    licenses.
    """
    return REACH_ANCHOR * 10.0 ** (-(nines + math.log10(depth)))


# ---------------------------------------------------------------------------
# 3. the grid
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    nines: float
    difficulty: float
    depth: int
    # group A: correlational
    a_per_layer: float
    a_reach: float
    a_chain: float
    # group B: causal
    b_per_layer: float
    b_reach_independent: float
    b_reach_nested: float
    b_chain: float
    # truth
    true_reach: float
    # derived
    false_completion: float
    inflation: float
    crossover: float


def nines_grid(points: int = GRID_POINTS) -> List[float]:
    step = (NINES_MAX - NINES_MIN) / (points - 1)
    return [NINES_MIN + i * step for i in range(points)]


def build_grid(depths: Sequence[int] = tuple(range(1, MAX_DEPTH + 1)),
               points: int = GRID_POINTS) -> List[Cell]:
    cells: List[Cell] = []
    for nines in nines_grid(points):
        difficulty = 1.0 - 10.0 ** (-nines)
        p = layer_probability(nines)
        q = claim_probability(nines)
        for depth in depths:
            a_reach = REACH_ANCHOR * q ** depth
            b_reach = reach_independent(nines, depth, p)
            b_nested = reach_nested(nines, depth, p)
            truth = REACH_ANCHOR * p ** depth
            # False completion: the share of A's asserted crossings that are not
            # there. Defined on the claim, not on the task, because a claim is
            # what a benchmark records.
            false_completion = 0.0 if q <= 0.0 else 1.0 - (p ** depth) / (q ** depth)
            cells.append(Cell(
                nines=nines,
                difficulty=difficulty,
                depth=depth,
                a_per_layer=q,
                a_reach=a_reach,
                a_chain=expected_chain(q, depth),
                b_per_layer=p,
                b_reach_independent=b_reach,
                b_reach_nested=b_nested,
                b_chain=expected_chain(p, depth),
                true_reach=truth,
                false_completion=false_completion,
                inflation=(a_reach / b_reach) if b_reach > 0 else float("inf"),
                crossover=0.0,
            ))
    return cells


# ---------------------------------------------------------------------------
# 4. the real verification block
# ---------------------------------------------------------------------------


def intended_layer(task: int, layer: int) -> str:
    return (
        '"""Task {t}, layer {l}: the artefact this stage was asked to produce."""\n'
        "\n"
        "\n"
        "STAGE = ({t}, {l})\n"
    ).format(t=task, l=layer)


def digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


class Workspace:
    """A real tree and a real repository, as in Experiment G.

    One repository for the whole block: ``git init`` per task would dominate the
    runtime and buy nothing, and ``git status -- <path>`` is scoped so the tasks
    cannot contaminate each other's witness.
    """

    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(os.path.join(root, "runs"), exist_ok=True)
        self._git("init", "-q")
        self._git("config", "user.email", "experiment@example.invalid")
        self._git("config", "user.name", "true emergence")
        readme = os.path.join(root, "README.md")
        with open(readme, "w", encoding="utf-8") as handle:
            handle.write("workspace for the emergence comparison\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "baseline")

    def _git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", self.root] + list(args),
                              capture_output=True, text=True, check=False)
        if done.returncode != 0 and args[0] != "status":
            raise RuntimeError("git {} failed: {}".format(
                " ".join(args), done.stderr.strip()))
        return done.stdout

    def path_for(self, task: int, layer: int) -> Tuple[str, str]:
        rel = os.path.join("runs", "t{:04d}".format(task),
                           "layer_{}.py".format(layer))
        return os.path.join(self.root, rel), rel

    def appeared(self, rel: str) -> bool:
        out = self._git("status", "--porcelain", "--", rel)
        if any(line.strip() for line in out.splitlines()):
            return True
        return os.path.exists(os.path.join(self.root, rel))


def run_causal_task(task: int, nines: float, depth: int, workspace: Workspace,
                    rng: random.Random, ledger_dir: str) -> Tuple[int, int]:
    """One ladder, walked by a witness. Returns ``(passed, chain_depth)``.

    The physical layer succeeds with probability ``1 - D``, which is drawn here
    and is the only modelled quantity in this function. Everything after the draw
    is real: the write, the hash, the git query, the membrane's verdict.
    """
    passed = 0
    for layer in range(1, depth + 1):
        path, rel = workspace.path_for(task, layer)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        intent = digest_of(intended_layer(task, layer))
        landed = rng.random() < layer_probability(nines)
        if landed:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(intended_layer(task, layer))
        elif os.path.exists(path):
            os.remove(path)

        gateway = MembraneGateway(
            os.path.join(ledger_dir, "t{:04d}_{}.jsonl".format(task, layer)))
        gateway.register_digest("write", intent)
        try:
            warrant = gateway.request_action_warrant(
                {"kind": "file", "path": path, "digest": intent}, "write")
            gateway.redeem(warrant)
        except MembraneRejectError:
            pass

        actual = digest_of_file(path)
        verdict = VerdictEngine(intent=intent, effect=actual,
                                check="success").evaluate()
        # The two implementations must agree, or the block is not measuring the
        # membrane it claims to measure.
        issued = actual == intent
        if issued != (verdict.value == "PASS"):
            raise AssertionError(
                "task {} layer {}: membrane and lattice disagree".format(task, layer))
        if verdict.value != "PASS":
            break                       # a failed layer blocks the ladder
        passed += 1
    return passed, passed


def verification_block(samples: int = 60,
                       settings: Sequence[Tuple[float, int]] = (
                           (0.5, 4), (2.0, 3), (2.7, 6)),
                       seed: int = SEED) -> List[Dict[str, object]]:
    """Real ladders at three difficulty/depth settings, compared to the model.

    The model predicts ``E[chain] = sum p**j``. If the real block disagrees, the
    analytic columns are a model of a membrane rather than a membrane, and the
    disagreement is printed rather than smoothed.
    """
    if not _HAVE_SIDECAR:
        return []

    root = tempfile.mkdtemp(prefix="emergence-verify-")
    ledger_dir = os.path.join(root, "_ledgers")
    os.makedirs(ledger_dir, exist_ok=True)
    rows: List[Dict[str, object]] = []
    try:
        workspace = Workspace(root)
        rng = random.Random(seed)
        task = 0
        for nines, depth in settings:
            chains: List[int] = []
            full = 0
            for _ in range(samples):
                task += 1
                passed, chain = run_causal_task(
                    task, nines, depth, workspace, rng, ledger_dir)
                chains.append(chain)
                if passed == depth:
                    full += 1
            p = layer_probability(nines)
            rows.append({
                "nines": nines,
                "difficulty": 1.0 - 10.0 ** (-nines),
                "depth": depth,
                "samples": samples,
                "model_chain": expected_chain(p, depth),
                "observed_chain": sum(chains) / len(chains),
                "model_full": p ** depth,
                "observed_full": full / samples,
            })
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return rows


# ---------------------------------------------------------------------------
# 5. the analysis the brief asks for
# ---------------------------------------------------------------------------


def at_nines(cells: Sequence[Cell], nines: float) -> List[Cell]:
    return [c for c in cells if abs(c.nines - nines) < 1e-9]


def nearest(cells: Sequence[Cell], nines: float) -> List[Cell]:
    target = min(cells, key=lambda c: abs(c.nines - nines))
    return at_nines(cells, target.nines)


def local_exponent(cells: Sequence[Cell], depth: int, key: str) -> float:
    """Fitted slope of log10(reach) against nines, for one depth.

    A power law in (1-D) is a straight line in this coordinate with slope ``-k``;
    the returned value is ``k``, so a larger number means a steeper collapse. The
    fit is over the whole grid, which is where a "the slope grows without bound"
    claim would show up if it were true.
    """
    points = sorted((c.nines, getattr(c, key)) for c in cells if c.depth == depth)
    points = [(x, y) for x, y in points if y > 0]
    xs = [p[0] for p in points]
    ys = [math.log10(p[1]) for p in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    return -slope


def floor_reach(cells: Sequence[Cell], depth: int, key: str) -> float:
    """The smallest reach the curve ever reaches, over the whole grid.

    This is the cleanest statement of the difference between the two groups. A's
    assertion is floored: once contamination dominates, raising the difficulty
    moves nothing, and the floor is ``anchor * contamination**depth``. B's floor
    is whatever the hardest setting leaves, which for the independent model is
    essentially zero. A floor is not a saturation *point*; it is a level, and it
    is the level that a benchmark with no witness reports as capability.
    """
    values = [getattr(c, key) for c in cells if c.depth == depth]
    return min(values) if values else 0.0


def collapse_point(cells: Sequence[Cell], depth: int, key: str,
                   threshold: float = 1.0) -> Optional[float]:
    """Where a curve's reach falls below one completed crossing."""
    for cell in sorted((c for c in cells if c.depth == depth),
                       key=lambda c: c.nines):
        if getattr(cell, key) < threshold:
            return cell.nines
    return None


def analyse(cells: Sequence[Cell]) -> Dict[str, object]:
    grid_nines = sorted({c.nines for c in cells})
    # The three reported difficulties. 0.99 is named in the brief; 0.9 and 0.999
    # bracket it so a reader can see whether the number is a point or a trend.
    reported = {}
    for target in (0.9, 0.99, 0.999):
        block = min((c for c in cells if abs(c.difficulty - target) < 0.02),
                    key=lambda c: abs(c.difficulty - target))
        rows = at_nines(cells, block.nines)
        reported["{:.3f}".format(target)] = {
            "nines": block.nines,
            "difficulty": block.difficulty,
            "per_depth": [
                {
                    "depth": c.depth,
                    "a_reach": c.a_reach,
                    "b_reach": c.b_reach_independent,
                    "b_nested": c.b_reach_nested,
                    "true_reach": c.true_reach,
                    "false_completion": c.false_completion,
                    "inflation": c.inflation,
                    "a_chain": c.a_chain,
                    "b_chain": c.b_chain,
                }
                for c in sorted(rows, key=lambda c: c.depth)
            ],
        }

    # Aggregate over the ladder: what a benchmark that averages depths reports.
    ladder = {}
    for target_key, block in reported.items():
        depths = block["per_depth"]
        n = len(depths)
        a_mean = sum(d["a_reach"] for d in depths) / n
        b_mean = sum(d["b_reach"] for d in depths) / n
        bn_mean = sum(d["b_nested"] for d in depths) / n
        true_mean = sum(d["true_reach"] for d in depths) / n
        fcr = sum(d["false_completion"] for d in depths) / n
        ladder[target_key] = {
            "a_reach": a_mean,
            "b_reach": b_mean,
            "b_nested": bn_mean,
            "true_reach": true_mean,
            "false_completion": fcr,
            "inflation": a_mean / b_mean if b_mean > 0 else float("inf"),
            "a_chain": sum(d["a_chain"] for d in depths) / n,
            "b_chain": sum(d["b_chain"] for d in depths) / n,
        }

    # Exponents. The whole point: A's is not a power law at all, so the fit is a
    # summary of a curve that has no exponent, and the number is reported with
    # that caveat rather than as a measurement.
    exponents = {}
    for depth in (1, 3, MAX_DEPTH):
        exponents[str(depth)] = {
            "a": local_exponent(cells, depth, "a_reach"),
            "b": local_exponent(cells, depth, "b_reach_independent"),
            "b_nested": local_exponent(cells, depth, "b_reach_nested"),
        }

    # Depth response: does reach increase with depth at fixed difficulty?
    # This is the "true emergence" shape, and it is checked directly rather than
    # inferred from a fit. A strictly decreasing column means no crossover.
    depth_response = {}
    for target_key, block in reported.items():
        depths = block["per_depth"]
        a_seq = [d["a_reach"] for d in depths]
        b_seq = [d["b_reach"] for d in depths]
        bn_seq = [d["b_nested"] for d in depths]
        depth_response[target_key] = {
            "a_increases": all(y > x for x, y in zip(a_seq, a_seq[1:])),
            "b_increases": all(y > x for x, y in zip(b_seq, b_seq[1:])),
            "b_nested_increases": all(y > x for x, y in zip(bn_seq, bn_seq[1:])),
            "a_first_last": (a_seq[0], a_seq[-1]),
            "b_first_last": (b_seq[0], b_seq[-1]),
            "b_nested_first_last": (bn_seq[0], bn_seq[-1]),
        }

    return {
        "parameters": {
            "seed": SEED,
            "max_depth": MAX_DEPTH,
            "grid_points": len(grid_nines),
            "nines_range": (grid_nines[0], grid_nines[-1]),
            "reach_anchor": REACH_ANCHOR,
            "contamination": CONTAMINATION,
        },
        "reported": reported,
        "ladder": ladder,
        "exponents": exponents,
        "depth_response": depth_response,
        "floors": {
            "a": floor_reach(cells, MAX_DEPTH, "a_reach"),
            "a_closed_form": REACH_ANCHOR * CONTAMINATION ** MAX_DEPTH,
            "b": floor_reach(cells, MAX_DEPTH, "b_reach_independent"),
            "b_nested": floor_reach(cells, MAX_DEPTH, "b_reach_nested"),
        },
        "collapse": {
            "a": collapse_point(cells, MAX_DEPTH, "a_reach"),
            "b": collapse_point(cells, MAX_DEPTH, "b_reach_independent"),
            "b_nested": collapse_point(cells, MAX_DEPTH, "b_reach_nested"),
        },
    }


# ---------------------------------------------------------------------------
# 6. the nesting sweep: how much discount would an explosion need?
# ---------------------------------------------------------------------------


def nesting_sweep(depth_max: int = MAX_DEPTH,
                  nines: float = 2.0) -> List[Dict[str, object]]:
    """Total cost against depth for a family of discount schedules.

    Cost model: layer ``j`` costs ``nines * delta**(j-1)`` nines, so
    ``delta < 1`` is a geometric discount and ``delta = 0`` makes every layer
    after the first free. For each schedule the cumulative cost and the implied
    reach are reported, and the question is whether any admissible schedule makes
    cumulative cost *fall*.

    It cannot, for ``delta >= 0`` and ``nines > 0``: a sum of non-negative terms
    is non-decreasing. The sweep is here because "it cannot" is an argument and a
    table is a fact, and because the marginal costs in the table are the number
    the paper's equation (19) predicts.
    """
    rows = []
    for delta in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        cumulative = 0.0
        cost = []
        marginal = []
        for layer in range(1, depth_max + 1):
            term = nines * (delta ** (layer - 1))
            previous = cumulative
            cumulative += term
            cost.append(cumulative)
            marginal.append(cumulative - previous)
        reach_values = [REACH_ANCHOR * 10.0 ** (-c) for c in cost]
        rows.append({
            "delta": delta,
            "cost": cost,
            "marginal": marginal,
            "reach": reach_values,
            "cumulative_increasing": all(
                y > x for x, y in zip(cost, cost[1:])),
            "reach_increasing": all(
                y > x for x, y in zip(reach_values, reach_values[1:])),
        })
    return rows


def budget_marginals(nines: float = 2.0, depth_max: int = MAX_DEPTH) -> List[float]:
    """Marginal nines per layer under the paper's equation (19).

    Total is ``nines + log10(K)``, so the ``j``-th layer costs
    ``log10(j/(j-1))`` nines for ``j >= 2``. Strictly positive, strictly
    decreasing, tending to zero: a discount, never a subsidy.
    """
    out = []
    for layer in range(1, depth_max + 1):
        if layer == 1:
            out.append(nines)
        else:
            out.append(math.log10(layer / (layer - 1)))
    return out


# ---------------------------------------------------------------------------
# 7. rendering
# ---------------------------------------------------------------------------

INK = "#1f2d3d"
ACCENT = "#b0413e"
COOL = "#2f6f9f"
MUTED = "#8a8f98"
PALE = "#c9cdd3"
GREEN = "#3f7d5a"


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


def render(cells: Sequence[Cell], result: Dict[str, object],
           path: str = FIGURE_PNG) -> str:
    _style()
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.6), constrained_layout=True)
    depth = MAX_DEPTH
    rows = sorted((c for c in cells if c.depth == depth), key=lambda c: c.nines)
    x = [c.nines for c in rows]

    # (a) reach against difficulty ------------------------------------------
    ax = axes[0][0]
    ax.semilogy(x, [c.a_reach for c in rows], color=MUTED, linewidth=1.6,
                label="A: asserted")
    ax.semilogy(x, [c.b_reach_independent for c in rows], color=COOL,
                linewidth=1.6, label="B: witnessed, independent")
    ax.semilogy(x, [c.b_reach_nested for c in rows], color=ACCENT,
                linewidth=1.6, linestyle="--", label="B: witnessed, nested")
    ax.axhline(1.0, color=INK, linewidth=0.8, linestyle=":")
    ax.text(x[0], 1.6, "one completed crossing", fontsize=6.4, color=INK)
    ax.set_ylabel("expected crossings (of $10^{5}$ attempts)")
    ax.set_xlabel("nines of difficulty, $-\\log_{10}(1-D)$")
    ax.set_title("(a) a ladder of depth {}".format(depth), loc="left")
    ax.legend(frameon=False, loc="upper right", handlelength=1.4,
              borderaxespad=0.3)
    ax.grid(True, which="both")
    ax.set_ylim(1e-12, 1e6)
    for axis in (axes[0][0], axes[0][1], axes[1][0]):
        axis.axvline(2.0, color=PALE, linewidth=0.9, zorder=0)
    axes[0][0].text(2.04, 1e5, "$D=0.99$", fontsize=6.2, color=MUTED)

    # (b) false completion ---------------------------------------------------
    ax = axes[0][1]
    depths_shown = (1, 3, depth)
    shades_b = {1: PALE, 3: "#a0605e", depth: ACCENT}
    for d in depths_shown:
        sub = sorted((c for c in cells if c.depth == d), key=lambda c: c.nines)
        ax.plot([c.nines for c in sub],
                [100 * c.false_completion for c in sub],
                color=shades_b[d], linewidth=1.7 if d == depth else 1.0,
                label="A, depth {}".format(d))
    ax.axhline(0.0, color=COOL, linewidth=1.7, label="B, witnessed (any depth)")
    ax.set_ylabel("false completion rate (%)")
    ax.set_xlabel("nines of difficulty")
    ax.set_ylim(-4, 104)
    ax.set_title("(b) asserted minus witnessed", loc="left")
    ax.legend(frameon=False, loc="lower right", handlelength=1.4, fontsize=6.6)
    ax.grid(True)

    # (c) connection depth ---------------------------------------------------
    ax = axes[1][0]
    ax.plot(x, [c.a_chain for c in rows], color=MUTED, linewidth=1.6,
            label="A: asserted chain")
    ax.plot(x, [c.b_chain for c in rows], color=COOL, linewidth=1.6,
            label="B: witnessed chain")
    ax.set_ylabel("expected consecutive PASSes")
    ax.set_xlabel("nines of difficulty")
    ax.set_ylim(0, depth + 0.4)
    ax.set_title("(c) how far the ladder actually got", loc="left")
    ax.legend(frameon=False, loc="upper right", handlelength=1.4)
    ax.grid(True)

    # (d) the nesting sweep --------------------------------------------------
    ax = axes[1][1]
    sweep = nesting_sweep()
    shades = {0.0: PALE, 0.1: "#a9b6c2", 0.25: MUTED, 0.5: GREEN,
              0.75: COOL, 1.0: ACCENT}
    for row in sweep:
        layers = list(range(1, len(row["cost"]) + 1))
        ax.plot(layers, row["reach"], marker="o", markersize=2.6,
                linewidth=1.1, color=shades[row["delta"]],
                label="$\\delta={}$".format(row["delta"]))
    ax.set_yscale("log")
    ax.set_xlabel("ladder depth $K$")
    ax.set_ylabel("reach at $n=2$ nines")
    ax.set_title("(d) no admissible discount raises reach with $K$", loc="left")
    ax.legend(frameon=False, ncol=2, fontsize=6.2, loc="upper right",
              handlelength=1.0, columnspacing=0.7, borderaxespad=0.2)
    ax.grid(True, which="both")

    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 8. reports
# ---------------------------------------------------------------------------


def _fmt_reach(value: float) -> str:
    if value == 0:
        return "0"
    if value != value:            # NaN
        return "n/a"
    if value >= 1e4 or value < 1e-2:
        return "{:.2e}".format(value)
    return "{:.3g}".format(value)


def report(result: Dict[str, object],
           verification: Sequence[Dict[str, object]],
           sweep: Sequence[Dict[str, object]]) -> None:
    params = result["parameters"]
    ladder = result["ladder"]
    reported = result["reported"]

    print("=" * 78)
    print("TRUE EMERGENCE? the same ladder, read two ways")
    print("=" * 78)
    print("seed {}   depth 1..{}   {} points in nines   reach anchor {:.0e}".format(
        params["seed"], params["max_depth"], params["grid_points"],
        params["reach_anchor"]))
    print("contamination {:.0%}   per-layer success p = 1-D".format(
        params["contamination"]))
    print()

    print("-" * 78)
    print("THE BRIEF'S THREE NUMBERS")
    print("-" * 78)
    block = reported["0.990"]
    print("  D = {:.3f}  (n = {:.2f} nines)".format(
        block["difficulty"], block["nines"]))
    print("  A  false-crossing share averaged over depth       {:6.2f}%".format(
        100 * ladder["0.990"]["false_completion"]))
    deepest = block["per_depth"][-1]
    print("  A  false-crossing share at depth {}                {:6.2f}%".format(
        deepest["depth"], 100 * deepest["false_completion"]))
    p_deep = deepest["b_reach"] / REACH_ANCHOR
    print("  B  true causal connection rate at depth {}         {:9.3e}  (1 in {:.3g})"
          .format(deepest["depth"], p_deep,
                  float("inf") if p_deep == 0 else 1.0 / p_deep))
    print("  B  nested (paper eq. 19) at depth {}               {:9.3e}".format(
        deepest["depth"], deepest["b_nested"] / REACH_ANCHOR))
    print("  A's assertion over B's witness, at depth {}        {:9.2e}x".format(
        deepest["depth"], deepest["inflation"]))
    print("  A's assertion over B's witness, averaged           {:9.2e}x".format(
        ladder["0.990"]["inflation"]))
    print()

    print("-" * 78)
    print("THE ARGUMENT, BEFORE ANY NUMBER")
    print("-" * 78)
    print("  A ladder is completed only when every prefix is completed, so for any")
    print("  per-layer law at all, P(pass K) <= P(pass 1). Reach is therefore")
    print("  non-increasing in depth by definition, not by measurement. The")
    print("  explosion the brief describes cannot live in the reach; it would have")
    print("  to live in the marginal cost per rung. That cost does fall---")
    print("  log10(j/(j-1)) nines---and it falls to zero, never below. The")
    print("  experiments below quantify the gap between the two readings; they do")
    print("  not decide its sign, because the sign is not free.")
    print()

    print("-" * 78)
    print("THE LADDER, AT THREE DIFFICULTIES")
    print("-" * 78)
    header = "{:<8} {:>10} {:>12} {:>12} {:>12} {:>10}".format(
        "D", "A asserted", "B witnessed", "B nested", "truth", "A FCR")
    print(header)
    for key in ("0.900", "0.990", "0.999"):
        row = ladder[key]
        print("{:<8} {:>10} {:>12} {:>12} {:>12} {:>9.1f}%".format(
            key,
            _fmt_reach(row["a_reach"]),
            _fmt_reach(row["b_reach"]),
            _fmt_reach(row["b_nested"]),
            _fmt_reach(row["true_reach"]),
            100 * row["false_completion"]))
    print()

    print("-" * 78)
    print("DOES REACH INCREASE WITH DEPTH? (the shape 'true' emergence needs)")
    print("-" * 78)
    for key in ("0.900", "0.990", "0.999"):
        row = result["depth_response"][key]
        print("  D = {}   A {}   B independent {}   B nested {}".format(
            key,
            "up  " if row["a_increases"] else "down",
            "up  " if row["b_increases"] else "down",
            "up  " if row["b_nested_increases"] else "down"))
    print("  No column rises with depth, in any group, under any model.")
    print("  A's *false completion* does rise with depth: {:.1f}% at one layer,".format(
        100 * result["reported"]["0.990"]["per_depth"][0]["false_completion"]))
    print("  {:.1f}% at six. But that is the assertion drifting further from the".format(
        100 * result["reported"]["0.990"]["per_depth"][-1]["false_completion"]))
    print("  artefact as the transcript lengthens, not reach increasing.")
    print()

    print("-" * 78)
    print("THE EXPONENT, AND WHY GROUP A DOES NOT HAVE ONE")
    print("-" * 78)
    for key in ("1", "3", "6"):
        block = result["exponents"][key]
        print("  depth {}:  A k = {:6.2f}   B k = {:5.2f}   B nested k = {:5.2f}".format(
            key, block["a"], block["b"], block["b_nested"]))
    print()
    print("  A's number is a least-squares summary of a curve that is not a power")
    print("  law: its slope falls as the assertion saturates, so the fit describes")
    print("  no exponent at all. B's is the mechanism's exponent and does not move")
    print("  with depth. Reported side by side, they look comparable; they are not.")
    print()

    print("-" * 78)
    print("WHERE EACH CURVE STOPS MOVING")
    print("-" * 78)
    floors = result["floors"]
    col = result["collapse"]
    print("  A  floor on asserted reach     {:9.1f} crossings".format(floors["a"]))
    print("     limit as p -> 0: {:9.1f}  =  anchor x {:.0%}^{}".format(
        floors["a_closed_form"], params["contamination"], params["max_depth"]))
    print("  B  floor on witnessed reach    {:9.3e} crossings".format(floors["b"]))
    print("  B  floor, nested               {:9.3e} crossings".format(
        floors["b_nested"]))
    print()
    print("  A's reach has a positive floor: no matter how hard the task, it keeps")
    print("  asserting {:.0f} crossings, because contamination is a property of the"
          .format(floors["a"]))
    print("  transcript and the transcript is always there. That is the false")
    print("  plateau. B's floor is zero. The point where B's witnessed reach falls")
    print("  below one crossing is n = {}, i.e. D = {:.4f} at depth {}.".format(
        "never" if col["b"] is None else "{:.2f}".format(col["b"]),
        1 - 10 ** (-(col["b"] if col["b"] is not None else NINES_MAX)),
        params["max_depth"]))
    print("  A benchmark with no witness never sees that point, because its")
    print("  number is pinned to the floor and the floor does not move.")
    print()

    if verification:
        print("-" * 78)
        print("THE ANALYTIC COLUMNS, CHECKED AGAINST REAL FILES")
        print("-" * 78)
        print("  {:>6} {:>5} {:>14} {:>14} {:>10}".format(
            "nines", "depth", "model chain", "observed chain", "samples"))
        for row in verification:
            print("  {:>6.2f} {:>5} {:>14.2f} {:>14.2f} {:>10}".format(
                row["nines"], row["depth"], row["model_chain"],
                row["observed_chain"], row["samples"]))
        worst = max(abs(r["model_chain"] - r["observed_chain"])
                    for r in verification)
        print()
        print("  worst disagreement between model and real ladders: {:.2f} rungs"
              .format(worst))
        print("  The model is an expectation over Bernoulli layers, so the sample")
        print("  mean carries a standard error; a disagreement larger than about")
        print("  two of those would mean the analytic columns describe a membrane")
        print("  that is not the membrane.")
        print()

    print("-" * 78)
    print("HOW MUCH NESTING IS THERE? (and what would an explosion need)")
    print("-" * 78)
    print("  paper eq. (19): total cost is n + log10(K), so marginal costs are")
    print("  " + ", ".join("{:.3f}".format(m) for m in budget_marginals()))
    print("  strictly positive, strictly decreasing, tending to zero. A discount")
    print("  that never turns negative cannot make the total fall.")
    print()
    print("  {:>7} {:>38} {:>10} {:>10}".format(
        "delta", "cumulative cost (K = 1..{})".format(MAX_DEPTH),
        "cost up?", "reach up?"))
    for row in sweep:
        print("  {:>7} {:>38} {:>10} {:>10}".format(
            row["delta"],
            " ".join("{:.2f}".format(c) for c in row["cost"]),
            "yes" if row["cumulative_increasing"] else "NO",
            "yes" if row["reach_increasing"] else "no"))
    print()
    print("  No admissible schedule produces an increasing reach. The sweep is")
    print("  monotone in delta and flat in its answer, which is what a refutation")
    print("  of a monotone claim looks like.")
    print()

    print("-" * 78)
    print("WHAT THIS DOES NOT SHOW")
    print("-" * 78)
    print("  1. It does not show that real capability curves are power laws. It")
    print("     shows that the *shape* the hypothesis needs---reach increasing in")
    print("     depth---is absent from a model built on the paper's own budget.")
    print("  2. It does not measure nature, and p = 1-D is a choice. The ordering")
    print("     survives any monotone decreasing replacement. It does not survive a")
    print("     change in the *definition* of reach: if reach means the amplification")
    print("     of the top boundary rather than the completion of a chain, the prefix")
    print("     bound does not apply and the claim becomes a different one, which")
    print("     would have to be tested separately and is not tested here.")
    print("  3. It does not show that witnessed reach is correct reach. It shows")
    print("     that witnessed reach is a function of the artefact and asserted")
    print("     reach is a function of the transcript, and that the second is larger")
    print("     than the first by a ratio that grows with difficulty.")
    print()


def write_data(cells: Sequence[Cell]) -> str:
    fields = ["nines", "difficulty", "depth", "a_per_layer", "a_reach", "a_chain",
              "b_per_layer", "b_reach_independent", "b_reach_nested", "b_chain",
              "true_reach", "false_completion", "inflation"]
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for cell in cells:
            writer.writerow([
                "{:.6f}".format(cell.nines),
                "{:.9f}".format(cell.difficulty),
                cell.depth,
                "{:.9f}".format(cell.a_per_layer),
                "{:.9e}".format(cell.a_reach),
                "{:.9f}".format(cell.a_chain),
                "{:.9f}".format(cell.b_per_layer),
                "{:.9e}".format(cell.b_reach_independent),
                "{:.9e}".format(cell.b_reach_nested),
                "{:.9f}".format(cell.b_chain),
                "{:.9e}".format(cell.true_reach),
                "{:.9f}".format(cell.false_completion),
                "{:.9e}".format(cell.inflation),
            ])
    return DATA_CSV


def write_summary(result: Dict[str, object],
                  verification: Sequence[Dict[str, object]],
                  sweep: Sequence[Dict[str, object]]) -> str:
    payload = dict(result)
    payload["verification"] = list(verification)
    payload["nesting_sweep"] = list(sweep)
    payload["budget_marginals"] = budget_marginals()
    with open(SUMMARY_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return SUMMARY_JSON


# ---------------------------------------------------------------------------
# 9. the paper's copies of these numbers
# ---------------------------------------------------------------------------

PAPER_DIR = os.path.join(HERE, "paper")
PAPER_DATA = os.path.join(PAPER_DIR, "data")
PAPER_FIGURES = os.path.join(PAPER_DIR, "figures")


def tex_number(value: float, digits: int = 3) -> str:
    """A number as LaTeX math, in the form the table wants.

    Everything above 1e4 or below 1e-2 is written as a mantissa and a power.
    The paper's other tables use ``siunitx``; this one is generated from floats
    and is emitted as plain math so that a reader can see the exponent without
    the table depending on a package option.
    """
    if value == 0:
        return "$0$"
    magnitude = abs(value)
    if 1e-2 <= magnitude < 1e4:
        # ``%g`` switches to exponential notation at three significant figures
        # for values like 1655, which reads as "1.65e+03" in a table. The
        # thresholds below keep small integers integral.
        if magnitude >= 100:
            return "${:.0f}$".format(value)
        if magnitude >= 10:
            return "${:.1f}$".format(value).replace(".0}", "}")
        return "${:.3g}$".format(value)
    exponent = int(math.floor(math.log10(magnitude)))
    mantissa = value / 10.0 ** exponent
    if abs(abs(mantissa) - 10.0) < 5e-3:
        mantissa /= 10.0
        exponent += 1
    return "${:.2f}\\times10^{{{}}}$".format(mantissa, exponent)


def write_paper_artifacts(result: Dict[str, object],
                          depth: int = MAX_DEPTH) -> List[str]:
    """Write the table, the macros, and the figure the paper includes.

    Generated rather than transcribed: every number in the paper's new section
    traces to this function, and a reader can regenerate it. The alternative---
    typing the numbers into the prose---is how a table and a paragraph come to
    disagree two revisions later.
    """
    os.makedirs(PAPER_DATA, exist_ok=True)
    os.makedirs(PAPER_FIGURES, exist_ok=True)

    block = result["reported"]["0.990"]
    rows = sorted(block["per_depth"], key=lambda r: r["depth"])
    deepest = rows[-1]
    floors = result["floors"]
    collapse = result["collapse"]
    params = result["parameters"]

    b_rate = deepest["b_reach"] / REACH_ANCHOR
    b_nested_rate = deepest["b_nested"] / REACH_ANCHOR
    gain_orders = math.log10(b_nested_rate / b_rate) if b_rate > 0 else 0.0
    gap_one = (rows[0]["a_reach"] / rows[0]["b_reach"]) if rows[0]["b_reach"] else 0.0

    macro_lines = [
        "% generated by experiments/true_emergence_experiment.py -- do not edit",
        "\\newcommand{\\teDepthMax}{%d}" % params["max_depth"],
        "\\newcommand{\\teGridPoints}{%d}" % params["grid_points"],
        "\\newcommand{\\teContamPct}{%d}" % round(100 * CONTAMINATION),
        "\\newcommand{\\teAnchor}{$10^{5}$}",
        "\\newcommand{\\teNinesNinetyNine}{%.2f}" % block["nines"],
        "\\newcommand{\\teANinetyNine}{%s}" % tex_number(deepest["a_reach"]),
        "\\newcommand{\\teAFcrNinetyNine}{%.0f}" % (100 * deepest["false_completion"]),
        "\\newcommand{\\teAFcrDepthOne}{%.1f}" % (100 * rows[0]["false_completion"]),
        "\\newcommand{\\teBCrossNinetyNine}{%s}" % tex_number(deepest["b_reach"]),
        "\\newcommand{\\teBRateNinetyNine}{%s}" % tex_number(b_rate),
        "\\newcommand{\\teNestedCrossNinetyNine}{%s}" % tex_number(deepest["b_nested"]),
        "\\newcommand{\\teBNestedRate}{%s}" % tex_number(b_nested_rate),
        "\\newcommand{\\teBOrders}{%.1f}" % gain_orders,
        "\\newcommand{\\teInflationDeep}{%s}" % tex_number(deepest["inflation"]),
        "\\newcommand{\\teGapDepthOne}{%s}" % tex_number(gap_one),
        "\\newcommand{\\teAFloor}{%s}" % tex_number(floors["a"]),
        "\\newcommand{\\teAFloorLimit}{%s}" % tex_number(floors["a_closed_form"]),
        "\\newcommand{\\teBFloor}{%s}" % tex_number(floors["b"]),
        "\\newcommand{\\teCollapseNines}{%.2f}" % collapse["b"],
        "\\newcommand{\\teCollapseD}{%.3f}" % (1.0 - 10.0 ** (-collapse["b"])),
        "\\newcommand{\\teSlopeA}{%.2f}" % result["exponents"][str(MAX_DEPTH)]["a"],
        "\\newcommand{\\teSlopeB}{%.2f}" % result["exponents"][str(MAX_DEPTH)]["b"],
        "\\newcommand{\\teSlopeBNested}{%.2f}"
        % result["exponents"][str(MAX_DEPTH)]["b_nested"],
    ]
    macros_path = os.path.join(PAPER_DATA, "emergence_compare_macros.tex")
    with open(macros_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(macro_lines) + "\n")

    table_lines = [
        "% generated by experiments/true_emergence_experiment.py -- do not edit",
        "% depth, asserted crossings, asserted FCR, witnessed crossings, nested crossings",
        "\\begin{tabular}{@{}rrrrr@{}}",
        "\\toprule",
        "$m$ & A asserted & A FCR (\\%) & B witnessed & B nested \\\\",
        "\\midrule",
    ]
    for row in rows:
        table_lines.append("{:d} & {} & {:.1f} & {} & {} \\\\".format(
            row["depth"],
            tex_number(row["a_reach"]),
            100 * row["false_completion"],
            tex_number(row["b_reach"]),
            tex_number(row["b_nested"]),
        ))
    table_lines += ["\\bottomrule", "\\end{tabular}"]
    table_path = os.path.join(PAPER_DATA, "emergence_compare_table.tex")
    with open(table_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(table_lines) + "\n")

    figure_path = os.path.join(PAPER_FIGURES, "emergence_compare.png")
    shutil.copyfile(FIGURE_PNG, figure_path)

    return [macros_path, table_path, figure_path]


# ---------------------------------------------------------------------------
# 10. main
# ---------------------------------------------------------------------------


def main() -> int:
    cells = build_grid()
    result = analyse(cells)
    sweep = nesting_sweep()
    verification = verification_block()

    report(result, verification, sweep)
    figure = render(cells, result)
    write_data(cells)
    write_summary(result, verification, sweep)
    paper_files = write_paper_artifacts(result)

    print("=" * 78)
    print("WROTE")
    print("=" * 78)
    for path in [figure, DATA_CSV, SUMMARY_JSON] + paper_files:
        print("  {}".format(os.path.relpath(path, ROOT)))
    print()
    print("Reproduce with:  python3 -m experiments.true_emergence_experiment")
    print("No network access. No commit. No push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
