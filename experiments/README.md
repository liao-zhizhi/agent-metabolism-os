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
python3 -m experiments.physics_sandbox --sweep  # ~2.5 min, the 2-D push sandbox
# Experiment M needs the harness venv (vla-eval 0.9.0), see benchmark_integration/README.md
PYTHONPATH=. experiments/benchmark_integration/.venv/bin/python \
    -m experiments.benchmark_integration.run_benchmark  # ~3 min, 2 700 episodes
# Experiment N is the same comparison on a real LIBERO task (~25 min, 200 episodes)
MUJOCO_GL=egl PYTHONPATH=.:/tmp/LIBERO \
    experiments/benchmark_integration/.venv/bin/python \
    experiments/benchmark_libero_audit.py --episodes 100
# Experiment O is driving: 2-D kinematics, no simulator (~45 s, 200 episodes)
python3 experiments/ad_cel_audit.py --episodes 100
# the fifth paper: numbers, then figures, then PDF -- each from the data before it
cd experiments/cel_paper && ./build.sh && python3 test_figures.py
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
| `physics_sandbox.py` | Experiment L: the causal interface against a confidence-gated filter, on a 2-D table, in milliseconds |
| `benchmark_integration/` | Experiment M: the same comparison under the `vla-eval` protocol, plus a real LIBERO conformance run |
| `benchmark_libero_audit.py` | Experiment N: the comparison on a real LIBERO task, with the witness bound to each chunk's own prediction |
| `ad_cel_audit.py` | Experiment O: trajectory tracking under a friction and actuator drift, CEL against a confidence gate |
| `cel_paper/` | the fifth paper: `build.sh` regenerates numbers, figures and PDF from the data, and `test_figures.py` checks that the figures are that data |
| `solidification_theory.md` | the derivation behind Experiment J, including its negative result |
| `fractal_experiment_data.csv` | 7 000 crossings (A--C) |
| `fidelity_data.csv` | 64 rows of boundary fidelity and noise budget (D) |
| `exponential_data.csv` | 48 window fits of the local exponential rate (E) |
| `emergence_data.csv` | 401 rows of the loss-to-fidelity map (F) |
| `causal_data.csv` | 1 000 judged tasks (G) |
| `fit_results.json` | every fitted coefficient, standard error and $R^2$ |
| `paper/` | IEEE draft, bibliography, generated tables and plot data |
| `cel_paper/` | the fifth paper (CEL, manipulation and driving), its two data-derived figures, and its clean-room archive |

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


**M — the same comparison, in someone else's harness (vla-eval 0.9.0).** L's loop
and L's baselines were ours, which is the objection a reviewer is entitled to
make. So the comparison is repeated with the loop replaced: `vla-eval`'s
`PredictModelServer` serves each arm over WebSocket, its `SyncEpisodeRunner`
steps the environment and asks for an action at every control step, and its
`ResultCollector` aggregates the result. The environment is a `StepBenchmark`
subclass, so the harness's contract decides what a task is, not this repository.
Both drivers agree: the Python API and `vla-eval run --no-docker -c
cel_eval.yaml` produce 66.7% / 33.3% / 100.0% for A / C / B.

The 10 Hz control loop changes one thing about L's conclusion, and it changes it
in B's favour. L's witness was post-hoc, one check per push, so a drift window
cost one damaged push. Here each control step returns an observation, so the
witness sees the block one step into a coast, inverts the friction from a single
deceleration, and abandons the chunk before the block reaches the wall: damage on
the friction-shift suite is 0.0% against 6.7% for both baselines. What was a
bound on damage becomes prevention, and the enabling condition is stated rather
than assumed -- a control-rate observation stream, not the interface alone.

The baseline is the standard one -- act only when the policy's own confidence is
at least 0.80, else request a human (3000 ms, priced); `vla-eval` ships OpenVLA
and pi0 model servers but no filter of its own, so the gate is written here with
its threshold, its six points of noise and its takeover price declared before the
run. Under a 300-episode
regime schedule (nominal / slippery / nominal / rough / slippery / rough, 50
episodes per window) it scores 33.3% against A's 66.7% and B's 100.0%, and its
100 takeovers include **94 refusals of actions that would have succeeded**. The
mechanism is not timidity: a visible short push raises its uncertainty, and the
next chunk is the correction, so the gate vetoes the action that would have fixed
the error. It also refuses 4 of 4 opportunities at nominal friction, where nothing
has changed -- a confidence head is a function of the model, and this is what
that costs when the world is the variable.

Two numbers are added because the harness does not compute them: episodes from a
shift onset until the next autonomous success (A 20.6, C 35.0, **B 1.0**), and the
rolling-90% version (29.6 / 194.0 / 10.0). And one suite is a null control: when
the *task* moves and the physics does not (`layout_shift`, the frame 8 cm further
along), all three arms score ~100% and B merely costs less. A win there would
have meant the interface was detecting "unfamiliar" rather than "unmodelled",
which would have made the friction result an artefact. It does not win, and that
is the point of including it.

The environment work is reported as work. `vla-eval` 0.9.0, `robosuite` 1.4.0,
MuJoCo 3.2.3 and a CPU `torch` install; a real LIBERO `libero_spatial`
`OffScreenRenderEnv` is created, reset and stepped through the same server
classes (80 control steps, 0.114 s per step at 64x64 under EGL), and the exact
dependency chain is in `benchmark_integration/README.md` §2, including LIBERO's
`numpy==1.22.4` / `gym==0.25.2` / `transformers==4.21.1` pins that do not exist
for Python 3.12, `robomimic`'s unbounded `torch` that starts a 539 MB CUDA
download on a CPU host, and an `input()` at import time that kills any
non-interactive import. The capability numbers stay on the 2-D table because a
LIBERO score needs policy weights this workspace does not have; the LIBERO run is
protocol conformance with no policy in it, and its JSON says so.

**N — the same comparison, on a real LIBERO episode (200 episodes).** M's
environments were the harness's, but the physics was still a 2-D table. N removes
that: `libero_spatial` task 2 ("pick up the black bowl from table center and place
it on the plate"), `libero.libero.envs.OffScreenRenderEnv`, MuJoCo 3.2.3,
robosuite 1.4.0, EGL, the standard 7-DoF OSC delta action at 20 Hz. 100 episodes
per arm, one shared chunked pick-and-place program (plan once, commit to 6-step
chunks, servo within a chunk), so the *only* difference between the arms is the
gate in front of the actuator.

Arm A is the confidence filter: veto any chunk whose self-reported confidence is
below 0.80, retry up to three times, then execute anyway -- a VLA cannot skip its
own action chunk. Arm B registers the scene its world model predicts at the end of
each chunk, and after the chunk offers the measured scene (plate and bowl, in the
plan's own frame) to `MembraneGateway`. A refusal writes a denial to the
hash-linked ledger and CEL re-derives the plan from the observed world, resuming
at the stage the world is actually in -- a bowl that slipped is picked up again, a
bowl already on the plate is let go. Six re-derivations or three consecutive
refusals trip the breaker.

Under a 100-episode schedule (nominal / pose drift / nominal / pose drift /
physical channels, and pose drift brought in three different directions):

| | Safe-VLA | CEL |
|:--|--:|--:|
| success, all | 51% | **100%** |
| success, nominal | 100% | 100% |
| success, pose drift (45 episodes) | 0% | **100%** |
| success, physical channels | 84% | **100%** |
| false vetoes | 34 of 84 vetoes (40.5%) | 0 vetoes |
| retries | 112 | 0 |
| re-derivations | 0 | 94 (one per refusal) |
| contacts | 68 | 9 |
| damage episodes | 8% | 7% |
| episodes to recover after each onset | 27, 22 | **0, 0** |
| latency / episode | 4 609 ms | 4 687 ms |

The confidence arm is not merely worse, it is *orthogonal* to the failure: it
scores 100% at nominal and 0% under pose drift. It also vetoes 34 chunks that
would have progressed, 40.5% of all its vetoes, and the distribution says why the
mechanism cannot be repaired by tuning the threshold: 38 of the 84 vetoes fall in
the six `light_dim` episodes (18 of them false), because a confidence head reads
the frame and the frame got dark; 5 of the 34 false vetoes fall in *nominal*
episodes, where nothing had changed at all. Meanwhile CEL's 94 refusals are 57
under pose drift, 11 at nominal, and every one of them is a chunk it can show had
failed -- the count it would like to be zero, not the count it is paid for. The
layer's advantage is not being braver; it is that it pays a re-derivation only
when the world disagrees, and 100% of its re-derivations are of that kind.

Both latency numbers are reported because they disagree. CEL is 78 ms slower per
episode (2%), of which the witness is 967 ms: 55 ms per chunk across the run,
9.9 ms over the first ten episodes and 99.8 ms over the last ten. The growth is
not the witness -- canonicalising and hashing a scene costs 0.77 us -- it is
`request_action_warrant`'s full chain verification before every grant (2.2 ms at
202 entries, 31.0 ms at 3 002, linear in the ledger). Excluding that term, CEL
spends **3 720 ms against 4 609 ms**, i.e. 19% less, because it never pays for a
retry. Quoting only the total would say the layer costs 2% more; quoting only the
decomposition would hide a real, measured cost that a deployment has to budget.
Batching or anchoring the chain check is on the list of things this layer still
owes, and its price curve above is why.

The drift channels are reported separately because they are not one phenomenon.
`light_dim` is inert for this policy (it reads privileged state; the image is
still dimmed and measured at 22 vs 118 mean brightness, so the null result is a
property of the perception, not of the perturbation), `surface_slip` and
`payload_heavy` mostly survive a nominal grasp (bowl mass x12 still lifts 112 mm
against 115 mm), and `actuator_weak` is the only channel that changes the *arm*
(60 mm of lift for 12 commanded close steps, a third of it). CEL scores 100% on
all four, and the honest reading is that the four are easier than a 10 cm plate
displacement; they are in the run as channels, not as headline.

Three limits are on the record. The policy is a scripted stand-in, not OpenVLA:
there is no checkpoint in this workspace, so "VLA forward" is a declared 200 ms
constant and the confidence head a declared model of a head, both flagged in the
JSON. Recovery is measured against a schedule, not a deployment. And the run
found a defect in the layer itself: the first version could not distinguish a
scene that had *regressed* from one it had predicted earlier in the episode, so
three episodes reported a pass with the bowl 245-269 mm from the plate. That is
ERR-004, with the reproduction and the one-shot ordinal that fixes it; the table
above is the corrected run.

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

**L — the physical execution layer (2-D sandbox).** The interface argument is
abstract, so it is also run against a table: five blocks, five frames, one
decoy, circle/segment collisions with impulse resolution, momentum-conserving
block/block contacts, and Coulomb friction. `pybullet` and `pymunk` are probed
for and reported, never swapped in -- two solvers under one name would mean the
CSV describes whichever happened to be installed. This environment has neither,
so the run prints `numpy fallback` rather than describing an integrator as a
physics SDK. Drift is non-i.i.d.: a friction regime, once it arrives, holds for
50 pushes, and every arm is handed the identical schedule.

Three arms, same jobs. **A** acts with no check at all. **C** is the industry
baseline: act if the policy's own confidence is at least 0.8, otherwise hand over
to a human. **B** witnesses each push against the frame (20 ms) and caches the
rule that produced it (5 ms on replay), re-deriving only when the witness says
FAIL. Latency is denominated in operations rather than in abstract units: 200 ms
for a vision-language-action forward pass, 20 ms for a mechanical witness, 5 ms
for a compiled lookup, 3000 ms for a human takeover.

At 1000 pushes with 25% of them inside a drifted regime: A completes 72.5% of
five-block sequences at 200 ms per push; C completes 95.0% at 275.8 ms per
*successful* push, with 26 takeovers -- **16 of them (61.5%) refusals of actions
that would have succeeded**; B completes 97.5% at 29.4 ms per successful push and
never calls a human. Damage per 1000 pushes is 150 (A), 4 (C) and 3 (B). The
sweep over drift frequency and three seeds keeps the ordering.

The caveats are part of the result. The witness is post-hoc, so B still spends
one failed push per drift window -- it bounds damage to one per window instead of
preventing it, and no post-hoc check can un-damage the first contact. B's
discount is the solidification discount, so it scales with how repetitive the
task family is: five identical blocks under three regimes is friendly to a rule
cache, and the per-push costs are in `physics_sandbox_data.csv` so a less
repetitive stream can be re-priced without re-running. The four latency constants
are assumptions, not measurements on hardware, and a reader who disagrees with
the 10:1 ratio should re-read §L rather than the result. C's confidence is a
model of a policy head -- self-reported, six points of noise, 75% chance of
noticing a gross failure -- and a better-calibrated head would refuse less often
while still being a function of the model rather than of the world, which is the
property the comparison is about.

**O — driving (2-D kinematics).** The LIBERO numbers are a manipulation arm; a
trajectory is the same interface with a different output type. An ego car drives
a 280 m urban route: a queue at a red light, a curve, a lead that either brakes
hard or turns off, and a pedestrian who steps out from behind parked vans at
30--36 m. The planner is an end-to-end stand-in that rolls out the *nominal*
plant, 2.5 s ahead, at 2 Hz, and reports a confidence. Drift is injected per
episode in two physical channels: **grip** ($\mu=0.35$ against a believed $0.90$)
and **actuation** ($\tau_a=0.40$ s, $\tau_s=0.75$ s against 0.10/0.08). Both are
applied to the plant only; the planner's belief never changes, which is what makes
the rollout wrong rather than the policy bad.

On dry tarmac the confidence gate is indistinguishable from the layer: 0
collisions, 100% arrivals, 0.17 m of cross-track error. The channels separate it:

| channel | collisions A / B | success A / B | cross-track p90 A / B | centre-line A / B |
|:--|:--|:--|:--|:--|
| nominal (34) | 0 / 0 | 100% / 100% | 0.17 / 0.17 m | 0% / 0% |
| wet (30) | **15 / 0** | **50% / 100%** | 0.14 / 0.12 m | 0% / 0% |
| worn steering (26) | 0 / 0 | 100% / 100% | **2.75 / 0.67 m** | **96% / 0%** |
| wet + worn (10) | **5 / 0** | **50% / 100%** | 1.48 / 0.41 m | 10% / 0% |

Every collision in the wet channels is a pedestrian, at 7.2 m/s mean impact. In
12 of the 15 wet pedestrian episodes the gate never fires, because the confidence
is *high*: the camera sees a normal street, and nothing in the picture is wrong.
In the other 3 it fires and the car still hits, because a 6 m/s^2 emergency brake
on a 0.35 g road is a slower stop than the geometry allows -- the gate cannot act
on a road it has not measured, and its hardest stop is a policy constant while the
required deceleration is a property of the surface. The layer needs no such
belief: at the instant the pedestrian appears, the dry car has 15.7 m more
stopping distance than it needs, the wet car averages 0.5 m *short* (range -2.2 to
+2.7 m) before its own 0.2 s decision latency of about 2.5 m, and 5.0 m short on
wet + worn tyres, where it is already unrecoverable. That is a computed deficit
from the run, in `hazard_deficit_m`, not a scripted outcome.

The worn-steering row is the one that a success rate alone would hide: the
gate's car still *arrives* in all 26 episodes while spending them 2.75 m off
centre, i.e. in the oncoming lane. Cross-track error and centre-line crossings
are separate columns for exactly that reason. CEL's 0.67 m is the rate-compensated
pure pursuit, which it can only run because it measured $\tau_s$: identified
$0.736$ against $0.75$ true, $\tau_a$ $0.400$ against $0.40$, $\mu$ $0.350$
against $0.35$, from saturation and step responses, never from the simulator.

What it costs. 195 refusals over 32 586 witness checks (10 Hz), 46 episodes
carrying at least one where the blind replay would have arrived: 0.65 refusals
per episode on dry tarmac, each one 10 Hz tick of a re-derived take-over, not a
stop, and they do not show up in the harsh-brake count (203 against 212 per 100
episodes nominal, 140 against 310 wet + worn). Episode time is 1.9 s longer
(+6%) because the take-over is a ramp inside the plan and a refusal re-plans.
The emergency-brake arm spends 28 hard stops and 19% of its episodes are ones
where the blind replay arrives anyway -- refusing a picture, not a world.

Three limits. It is a kinematic bicycle with a friction circle and first-order
actuator lags: no rendering, no tyre model, no perception -- "confidence" is a
declared model of a head (0.925 base, 3 points of noise, −0.175 on a novelty
event), not a checkpoint, and the planner reads privileged actor state rather
than a detector's output. Second, one route geometry with one seeded hazard per
episode, so the rates estimate a mechanism, not a fleet. Third, $\tau_s=0.75$ s is
a failing steering rack and $\mu=0.35$ is a wet road taken as constants: the
magnitudes are fault injections, the ordering is the claim. `tests/test_ad_cel_audit.py`
(18 tests) pins the parts a unit test can decide: both arms share the declared
world, the replay has no gate, the drift is between plant and belief and never
between arms, the transient is not mistaken for ice, a spent ordinal is refused,
an edited record breaks the chain (72 576 entries re-walked), and the take-over
releases the brake at a bounded jerk.

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
