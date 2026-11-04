# Literature audit and prior-art alignment check

*Fractal Interfaces* (26 pp., `experiments/paper/main.tex`), audited 2026-11-04
before release. What follows is a retrieval record, not a similarity score. A
similarity score over titles measures shared words; the two words most likely to
be shared here, *fractal* and *interface*, are also the two words this paper uses
in a sense that the literature does not. The check that matters is whether the
*claims* are already occupied, and that is answered by reading abstracts, one at a
time, and saying which claim each paper does not make.

## 1. Channels, and what they answered

| channel | query | answered | notes |
| --- | --- | --- | --- |
| arXiv API | 8 query families, 6–8 hits each | yes | relevance-sorted; 2000–2026 returned, 51 of 60 records ≥ 2023 |
| CrossRef `works?query.bibliographic` | 4 query families, `from-pub-date:2023-01-01` | yes | finds journal/venue records arXiv does not index |
| Semantic Scholar Graph API | 2 attempts | **no** | HTTP 429, no key. Recorded as `UNAVAILABLE`; not substituted by a guess |
| identifier resolution | 29 bib entries | yes | `agent-harness-audit/audit_bib.py`: 27 `PASS`, 0 `FAIL`, 2 `INVALID` |

Snapshot: `experiments/paper/data/literature_search.json` — 12 query families,
60 records, 58 unique, dated `2026-11-04T13:2xZ`. Reproduce with:

```bash
python3 experiments/literature_search.py --rows 6 \
        --out paper/data/literature_search.json
```

The script prints the queries, keeps the raw records, and refuses to rank them.
Two of its twelve queries returned nothing at all, and the empty results are part
of the evidence: `ti:"emergent abilities" AND abs:mirage` (the phrase query is
stricter than the index) and `abs:"verification cost" AND abs:hierarchy` (no arXiv
paper joins those two concepts — see §3.2).

## 2. The five nearest papers, and what each one does not do

Ranked by proximity to a *claim*, not by citation count.

| # | paper | what it establishes | what it does not establish (our delta) |
| --- | --- | --- | --- |
| 1 | Schaeffer, Miranda & Koyejo, *Are Emergent Abilities of Large Language Models a Mirage?*, NeurIPS 2023 (`2304.15004`) | apparent capability jumps can be produced by a discontinuous metric over a continuous capability | no mechanism-indexed exponent; no statement of how *wide* the illusion is at a given depth; no separation between a crossing that held and a crossing that was merely reported |
| 2 | Zhao, Qin, Alvarez-Melis & Kakade, *Random Scaling of Emergent Capabilities*, 2025 (`2502.17356`) | randomness in the scaling process can generate breakthrough-shaped curves | treats the curve as a statistical object, not as a cost structure; derives no budget and no threshold |
| 3 | Greenblatt, Shlegeris, Sachan & Roger, *AI Control: Improving Safety Despite Intentional Subversion*, 2023 (`2312.06942`) | safety protocols can be compared when the model is treated as adversarial | compares protocols by outcome; does not ask whether the *kind* of evidence (recomputed witness vs. judgement about a self-report) is what decides, and measures no causal gap |
| 4 | Alabdulmohsin & Steiner, *A Tale of Two Structures: Do LLMs Capture the Fractal Complexity of Language?*, 2025 (`2502.14924`) | fractal self-similarity is a measurable property of language and of what models reproduce | fractality is a property of the *data*; the boundary between layers is not modelled, and no crossing cost or reach law follows |
| 5 | Gill, *The Economics of Machine Verification: Verification-Cost Shocks and the Extensive Margin of Monitoring*, SSRN 2026 (`10.2139/ssrn.7307578`) | a verification frontier: the minimum claim value at which verifying is worth doing, from fixed/marginal cost and pipeline reliabilities | a threshold on the *extensive margin* (which claims to verify). Ours is on the *intensive margin of depth*: how far a chain can be compiled at budget $B$, which moves only when the compiled regime pays a premium ($S$, $c_d$) the unlabelled one does not |

Papers 1–4 are cited in the new Related Work subsection; 5 is cited there *and*
in the threshold discussion, because it is the one genuine near-miss and the paper
should not be read as claiming to have invented the idea of a verification
threshold.

Also verified and cited, one line each on why they are not top-five: Wei et al.
2022 (`2206.07682`) states the emergence claim that paper 1 answers — cited as the
position under test, not as a neighbour; Du et al. 2024 (`2403.15796`) explains
continuity through pre-training loss rather than through the metric; van der Weij
et al. 2024 (`2406.07358`) is about evaluations being gamed, one level below the
witness question; Torkamandi 2025 (`2501.04286`) uses *fractal-like* for
trainability boundaries, a different object; Necula 1997
(`10.1145/263699.263712`) is the ancestor of check-cheaply, and is cited where
$\kappa$ is introduced so the debt is visible; Pearl 2009
(`10.1017/CBO9780511803161`) is cited at the point where the do-calculus notation
appears, which it previously was not.

## 3. The three questions the architect asked

### 3.1 Has anyone modelled an interface quantitatively with fractal theory?

**No — the construction is unoccupied; the word is not.** Every arXiv hit for
`"fractal interface"` is a physical object: contact friction, colloidal phase
separation, numerical homogenization, transmission problems, Schramm–Loewner
evolution. That literature asks how flux crosses a rough *surface* of fractal
dimension; none of it posits a difficulty, a span, a narrow condition, or a reach
law, and none of it is about a hierarchy of layers with a verdict at each rung.
The ML-side fractal work (paper 4; Torkamandi 2025; multifractal neuron analysis,
`2402.09099`) uses fractality as a *descriptive statistic* of data, weights, or a
loss landscape.

The risk this creates is discoverability, not duplication: a reader searching
"fractal interface" lands in tribology. The title cannot fix that, but the first
line of the abstract and the keyword list (`fractal hierarchy`, `cross-scale
interfaces`, `critical budget`) already disambiguate, and the Related Work
subsection now says explicitly what the word is being used *for*.

### 3.2 Has anyone derived a critical budget $B^{*}$ or a reuse rate $\kappa$?

**No derivation of either was found in the channels searched.** `"critical
budget"` returns bidding games, mixed-criticality scheduling, and auction theory —
budgets as resources in games, not as the point where compiling a layer stops
paying. `abs:"verification cost" AND abs:hierarchy` returns nothing on arXiv.

Three near neighbours, in descending order of discomfort:

- **Gill 2026** (paper 5) derives a verification frontier $L^{*}(\tau,N)$ in claim
  value. It is the closest published thing to a "$B^{*}$", and the paper now
  cites it and states the difference: Gill prices *whether* to verify a claim,
  we price *how deep* a compiled chain can run, and the interior threshold exists
  only because the compiled regime pays $S$ and $c_d$ (the degenerate case
  collapses to $c_v$, §IV-G).
- **Necula 1997**, proof-carrying code: the compile-once/check-cheaply idea, and
  the honest ancestor of $\kappa$. What is not inherited is the threshold
  $\kappa^{*}=1-1/L$ and the compound base $\min(L,1/(1-\kappa))$; the paper says
  so where it uses them.
- **StepCache** (`10.21203/rs.3.rs-9077245/v1`, Research Square 2026, single
  author) is step-level reuse *with lightweight verification* for LLM serving —
  the closest engineering implementation of the same economy. It is not cited:
  it is a serving-system preprint with no cost law, and citing it would imply an
  intellectual debt that is not there. If it acquires a venue, it becomes a
  citation, not a competitor.

### 3.3 Is the "strobe effect" / emergence mirage already settled mathematically?

**Settled as a diagnosis, not as a law.** Schaeffer et al. establish *that* a
discontinuous metric can manufacture a jump; Zhao et al. add a random-scaling
mechanism; Du et al. replace the metric with loss. None of them produces an
exponent, predicts the *width* of the transition, or ties the width to task depth.
The paper's claim is deliberately narrower than "emergence is fake": it derives
$k=\gamma P_0|\ln s|$, shows the exponent is mechanism-indexed rather than
universal, and predicts a transition ~four decades wide independent of depth — a
number that can be checked against a published scaling law, and is
(Experiment F). The Related Work now states this ordering so a referee does not
have to infer it.

## 4. What changed

**Bibliography: 19 → 29 entries.** Ten added, all with a witness:

| key | identifier | verdict | why |
| --- | --- | --- | --- |
| `pearl2009` | `10.1017/CBO9780511803161` | `PASS` | the do-calculus is named in §V-C and was uncited |
| `necula1997` | `10.1145/263699.263712` | `PASS` | ancestor of $\kappa$, cited to keep the debt visible |
| `wei2022` | `2206.07682` | `PASS` | the claim under test |
| `greenblatt2023` | `2312.06942` | `PASS` | agent reliability under subversion |
| `vanderweij2024` | `2406.07358` | `PASS` | evaluation integrity |
| `du2024` | `2403.15796` | `PASS` | continuity via loss |
| `zhao2025` | `2502.17356` | `PASS` | random scaling of emergence |
| `alabdulmohsin2025` | `2502.14924` | `PASS` | fractal lens on language |
| `torkamandi2025` | `2501.04286` | `PASS` | fractal-like trainability boundaries |
| `gill2026` | `10.2139/ssrn.7307578` | `PASS` | verification threshold, the near-miss |

`CITATION_AUDIT_REPORT.md` and the Appendix table are regenerated, not edited:
27 `PASS`, 0 `FAIL`, 2 `INVALID` (Mandelbrot 1982 and Simon 1962, both pre-DOI and
both already disclosed as `INVALID` in Appendix A.1). No new `INVALID` was
introduced. No `FAIL` — no identifier was found to name the wrong paper.

**Text:** a `Relation to prior work` subsection in the Introduction (six
sentences, cites 1–5 above and qualifies the $B^{*}$ claim), and `\cite{pearl2009}`
at the do-calculus sentence in §V-C. Nothing else in the argument was changed;
no experiment, number, or verdict moved.

## 5. What this audit cannot establish

- **It is not exhaustive.** Two channels answered; Semantic Scholar did not
  (`UNAVAILABLE`, §1). Google Scholar, Scopus, and publisher-side search were not
  queried. A derivation sitting in a paywalled venue that CrossRef's
  bibliographic index ranks below the top six for these queries would not have
  been seen. This is a `BLIND` region, and it is the reason the paper says "to our
  knowledge" rather than "no one has".
- **It does not measure overlap.** No similarity score is reported because none
  would be a witness. The evidence is the per-paper statement of what each paper
  does *not* do, which a reader can disagree with by reading the paper.
- **It does not cover the quantum-biology half.** Those citations were audited by
  identifier, but the physics claims were not re-searched; that literature
  predates 2023 and was already reviewed in the manuscript's own narrative.
- **It found one uncomfortable neighbour** (Gill 2026) and cites it rather than
  routing around it. A novelty claim that survives only by not citing the nearest
  paper is not a novelty claim.
