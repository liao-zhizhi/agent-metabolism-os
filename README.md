# agent-metabolism-os

A zero-dependency prototype that puts metabolic regulation **outside** a large
language model, by treating the model as a stateless CPU.

The model has no memory of its own charge. Ask for a long chain of thought on
the last joule and it will produce one, and the bill arrives after the fact. So
the constraint has to be somewhere else: in a scheduler that must pay before it
thinks, and that changes *which* thinking it asks for once the budget gets thin.

And it has no memory of its own mistakes. Ask it to prove the invariant it just
failed to prove and it will try again with the same confidence. So there is a
second constraint, outside the model and outside the account: a ledger that
remembers what the system has cost, and takes capability away when the answer is
"too much".

## Bio-Inspired Architecture

### The two outside interfaces

The internal layers regulate what a call may do. They are not, on their own, an
interface: they never touch the two things this system actually borders on. Every
crossing to a model, or to an agent's tools, goes through one of these two, and
each carries a potential difference -- something must be paid before the inside
will act.

```
                    ┌──────────────────────────────────────────┐
                    │            MetabolicGateway              │
                    │  one read-only surface, no setters:      │
                    │  L1 charge · L3 headroom · 8 laws        │
                    └───────────────▲──────────────┬───────────┘
                                    │ status       │ routing_view()
                                    │              ▼
   ┌────────────┐          ┌────────┴─────────┐      ┌──────────────┐
   │    LLM     │◄────────►│ agent_metabolism │◄────►│    Agent     │
   │ (OpenAI /  │          │                  │      │ (framework / │
   │ Anthropic) │          │  L1 .. L7        │      │  your code)  │
   └────────────┘          │  + penalty       │      └──────────────┘
        ▲                  └────────┬─────────┘             ▲
        │                           │                       │
        │  LLMProxy                 │                       │  MembraneAgent
        │  · charge paid first      │                       │  · promise first
        │  · beat chosen, degrade   │                       │  · hash must match
        │    is labelled           │                       │  · mismatch = FAIL
        │  · every call recorded    │                       │
        │                           ▼                       │
        │              ┌────────────────────────┐           │
        └──────────────┤  AppendOnlyLedger      ├───────────┘
           cost record │  sha256-linked chain   │  verdict record
                       │  + head anchor         │
                       └────────────▲───────────┘
                                    │ tamper → chain breaks
                            ┌───────┴────────┐
                            │  verify_chain  │
                            └────────────────┘
```

Read the diagram as three claims, not as boxes:

1. **Into the model costs charge.** `LLMProxy.generate` pays through the energy
   account *before* the call. An account that cannot fund it raises
   `EnergyExhausted` and the model is never reached. When it can fund the cheap
   beat but not the expensive one, the answer is degraded and labelled
   `degraded=True` -- a cheap answer that reads like an expensive one is a false
   completion in the layer above.

2. **Into a tool costs a promise.** `MembraneAgent.execute_tool_with_warrant`
   requires `expected_artifact_hash` and registers it *before* the tool runs, so
   the agent commits while it still cannot know the outcome. The produced
   artefact is then hashed and offered to the membrane, which recomputes it
   independently. A mismatch is a recorded `FAIL`, not a retry.

3. **Both leave one trail.** Neither interface writes its own log. Costs and
   verdicts land in the same SHA256-linked chain, within one head anchor, so
   "what did this cost" and "what did it claim" cannot be edited separately.

The ordering in (2) has an unavoidable asymmetry worth stating: the tool must run
before its output can be hashed. What is guaranteed is that the *expectation*
precedes the act. The gap between them is exactly what the `FAIL` record is for.

```python
account   = EnergyAccount(100.0)
scheduler = DualBeatScheduler(account)
ledger    = AppendOnlyLedger("audit.jsonl", anchor_path="audit.jsonl.head")
proxy     = LLMProxy(scheduler, ledger)          # L1 + L2 + one-way lock

gateway = MembraneGateway("audit.jsonl")         # the membrane itself
agent   = MembraneAgent(gateway, tools={"write_file": write_file})

answer = proxy.generate("prove the invariant", complexity_score=0.95)
agent.execute_tool_with_warrant(
    "write_file",
    {"path": "spec.yaml", "body": rendered},
    expected_artifact_hash="sha256:...",          # promised in advance
)
ledger.verify_chain()["status"]                   # "PASS"
```

### Where the membrane lives, and why this package does not import it

`MembraneGateway` and `AppendOnlyLedger` are in the companion repository,
`harness-audit-sidecar`. This package imports neither: it duck-types them, so
`pip install agent-metabolism-os` keeps its `dependencies = []` promise and the
auditor stays a separate trust domain. The cost of that choice is honest -- a
membrane that is not injected is a membrane that is not there, and
`MembraneAgent` refuses to be constructed without one rather than running
ungated.

This package is one half of a two-layer design: **internal regulation follows
eight laws, external interface follows five**. The split is deliberate. The
metabolic system decides *what may run*; the immune system decides *what may be
believed*.

**Internal — eight laws (L1–L7 plus the penalty ledger).** Reported in a single
read-only call by `MetabolicGateway.get_current_laws_status()`:

| Law | Layer | Reported as |
|---|---|---|
| L1 | Energy account | `L1_Energy` — remaining charge, budget, ratio |
| L2 | Dual-beat scheduler | `L2_Beat` — OXPHOS or SLP, observed when a response is supplied |
| L3 | Regime switch | `L3_Regime` — GROW / CONSERVE / EMERGENCY |
| L4 | Substrate channel | `L4_Channel` — keys, not payloads (no gauge to read) |
| L5 | Saturation brake | `L5_Brake` — **hosted externally**, by the immune sidecar |
| L6 | Circadian pass | `L6_Circadian` — consolidation is not tracked here |
| L7 | Compartment | `L7_Compartment` — how many units are still alive |
| — | Penalty ledger | `Penalty` — accumulated damage and trust |

L5 is the seam. This package does **not** import the sidecar, and the gateway
does not answer `tripped: False` for a brake it cannot see: with nothing
attached it reports `attached: False` and `tripped: None`. A brake that reads
the regulated system's own accounting can be tripped by the same defect that
corrupts the accounting, so it is hosted where the contradiction is observed.

Every law carries a `measured` / `attached` / `observed` flag, and `_witness`
names in words what was actually read. A layer with no instrumentation reports
`None`, never a healthy-looking default.

**External — five laws (ecology interface).** Served by the companion
repository, `harness-audit-sidecar`, as three stdlib-only pure functions in
`audit_sidecar/tools/eco_api.py`: energy gradient, edge-to-mainstream
succession, and waste recycling (plus a keystone-source and a carrying-capacity
reading). Those laws concern how a *neighbouring* system decides whether to
route work here, trust a claim from here, or mine this system's failures — which
is why they live outside the regulator rather than inside it.

The two halves agree on one rule: **a quantity that cannot be witnessed is
reported as unmeasured, not estimated.**

### A refusal is not a failure

`MembraneAgent._deny` distinguishes *when* a request was stopped, because the two
cases are opposite evidence:

| When | Record | Counts as damage? |
|---|---|---|
| Stopped before the tool ran | `record_type="deny"`, `outcome="DENIED"`, no verdict | **No** — the gate held |
| Effect landed, no warrant | `FAIL` verdict | **Yes** — unsanctioned write |

Recording both as `FAIL` made a system inflame itself through its own successful
blocks: two attacks and it was in inflammation, one whole scenario before any
real false completion. The denial stays in the hash chain, so "I refused" is
still provable — it simply is not a verdict about work, because none was
witnessed.

## Cross-Audit Evidence

The two claims in that introduction — *it cannot overspend* and *it cannot be
lied to about its own health* — are each cheap to assert and expensive to
believe. So they are run against each other: an independent auditor
([`harness-audit-sidecar`](https://github.com/liao-zhizhi/harness-audit-sidecar))
watches a tool call, contradicts it with a git witness, and this system is then
required to act on the contradiction.

**→ [`FINAL_AUDIT_REPORT.md`](FINAL_AUDIT_REPORT.md)** — the full transcript, with
every figure read back out of a live object after the run.

```bash
python3 final_audit.py    # ~8s, writes the report; exits 2 if any link breaks
```

**→ [`FINAL_ECOSYSTEM_AUDIT.md`](FINAL_ECOSYSTEM_AUDIT.md)** — the adversarial
run: five ways an agent tries to cheat, each checked against a control. Its first
execution found four defects, three of which are now repaired and re-verified
there (`F2` remains open by choice — see the report).

```bash
python3 final_ecosystem_audit.py    # writes FINAL_ECOSYSTEM_AUDIT.md, exits 0
```

A false completion is injected, the auditor returns `FAIL / no_file_drift`, the
ledger charges 5.0 × 10.0 = 50, and capability is taken away:

| stage | charge | ledger | L3 regime | concurrent | new writes | L2 beat |
|:--|--:|:--|:--|--:|:--|:--|
| baseline | 90% | HEALTHY | **GROW** | 5 | allowed | SLP |
| after the `FAIL` | 90% | CRITICAL | **EMERGENCY** | 1 | **refused** | OXPHOS |
| after 12 verified passes | 88% | WARNING | **CONSERVE** | 1 | allowed | SLP |

The charge never drops more than two points. Every restriction in the middle row
is attributable to the ledger, not to the account — which is the whole argument:
a full battery is not a permission slip.

Two figures that a report like this usually asserts are deliberately **not**
asserted here, because they would have been false:

- Trust does not fall to zero. It falls 100.0 → 75.0; the state is `CRITICAL`
  because the *fine* crossed 40.0. A metric pinned at zero could not show the
  climb back, which is the point of having two of them.
- The two projects are not both zero-dependency. This one is; the auditor is not,
  and should not be — its rules are YAML policy files so that changing audit
  policy leaves a diff instead of requiring a code release.

The auditor is run against this repository, not shipped with it, and it is
deliberately *not* wired into CI: 371 unit tests run on every change, and the
cross-audit is a heavier manual gate for release candidates.

Six layers, standard library only:

| Layer | File | What it regulates |
|:--|:--|:--|
| **L1** | `agent_metabolism/energy.py` | A finite charge. Below 30% the high-cost beat is forbidden; below 10% the account funds nothing. |
| **L2** | `agent_metabolism/scheduler.py` | Two beats: `OXPHOS` (fast, 1× energy) and `SLP` (slow, 5× energy, 2× wall clock). A `CRITICAL` ledger forbids the slow beat at any charge. |
| **L3** | `agent_metabolism/regime.py` | How much of the organism runs: `GROW` (5 agents), `CONSERVE` (1), `EMERGENCY` (interrupt). A `CRITICAL` ledger forces `EMERGENCY` at any charge. |
| **L4** | `agent_metabolism/channel.py` | A shared substrate pool. Agents trade keys, not payloads. |
| **L6** | `agent_metabolism/circadian.py` | Night mode compresses the day's context into content-addressed memory. |
| **L7** | `agent_metabolism/compartment.py` | Membrane-bounded units. A failing unit is lysed; its neighbours keep their memory. |

Plus one component that is not a layer, because it measures a different axis:

| Component | File | What it measures |
|:--|:--|:--|
| **PenaltyLedger** | `agent_metabolism/penalty.py` | Accumulated damage: a fine and a trust score. Read by L3 (concurrency, new writes) and L2 (slow beat), so a failure can stop a system that still has energy. |

## Formal QC for machine-written proofs

A new verification domain: Lean 4. Nobody on this side of the membrane can check
a Riemann proof, and that is the point of a formal language — an artifact either
elaborates or it does not, and that judgement needs no understanding of the
mathematics (see [`agent_metabolism/lean_auditor.py`](agent_metabolism/lean_auditor.py)).

Three witnesses, composed by worst-wins, where no witness may ever loosen
another:

| Witness | Sees | Verdict |
|---|---|---|
| Placeholder | `sorry`, `admit` in code — not in comments or strings | `FAIL` |
| Axiom | `axiom` declarations, and `#print axioms` naming non-standard ones | `FAIL` |
| Compiler | the real toolchain: exit code, plus `#print axioms thm` | `PASS` / `FAIL` / unavailable |

The compiler cannot be the only witness, and this was **measured** on Lean
4.33.1 rather than assumed:

```
theorem t : (1:Nat) = 1 := by sorry     ->  exit 0, warning only
axiom riemann : True
theorem u : True := riemann             ->  exit 0, no output at all
```

Both cheats compile. A gate that trusted `returncode == 0` would report a
sorry-riddled, axiom-smuggling file as a complete proof — a false completion
committed by the auditor. `#print axioms` is the stronger half: `sorry`
elaborates to `sorryAx`, so it catches a hidden hole, and it names a smuggled
axiom directly.

The converse rule also holds: **`PASS` requires that something actually
elaborated.** A clean text scan with no toolchain available is `INVALID`, never
`PASS`, because a regex cannot tell a proof from a file with a syntax error.

Two near-misses are deliberately *not* failures. `constant` is Lean 3 spelling
and does not parse. A bare `opaque x : T` looks like `axiom x : T` with one token
changed, but Lean requires `T` to be inhabited — `opaque boom : False` is
rejected — so it cannot manufacture a false proposition. It is reported as an
advisory note that is structurally incapable of changing a verdict. The first
version of this auditor failed it; the false accusation was found by running it.

A hole and an axiom are one offence, not two: in both cases the agent said the
proof was complete and the code disagrees. Both score `FAIL`, both are charged as
`FALSE_COMPLETION`, and two of either inflames the immune layer. Scoring a hole
`INVALID` — the earlier design — inverted the incentive, because the thresholds
are asymmetric (one `INVALID`, two `FAIL`), so leaving holes would have hurt
*more* than smuggling an axiom.

```bash
python3 demo_lean_fake_proof.py    # exits non-zero if the cheat is not caught
```

## Double-entry validation: who is actually working

A single-entry book records what the agent says happened, so the only witness in
it is the party being audited. [`agent_metabolism/double_entry.py`](agent_metabolism/double_entry.py)
books the claim and the evidence as two separate lines and refuses to call an
entry settled until the two agree — the whole rule is one line:

```python
if witness is None:  return INVALID      # nobody looked
if check is witness: return PASS         # the books agree
return FAIL                              # the books contradict
```

`check is witness` is not `check and witness`. Two `False` values balance too: an
agent that reports failure and is confirmed to have failed has filed an *accurate*
report, and charging it would train agents to hide failures — the behaviour the
ledger exists to catch.

| claim | evidence | verdict | consequence |
|---|---|---|---|
| success | matches | `PASS` | verified work; buys trust back |
| success | contradicts | `FAIL` | false completion; charged |
| success | not inspected | `INVALID` | the book stays open; agent locked |
| failure | confirmed | `PASS` | honest news; recorded, earns nothing |
| failure | contradicts | `FAIL` | under-claim; charged at a lesser weight |

**Two failures in a row stop the agent.** The first `FAIL` is a charge, because a
single wrong report is a normal day and stopping on it would be the
over-accusation this design avoids. A second *consecutive* `FAIL` locks the book
until a human intervenes. A `PASS` of either kind clears the streak, so an
accurate bad report never creeps toward a stop — only sustained dishonesty
accumulates.

The rule exists because the alternative was measured. With the lock on `INVALID`
alone, a liar survived five attempts (trust to 0, penalty `CRITICAL`, still
operational) while an agent that merely omitted its witness was stopped on its
second. The cheapest path to *staying operational* was to lie, which makes a
ledger worse than none: it manufactures the appearance of accountability while
selecting for the opposite.

The second `FAIL` is still booked and still returns a receipt — the caller has to
be told what happened, so only the entries *after* it are refused. `unlock()`
does not reset the streak either: the operator unblocked the *book*, not the
record, and an agent whose first act after a second chance is another false
completion has shown the intervention changed nothing. The two locks stay
distinguishable (`lock_kind` is `"unwitnessed"` or `"repeat_fail"`) because an
operator deciding whether to unlock needs to know which one they face.

The lock is bookkeeping, not punishment: a closed book with a loss blocks
nothing, but an *open* entry means the books cannot be reconciled, so the next
entry may not be opened. Neither lock is reachable from `record_transaction` — an
agent able to clear its own lock by filing a `PASS` would hold exactly the power
the lock withholds — and `unlock()` does not erase the entry, because history is
not a draft.

Only verified work buys trust back. An honest failure is booked and earns
nothing: paying for accurate bad news makes failing cheap.

**Reconciliation.** Hand the ledger an L1 `EnergyAccount` and it checks that
every unit charged against the account also appears in the journal. An account
that moves without an entry is exactly the out-of-band write double-entry exists
to reveal, and it is the one thing a single-entry book cannot notice:

```
  journal energy    : 27.0
  account energy    : 52.0
  reconciled        : NO -- the books do not balance
  difference        : 25.0 unaccounted
```

**What it does not do.** It does not establish the witness: `witness=True` is a
claim by the caller, and if the caller is the agent's own harness this is a
self-certification with extra steps. The honest path is `digest_file()` plus a
comparison against a digest promised *before* the work ran — the same ordering
constraint the membrane enforces. Its guarantee is narrower and should be stated
narrowly: both sides are recorded together, the contradiction is charged, and the
totals reconcile. It is also process-local, and not tamper-evident by itself
(that belongs to the membrane's hash chain, and duplicating it here would mean
two chains to verify).

```bash
python3 demo_one_person_company.py      # 3 agents, 1 liar, exits non-zero if it lies uncaught
```

## Run it

```bash
PYTHONPATH=.:../agent-harness-audit python3 -m unittest discover -s tests -p 'test_*.py'   # 371 tests, ~2s
python3 demo.py                         # the full lifecycle
python3 integration_demo.py             # audit -> charge -> next-day shutdown
python3 demo_lean_fake_proof.py         # a Riemann "proof" that is not one
python3 demo_one_person_company.py      # three agents, one liar, one balance sheet
```

No install step, no dependencies, no network. Python 3.9+.

## What the demo shows

```
  #  complexity beat   mode  energy               state     tokens latency
  1  0.92       SLP    slow  100.0 → 65.0 ( 65%)  GROW      82        312ms
  2  0.88       SLP    slow   65.0 → 30.0 ( 30%)  GROW      94        336ms
  3  0.95       OXPHOS fast   30.0 → 23.0 ( 23%)  CONSERVE  21        153ms
  4  0.91       OXPHOS fast   23.0 → 16.0 ( 16%)  CONSERVE  22        156ms
  5  0.25       OXPHOS fast   16.0 →  9.0 (  9%)  EXHAUSTED 15        135ms
  6  0.97       --     --    refused              EXHAUSTED --     --
```

Task 3 is as complex as task 1. It gets the fast beat anyway, because the
account no longer contains the price of the slow one. **The mode followed the
budget, not the difficulty** — that is the whole design.

## L1: the account is a constraint, not a counter

```python
from agent_metabolism import EnergyAccount

account = EnergyAccount(100.0)   # budget
account.consume(75.0)
account.state                    # 'CONSERVE'
account.ratio                    # 0.25
```

Two failure modes, kept distinct on purpose:

- `InsufficientEnergy` — this cost is larger than the charge. A pricing problem:
  run something cheaper.
- `EnergyExhausted` — the account is below 10%, so it funds **nothing**, even
  with charge left on the clock. A policy floor: refill or stop.

Thresholds are strict (`ratio < 0.30`), so an account sitting on exactly 30.0%
is still `GROW`. That is not rounding pedantry; it is what lets the demo show
the precise moment the account arrives at the boundary, and lets a test assert
the two sides differ.

## L2: two beats, chosen before the call

```python
from agent_metabolism import DualBeatScheduler

scheduler = DualBeatScheduler(account, base_cost=7.0)   # slow costs 35.0
response = scheduler.execute("prove the invariant", complexity_score=0.95)
response.mode, response.energy_cost, response.degraded
```

The slow beat runs only when `complexity > 0.7` **and** the account is `GROW`
**and** the ledger does not say `CRITICAL`. When it deserves the slow beat but
cannot have it, the scheduler **degrades rather than overdraws**: it runs fast
and records *why*, so the decision is visible afterwards instead of showing up
as a negative balance or an unexplained quality drop.

```python
response.degraded          # True  -- wanted SLP, did not get it
response.degraded_reason   # 'insufficient_energy'  or  'penalty_critical'
response.trust_blocked     # True only for the second
```

`degraded` alone would be a lie of omission. "I ran out of joules" and "I am not
trusted with the expensive sentence" call for different responses — refill the
account, or do cheap verifiable work — so the cause is a field, not something a
caller has to re-derive from context.

`latency_ms` is modelled, never slept. That keeps the tests at ~10 ms and
deterministic, and it is why the demo can afford to be honest about the numbers
being a model rather than a measurement.

### What the witness tier costs

The beat is only half of what a call costs. Proving what happened -- a `git diff`
plus a content read, rather than a `stat` -- costs work too, and a scheduler that
billed both tiers the same would make escalation free, which is to say it would
make escalation a policy nobody has to budget for.

```python
scheduler.execute("rename a variable", 0.2, tier=TIER_INNATE)    # 7.0
scheduler.execute("rename a variable", 0.2, tier=TIER_ADAPTIVE)  # 17.0
```

`ADAPTIVE_SURCHARGE` is added on top of whichever beat runs, is affordability
checked like any other charge, and can therefore refuse a call outright: at 13%
charge a tier-2 call raises `EnergyExhausted` and charges nothing. Unknown tiers
are rejected rather than treated as cheap, and the default is the **cheap** tier
-- a proxy that escalated by default would make the safe path the expensive one.

The two packages cannot import each other (`dependencies = []`), so the tier
vocabulary matches by contract, and a test pins that agreement. Two modules
independently deciding that `"tier2"` means the expensive one is a coincidence
until something asserts it.

## L3: how much of the organism runs

```python
from agent_metabolism import RegimeSwitch, EnergyAccount

switch = RegimeSwitch(EnergyAccount(100.0))
switch.evaluate()                       # 'GROW' -- 5 agents may explore
switch.feedback(success=False, critical=True)
switch.feedback(success=False, critical=True)
switch.evaluate()                       # 'EMERGENCY' -- interrupt long tasks
```

Two inputs, worst-wins. Budget alone would keep the system confident while it
is failing, so the regime is a function of charge **and** recent error feedback:

1. `charge < 30%` or `2 consecutive major failures` → `EMERGENCY`
2. `charge > 70%` and `error rate <= 30%` → `GROW` (5 concurrent agents)
3. otherwise → `CONSERVE` (1 agent, divergent thinking off)

Pass a `penalty_ledger` and a third input joins, and it is the one that cannot
be refilled:

```python
switch = RegimeSwitch(EnergyAccount(100.0), penalty_ledger=ledger)
switch.evaluate()                 # 'GROW'
ledger.record_violation("FALSE_COMPLETION")
switch.evaluate()                 # 'EMERGENCY' -- at 100% charge
switch.plan().reason              # 'penalty ledger is CRITICAL (fine 50.0, ...)'
```

`penalty CRITICAL → EMERGENCY` and `penalty WARNING → at best CONSERVE`. The
direction is fixed: damage can **tighten** the regime and never loosen it. No
amount of trust turns a spent account back into `GROW`, because the alternative
— a system that trades accumulated trust for energy — is a way to argue with
the audit.

The plan is data, not branches: `RegimePlan` carries `max_concurrent_agents`,
`allow_divergent_thinking`, `interrupt_long_tasks` and `allows_new_writes`, so
what a regime permits is reviewable in one place. `allows_new_writes` exists so
that "refuses to write" is structural rather than narration — `integration_demo`
checks the directory afterwards and finds nothing there.

**L3 cuts at 70% / 30%; L1 cuts at 30% / 10%.** That is not a contradiction.
L1 asks whether *one call* may think slowly; L3 asks *how many* run at once. A
cell with ample ATP for a reaction can still be too stressed to divide, so an
account at 65% is legitimately `GROW` for L1 and `CONSERVE` for L3.

**Three layers, one ledger, three different punishments.** The same `CRITICAL`
state removes concurrency (L3), removes the slow beat (L2), and removes the
new-write permission (L3 plan). They are not redundant: L3 stops the system from
multiplying, L2 stops it from thinking expensively, and `allows_new_writes`
stops it from leaving a mark. A system can be wrong in exactly one of those ways
at a time, which is why the answer is granted per-permission rather than by one
global "healthy" flag.

## L4: hand over a key, not a payload

```python
from agent_metabolism import SubstrateChannel, AgentProxy

channel = SubstrateChannel()
key = producer.publish(diff_text, key="patch_1")   # 48 KB of diff
body = producer.send_substrate(consumer, key)      # 'substrate:patch_1'
len(body)                                          # 17 bytes on the wire
consumer.receive_substrate(key) is diff_text       # the real object
```

The membrane (L7) carries the key; the channel carries the payload. Neither has
to know the other's job, and the token cost of a handover stops depending on the
size of what is handed over. In the demo: **48,035 bytes → 17 bytes (0.035%)**,
and the transcript keeps only the reference.

One hazard is stated rather than hidden: `get` returns the *live* object, so two
agents holding a key hold the same list, and mutation is immediately visible to
both. That is a shared substrate, not an isolated compartment — the distinction
between L4 and L7. `get_snapshot()` deep-copies; `put(..., copy_data=True)`
protects the sender. The default is the fast shared behaviour, because a channel
that silently copied everything would reintroduce the cost it exists to remove.

## L6: the day accumulates, the night consolidates

```python
from agent_metabolism import CircadianScheduler

cycle = CircadianScheduler(manager, "long_term_memory.json")
cycle.is_daytime()                # False at 02:00
report = cycle.run_offline_mode()  # 3 compartments -> 3 fragments
cycle.run_offline_mode()           # 0 created, 3 skipped: idempotent
```

Two boundaries, both easy to get wrong:

- **L6 only reads.** It never mutates a compartment's `local_context`.
  Consolidation that pruned its sources would delete the day's record on a
  schedule, which is the opposite of what an audit trail is for.
- **Idempotence is by construction.** A fragment's id is a digest of the exact
  messages it summarises, so a second pass appends nothing. Content addressing
  does the work a "have I already summarised this?" flag would otherwise do, and
  it cannot fall out of sync because it *is* the content.

Running it during the day raises rather than quietly doing the work: a
consolidation pass that can fire at any moment is not a night cycle. The output
is labelled `method: "structural-digest"` — it deduplicates, counts and ranks
repeated terms; it does not understand the transcript and never claims to.

## L7: the membrane is structural

```python
from agent_metabolism import CompartmentManager

manager = CompartmentManager(anomaly_threshold=3)
planner, coder = manager.create("planner"), manager.create("coder")

planner.send_message("coder", "spec v1")
coder.local_context        # () -- it arrived, it was not admitted
coder.inbox_depth          # 1
coder.receive()            # only the recipient can admit
len(coder.local_context)   # 1
```

Three guarantees, all tested:

1. `local_context` is a `tuple`. `other.local_context.append(...)` raises
   `AttributeError` instead of quietly editing someone else's memory.
2. The only cross-compartment path is `send_message`, which writes to the
   **inbox**, never the context. Admission is the recipient's own call.
3. Three consecutive anomalies lyse the compartment: memory discarded, routing
   refused, neighbours untouched. A replacement can be created and the system
   keeps running.

Honest limitation: Python has no access control, and `_context` is reachable if
someone insists on reaching for it. The boundary here is an immutable public
view plus one private delivery path — real, and enforced by convention at the
last millimetre rather than by the interpreter.

## The penalty ledger: damage outranks supply

Every other layer measures *supply* — charge left, agents allowed, context still
readable. None of them measures *damage*. A system can sit at 100% charge with a
clean error window and still have shipped three false completions, and L1, L2
and L3 would all report health, because none of them is about what the system
already broke.

```python
from agent_metabolism import EnergyAccount, PenaltyLedger, RegimeSwitch

ledger = PenaltyLedger()
switch = RegimeSwitch(EnergyAccount(100.0), penalty_ledger=ledger)
switch.evaluate()                                  # 'GROW'

ledger.record_violation("FALSE_COMPLETION")        # weight 5.0 -> fine 50.0
ledger.trust_score                                 # 75.0
switch.evaluate()                                  # 'EMERGENCY'
switch.plan().allows_new_writes                    # False
```

Two numbers, because one number would have to answer the wrong question. The
**fine** is the accumulated cost and it amortises; the **trust score** is how
much benefit of the doubt remains, and only verified successes buy it back.

The recovery is deliberately asymmetric: a fine decays 2% per clean pass while
trust climbs a flat 2.0. One false completion (fine 50) therefore keeps the
system out of `CRITICAL` for about twelve verified passes and out of `WARNING`
for about sixty. A clean pass amortises the fine; it does not retract the
verdict — which is the point, because a lie that five good afternoons can erase
was never being priced at all.

### Where the ledger actually bites

`allows_new_writes` is only worth having if something acts on it. It did not:
`evaluate_regime` computed `EMERGENCY` at 10% charge with a `CRITICAL` ledger and
every write went through, because the regime was a *report* and the membrane only
read the immune brake. That is now wired:

```python
gateway = MembraneGateway(ledger, immune_state=immune, penalty_ledger=penalty)
gateway.request_action_warrant(witness, "write")
# MembraneRejectError: PENALTY_CRITICAL
```

The two inputs stay separate because they answer different questions with
different lifetimes — inflammation heals after enough passes, damage does not —
and the gate reports which one refused. `State` on this ledger is named
`penalty_state` precisely so that a duck-typed gate cannot read `CRITICAL` as if
it were `INFLAMED`: the two vocabularies share no word, and wiring one into the
other's slot raises `TypeError` instead of returning a calm answer.

`demo.py` runs the loop with the charge held still, so nothing that moves can be
blamed on the account:

```
                         charge  regime       agents  slow beat ledger
  morning, clean           100%  GROW              5  yes       HEALTHY
  after a slow task         90%  GROW              5  yes       HEALTHY
  after the conviction      90%  EMERGENCY         1  NO        CRITICAL
  after 12 clean passes     88%  CONSERVE          1  yes       WARNING
  after 60 clean passes     88%  GROW              5  yes       HEALTHY
```

The charge falls by two points across the whole sequence. The regime falls from
`GROW` to `EMERGENCY` and back, and the slow beat goes with it — because the
ledger, not the account, decided.

`integration_demo.py` runs the same idea against the real sidecar:

```
[5] penalty ledger  -- the failure now costs something
  violation : FALSE_COMPLETION
  weight    : 5.0  ->  fine 50.0 (base 10.0)

                          trust          fine  state
  before                  100.0           0.0  HEALTHY
  after                    75.0          50.0  CRITICAL
  trust damaged, risk circuit-breaker tripped

[8] next morning  -- full account, no permission
  energy account : 100%  (never touched by the failed write)
  regime         : EMERGENCY
  new writes     : REFUSED
  morning write  : REFUSED
  on disk        : False
```

That last line is the assertion, not the narration: `DaytimeWriter` consults
`plan.allows_new_writes` before it opens a file, so the demo checks the
directory afterwards and finds nothing.

Honest limitation: the weights are policy, not measurement. Nothing here
computes what a false completion actually costs; it records that one happened
and charges a price the operator chose. That is the same trade as L1's energy
figures, and the same caveat applies.

## Design decisions

Thirteen choices look like arbitrary constants until you know what they buy. Each one
is pinned by a named test, so the behaviour cannot drift silently — but a test
that goes red only says *what broke*, not *why it mattered*. This section is the
why.

**The thresholds are strict (`ratio < 0.30`), so exactly 30.0% is still `GROW`.**
A task that lands precisely on the boundary keeps the expensive beat. Rounding
conventions would hide that moment; an off-by-a-hair threshold would spend the
demo's most instructive row. The boundary is a description of the charge, not a
tolerance. (`test_exactly_thirty_percent_is_still_grow`)

**When the slow beat is unaffordable, the scheduler degrades instead of
overdrawing.** There is a gap the spec does not name: the account is still
`GROW`, the task is genuinely complex, and the slow beat costs more than the
charge holds. Borrowing would make the balance lie; raising would turn a budget
constraint into an outage. Running fast and setting `degraded=True` keeps the
system serving while making the compromise *visible afterwards* — the record
says a richer account would have thought harder. That is resilience, not
shortfall, and the flag is the distinction.
(`test_degrades_rather_than_overdrawing`)

**A withheld slow beat records *why*, not just that.** `degraded=True` now comes
with `degraded_reason`, because the two causes want opposite fixes: a poor
account needs a refill, a distrusted one needs cheap verified work. Collapsing
them into one boolean is the same mistake as collapsing `EnergyExhausted` into
`InsufficientEnergy`, and it would have the same cost — every caller parsing a
message string to decide what to do. When both apply the refusal blames trust,
because the charge is refillable and the trust is the part nobody can top up.
(`test_the_reason_distinguishes_trust_from_poverty`)

**`EnergyExhausted` and `InsufficientEnergy` are separate exceptions.** One is
a policy floor ("below 10% I fund nothing, even with charge left"), the other is
a pricing problem ("this costs more than I have"). Callers must react
differently — refill versus run something cheaper — so collapsing them into one
type would force every caller to parse a message string to decide. The type
carries the decision. (`test_exhausted_and_insufficient_are_different_failures`)

**L3's thresholds (70/30) differ from L1's (30/10) on purpose.** They regulate
different quantities: how many agents may run versus whether one call may think
slowly. Unifying them into one scale would be tidier and wrong — it would tie
concurrency to per-call affordability, so a system with plenty of charge for one
expensive call would also conclude it can afford five parallel explorations. The
overlap is real and intended: at 25% both layers contract, one by refusing the
slow beat and the other by refusing to expand.
(`test_middling_budget_conserves_even_without_errors`)

**A shared substrate is shared by reference, and that is the point.** L4 exists
to avoid re-serialising a large payload into two context windows; a channel that
deep-copied on every `get` would pay the cost it was built to remove. So the
hazard is documented, the safe path is opt-in (`get_snapshot`), and the danger
is pinned by a test rather than left as a comment:
`test_get_returns_the_live_object_so_mutation_is_shared`. Note this is the exact
opposite of L7's guarantee, which is why they are separate layers — a membrane
isolates, a channel deliberately does not.

**Consolidation is idempotent by content address, not by a flag.** A boolean
"already summarised" marker drifts: it survives a file edit, or dies with the
process. Deriving the fragment id from the digest of its source messages cannot
drift, because the identity *is* the content. Running night mode five times
appends exactly what running it once does.
(`test_second_pass_appends_nothing`, `test_idempotence_survives_a_new_scheduler_instance`)

**Night mode refuses to run during the day.** The alternative — running whenever
called — turns a scheduled consolidation into a background thread that can
capture a half-written transcript and call it knowledge. Raising makes the state
transition explicit and keeps the output reproducible.
(`test_consolidating_during_the_day_is_refused`)

**The penalty boundaries belong to the *worse* side — the opposite of L1's.**
Exactly 30% charge is still `GROW`, but a fine of exactly 40.0 is already
`CRITICAL`. The asymmetry is not sloppiness in one of the two: charge is a
resource being consumed, so standing on the line is not yet a problem, while
damage is a fact being recorded, so standing on the line is already the damage.
Rounding a *damage* threshold in the system's own favour is how a ledger stops
being a ledger. (`test_the_boundary_belongs_to_the_worse_side`)

**The penalty can tighten the regime and never loosen it.** A `CRITICAL` ledger
forces `EMERGENCY` at 100% charge; a `WARNING` one demotes `GROW` to `CONSERVE`;
but no amount of trust turns a spent account back into `GROW`. The rule is
one-directional because the alternative — a system that can trade accumulated
trust for energy — is a way to argue with the audit, and an audit you can argue
with is a suggestion. (`test_penalty_only_tightens_never_loosens`,
`test_a_penalty_cannot_upgrade_an_emergency`)

**An unknown violation type must state its own weight.** Known types carry a
default severity, but `record_violation("SOMETHING_NEW")` raises rather than
charging a guessed price. Guessing is how a novel failure gets under-reported,
and the first occurrence of a new failure mode is exactly when the price matters
most. (`test_an_unknown_type_must_state_its_severity`)

**The slow beat is withheld for `CRITICAL` but not for `WARNING`.** A caution
and a ban should not be the same thing, or `WARNING` becomes `CRITICAL` with a
nicer name and the middle tier stops meaning anything. At `WARNING` L3 already
refuses expansion; taking the slow beat away as well would make the two
thresholds indistinguishable in practice while keeping them distinct in the
code. (`test_warning_is_not_enough_to_take_the_beat_away`)

**Withholding the slow beat makes recovery harder, and that is intended.** A
distrusted system now does its *worst* work at the exact moment it most needs to
prove itself — a genuine trap, not an oversight. It is the point: trust is
earned by cheap, checkable work, not by an expensive essay nobody can verify.
The consequence is worth stating because it makes the weights load-bearing: a
severity that keeps the system `CRITICAL` for a hundred passes is also a
decision to keep it shallow for a hundred passes.
(`test_critical_ledger_forbids_the_slow_beat_despite_full_energy`)

## On the numbers

The energy figures are policy, not measurement, `quality` is a fixed constant
per beat, and the "48 KB → 17 bytes" figure is a byte count rather than a claim
about your tokenizer's arithmetic. Nothing here calls a model, measures a joule,
or times a token. The penalty weights are the same kind of number: a policy
price, not a measured cost of a shipped lie.

What is real is the *structure*: a budget that can refuse, a beat selection that
must consult it, a concurrency limit that answers to failures, a ledger whose
arithmetic decides which capabilities come back and when, a handover whose cost
does not scale with the payload, and a consolidation pass that cannot duplicate
itself. The constants are where a real system would put its own measurements.

## Tests

```
Ran 371 tests in ~2s
OK
```

Organised by invariant rather than by module, because the interesting cases span
layers: a poor account cannot buy the slow beat (L1→L2), a handover crosses the
membrane as a key while the payload stays in the channel (L4+L7), a night pass
reads its sources without clearing them (L6+L7), a lysis cannot disturb the
neighbours (L7), and a false completion stops a system that still has 100%
charge — no concurrency (penalty→L3) and no slow beat (penalty→L2) at once. Two
tests check the zero-dependency claim itself: one parses every import with `ast`
against `sys.stdlib_module_names`, the other asserts `dependencies = []` in the
metadata.

`tests/test_external_interfaces.py` covers the two outside crossings. The
cross-package tests reach `harness-audit-sidecar` through `sys.path` and **skip**
when the adjacent checkout is absent, so the suite never acquires a hard
dependency on it; a small in-test double pins the protocol those tests rely on,
so a change on either side fails here instead of silently agreeing with itself.
