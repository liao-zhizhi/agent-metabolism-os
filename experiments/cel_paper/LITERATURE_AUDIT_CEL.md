# LITERATURE_AUDIT_CEL.md

**Audit of `experiments/cel_paper/main.tex` + `references.bib` before release.**
Performed 2026-11-05, entirely locally, with no commit and no push.
Target artifact: `main.pdf`, 12 pages, SHA-256 `982f891a…54dfdd9c2`
(terminology sweep applied after the audit; see §9).
Bibliography: 24 entries, SHA-256 `f800602d…99f285`, **24/24 PASS** against arXiv/CrossRef.

---

## 0. Verdict in one paragraph

The paper's *mechanism* is not pre-empted, but its *framing* was too broad and is
now tightened. Two 2025–2026 lines of work sit close enough that an uninformed
claim of novelty would be rejected: **runtime safety layers for VLA policies**
(VLSA/AEGIS, RoboSafe, AnySafe, and the training-time SafeVLA we already benchmark
against) and **causal/physics-grounded auditing of driving planners** (CADET,
robust-conformal planning under shift). None of them does what this paper does —
register a *prediction* before an action chunk, measure the *outcome* after it,
and refuse the claim rather than clip the action — and none carries a
tamper-evident ledger or an ordinal bound to the attempt. The paper now says this
explicitly in Related Work instead of leaving it to the reader. Three genuine
bibliographic defects were found and fixed (§5), including one identifier that
pointed at an unrelated mathematics preprint.

---

## 1. Method, and what it could not see

Two tools were used, deliberately separated: a **search** (reproducible, dumb)
and a **judgement** (signed, below).

*Search.* `experiments/cel_paper/lit_audit.py` queries arXiv `export.arxiv.org`
and Semantic Scholar `graph/v1/paper/search` for the six architect-specified
phrases plus two neighbour phrases, filters to 2023→present, dedupes by title and
writes `lit_audit_results.json`.

**The automated search did not work, and the record says so.** All eight queries
returned HTTP 429 from both agencies for the duration of this audit (this host is
behind a shared, throttled egress; arXiv and CrossRef *did* answer the
bibliographic witness in §5, at roughly one request per three seconds, so the
limit is on broad keyword queries, not on identifier lookups).
`lit_audit_results.json` therefore contains `failures` for 15 of 16
query/source pairs and zero kept hits, except one Semantic Scholar return for
"Metabolic Solidification" that is entirely unrelated (gypsum purification,
hydrogels) — which is itself the useful answer for that phrase.

*What replaced it.* A web search over the same phrases, restricted to arXiv and
PMLR pages, with every candidate then confirmed by reading its arXiv abstract
page (saved verbatim in the session log). That is weaker evidence than an API
sweep — it has no hit counts, no recall guarantee, and it cannot prove absence —
and it is reported as such. Any claim below of the form "nobody has done X"
should be read as "we did not find it, searching these eight phrases in this
way".

*Judgement.* Reading abstracts and, where the distinction is subtle, the method
sections, then asking one question per paper: **would a referee reasonably say
this paper already is ours?** The five that come closest are below.

---

## 2. The five closest works

| # | Work | Venue / id | What it actually does | Why it is not this paper |
|---|------|-----------|----------------------|--------------------------|
| 1 | **VLSA / AEGIS** — *Vision-Language-Action Models with Plug-and-Play Safety Constraint Layer* | arXiv 2512.11891 (2025) | Adds a CBF-QP layer between a VLA and its actuators; VLM + open-set detection + depth turn language into geometric constraints; the layer activates only when a violation is predicted; evaluated on **SafeLIBERO**, +59.16% obstacle avoidance, +17.25% success | The constraint is **stated** (obstacle geometry from perception). Under a drift that changes friction and not geometry, the CBF enforces the *wrong* model exactly. CEL has no constraint: its spec is the policy's own next-state prediction, and it refuses the *claim* rather than clipping the action |
| 2 | **CADET** — *Physics-Grounded Causal Auditing of End-to-End Driving Planners* | arXiv 2606.14438 (2026) | Training-free audit of a deployed E2E planner's reliance on scene elements that cannot physically influence the decision; fuses perturbation influence with a physics-geometric prior (TTC, drivable corridor); repairs by masking flagged queries at test time | Audits the **input dependence** of a plan, offline, with a hand-built prior. An input audit is blind to a drift that leaves the input distribution intact — a wet road looks like a dry road. CEL audits the **executed outcome** against a prediction registered before execution, which is exactly the signal CADET cannot see |
| 3 | **AnySafe** — *Adapting Latent Safety Filters at Runtime…* | arXiv 2509.19555 (2025) | Hamilton–Jacobi safety filters in a world model's latent space, re-conditioned at runtime on a **constraint image**, with conformal calibration of the failure threshold; Franka hardware | Still requires an image that *depicts the constraint*, and still certifies against the world model's imagined dynamics. CEL requires neither, and its witness is compared to the plant, not to the imagination |
| 4 | **RoboSafe** — *Safeguarding Embodied Agents via Executable Safety Logic* | arXiv 2512.21220 (2025) | A guardrail VLM emits executable temporal safety predicates from long/short-horizon memory; actions are **blocked or re-planned** on violation; −36.8% risk occurrence | Predicates are authored/generated about *hazardous instructions*; the mechanism is a veto (`block`) or a replan from the guardrail's own reasoning. CEL's refusal is a **measurement of mismatch** and its replan is a re-estimate of *task stage* from the observed state, with zero false refusals by construction (§V of the paper) |
| 5 | **When Environments Shift** — *Safe Planning with Generative Priors and Robust Conformal Prediction* | arXiv 2602.12616 / PMLR v331 (2026) | Diffusion prior + robust conformal prediction inside MPC; the nuisance parameter (traffic density, visibility) is observed online and the conformal region is rebuilt for it | Shares the diagnosis — guarantees computed on the training environment collapse under shift — and answers it by re-estimating a **statistical** object. CEL re-estimates a **physical** one ($\hat\mu$, $\hat\tau_a$, $\hat\tau_s$) from the actuator's own saturated and stepped responses, and never needs the shift to be observable in the input |

Two secondary clusters, cited and differentiated in the paper but not ranked
above because they answer a different question:

* **Physical hallucination in world models** — Grounded-Dreamer (2610.13164),
  Causal-Plan (2606.01810). Both use a *simulator or geometric prior* to filter
  generated rollouts. That oracle is unavailable at execution time on a physical
  robot, which is where CEL lives.
* **Uncertainty for E2E driving** — UniUncer (2603.07686) and CVaR-constrained
  diffusion planning (2606.03296). Both improve the *plan's* handling of
  uncertainty; neither measures the executed consequence.

---

## 3. Terminology collisions (naming risk, not novelty risk)

| Phrase | Status | Consequence |
|--------|--------|-------------|
| **"Causal Execution Layer"** | **Used before, in another field.** Torun (2020) introduced a "causal execution layer" for ANN-based S-parameter modelling of RF transistors, to keep predicted scattering parameters physically consistent. Also used loosely as a product phrase for agent infrastructure. No robotics/VLA paper uses it. | Low severity, real risk of a confused referee search. The paper defines the term in §III on first use; keep that definition. Do **not** claim the coinage in the abstract |
| **"Physical Hallucination"** | Established term (Grounded-Dreamer, RoboStereo, HaWMPO, RoboWorM), always about *generated* content | Fine to use descriptively; do not present it as our coinage |
| **"Safe VLA"** | Occupied by SafeVLA (NeurIPS 2025 Spotlight), a *training-time* CMDP method | We use it only as the name of the baseline arm, correctly |
| **"Metabolic Solidification"** | **No relevant prior art.** Web search and Semantic Scholar return biology (gypsum, hydrogels). "Code solidification" exists in the LLM-agent-systems literature (SkVM) but is a different claim | Safe to use, but it appears in *this* paper only in Future Work; the phrase is load-bearing in paper #4, not #5 |
| **"Safety Filter" / "Shielding"** | Occupied, precisely, by control theory (CBF, predictive safety filter) and by RL | Resolved: the paper now states in the abstract, the intro and Related Work that CEL is **not** a shield, safety layer or safety filter. The names are kept only where they denote the prior art they belong to |

---

## 4. What the paper now claims, and what it must not

**Survives scrutiny (claim it):**
1. The witness is the policy's **own prediction, registered before execution**, and
   the comparison is made after execution. No reviewed work registers a prediction
   and later redeems it against a measured state.
2. **Refusal is a re-estimate, not a veto or a clipped action.** The evidence is
   counterfactual pricing of false vetoes — a metric the safety-filter literature
   does not report, because its mechanisms cannot produce one by construction.
3. **Tamper-evident, ordinal-bound registration** (ERR-004). The replay defect and
   its fix appear to be novel as a *statement about physical witnesses*, and the
   paper's own defect is the strongest evidence for its thesis.
4. **Cross-domain transfer with online system identification** — the same layer on
   a LIBERO manipulation task and a 2D driving plant, recovering $\mu$ and the two
   actuator lags from saturation and step responses, with no privileged state.
5. **Cost accounting** — decision latency vs tamper-evidence latency, and
   $O(\text{ledger})$ verification reported as a defect with a price curve.

**Must not claim (removed or qualified):**
- "First runtime safety mechanism for VLA" — false; VLSA/AEGIS, RoboSafe, AnySafe
  and SafeVLA exist.
- "First causal audit of a driving planner" — false; CADET exists.
- "Safety filtering under distribution shift" — occupied by robust-conformal
  planning.
- Any absolute statement of novelty in the abstract beyond the witness/ordinal
  framing.

**The residual vulnerability a referee will find.** Our driving planner is a
scripted receding-horizon controller, not UniAD or VAD, and the confidence head is
a declared scalar, not a trained head. The paper already states both under Threats
to Validity; the sharpest attack is "your baseline is a strawman". The defence is
the one the paper already makes: the guard is *arithmetic* — the friction-limited
stopping distance exceeds the available road on average, before any policy is
consulted — and the same deficit reappears in a CBF-based layer unless it is given
the true $\mu$. A reviewer who wants the experiment re-run with a real checkpoint
should be answered by the Future Work item, not by argument.

---

## 5. Bibliography audit (`audit_bib.py`)

`agent-harness-audit/audit_bib.py references.bib` resolves every entry against
arXiv or CrossRef and compares titles. Result after fixes: **24 entries, 24 PASS,
0 FAIL, 0 INVALID**. Reproduce with

```
python3 ../../agent-harness-audit/audit_bib.py references.bib            # grouped report
python3 ../../agent-harness-audit/audit_bib.py references.bib --json     # per-entry verdicts
```

Exit status is 0 when nothing FAILs, which is what a release gate should read.

Before the audit, 12 of 14 entries carried no resolvable identifier at all
(`INVALID`: "no DOI and no arXiv id"), so nothing could be checked. Adding
identifiers is what made the errors visible. Four defects were found:

| Entry | Defect | Fix |
|-------|--------|-----|
| `zhang2025safevla` | Title said "**via Safe Reinforcement Learning**"; arXiv says "**via Constrained Learning**". Authors were wrong: "Zhang, Yichen … Tian, Xiaoyu … Wang, Jinghan" instead of **Zhang, Yuhao; Ji, Jiaming; Lei, Yingshan; Cai, Yishuai; Dai, Josef; Chen, Yuanpei; Yang, Yaodong**. Venue is NeurIPS 2025 (Spotlight) | Corrected in full; `eprint` 2503.03480 added |
| `chen2023vad` | Author list was **fabricated in part**: a "Gao, Hao" who is not an author, first two authors swapped, three authors missing. Actual: Jiang, Bo; Chen, Shaoyu; Xu, Qing; Liao, Bencheng; Chen, Jiajie; Zhou, Helong; Zhang, Qian; Liu, Wenyu; Huang, Chang; Wang, Xinggang | Replaced with the correct ICCV author list; `eprint` 2303.12077 |
| `hu2023uniad` | "Zhu, **Xiegang**" → "Zhu, **Xizhou**"; `eprint` missing | Corrected; `eprint` 2212.10156 |
| `zhu2020robosuite` | **Introduced by this audit, then caught by it**: the identifier first added was arXiv **2003.08584**, which is *"Hardy type inequalities and parametric Lamb equation"* — an unrelated mathematics preprint. Correct id is **2009.12293** | Corrected; author list completed to the official eight |

Also corrected: `guo2026cadet` now carries the arXiv-registered title
("Physics-Grounded Causal Auditing of End-to-End Driving Planners") rather than
the longer HTML title, so the witness matches the metadata a reader will see.

Three entries that were in the file but never cited (`todorov2012mujoco`,
`zhu2020robosuite`, `amodei2016concrete`) are now cited where they belong — the
MuJoCo/robosuite pins in the experimental protocol, and the specification problem
in the safety-filter comparison.

### Must-cite checklist

| Requirement | Status |
|-------------|--------|
| LIBERO dataset paper | ✅ `liu2023libero` (NeurIPS 2023, 2306.03310), cited in Intro and Protocol |
| UniAD **or** VAD (E2E driving planner) | ✅ **both** — `hu2023uniad` (CVPR 2023, 2212.10156) and `chen2023vad` (ICCV 2023, 2303.12077), cited in Related Work and §VI |
| VLA milestone papers | ✅ RT-2 (`brohan2023rt2`), OpenVLA (`kim2024openvla`), $\pi_0$ (`black2024pi0`) |
| Safety filter / CBF classics | ✅ **added** — `ames2019cbf` (ECC 2019, DOI 10.23919/ECC.2019.8796030) and `wabersich2021safetyfilter` (Automatica 129:109597, DOI 10.1016/j.automatica.2021.109597), both cited in Intro and Related Work |
| Shielding / runtime verification | ✅ `alshiekh2018shielding`, `bartocci2018runtime`, `katz2017reluplex` |
| VLA runtime guardrails (2025) | ✅ **added** — `hu2025vlsa`, `wang2025robosafe`, `agrawal2025anysafe` |
| Causal / physics-grounded auditing | ✅ **added** — `guo2026cadet`, `rahaman2026environments`, `sharma2020dowhy` |
| World models & physical hallucination | ✅ **added** — `ha2018worldmodels`, `zhou2026groundeddreamer`, `lu2026causalplan` |
| Safe VLA baseline | ✅ `zhang2025safevla`, metadata now correct |

---

## 6. Changes made to `main.tex`

1. **Intro.** The confidence gate is now presented as the weakest member of a
   family that includes safety filters and CBFs, with the shared assumption named
   ("they are exact with respect to the model they are given").
2. **Related Work** rewritten into seven paragraphs: VLA policies; shielding,
   safety filters and CBFs (with the *model-is-wrong* distinction spelled out);
   runtime guardrails for VLA (VLSA, RoboSafe, AnySafe, SafeVLA — with the two
   structural differences: no stated constraint, no action modification);
   uncertainty gating (robust conformal + CADET); causal framing (DoWhy + CADET,
   input audit vs outcome audit); world models and physical hallucination;
   end-to-end driving (UniAD, VAD).
3. **§VI intro.** Cites the conformal-planning and CADET lines and states that
   both are statements about the plan and its inputs, whereas the layer measures
   what the vehicle did.
4. **Protocol.** MuJoCo and robosuite are now cited, not merely version-pinned.
5. **Terminology sweep** (§9): self-description renamed to *causal auditor* /
   *execution warranty*; baseline arm renamed; prior-art limitations added.

All additions are prose-level; no number, table or figure was touched. `make_numbers.py`
remains the only source of statistics.

---

## 7. Rebuild and packaging

```
pages        12
sha256       982f891ad736e35178ab22c20e7b258db3f2869f1cbee6a4b80fef354dfdd9c2
errors       0
overfull     0        (one Underfull \vbox, from IEEEtran's column balancing)
undefined    0        citations and references
bibitems     24       all 24 entries cited
```

The page count moved 11 → 12 because the terminology sweep and the honest
prior-art limitation (both below) add roughly two thirds of a column; the last
page carries the end of the conclusion and the bibliography at 58% fill.

`build.sh` now pins `SOURCE_DATE_EPOCH` and `FORCE_SOURCE_DATE=1`, so the digest
is a function of the sources rather than of the wall clock: two consecutive clean
builds produce byte-identical PDFs and the same SHA-256. Before that change, each
build differed because pdfTeX stamps `/CreationDate` and `/ID`.

**Reproducing the artifact from the archive alone.** The archive is flat (as arXiv
requires) and carries everything the build consumes under `provenance/`: both
audit summaries, both episode CSVs, the two harness scripts that
`make_numbers.py` hashes, and the driving figure. Unpack it one level down, lift
`provenance/*` into the parent, and run `build.sh`:

```
mkdir -p /tmp/v/cel_paper && cd /tmp/v/cel_paper
unzip cel_paper_source.zip && cp provenance/* .. && ./build.sh
```

That was executed from a clean directory and reproduced
`982f891a…54dfdd9c2` **byte for byte**, with the same 12 pages and the same
0 error / 0 overfull / 0 undefined counts. The 28 MB ledger
(`ad_cel_ledger.jsonl`) is deliberately *not* shipped; the CSVs are the audit's
summaries of it, and the paper's claims are about the summaries.

Artifacts: `main.pdf`, `cel_paper_source.zip`, `LITERATURE_AUDIT_CEL.md`,
`lit_audit.py`, `lit_audit_results.json` (the failed-search record, kept because
a search that could not run is a finding).

---

## 8. Residual risks, ranked

1. **A referee knows VLSA/AEGIS and reads the abstract as a safety-layer paper.**
   Mitigation: the second paragraph of Related Work and the explicit "this is not
   a filter" sentence. Do not let the abstract say "safety layer".
2. ~~The baseline is called "Safe-VLA" but is a confidence threshold.~~
   **Resolved** in §9: the arm is now *Confidence-gated Baseline*, so a reader
   cannot mistake it for the NeurIPS 2025 method.
3. **The driving plant is kinematic.** Mitigation: Threats to Validity plus the
   arithmetic-deficit argument, which is model-independent.
4. **"Causal Execution Layer" is a used term in RF engineering.** Mitigation: the
   definition on first use; a footnote is defensible but not required.
5. **Search recall is unproven** (§1). If a reviewer produces a paper that does
   the same thing, the honest reply is that this audit could not run its scripted
   sweep; the manual sweep covered eight phrases and 2023→present.

---

## 9. Terminology sweep (after the audit)

The audit's own §3 said the framing was the risk, not the mechanism. This sweep
acts on that.

| Was | Is | Where |
|-----|----|-------|
| "CEL … causal execution layer" as the only name | plus **"a *causal auditor* of the policy's own physical claims"**, and an explicit denial: "It never filters, clips or blocks an action: it audits the claim attached to one." | Abstract |
| "a grant means the world matched" | a matching digest earns an **execution warranty** — evidence bound to *this attempt* | Abstract, Intro, §III, Fig. 1 caption, protocol |
| "warrant" (bare noun) | "execution warranty" | all four prose sites renamed |
| — | New: "The layer is therefore an auditor of physical truth and not a filter on action: it holds no safe set, states no constraint, and has no authority to clip a command." | Intro |
| — | New paragraph defining the warranty as "a receipt for a claim, not a permission to act… an unmodified action with a checkable warranty is an audit trail, whereas a clipped action has lost the evidence of what the policy wanted." | §III |
| — | New: "CEL is not a shield, a safety layer or a safety filter: it has no safe set, certifies nothing, and never touches the action." | Related Work |
| Arm A "(Safe-VLA)" | **Arm A (confidence-gated baseline)** | §IV protocol |
| Table column "Safe-VLA" | **"Conf.-gated"** (generated, so `make_numbers.py` was patched too) | Table I |
| Table columns "Safe" | **"Conf."**, with a one-line caption note defining it | Table II |
| — | New Limitations paragraph: scripted sweep blocked by HTTP 429 at both agencies; manual search of the eight phrases; no recall guarantee; novelty claims are about the cited works | Threats to Validity |

**What was deliberately not renamed.** The words *safety filter*, *shielding*,
*control barrier function* and *SafeVLA* still appear wherever they name the
prior art. Renaming `wabersich2021safetyfilter` to "predictive causal auditor",
or the NeurIPS 2025 SafeVLA method to anything else, would be a factual error and
would destroy the very distinction the paper depends on. The rule applied is:
**our own mechanism may never be described as a safety layer; other people's
mechanisms must be described by their own names.**

One consequence worth flagging: with the baseline now called
*Confidence-gated Baseline*, a reader can no longer mistake it for the SafeVLA
*algorithm*. That removes risk (2) from §8 entirely, at the cost of making it
slightly less obvious that we benchmark against a *published* system — the
Related Work paragraph on runtime guardrails carries that job now.
