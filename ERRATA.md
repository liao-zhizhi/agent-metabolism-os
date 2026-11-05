# ERRATA

Defects found in this repository, recorded the moment they were found rather than
after they were fixed.

This file exists because of a specific failure mode. A defect that is quietly
patched leaves no trace that it ever existed, and the audit trail then looks
cleaner than the code was. So the rule here is:

1. **Every defect gets an entry** with a runnable reproduction, the severity, and
   what it corrupts downstream.
2. **Nothing is fixed silently.** An entry is only closed when the fix and its
   blast radius are written down.
3. **An open entry is not a scandal; a missing one is.** The status column is the
   point of the file.

All three open entries below were found during the work reported in
`experiments/paper/` (November 2026). Entries ERR-001 and ERR-002 were surfaced by
building the experiments; ERR-003 was surfaced by verifying ERR-001, which is why
the count is three and not two.

One further entry, PRE-003, is of a different kind and is recorded here because it
is the closest this repository has come to publishing a defect. It is not a bug in
the code. It is a hypothesis that arrived with the desired answer attached, and the
document below is the record of refusing it.

## Status

| id | defect | severity | status |
|:--|:--|:--|:--|
| ERR-001 | six-decimal rounding turns passable gaps into reported walls | **high** — silent data corruption | **closed** (v0.6.0) |
| ERR-002 | clamped successor keeps returning `PASS` after the ladder stops being fractal | **high** — silent false positive | **closed** (v0.6.0) |
| ERR-003 | witness digest does not bind the gap's difficulty | **high** — two distinct claims share one witness | **closed** (v0.6.0) |
| PRE-001 | the paper's biological premise was corrected before publication | medium — premise, not code | closed |
| PRE-002 | the exponential-fit measurement was conditioned on $D\approx0.999$, biasing the rate it was meant to check | medium — a measured number, not a released one | closed |
| PRE-003 | the "true emergence" hypothesis asked the experiment to confirm a shape that its own budget forbids | **high** — would have published a fitted non-result as a discovery | **closed** (v0.6.1) |
| PUB-001 | the archived v1.0.0 manuscript went to press without a literature-audit pass; the released PDF and the repository diverged by ten references | medium — a released artefact, not a code path | **closed** (v1.0.1) |
| ERR-004 | the execution layer's witness was not bound to the prediction it checked, so a scene could satisfy an earlier chunk's registration | **high** — three episodes reported a pass after the bowl had been dropped | **closed** (v1.0.3) |
| ERR-005 | the CEL paper's figures were not derived from the audit's data: one shipped PNG had no producing script, carried numbers that exist in no summary, dropped a declared world, and labelled both arms as crashing | **high** — a figure that cannot be re-derived is an assertion, in a paper about unwitnessed assertions | **closed** (v1.0.3) |
| PUB-002 | a released figure caption asserted "every crash is a pedestrian" and the figure had no recovery panel, though both captions promised content the files did not contain | medium — a released claim, not a code path | **closed** (v1.0.3) |

Severity is assigned by one criterion: *can a consumer of this output be
misled without seeing an error?* All three entries passed that test, which is why
all three are high. A crash would have been cheaper.

All three are now closed in the same change, because they could not be closed
honestly one at a time: fixing the rounding exposes the clamp, and fixing the
clamp is what made anyone look at the witness. The three closed entries below
keep their original "what a consumer would see" section verbatim, since that is
the evidence, and append a **Fix** section describing what replaced it.

---

## ERR-001 — six-decimal rounding invents walls

**Location.** `agent_metabolism/interface_engine.py`, four sites:
`:538` (`Interface.as_dict`), `:1100` (escalate preview), `:1158`
(`cross_interface` result), `:1239` (escalate result).

```python
"difficulty": round(self.difficulty, 6),
```

**What happens.** `round(x, 6)` returns `1.0` for any `x >= 0.9999995`. A gap with
`1 - D <= 5e-7` is therefore *reported* as difficulty exactly `1.0`. But `1.0` is
not a difficulty in this model — it is a wall, the one value
`_next_difficulty` refuses to return and `potential_from_difficulty` refuses to
invert. The record says "this gap cannot be crossed" about a gap that was just
crossed successfully.

**Reproduction.**

```python
import sys; sys.path.insert(0, '.')
from agent_metabolism.interface_engine import *

m = Layer("Macro", "M", measured={"t": 1.0})
q = Layer("Quantum", "Q", measured={"t": 1e-12})

for k in (6, 7, 8):
    D = 1.0 - 10.0 ** (-k)
    e = FractalEvolver([m, q])
    g = Interface("Macro", "Quantum", "c", span=10, difficulty=D)
    e.seed(g)
    r = e.cross_interface(
        g, NarrowCondition("x", "mech", "art-1", 2.0, replicable=False)
    )
    print(D, r["difficulty"], r["verdict"])
```

Observed:

```
0.999999   0.999999  PASS     <- 1e-6 tolerance survives
0.9999999  1.0       PASS     <- 1e-7 tolerance is reported as a wall
0.99999999 1.0       PASS
```

**What it corrupts.** Every consumer of the result dictionary: `render_chain`
output, `evolver.history`, the membrane's log entries, and — see ERR-003 — the
witness digest's antecedents. A reader of the history cannot tell a
1e-7 gap from a 1e-12 gap, and cannot tell either from a genuine wall.

**Why it was not caught.** The suite's difficulties cluster at `0.8`, `0.9`,
`0.95` and `0.99`, all of which round-trip through six decimals without loss. There
is exactly one near-wall test — `test_difficulty_never_reaches_one` at
`tests/test_interface_engine.py:573` — and it is the clearest illustration of the
blind spot. It seeds `difficulty=0.999999` and asserts the *child* is below `1.0`:

```python
evolver.seed(Interface("A", "B", "c", span=1, difficulty=0.999999))
result = evolver.cross_interface(("A", "B", 0),
                                 {"mechanism": "c", "evidence": "e"})
self.assertLess(result["sub_interface"]["difficulty"], 1.0)
```

The child is `0.9999995`, which floats just below the rounding threshold and
survives as `0.999999`. The test passes *one step away* from the defect. Seed
`0.9999999` instead and the child is reported as `1.0` — the guard against walls
would fail, but only because it happened to trip over the rounding, not because it
was testing for it. A guard that checks the child's value cannot detect a parent
that was already misreported.

The defect was found because the regression in `experiments/` needed `1 - D` down
to `1e-9` for the representation-boundary probe, and the fit collapsed outright:
every hard row shared the same `difficulty`, and `math.log(1.0 - 1.0)` raised
`ValueError` because the reported value was exactly `1.0`.

**Workaround in `experiments/`.** The CSV carries both `difficulty` (the engine's
report) and `difficulty_exact` (the value that was set); all regressions use the
latter. This is a workaround, not a fix: the corruption is still in the engine's
public output.

**Proposed fix.** Do not round in the result dictionary. Round at render time, in
`format_span`/`render_chain`, where a human is the consumer and six decimals are
appropriate.

Blast radius, checked rather than assumed: `_install`/`_replace_node` copy the
`Interface` object and never round-trip through `as_dict`, so the fix does not
change the model's state. `Layer.as_dict` and `NarrowCondition.as_dict` are
unaffected. The at-risk call sites are tests that compare a rounded field to
9 decimal places — `tests/test_interface_engine.py:595`
(`assertAlmostEqual(..., places=9)`) is the one that would tighten, not loosen,
once the rounding is gone.

---
**Fix.** Rounding left the data and moved to the renderer. `Interface.as_dict`,
`cross_interface`, `escalate` and the failure record now carry `float(...)`, the
value the engine actually holds; `_format_difficulty` decides how many digits a
human should see and spends more of them as `1 - D` shrinks. Rendering at three
decimals for a 0.9 gap and at twelve for a `1 - 1e-9` gap is the same number
reported honestly, which rounding to a fixed six was not.

Pinned by `tests/test_witness_binding.py::TestNoFalseWalls` and, from outside the
engine, by `tests/test_experiments.py::test_the_engine_report_and_the_exact_value_now_agree`
-- a test that asserts the two columns agree everywhere and therefore *failed*
before the fix. That is the distinction the README has to make now: the old test
pinned a workaround, the new one pins an invariant.


## ERR-002 — the ladder stops being fractal and keeps reporting PASS

**Location.** `agent_metabolism/interface_engine.py:588`, inside
`_next_difficulty`.

```python
def _next_difficulty(difficulty: float, ratio: float) -> float:
    """Close half the remaining room. Never returns 1.0, which would be a wall."""
    candidate = difficulty + (1.0 - difficulty) * ratio
    ceiling = 1.0 - 1.0e-9
    return min(candidate, ceiling)
```

The clamp is deliberate and correctly motivated: a child at difficulty `1.0` would
be a wall, and a wall has no successor. The defect is not the clamp. It is that
**nothing reports when the clamp engages**.

**What happens.** Once `difficulty >= 1 - 1e-9`, `_next_difficulty` returns the
ceiling unchanged, so the child is exactly as hard as the parent. The engine's
central invariant — that each generation is strictly harder — is violated. Yet
`cross_interface` returns `PASS`, because the crossing itself did happen. The
fractal law silently stops holding while every signal a caller normally checks
stays green.

**Reproduction.**

```python
for k in (8, 9, 10):
    D = 1.0 - 10.0 ** (-k)
    e = FractalEvolver([m, q])
    g = Interface("Macro", "Quantum", "c", span=10, difficulty=D)
    e.seed(g)
    r = e.cross_interface(
        g, NarrowCondition("x", "mech", "art-1", 2.0, replicable=False),
        next_condition="inside",
    )
    child = e.frontier()[0]
    print(k, r["verdict"], child.difficulty > g.difficulty,
          e.invariants()["self_similar"])
```

Observed:

```
8   PASS  True   True      <- still a fractal
9   PASS  False  False     <- clamp engaged, verdict unchanged
10  PASS  False  False
```

Bisection on `_next_difficulty` locates the boundary at
`D* = 0.9999999990000008` (`1 - D* = 1e-9`). From `D = 0.99` the ladder holds 24
generations before the clamp; the paper reports this number.

**What it corrupts.** A chain that reports `PASS` at every rung and is not
self-similar. The only detector is `FractalEvolver.invariants()`, which is a
whole-chain check the caller has to remember to run. `cross_interface` cannot
detect it alone, because the clamp is a property of the successor rather than of
the crossing.

**Second-order effect.** The violation message is itself corrupted by ERR-001:
it prints `difficulty did not rise (1.0000 -> 1.0000)` for a parent at
`0.999999999` and a child at `0.999999999`. The diagnostic that is supposed to
explain the failure repeats the same rounding that hides it.

**Proposed fix.** Return a fault (`DIFFICULTY_SATURATED`, non-decisive) from
`_next_difficulty`'s callers when `candidate >= ceiling`, and have
`cross_interface` attach it to the result. The crossing stays `PASS`; the status
gains a note that the chain has stopped being a fractal. Blast radius: tests
asserting exact `faults` lists, and `invariants()`, which would gain a nameable
cause. Backwards-compatible if the fault is non-decisive.

---
**Fix.** Saturation is now reported instead of hidden. `_successor_saturates`
asks, before any child is built, whether closing half the room would in fact
narrow it. If not, the crossing still returns `PASS` -- it happened, and claiming
otherwise would be the opposite lie -- but no rung is spawned, `terminal` is set
on the record, and a **non-decisive** `DIFFICULTY_SATURATED` fault explains why.
`invariants()` gains `terminal_rungs` so a caller can tell "the chain is short"
apart from "the chain has nowhere further to go", and `self_similar` keeps its
old meaning: no counterfeit rung exists, so no violation is manufactured.

The alternative considered and rejected was to keep the child and make the
crossing `FAIL`. That would report a crossing that demonstrably happened as not
having happened; the defect was the silence, not the `PASS`.


## ERR-003 — the witness does not bind the claim

**Location.** `agent_metabolism/interface_engine.py:1185`, the digest payload
built by `cross_interface`.

```python
self._record(result, digest_payload={
    "interface": list(crossed.key),
    "mechanism": spec.mechanism,
    "evidence": spec.evidence,
    "amplification": checked,
})
```

**What happens.** The witness commits to *which* interface, *which* mechanism,
*which* artefact, and the amplification actually granted. It does not commit to
the gap's **difficulty** and does not commit to **span before or after**. Two
crossings of different gaps by the same mechanism with the same cited artefact
therefore produce byte-identical digests.

**Reproduction.**

```python
digests = {}
for D in (0.90, 0.99, 0.9999):
    e = FractalEvolver([m, q])
    g = Interface("Macro", "Quantum", "c", span=10, difficulty=D)
    e.seed(g)
    e.cross_interface(
        g, NarrowCondition("x", "mech", "art-1", 2.0, replicable=False)
    )
    digests[D] = e.history[-1]["digest"]

print(digests)
print(len(set(digests.values())), "distinct digests for", len(digests), "claims")
```

Observed:

```
0.9     sha256:d5094052d5db629bddba563af7af33034538e131fe9fef119c87e92af11d1fbc
0.99    sha256:d5094052d5db629bddba563af7af33034538e131fe9fef119c87e92af11d1fbc
0.9999  sha256:d5094052d5db629bddba563af7af33034538e131fe9fef119c87e92af11d1fbc
3 claims, 1 distinct digest
```

**Why this one matters most.** The premise of this repository is that a claim is
only as good as the witness that binds it, and that an un-witnessed claim is
`INVALID` rather than merely weak. A digest that omits the quantity distinguishing
two claims is exactly the failure the ledger exists to prevent: it lets a
recorded crossing at `D = 0.90` stand as a witness for a crossing at
`D = 0.9999`. The ledger is not lying; it is answering a different question than
the one being asked of it.

The paper's Experiment C is directly affected in interpretation. It reports
`D_50` values to four decimals for three fidelity tiers. Those measurements are
sound — the experiment carries `difficulty_exact` — but the *engine's own witness*
could not have told the three tiers apart.

**Proposed fix.** Add `difficulty` and `span_before`/`span_after` to the digest
payload. This changes every digest in the system, so it is a breaking change for
any stored audit trail; it needs a version prefix (`sha256:v2:...`) rather than a
silent swap. Also consider digesting the full `NarrowCondition`, including
`replicable` and `cost`, since status is derived from `replicable` and an auditor
cannot currently verify a `NARROW`/`EXPANSIVE` call from the witness alone.

---
**Fix.** The witness is now **derived from the record**, not assembled at the call
site. `witness_payload` walks a declared field list
(`interface`, `verdict`, `difficulty`, `span_before`, `span_after`,
`amplification`, `status`, `mechanism`, `evidence`, `replicable`) plus the sorted
fault kinds, and `_record` calls it on the record it is about to append. A field
present in the record is in the witness; a field the record lacks shows up as
absent rather than as silently unbinding. Hand-assembly is how the difficulty was
lost, so hand-assembly was removed as a possibility rather than corrected once.

The digest is versioned in place -- `sha256:v2:<hex>` -- so `startswith("sha256:")`
keeps working for existing consumers while a reader can tell which contract a
stored witness was written under. `verify_witness` recomputes the digest from the
record, which is the half of the contract nobody exercises: editing the
difficulty, either span, the verdict, the status, the evidence or the recorded
provenance all now break it.

`upgrade_witness` re-witnesses a version-1 record. It **does not** restore the
old guarantee, and the docstring says so: a v1 witness never committed to the
difficulty, so nothing can check whether its difficulty moved. Upgrading records
the superseded digest (bound into the new payload) and freezes the record as it
now stands. An upgraded witness is trustworthy from the moment of upgrade, not
from the moment of the original crossing. There was no persisted v1 ledger in this
repository to migrate -- history lives in memory on each evolver -- so the upgrade
path exists for external consumers, not to rewrite a history that does not exist.

Pinned by `tests/test_witness_binding.py::TestTheWitnessBindsTheClaim`,
`TestVerifyWitness` and `TestUpgradeWitness` (32 tests in that file, including the
exact collision that motivated the entry: four difficulties, four distinct
digests where there used to be one).


## PRE-001 — the biological premise, corrected before publication

Not a code defect, but a defect in the paper's premise, and it would have been
fatal with a physics referee. Recorded here because it was caught by the same
process and because the correction is what made the quantitative result possible.

**The error.** The first draft of the parameter table took the long-lived
oscillations reported for the FMO complex at cryogenic temperature
(Panitchayangkoon et al., PNAS 2010) as evidence that the protein scaffold
extends electronic coherence at room temperature. Under that assumption the
biomimetic mechanism would gain a coherence advantage, and the paper's
counterfactual would have claimed that a protein matches or beats a dilution
refrigerator *in coherence time*.

**The correction.** The long-lived beats do not survive to physiological
temperature; the room-temperature residual oscillations are assigned to
vibrational rather than electronic coherence (Duan et al., PNAS 2017). At 300 K the
scaffold buys **no** coherence-time advantage over bulk solvent. The parameter
table now encodes `T2_FMO_300K = 1e-12 s`, identical to the bulk value, tagged
`literature`.

**Why this rescued the argument rather than weakening it.** Under the corrected
premise the biological mechanism still wins the counterfactual by three orders of
magnitude (reach `1e5` versus `1e2`), while gaining *nothing* in coherence. That
makes reach a function of replicability and fidelity rather than of physical gain,
which is a sharper and more falsifiable claim than the one the first draft was
making. The wrong premise would have produced a result a referee could dismiss by
citing one paper; the corrected premise produces a result that contradicts the
field's usual progress metric.

**Status.** Closed. Encoded in `experiments/parameters.py`; the contested reading
is discussed in the paper's §IV-D.

---

## PRE-002 -- the fit that measured its own conditioning

### What would have been wrong

Experiment E checks the derived rate of the local exponential form, `1 + f**2/5`,
against a least-squares fit on a window of relative half-width `f`. The windows sit
at `D` between 0.99 and 0.99999, where the spread in `D` is `1e-4` or smaller. The
first version fitted `y = a + alpha * D` on the raw values, with `D` near 1.

Two defects, both invisible in the summary number:

1. **The design matrix is ill-conditioned.** Columns of `1` and `D ~ 0.999` differ
   by four orders of magnitude in scale, so `numpy.linalg.lstsq`'s singular-value
   cutoff can drop the slope. The fit is biased by roughly `1e-3 * f**2`, which is
   the same order as the `O(f**4)` term the check exists to resolve.
2. **The window was sampled endpoint to endpoint.** The two endpoints stand for
   half a cell each and were counted as full ones, giving the raw moment sums an
   `O(1/samples)` bias: `1e-5` in the fitted rate at 401 points, again the same
   order as the quantity under test.

The visible symptom was a residual against `1 + f**2/5` that did not scale as
`f**4` -- it hovered at `~1e-3 f**2` and shrank by a factor of 4 per halving
instead of 16. Had the paper reported only the agreement at one `f`, it would have
published a rate agreeing to five digits with a theory that was right for reasons
the fit was not testing.

### What a consumer would have seen

Nothing. There was no error, no warning, and no NaN. The fit converged, the R^2
was 0.9993, and the number was wrong in the fourth significant figure of the
correction term -- exactly the figure the experiment was built to check.

### Fix

- `_linear_fit` now fits on centred data (`x - mean(x)`), which is algebraically
  identical and numerically stable. The docstring states why, because "centring is
  not cosmetic" is the sort of comment that saves the next person an hour.
- `model.geometric_window` and `model.absolute_window` sample cell centres rather
  than endpoints, removing the `O(1/N)` moment bias instead of arguing about it.
- The residual is now a *tested* quantity rather than a reported one:
  `test_the_residual_matches_the_fourth_order_coefficient` checks it against the
  derived `3 f**4 / 35` to 5%, and `test_the_residual_really_is_fourth_order`
  checks that it shrinks by at least 8x per halving of `f`. Against the old code
  both fail.

### Why this one is worth recording

PRE-001 was a premise corrected by reading the literature. This one is a
*measurement* corrected by deriving the number it was measuring. The check that
found it -- does the residual scale as the next term of the series -- is the same
check the paper applies to its own law, and it took a closed form to make it
possible. A numerical experiment whose target has no closed form cannot be audited
this way, which is an argument for keeping the algebra in `model.py` where the
experiments can be tested against it.

---

## PRE-003 — the experiment was asked to confirm a shape its own budget forbids

### What was proposed

A design brief for `experiments/true_emergence_experiment.py` specified the
conclusion before specifying the measurement. Group A (a correlational agent) would
inflate its apparent reach; Group B (a causally witnessed agent) would show a
genuine crossing of difficulty boundaries and, because only real connections nest
fractally, "once the base layer is connected, each further layer costs less, so
reach explodes super-exponentially". The deliverable was a plot with B's curve
turning up, and a paragraph explaining the turn.

The brief is internally coherent, which is what makes it dangerous. Each sentence
is plausible, and the requested figure is the one the paper would most like to
publish: it would credit the labelling system with a capability rather than with an
absence of lies.

### Why the requested result cannot occur

A ladder of depth $K$ is completed only when its prefixes are, so

    P(pass K) <= P(pass j)  for every j <= K

under *any* joint law of the layers. No independence assumption is used, so no
conditional structure and no nesting discount can reverse the inequality. Reach,
defined as the completion of a chain, is non-increasing in depth by construction.

The brief's mechanism --- each layer cheaper than the last --- is real and is
already in the paper as Eq. (19): a path of $K$ boundaries costs $n+\log_{10}K$
nines rather than $nK$, so the $j$-th layer costs $\log_{10}(j/(j-1))$ nines. Those
marginals are strictly positive and strictly decreasing: `0.301, 0.176, 0.125,
0.097, 0.079`. Decreasing towards zero is a discount. A sum of non-negative terms
cannot fall, so the total cost is increasing and reach cannot turn up. An
"explosion" requires a *negative* marginal cost, which is not a discount but a
different claim about what is being measured.

### What a consumer would have seen

Had the brief been implemented as written, Group B's curve would have had to be
produced by construction: either a per-layer probability that rises with depth
(assumed, not measured) or a reach metric silently swapped for per-crossing
amplification. Both produce a rising curve. Neither is a discovery, and both are
indistinguishable in a PDF from the result the brief predicted. The paper would
have reported an "exponential emergence" that is an assumption wearing a plot.

### What was done instead

- The nesting discount was made a *parameter* $\delta$ rather than an assumption,
  and swept over $\delta \in \{0, 0.1, 0.25, 0.5, 0.75, 1\}$. No admissible schedule
  raises reach with depth; $\delta = 0$ --- every layer after the first free --- is
  flat, and the paper's budget is the least generous of the family.
- The refutation was made load-bearing in code: `prefix_bound()` states the
  inequality, and `test_the_model_can_express_an_explosion` asserts that a schedule
  with *negative* marginal cost does produce rising reach. Without that test,
  "no admissible schedule explodes" would be a statement about the arithmetic of
  the sweep rather than about the hypothesis.
- The comparison was kept, including the part that flatters the labelling system:
  Group A's assertion overstates the causal rate by $2.08\times10^{10}$ at
  $D=0.99$ and depth 6, and Group A holds a *positive floor* of 1572 crossings
  while Group B's witnessed reach passes through one crossing and keeps falling.
  The negative result is about the explosion, not about the value of the witness.
- The one real gain was quantified: nesting buys 9.3 orders of magnitude in
  the *cost of depth* ($7.94\times10^{-13}$ to $1.60\times10^{-3}$) and nothing in
  the difficulty exponent, which stays at $1.00$ while the independent model's
  equals the depth.

### Why this one is worth recording

ERR-001..003 were defects in code: the code said one thing, the artefact another,
and a test could be written that fails against the parent commit. PRE-003 has no
parent commit. What it has is a specification whose acceptance criterion was
"produce this curve", and the only defence against that is a mathematical
statement that the curve is not available --- plus a test that the machinery could
have produced it if it were.

The repository now carries both. `experiments/true_emergence_experiment.py` is the
measurement, `tests/test_true_emergence.py` (39 tests) pins the inequality against
500 adversarial ladders and pins the model's ability to say yes, and the paper's
Section V-E (Experiment H) reports the negative result with the numbers that
support it.

The general lesson is not "check your assumptions". It is that a negative result
needs *two* things to be publishable: an argument for why the effect cannot exist,
and a demonstration that the apparatus was capable of detecting it. The first stops
the paper from being wrong; the second stops the reader from suspecting the authors
of incompetence rather than of honesty.



They are not independent, and the compound failure is worth stating plainly.

ERR-001 makes hard gaps indistinguishable in the record. ERR-003 removes
difficulty from the witness entirely. Together they mean that the region the
paper studies — the last few orders of magnitude before the wall — is the region
in which the engine's own audit trail is least able to reconstruct what happened.
ERR-002 then lets the ladder leave the self-similar regime while `cross_interface`
keeps answering `PASS`. A chain can therefore walk into the clamp, cross a gap
that does not exist, and emit a witness that does not distinguish it from the last
one.

None of this affected the paper's *numerical* results, because `experiments/`
read `difficulty_exact` and ran `invariants()` explicitly. What it affected is the
claim that the engine could have discovered this on its own. It could not. It was
found by writing an experiment that needed precision the engine never promised,
which is an argument for the experiments beyond the ones the paper makes.

That is now also the paper's §IV-C ("Self-Auditing the Auditor"): the defects are
reported as the model's strongest internal evidence, because a witness chain that
cannot be shown to fail is not a witness chain.

## PUB-001 — a released paper with no prior-art pass

This entry is not a code defect. It is a process defect, recorded because the
artefact it produced is public and citable.

**What happened.** v1.0.0 (commit `8308367`, DOI `10.5281/zenodo.23949827`) shipped a
26-page manuscript whose bibliography carried 19 entries and no section stating
what the paper's claims owe to prior work. The repository's own rule for
citations — resolve an identifier before writing the entry — had been applied to
the 19 entries, and that was mistaken for having checked whether the *claims* were
already made. It is not the same check. The citation auditor answers "does this
identifier name this paper"; it cannot answer "has someone else already derived
this".

**What a consumer would see.** A reader who knows Schaeffer et al. (2023) would
find the paper's emergence section plausible and its bibliography silent about the
paper it is responding to. A reader who knows the verification-cost literature
would find $B^{*}$ presented without the nearest existing threshold. Neither is a
false number; both are an unearned appearance of priority.

**Blast radius.** Text and bibliography only. No experiment, no figure, no
measured coefficient, and no verdict in Appendix A moved. The 26-page length is
unchanged, so pagination in the archived v1.0.0 PDF still matches the v1.0.1 PDF
except in the Introduction and Appendix A.1.

**Fix.** A retrieval pass over arXiv and CrossRef
(`experiments/literature_search.py`, snapshot in
`experiments/paper/data/literature_search.json`), an audit written as
`experiments/paper/LITERATURE_AUDIT.md` that states what each near neighbour does
*not* do, ten identifiers added and all re-resolved (27 `PASS`, 0 `FAIL`, 2
`INVALID`), and a `Relation to prior work` subsection in the Introduction. One
near neighbour (Gill 2026, SSRN) is cited rather than routed around, and the paper
now says explicitly that its $B^{*}$ is not the first verification threshold.

**Not closed by this entry.** The retrieval was two channels deep; Semantic
Scholar returned HTTP 429 and is recorded as `UNAVAILABLE` rather than replaced.
Paywalled venues are a `BLIND` region, which is why the text says "to our
knowledge" and not "no one has".

---

## ERR-004 — the witness is not bound to the prediction it checks

**Location.** `experiments/benchmark_libero_audit.py`, `MembraneCEL.expect` and
`MembraneCEL.check`, in the first version of the LIBERO audit.

```python
    def expect(self, expected):
        return self.gateway.register_digest("execute", expected.digest(self.grid))

    def check(self, expected, observed):
        text = observed.text(self.grid, origin=expected)
        warrant = self.gateway.request_action_warrant(
            {"kind": "inline", "text": text, "digest": witness_digest(text)}, "execute")
```

**What happens.** `MembraneGateway` keeps *one set of acceptable digests per
capability*, and registering a digest adds to that set; a warrant is issued if the
offered digest is a member. Nothing ties a grant to the registration that was made
for the chunk currently under review. So a scene that was legitimately predicted
once is accepted for the rest of the run: the gate answers "was this scene ever
predicted?" when the experiment meant "is this the scene this chunk predicted?".

**Reproduction.** 100 episodes of `libero_spatial` task 2 with a friction drift.
At transport chunk 3 the bowl slips out of the gripper and falls back to the
table. The plan had registered the canonical text

```
scene@8,22,90:0,0,0:table
```

ten chunks earlier, for `close`, when the bowl really was on the table. The
observation at transport chunk 3 is byte-identical to it, so
`request_action_warrant` granted a claim of `gripped` and the refusal counter did
not move:

```
CHECK expected='scene@8,22,90:0,0,0:gripped' observed='scene@8,22,90:0,0,0:table' -> True
CHECK expected='scene@8,22,90:0,0,0:placed'  observed='scene@8,22,90:0,0,0:table' -> True
succ False ref 0
```

Three episodes ended with the bowl 245-269 mm from the plate, having passed **every**
witness check (`refusals == 0`, `replans == 0`). The reported comparison was
therefore flattering in the one direction that matters: it credited the layer with
successes it had not verified.

**Why this one matters.** It is ERR-003 in a new place. There the digest omitted a
field that distinguished two claims; here it omitted the *identity of the claim*.
Both let one witness stand for another, and both are silent: the ledger verifies,
the chain passes, and the only symptom is a number that is too good.

**Fix.** Every registration is prefixed with a monotonically increasing ordinal
that is never reset, and the check for that chunk carries the same ordinal:

```python
    def _canonical(self, inputs, ordinal, origin=None):
        return "chunk{}:{}".format(ordinal, inputs.text(self.grid, origin))
```

A registration is now redeemable exactly once, by the chunk that made it. A
regression cannot satisfy a stale prediction because no two predictions share a
key, and an episode boundary cannot alias an earlier episode's keys because the
counter lives on the gateway wrapper, not on the episode.

**Blast radius.** The layer's success, refusal, replan and latency numbers on
LIBERO, and nothing else: the two baselines never call the gateway, and no other
experiment imports this module. The corrected run is the one in
`experiments/libero_audit_summary.json`; the pre-fix run is not archived, which is
why the reproduction above is written out rather than linked.

**Pinned by.** `tests/test_benchmark_libero_audit.py::GateBindingTest`, five tests:
a predicted scene is granted, a diverged scene is refused, a regression cannot
reuse an earlier prediction, an ordinal is never reused across episodes, and the
ledger still verifies afterwards. The second of those tests fails against the
pre-fix code.

---

## ERR-005 — the figures are not a function of the data

**Location.** `experiments/cel_paper/figures/ad_cel.png`,
`experiments/cel_paper/figures/libero_audit.png`, and the `cp` in
`experiments/cel_paper/build.sh` that shipped the first of them.

**What happens.** Both figures were hand-assembled PNGs. The driving one was
copied into the paper at build time from `experiments/ad_cel_figure.png`, but no
script in the repository writes that path: a search for its two panel titles
(`Cost in milliseconds`, `Reliability across drift`) across the whole tree returns
nothing. Four consequences, all of them checked against
`experiments/ad_cel_summary.json` and `experiments/ad_cel_data.csv`:

1. Its price panel drew 1.69 s / 1.70 s and 905 ms / 16 ms, and annotated the gap
   as `+78 ms / −889 ms`. No file contains the first two pairs, and the annotation
   is the *manipulation* audit's latency decomposition. The driving audit's own
   cost is 30.7 s → 32.6 s per episode.
2. Its reliability panel had three categories, merging `wet_and_lag` into the
   others: ten episodes and five of the twenty collisions were not drawn.
3. Its trace panel labelled both arms "collision" using `final speed > 0.5 m/s`
   as the test. The surviving car ends its trace at 10.6 m/s because it is
   still driving, so the panel said the layer crashed too.
4. Both files' legends said `Safe-VLA` / `Safe-Planner`, names the prose had
   retired (and the driving file said both, in different panels).

The manipulation figure failed the same way from the other side: its
"Recovery after each drift onset" panel plotted arrival counts (6, 9, 3, 6) that
correspond to no field in `libero_audit_summary.json`, and the rolling-success
curve the caption described was not in the file at all.

**Reproduction.**

```bash
cd experiments/cel_paper
grep -rn "Cost in milliseconds" ../..          # no hit outside a shell log
python3 - <<'PY'
import csv
rows = list(csv.DictReader(open("../ad_cel_data.csv")))
crashes = [r for r in rows if r["collision"] == "True"]
print(len(crashes), sum(r["collision_kind"] == "pedestrian" for r in crashes))
PY                                             # 20 crashes, 19 pedestrian
```

**What a consumer would see.** A panel of numbers that look like measurements and
are not, in a paper whose thesis is that an unverified claim must not be trusted.
The referee-visible symptom is milder and still fatal: four figures, none of them
cited by number anywhere in the body.

**Fix.** `experiments/cel_paper/make_figures.py` draws both figures from the
audit's JSON/CSV and from nothing else; `build.sh` runs it on every build, so a
figure can no longer belong to an older run. The traced episode is exported by
the audit itself (`--trace-json`) and its key instants are macros
(`\ADTraceVGate`, `\ADTraceImpact`, `\ADTraceCELStopT`, and the rest) rather than
numbers
typed into a caption. `test_figures.py` pins the property:

```bash
cd experiments/cel_paper && python3 test_figures.py   # 8/8
```

including a byte-for-byte comparison between the shipped PNG and a fresh draw
from the data.

**Blast radius.** The CEL paper's Figures 2 and 3, their captions, and the two
sentences in the driving section (VI) that quoted panel numbers. No experiment,
no summary and no table was affected: the numbers in `data/*.tex` were already
generated from the audit outputs, which is why the discrepancy was visible at all.

---

## PUB-002 — a caption asserted what the figure did not show

**Location.** `experiments/cel_paper/main.tex`, the captions of
`fig:results` and `fig:driving`.

**What happens.** Two claims were checked against the files and failed. The
driving caption said "every crash is a pedestrian, and every one of them is in a
wet channel"; the episode rows give **19 pedestrian and one lead-vehicle**
collision, the latter in `wet_and_lag`. The manipulation caption described a
rolling-five recovery panel that its figure did not contain, and the prose
quoted recovery counts (27 and 22 episodes for the baseline, 0 and 0 for the
layer) that the figure could not support because the panel was something else
entirely.

**Reproduction.**

```bash
python3 -c "
import csv, collections
rows = list(csv.DictReader(open('experiments/ad_cel_data.csv')))
print(collections.Counter(r['collision_kind'] for r in rows if r['collision']=='True'))"
# Counter({'pedestrian': 19, 'lead': 1})
```

**Fix.** The claim is now scoped to what the data says (nineteen of twenty, and
every collision in a wet world), the recovery panel exists, and the caption's
per-panel sentences name the panel that carries each number. A test asserts the
count and the wetness, so the sentence cannot drift again without failing:

```bash
cd experiments/cel_paper && python3 test_figures.py   # test_wet_road_claims
```

**Blast radius.** One caption sentence, one paragraph of the driving section (VI),
and the `(a)`–`(d)` citations added to the surrounding text. The tables were
already correct; this was the prose disagreeing with them.

---

## Reprocessing

```bash
python3 -m unittest discover -s tests            # 1 086 tests, all green
python3 -m unittest tests.test_witness_binding -v # the three defects, pinned
python3 -m experiments.run_experiments           # regenerates the evidence
cd experiments/cel_paper && ./build.sh           # regenerates numbers, figures, PDF
cd experiments/cel_paper && python3 test_figures.py   # the figures are the data
```

`tests/test_witness_binding.py` is the file that ERR-001..003 produced, and
`tests/test_topology.py` is the file PRE-002 produced. The former has 32 tests
and asserts the *properties* that were missing rather than the fixes that supplied
them: no reported wall below 1.0, no rung spawned at the ceiling, and no two
distinct claims sharing a witness. Each of those tests fails against the parent
commit of the fix, which is the only property a regression test needs.
