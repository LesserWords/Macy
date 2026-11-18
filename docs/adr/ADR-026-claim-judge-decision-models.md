# ADR-026: Claim-judge models for the verification add-on

**Status:** Accepted · **Date:** 2026-11-11 · **Deciders:** Product owner
**Related:** F14, ADR-002, ADR-005, ADR-007, ADR-016, `evals/claim_benchmark/`

## Context

The per-claim prototype makes one LLM call to extract claims, then one per claim to judge it. On Haiku 5.5 over 80 short planted-error articles it caught 96.6% of errors, passed 3.4% of bad articles and rejected 8.3% of clean ones, at US$0.0009 per article. A plain prompt caught 100% and passed 0%, but rejected 64% of clean articles. The preset bar was not met, and the set is too easy to separate methods on catch rate.

We want a cheaper judge before building a harder benchmark. Two "decision models" answer typed questions in one forward pass instead of generating text:

- **Jev** (TypeSafe, closed): [`typesafe/jev-1.13` on OpenRouter](https://openrouter.ai/typesafe/jev-1.13), open to any OpenRouter key. US$0.042/M input, output free, about 80 ms latency, one provider, `usage.cost` on every response. Several questions per request. Text above about 32,000 bytes uses a slower, less accurate mode. As of 2026-11-10 the model page, [tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial) and [API reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request.md) all still use `POST /api/alpha/decisions`, and the reference labels its shape alpha. The request supports `provider.data_collection: deny`. [OpenRouter's own test](https://openrouter.ai/blog/tutorials/jev-vs-llm-as-a-judge/) found it matched an LLM judge on closed, evidence-supplied faithfulness (84/88 vs 83/88) and was weaker on open rubrics over long text. That test is vendor-run and small; its [cascade recipe](https://openrouter.ai/docs/cookbook/evaluate-and-optimize/jev-verified-cascade) starts at confidence 0.8.
- **Laya** (Convai, [Apache-2.0](https://huggingface.co/convaiinnovations/laya)): self-hosted. Multilingual checkpoint 322M parameters, `max_len` to 8,192, accuracy varying beyond about 4,000 tokens. About 33 ms on a T4. Its card says base checkpoints are near chance zero-shot on typed decisions, probabilities need calibration on your own data, and `noul` answers can follow option labels instead of the state. Portuguese is not named. XNLI (English) is 0.860.

Facts that shape the decision:

- **Neither vendor documents claim verification.** Fit is a hypothesis.
- **Neither returns a quote or reasoning.** The evidence quote and code guard are the prototype's differentiator, and the buyer-facing link needs a source passage.
- **Neither can extract claims,** so an LLM stays in the path.
- **Cost is not the binding problem.** At about 2,000 source tokens and 8 claims (estimates, list prices): Haiku judge about US$0.002 per article; Jev batched about US$0.0001, plus about US$0.0003 for Haiku extraction. Saving is about US$0.19 per 100 articles. Buyers objected to US$0.50-2 *writing* costs. The likelier value of a decision model is probabilities for gating, which could reduce false alarms.
- **Laya's economics need batching.** A T4 is about [US$0.59/hour](https://computeprices.com/providers/modal/gpus/t4). A serverless cold start of about 10 seconds costs about US$0.002 per burst, the same as a Haiku judge. A 4 vCPU VPS measured [49 seconds warm](https://flowtivity.ai/blog/laya-open-source-jev-alternative/) in one test. Hosting also conflicts with the no-server BYOK product.

## Options considered

| Option | Complexity | Cost per article (est.) | Fit |
|---|---|---|---|
| A. Keep Haiku judge | None | US$0.002 | Keeps quotes and guards; no probabilities |
| **B. Jev first, Haiku on uncertain or guard failure** (chosen to test) | Medium | US$0.0004-0.001 | Same key, no hosting; alpha API; no quotes unless Haiku runs |
| C. Jev only | Low | US$0.0004 | Loses quotes; unsafe without a lexical evidence step |
| D. Laya, self-hosted | High | ~US$0 to US$0.002, plus hosting and fine-tune labour | Open weights suit self-host tier; needs labeled data we lack |
| E. Laya via third-party gateway | Low | Unverified | [Listing contradicts itself](https://www.llmreference.com/provider/vercel-ai-gateway/laya); do not rely on it |

## Decision

**Adopt Option C (`jev_judge`)** for the claim verification engine add-on.

Across 3 holdout runs (`3b2c8456`, `0a29e8af`, `8c8adb82`) on 278 real articles (SHA `95881bce...`), `jev_judge` was the sole candidate that passed all 9 Worth Key Results (W1–W9):
- **Catch Rate:** 97.3% (threshold $\ge 90\%$)
- **False Pass Rate:** 1.6% (threshold $\le 10\%$)
- **Clean Rejection:** 0.0% (0 false alarms across all clean articles in both languages)
- **Cost:** US$0.00059 per article (84.4% cheaper than `per_claim_v2` US$0.00377, well under the 50% limit)
- **Auto-accept Precision:** 99.6% (threshold $\ge 97\%$)
- **Evidence Audit:** 100.0% correct on 50 sampled claims
- **Bilingual Gap:** 0.3% catch gap, 0.0% clean rejection gap
- **Stability:** Range across 3 runs was 0.3% on catch, 0.0% on false pass, 0.0% on clean rejection.

Decision rule evaluation produced **`GO (jev_judge)`**. Build the verification add-on on `jev_judge` with code-selected evidence passages. **Defer Laya** behind the trigger below.

Cascade:

1. Haiku extracts claims.
2. Jev answers one batched request per source: a `choice` of supported, contradicted or unverifiable per claim, with the source as `state`. Keep each state under the 32,000-byte limit.
3. Code guard: numbers in a "supported" claim must appear in the source.
4. Haiku with quote guard re-judges anything below a confidence threshold or failing the guard. Claims Jev accepts get a source passage selected in code.

Thresholds are fitted on the development set only.

**Skills.** Do not install skills.sh packages into the runtime. They instruct coding agents and assume web tools, while this check uses supplied text only. Borrow concepts and write our own prompt, because licenses are not all stated:

- A separate adversarial pass that treats claims as unsupported until the text backs them ([jwynia/teach](https://www.skills.sh/jwynia/teach/fact-check)).
- Claim types and a "not checkable" class ([seb1n](https://skills.sh/seb1n/awesome-ai-agent-skills/fact-checking)), which targets the advice false alarms.
- Fix our own gap: attributed predictions are checkable. That caused 6 of the 7 misses.

The [AFP-based SkillCheck study](https://www.opentrain.ai/papers/standing-on-the-shoulders-of-giants-enhancing-llm-agents-for-automated-fact-chec--arxiv-2610.25777/) reports gains of 2.2-4.8 accuracy points, mostly at retrieval, a step we do not have. Expect less.

## Consequences

- Easier: per-claim probabilities for gating, near-zero judge cost, no new credentials.
- Harder: two vendors in one path, an alpha-labeled endpoint, and a second response format. TypeSafe's data policy must be reviewed, and `data_collection: deny` confirmed accepted, before customer text is sent.
- Benchmark rules must be frozen again before new runs, since the current 80 articles have now informed design.

## Revisit when

- **Adopt B, or drop Jev:** per the Key Results and decision rule in the [OpenSpec change](../../openspec/changes/2026-11-10-claim-judge-jev-cascade/proposal.md).
- **Replace the endpoint constant** when the Decisions API leaves `/alpha/`.
- **Open a Laya spike** if we have at least 2,000 labeled claims in both languages *and* either self-hosters ask for it or Jev is dropped.

## Action items

Tracked as BM-1 to BM-6 in [the plan](../plans/BM.md), with tasks and verification in the [OpenSpec change](../../openspec/changes/2026-11-10-claim-judge-jev-cascade/tasks.md). None has started.
