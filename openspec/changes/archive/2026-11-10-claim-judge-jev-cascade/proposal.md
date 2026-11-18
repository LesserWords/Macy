# Proposal: Jev-Verified Claim Judge, with Key Results

**Plan Item:** BM-1 to BM-6 ([docs/plans/BM.md](../../../docs/plans/BM.md)) · **Features:** F14 · **ADRs:** ADR-002, ADR-005, ADR-007, ADR-016, ADR-026

## Why

Macy's planned pocket add-on must return a claim-by-claim publish or revise decision that is cheaper than a Haiku-only checker and better than a plain prompt. The first benchmark did not establish that: on 80 short articles the plain prompt caught every planted error, the per-claim prototype had far fewer false rejections (8.3% vs 64%), and the preset bar was not met because the set is too easy.

Jev (`typesafe/jev-1.13`) is an open-access decision model on OpenRouter at US$0.042 per million input tokens, output free. OpenRouter's own test found it matched an LLM judge on closed, evidence-supplied faithfulness checks (84/88 vs 83/88, Brier 0.043 vs 0.054) and was weaker on open rubrics over long text. That result is vendor-run and small, so this change tests it independently, and states in advance what "delivered" and "worth it" mean.

## Objective

Determine, with results that were fixed before the evidence, whether a Jev-verified cascade is a cheaper claim judge that beats a tuned prompt, or stop.

## Key Results: Delivered

Delivered means the experiment was executed correctly. A negative outcome can still be delivered. All must be true.

| KR | Result | Evidence |
|---|---|---|
| **D1** | Rules, thresholds and development/holdout split are committed before any new benchmark run | That commit is an ancestor of every run's recorded SHA (`git merge-base --is-ancestor`) |
| **D2** | Decisions client sends the documented request, pins the model and parses answers, refusals and errors | Offline tests pass; one live smoke run reports `usage.cost` above 0 |
| **D3** | `plain_tuned`, `per_claim_v2`, `jev_judge` and `jev_cascade` all run | 3 development runs, 0 failed articles |
| **D4** | Private holdout exists: at least 30 real public articles (15 per language), at least 120 planted errors, at least 30 clean articles, at least 15% with more than one error, median source at least 800 words | Dataset report; every label reviewed by a person; SHA-256 recorded |
| **D5** | Every run report records git SHA, dataset SHA-256, resolved model IDs, measured cost, escalation rate, per-language results and Jev reliability table | Report JSON validated by an offline test |
| **D6** | Data handling confirmed before any customer text is sent | TypeSafe policy reviewed and recorded; a live request with `provider.data_collection: deny` accepted, or documented as refused |
| **D7** | Outcome recorded | ADR-026 moved to Accepted or Rejected with the holdout numbers |

## Key Results: Worth It

Measured once on the holdout, as the mean of 3 runs of `jev_cascade`, using measured cost. Rates are percentage points.

| KR | Result | Threshold | Why it matters |
|---|---|---|---|
| **W1** | Beats a tuned prompt | At least 10 points better than `plain_tuned` on false passes or clean rejections, and no more than 3 points worse on the other | The reason anyone would pay |
| **W2** | Safe | Catch at least 90%; false pass at most 10% | A wrong "publish" is worse than no gate |
| **W3** | Usable in a loop | Clean rejection at most 25% | Otherwise good work keeps being sent back |
| **W4** | Cheaper | Cost per article at most 50% of `per_claim_v2` | The reason to add a second vendor |
| **W5** | No quality loss from Jev | Within 3 points of `per_claim_v2` on catch and false pass | Savings must not buy errors |
| **W6** | Trustworthy acceptance | Precision of claims auto-accepted by Jev at least 97% | The threshold must mean something |
| **W7** | Shows its evidence | Every verdict has a source passage; at least 90% correct in a 50-claim human audit | Buyers asked to check, not trust |
| **W8** | Works in both languages | pt-BR within 5 points of en-US on catch and clean rejection | The product is bilingual |
| **W9** | Stable | Range across 3 runs at most 5 points on catch, false pass and clean rejection | One lucky run proves nothing |

### Decision rule

> **Amended 2026-11-11, before any holdout run:** `jev_judge` is also a GO candidate, and GO builds on the cheaper passing candidate. See [2026-11-11-claim-judge-add-jev-judge](../2026-11-11-claim-judge-add-jev-judge/proposal.md).

- **GO:** W1 to W9 all pass. Build the add-on on the cascade.
- **GO WITHOUT JEV:** `per_claim_v2` passes W1, W2, W3, W7, W8 and W9, but the cascade fails W4, W5 or W6. Ship Haiku-only; drop Jev.
- **STOP:** No method passes W1 to W3. The board's condition applies: do not build the add-on.

The holdout is single-use. Tuning after seeing it requires a new holdout.

## What Changes

- Freeze rules and a development/holdout split in `evals/claim_benchmark/`.
- Add a Decisions API client and four methods to the benchmark.
- Add a private real-article holdout and richer run reports.

## Capabilities

### New Capabilities

- `claim-benchmark`: rules, methods, datasets, reports and the outcome decision for evaluating claim judges.

## Out of Scope

Production engine changes, `DecisionPort`, the n8n node, API, MCP server, billing, Laya, Opus runs, Jev 1.14 comparison, writing or drafting quality, and pre-sale. Skills from skills.sh are not installed; only concepts are reused.

## Impact and Risks

Only `evals/`, `.gitignore` and docs. Live budget for the whole change: US$5.

- **Alpha endpoint:** the model is open to all, but OpenRouter's reference, tutorial and the model page's own code still use `POST /api/alpha/decisions` and label its shape alpha. The URL lives in one constant and the model is pinned.
- **Single provider:** TypeSafe is the only host; if it fails, requests fail.
- **Vendor evidence:** published comparisons are vendor-run, so none of them counts toward a key result.
- **Long text:** above about 32,000 bytes Jev uses a slower, less accurate mode, so state is kept under that limit.
