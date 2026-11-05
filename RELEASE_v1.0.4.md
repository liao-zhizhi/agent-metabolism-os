# v1.0.4 — The Cost Reduction Theory, and the Standard It Implies

This release adds the fourth pillar: a cost theory that says which lever to pull.
The claim is not "verification is cheap". It is that the *direction* of cost
reduction is fixed by the sign of two derivatives, and only one of them is safe.

Two things follow from that theory in this release, and they are why the version
number moves. The first is a game-theoretic answer to a question the theory raises:
if deterministic verification is this valuable, why would anyone without a
reputation be able to sell it? `experiments/game_theory_sim.py` models the paradox
and finds the answer is conditional on two parameters set in the first month. The
second is a conformance specification that turns the audit into something a
procurement department can buy, plus the pilot proposal that sells it.

## The four laws

**I — The Blind Retry Explosion Law.** An unobserved failure can only be retried,
so `C_retry = C_gen + p·C_retry` and the cost diverges as `1/(1-p)`. Measured on a
real LIBERO episode: the confidence-gated baseline pays **9 037 ms per success**
against the layer's **4 687 ms** (1.93×), with 34 of 84 vetoes overturning an action
that would have worked and 27 episodes to recover after one drift onset, against 0.

**II — The Solidification & Steady-State Cost Law.** A witnessed interface is
compiled to a lookup, so `C_steady = c_c + ρ(c_v - c_c)`. At `ρ = 0.1` that is
**89.4% below** full derivation, the payback is the *second* traverse (`T* = 1`), and
the price of drift is exactly one verification: regressing measured cost on the
realised drift rate gives **slope 0.9994** against a target of 1.0. Not a trend — an
accounting identity, shipped with the entry that closes it.

**III — The Cross-Layer Compound Interest Law.** The recursion
`x_d = κ + (1-κ)L·x_{d-1}` has geometric ratio `r = (1-κ)L`, so the critical reuse
rate is **`κ* = 1 - 1/L = 0.75`**. Above it the marginal cost per layer converges;
below it the cost is `r^d`. Measured κ moves 0.904 → 0.692 as ρ moves 0.1 → 0.3, and
the depth-8 cost moves **1.47 → 18.2**: a phase transition, not a gradient.

One correction travels with the law: cost does not fall exponentially, because the
discount is a ratio of two positive prices. What grows exponentially is the *depth a
fixed budget reaches* — 37 578× at six levels, 601 248× at eight.

**IV — Catastrophic Avoidance Economics.** With
`E[C] = C_exec + P(collision)·C_disaster`, the layer wins when
`C_disaster > ΔC/ΔP`. On the driving audit that threshold is **0.95 seconds of
driving time**; on wet tarmac it is 9.8 s, and in the steering-lag channel the time
difference is *negative* — the layer is faster while deleting 25 of 26 lane-paint
crossings. Collisions go 20 → 0.

## The derivative the industry gets wrong

```
∂C/∂T  = c̄/p_s > 0        always: fewer verifications is always cheaper
∂C/∂c_v = [1+(T-1)ρ]/p_s + (∂π/∂c_v)·C_disaster     changes sign
```

Cutting the price of a verification lowers the first term and raises `π`, and once
`C_disaster` is large enough the second term wins. Measured: shrinking the
verification window from 400 ms to 40 ms cuts its price tenfold and takes damage
from 0% to 64%. **Cheaper verification is not cheaper execution.**

The core statement the document proves rather than asserts: *true cost reduction is
not omitting verification, but using deterministic causal solidification to avoid
endless physical catastrophe.*

## The energy-gradient paradox, and where the model says it breaks

A solo researcher with no name has no interface with the economy, and with no
interface cannot take an energy gradient, yet must keep a moat. `experiments/game_theory_sim.py`
writes that argument down as a dynamical system instead of debating it.

Potential difference is `ψ = E·D` — energy times height. Fame alone raises `E` and
leaves `D` at zero, which is why a paper-only strategy has an `E` above one
laboratory and a `ψ` of 0.02. Energy has two ceilings, and the distinction does the
work: fame saturates at 2.5 because no amount of prestige buys a laboratory;
dependency saturates at 8 because a paying installed base scales.

The result that mattered was not the ranking but its condition. A tollbooth on a
certification layer wins the six-month potential difference — 0.06 against 0.00 for
a closed product and open source — and reaches a 65% probability of success at month
60. But move the certification pilot past nine months, or halve the dependency
conversion rate, and the six-month winner flips to academic positioning. **The
recommendation is not "pick the right strategy"; it is that the strategy is
determined by two parameters set in the first thirty days.**

Two honest limits travel with it: the ranking has three distinct winners across the
sensitivity grid (so it is falsifiable rather than rigged), and no strategy builds a
6% potential difference in six months — six months buys a slope, not a stock.

## Certification: the standard, and the pitch

`experiments/certification_spec/CERTIFICATION_SPEC_V0.1.md` turns the audit into a
procurement instrument. Its load-bearing choices:

- **`INVALID` is not `FAIL`.** Merging them trains a supplier to declare less, not
  to evidence more; treating `INVALID` as `PASS` hands a certificate to not looking.
- **`NOT RUN` is a third bucket.** Scored as `INVALID` it rewards registering fewer
  checkpoints; scored as `PASS` it rewards registering checkpoints and not running
  them.
- **A verdict must precede failure by 0.5 s.** A `FAIL` after impact is correct and
  worthless.
- **The 5% `INVALID` cap is measured, not asserted.** Even at a 61% grip loss the
  reference implementation spends 1.20%; at 20% drift it spends 0.17%.

The 20% drift case is the one to read. At that level both the confidence-gated stack
and the layer record **zero collisions** — the acceptance gate cannot separate them.
Only the `μ` estimate does: 0.720 tracked, against 0.900 unmoved. Collision rate
separates the two systems only at 61% drift, by which point tyre wear has already
happened.

`PILOT_PROPOSAL.md` is the commercial version, including the liability clause that
does the actual work: not "liability is non-exemptable", which is a boilerplate term
in most jurisdictions, but removal of the state-of-the-art defence, removal of the
liability cap, and **reversal of the burden of proof** — three mechanisms that are
each mechanically checkable against the witness package.

## Also in this release

- `experiments/cost_theory/COST_THEORY.md` — the theory, with an executive summary
  that leads with the two derivatives above.
- `experiments/cost_theory/cost_theory_numbers.py` — recomputes every number in the
  document from nine experiment artefacts and fails if a claim does not hold.
- `experiments/cost_theory/cost_theory_numbers.json` — the computed values.
- `tests/test_cost_theory.py` — 20 tests, including a check that the document's
  headline numbers still match the generated JSON.
- `experiments/game_theory_sim.py` — the four-strategy simulation, its sensitivity
  grids, and its 30-day plan; `experiments/game_theory_results.json` holds the
  trajectories, the Monte Carlo draws and the winner grid.
- `tests/test_game_theory_sim.py` — 16 tests: the energy accounting closes, `ψ ≡ E·D`
  month by month, more copyability never helps the moat, and the six-month winner
  actually moves within the sensitivity grid.
- `experiments/certification_spec/CERTIFICATION_SPEC_V0.1.md` — the conformance
  specification (four-valued verdict, seven witness artifacts, red-line cases A–D,
  liability transfer, certifier pseudocode).
- `experiments/certification_spec/PILOT_PROPOSAL.md` — the B2B pilot proposal and
  pricing.
- **ERR-006 (open)** — per-grant witness cost is `O(N)` in ledger length (10.3 µs per
  entry), so the layer's total latency crosses above the baseline at ~4 710 entries,
  i.e. under 150 episodes. The published "+1.7%" is a measurement of an interval, not
  of a deployment. The fix is specified: a Merkle tree or incremental accumulator,
  `O(log N)` or `O(1)` per grant, with a price test as its acceptance criterion.

1 122 tests pass. No experiment verdict changed; one published claim acquired the
qualifier it always needed.
