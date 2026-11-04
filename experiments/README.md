# Fractal-interface experiments

The paper in `paper/` argues that an interface is a narrowing condition, not a
published surface. This directory is where the argument is made checkable.

```bash
python3 -m experiments.run_experiments      # ~2 s, regenerates every number
python3 -m experiments.emergence_experiment # ~3 s, Experiment F and its figure
python3 -m experiments.causal_experiment    # ~20 s, Experiment G, real disk IO
python3 -m experiments.solidification_experiment # ~2 s, Experiment I
python3 -m experiments.critical_budget_experiment # ~12 s, Experiment J
python3 -m experiments.nested_solidification_experiment # ~5 s, Experiment K
cd experiments/paper && latexmk -pdf main.tex
```

Everything the paper quotes is produced by the first two commands. Nothing is typed
into the manuscript: the tables are `\input` from `paper/data/*.tex`, the figures
read `paper/data/*.csv`, and the emergence section's numbers arrive as macros in
`paper/data/emergence_macros.tex`. If a number in the body disagrees with the data,
the build is wrong, not the prose.

## What is here

| file | what it is |
| --- | --- |
| `parameters.py` | every constant, tagged `literature`, `derived`, `policy` or `assumed` |
| `model.py` | the algebra, with no knowledge of the engine it is testing |
| `topology.py` | five strata, fidelity per boundary *and per function*, the seamless criterion |
| `run_experiments.py` | the five experiments, the regressions, the exports |
| `emergence_experiment.py` | Experiment F: a published scaling law read through the interface law, and the strobe |
| `causal_experiment.py` | Experiment G: does the label cause the verdict? A do-operator test |
| `solidification_experiment.py` | Experiment I: what eats the marginal cost, and why the discount is bounded |
| `critical_budget_experiment.py` | Experiment J: how deep a budget goes, and the critical budget $B^{*}$ |
| `nested_solidification_experiment.py` | Experiment K: does compiling one layer make the next one cheap? |
| `solidification_theory.md` | the derivation behind Experiment J, including its negative result |
| `fractal_experiment_data.csv` | 7 000 crossings (A--C) |
| `fidelity_data.csv` | 64 rows of boundary fidelity and noise budget (D) |
| `exponential_data.csv` | 48 window fits of the local exponential rate (E) |
| `emergence_data.csv` | 401 rows of the loss-to-fidelity map (F) |
| `causal_data.csv` | 1 000 judged tasks (G) |
| `fit_results.json` | every fitted coefficient, standard error and $R^2$ |
| `paper/` | IEEE draft, bibliography, generated tables and plot data |

The separation between `model.py` and `run_experiments.py` is deliberate. The
engine is the object under test; if it also computed the prediction, the test
would be circular.

## The experiments

**A — counterfactual (1 000 rows).** 500 gaps, each crossed twice: by a cryostat
that extends coherence $10^9\times$ and by a protein scaffold that extends it
$10^0\times$. Difficulty is identical within a pair.

**B — the law (3 000 rows).** 1 000 three-generation chains over sampled potential
and slack. Tests the derived law against a single-curve alternative and against
an exponential rival.

**C — the boundary (3 000 rows).** Three fidelity tiers swept down the tolerance
window, plus a deterministic probe of the engine's successor function.

**D — the topology (64 rows).** Five strata and four boundaries. Each boundary's
fidelity, resistance, nines and verdict at four thresholds; the path's cumulative
transmittance; and the noise each coarser stratum delivers to the quantum one. No
randomness: this is arithmetic on the fidelity table, and the experiment exists so
that the arithmetic is in the package rather than in a paragraph.

**E — the limit map (48 rows).** Two mechanisms, four anchor difficulties, four
relative window widths, two absolute widths. Fits `ln A = a + alpha D` in each and
compares the measured rate against the derived `1 + f**2/5`. The measured residual
is then compared against the derived `3 f**4/35`, so the check tests the second
order term as well as the first.

Experiments D, E, F and G write their own CSVs because they are not crossings: a
boundary has no reach, a window fit has no verdict, a scaling-law sweep has
no mechanism, and a judged task has a verdict but the ordinary schema has no
column for which judge produced it. Forcing them into the
crossing schema would mean inventing a `reach` for a number that has none.

**F — the mirage (401 rows, 4 panels).** A published language-model scaling law
(Chinchilla; Kaplan is carried as the robustness check) is mapped through
`F = exp(-(L-E))` onto the same difficulty coordinate as the quantum gap. The
model's power law then fits with `R^2 = 1` *by construction* — reach and the
narrowing coordinate are both affine in `log N` — so the fit is reported as a
check on the implementation and not as evidence. What the experiment actually
claims is smaller and stranger: composing `m` boundaries translates the accuracy
curve rather than steepening it, so the 10–90% transition is `3.94` decades wide
and **independent of `m`**. Frontier models are trained about a decade apart,
which puts 3.9 points inside that ramp; a smooth sigmoid sampled four times reads
as a step. That is the mirage.

The falsifiable statement is the one in the paper: if a deeper task shows a
narrower transition, the bridge is wrong. The bridge itself is the softest joint
in the paper, and no scaling-law data can test it — a point the experiment's own
console output says out loud.

**G — the mirage, turned on ourselves (1 000 tasks).** Three judges score the
same agent task: one reads the context, one reads the harness log, one forces a
physical witness through `MembraneGateway`. The physical side is real — a real
directory, a real git repository, real SHA-256 digests, and the package's own
gateway recomputing every hash rather than trusting the declared one. 30% of
tasks are false completions (the tool reports success, nothing was written) and
50% of contexts are contaminated with an earlier "task completed successfully"
block, drawn independently of the outcome.

Measured false-completion rates: **100%** for the context judge, **79.8%** for
the log judge, **0%** for the causal judge. The interesting part is the
intervention, not the ranking: delete the artefact while holding the context
byte-identical and the two text judges do not move by a single task, while the
causal judge's pass rate goes 69.8% → 0. That is `P(PASS | do(Witness))` being
different from `P(PASS | Context)` *as an equality about a system*, not as a
claim about judgement quality.

Two limits are reported rather than buried. A hash witnesses a state, not an
action: an artefact already in place is indistinguishable from one just written,
and no measurement can separate them. And the witness is only as good as its
registration — poison the expected digest and the membrane certifies whatever is
on disk, which is why the intended hash has to be computed before the run and
held out of band.

**I — solidification (drift sweep, depth sweep).** A rung crossed against a
witness is compiled: it costs `c_c` instead of `c_v` until the environment moves.
The experiment prices the mechanism and bounds it. At fixed depth the discount is
`c_v/(c_c+c_d)`, a ratio of positive terms, so it cannot be exponential; the
exponential the paper's earlier refutation forbids reappears, correctly placed, as
*reach per unit budget*; and a detector is worth its price only while
`c_d < (1-rho)(c_v-c_c)`. The last row of that inequality is what Experiment J
turns into a budget.

**J — the critical budget (three sweeps, one negative result).** Given a budget
`B`, how much depth does each regime buy, and is there a budget below which
compiling is the wrong choice? The answer is a closed form,
`B* = c_v[p(c_v-c_c)+S]/(p(c_v-c_c)-c_d)`, and a refusal. The brief that
motivated the experiment expected the first verification -- a one-off `c_v` --
to put the compiled arm behind at small budgets. It does not: the unlabelled arm
pays the same `c_v` for its first rung, the difference factorises to
`(1/c_bar - 1/c_v)(B - c_v)`, and the compiled arm is never behind anywhere. An
interior threshold needs a premium only the compiled arm pays, and then it is
affine in that premium with a slope `G/(G-1)` nothing was fitted to, and it
diverges at a monitor price of `(1-rho)(c_v-c_c)`. Reach breaks the other way:
the depth advantage is unbounded, the reach advantage peaks at a finite budget and
returns to one at the horizon. The derivation, including the task-granularity
correction that moves the threshold by a rung, is in `solidification_theory.md`.

**K — nested solidification (two layers, one knee).** The previous experiment's
gain is a price ratio: a constant. This one asks whether a compiled layer makes
the layer above it cheap *in a way that compounds in the number of dimensions*,
and the answer is conditional. With a task spanning `d` levels of `L` children,
the flat arm re-verifies `c_v*L^d`; the nested arm consults each child's compiled
label, which succeeds with probability `kappa`, giving
`m_d = kappa*c_bar + (1-kappa)*L*m_{d-1}`. At `kappa = 0` the multiple is exactly
`G = 9.17` at every depth -- arithmetic, the architect's own objection. At
`kappa = 1` the marginal cost is `c_bar` for every `d` and the multiple is
`G*L^d`. The compounding base is `min(L, 1/(1-kappa))`, its knee is
`kappa* = 1-1/L = 0.75`, and the budget axis carries two thresholds rather than
one: `B* = 6.61` buys the base, `B_rec = 39.23` buys back the per-level premium,
and between them the nested arm is strictly behind -- the first non-degenerate
loss region in the paper. Reach is not consulted: the horizon still caps value.

## The CSV

Required columns come first, then the analysis columns:

```
generation, difficulty, narrow_condition, reach, status,
experiment, run_id, verdict, mechanism, potential, slack, amplification,
replicable, cost, censored, fidelity_mean, difficulty_exact,
achieved_slack, failure_kind, one_minus_d
```

Two conventions that look like defects and are not:

* **`status` is empty on uncrossed gaps.** `NARROW` and `EXPANSIVE` describe how
  far a crossing *carried*. A gap that was never crossed has no reach, so it has
  no status. Writing `NARROW` there would assert something no run measured.
* **`difficulty` and `difficulty_exact` now always agree.** They did not used to:
  `cross_interface` rounded its result to six decimals, so any gap with
  $1-D<5\times10^{-7}$ was *reported* as `1.0` although it is not a wall
  (ERR-001). The engine no longer rounds, and
  `tests/test_experiments.py::test_the_engine_report_and_the_exact_value_now_agree`
  asserts the equality across the whole sweep. Both columns are kept so an older
  CSV reads the same way and so the fix stays auditable.

`verdict` is `PASS`, `FAIL` or `INVALID` and is independent of `status`. A
cryostat that closes a gap for one laboratory is `PASS` + `NARROW`.

## What the experiments do not show

They do not show that the physical anchors are correct, that inverse rarity is
the right model of reach, or that the 300 K coherence numbers are settled. The
power law is *derived*, and the regressions check that the implementation realises
the derivation and where it stops — not that nature prefers power laws. The
paper's §IV-E says this at length; this note says it here so the CSV is not
mistaken for a measurement of nature.

They also do not show that the engine's own audit trail was sound while they ran.
It was not: running them exposed three silent defects in the engine
(`ERRATA.md`, ERR-001 to ERR-003), including a witness digest that did not bind the
gap's difficulty. A fourth defect, PRE-002, was in Experiment E itself: an
uncentred regression on `D ~ 0.999` biased the rate by the same order as the term
the experiment was checking. It is recorded because a measurement corrected by
deriving the number it measures is the same argument the paper makes about the
engine. The
structural probe now records `terminal` instead of a rung that was never really
narrower, which is why `structural_table.tex` gained a column. The 32 regression
tests that pin all three properties live in the repository at
`tests/test_witness_binding.py`; they need the package, so they are not in this
package's `source/` directory.

Several anchors are assumptions, not literature: the biomimetic reach ($10^4$,
swept over four orders of magnitude in the paper), the slack-noise fraction
($\sigma=\kappa(1-\mu)$, $\kappa=0.1$), and three of the four boundary
fidelities in `topology.py`. The one measured fidelity is the FMO trapping
efficiency, reused as the routing fidelity of the quantum--chemical boundary.

`topology.py` also makes a structural claim that a single number cannot express:
the same boundary has a routing fidelity and a phase fidelity five orders apart
(0.98 against $10^{-5}$). A model that reports one `F` per boundary is not
simplifying; it is answering a question nobody asked. That distinction is why the
seamless verdict is reported per *function*, and why the paper's §IV-E counts the
assumptions rather than hiding them behind one symbol.
