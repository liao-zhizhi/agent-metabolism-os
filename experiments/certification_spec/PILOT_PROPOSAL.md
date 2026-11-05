# Causal Certification: Pilot Proposal

**To:** Head of Autonomy Safety / ADAS Validation, [OEM]
**From:** [Architecture Office]
**Re:** A 14-day certification pilot that survives a court transcript
**Version:** 1.0 · November 2026

---

## 1. The Illusion of Safety

Your validation suite measures collisions. Collisions are the one metric that cannot detect the failure that matters.

We ran 100 matched episodes on a wet road where grip fell from μ = 0.90 to 0.35 and **the road looked identical**. A confidence-gated stack — the standard architecture — collided in **50%** of wet-road episodes (15/30), and **5 of 10** once actuator wear was added, with a minimum gap of 0.16 m. Its internal confidence stayed high throughout. It never knew.

Then the same episodes at **20% grip loss**. Both the gated stack and ours recorded **zero collisions**. Both would have passed your acceptance gate. Only one knew the road had changed: our estimate tracked to 0.720; theirs held at 0.900 and never moved.

That is the blind spot. At 20% drift your test suite, your KPI dashboard, and your release gate all report green — not because the system is safe, but because the failure has not yet grown large enough to produce a crash.

By the time collision rate separates good systems from bad, the drift is 61%. At 20%, the only observable difference is a number nobody is recording.

---

## 2. The Regulatory Timebomb

Two clocks are running.

**Europe.** The AI Act's high-risk regime reaches safety components of vehicles and machinery. The obligations are procedural: risk management, technical documentation, logging, human oversight, post-market monitoring. Note what is absent — a definition of *sufficient evidence*. The regulation tells you to document. It does not tell you what a defensible document is.

That gap will be filled case by case, in court, against the first manufacturer whose logs cannot answer one question: *what did the system believe, and what proved it wrong?*

**The state-of-the-art defence is expiring.** Product-liability doctrine has shielded autonomy developers because the failure modes were genuinely unknown. That defence weakens the moment a failure mode is published, measured, and preventable by an available method — which is what this proposal puts on the record.

The first fatality in which the plaintiff's expert cites a published conformance specification, and the defendant holds no witness log, will not be a product-liability case. It will be a documentation case.

---

## 3. The Solution: Causal Certification

We do not certify that your system is safe. We certify that **every safety claim your system makes is backed by physical evidence an independent party can re-run.**

Fourteen days, four steps:

- **Days 1–3 — Intake.** Seven witness artifacts: braking curves, μ-estimate timeseries, actuator identification, commitment-vs-reality deviation, verdict ledger, checkpoint registry, sensor health. A missing artifact is *refused*, not graded.
- **Days 4–9 — Red-line execution.** Wet road at high confidence; 20% drift; sensor corruption; nominal baseline. Each yields one of four verdicts — **PASS / FAIL / INVALID / NOT RUN** — under one constraint that makes a verdict causal rather than retrospective: it must precede the failure by 0.5 s. A FAIL after impact is worth nothing.
- **Days 10–12 — Cross-checks.** Replay detection, reason-code alignment, independent re-run of a random 5% of PASS claims.
- **Days 13–14 — Certificate.** C1 or C2 conformance, plus a defect report your engineers will use more than the certificate.

What you buy is the **ledger**: 32,586 witness checks in our reference run, each bound to a prediction ordinal and a hash. It is the only artifact in the file that can be re-read by someone who does not trust you.

---

## 4. ROI & Legal Shield

**Cost.** Once a validated behaviour is solidified, repeated verification collapses into a lookup. Measured at 10% drift, steady-state cost falls from 1.00 to 0.085 per cycle — an **89.4% reduction** — with the marginal price of drift converging on the verification unit price (regression slope 0.9994). This is not a discount on testing. It is a change in what testing is.

**Liability.** Three auditable mechanisms:

1. **A timestamped chain of belief.** What the system predicted, what the world did, and when it knew.
2. **A failure budget with teeth.** INVALID is capped at 5%. A system that cannot see is *shown* to be unable to see, rather than assumed safe.
3. **A transferable defence.** Certified with a verified log, the burden of proof moves to the plaintiff. Uncertified with no log, it moves to you.

The number that decides budget lines: the entire safety increment costs **0.19 s of average journey time**, and one avoided collision is worth **0.95 s**. You are buying insurance for less than one second of a drive.

---

## 5. Pilot Pricing

| Item | Price | Terms |
|---|---|---|
| Certification pilot (14 days) | **$5,000 – $10,000** | Fixed fee. One system, one ODD. 50% credited against the first annual licence. |
| Conformance certificate | Included | C1 or C2. |
| Production royalty | **$2.00 per unit** | Units shipped under the certified configuration only. Annually capped. |
| Re-certification | 40% of pilot fee | Required on model, calibration, actuator, or ODD change. |

**No charge for a refusal.** If we refuse your submission for missing artifacts, we tell you exactly what is missing and why, at no cost. A refused submission is a finding about your evidence pipeline, and it is worth more than the fee.

The $2 is not the business. The certificate is not the business. The business is that your release gate acquires a definition of *evidence*, and every future release is measured against it.

---

## Next Step

Send one system, one ODD, and one week of existing logs. We return a free three-page gap assessment: which of the seven witness artifacts you can already produce, and which red-line case you would fail today.

You will know within five days whether this is a $10,000 purchase or a $10 million problem.

---

**Boundaries — read before circulating.** An evidence certification, not a functional-safety approval (ISO 26262) and not a guarantee against harm. Figures come from a 2-D kinematic audit: mechanism transfers, absolute values do not. Verify regulatory dates against current texts. Article 11 language requires counsel in your jurisdiction.

*Sources: `ad_cel_summary.json`, `cost_theory_numbers.json`, `CERTIFICATION_SPEC_V0.1.md`. Every figure above reproduces from those files.*
