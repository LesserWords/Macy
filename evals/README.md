# Evaluations

Two kinds of evaluation live here:

- `evals/test_regression.py` and `evals/cases/`: the original pipeline regression suite (`pytest -m eval`).
- `evals/claim_benchmark/` and `evals/public_suite/`: the claim-judge benchmark behind [ADR-026](../docs/adr/ADR-026-claim-judge-decision-models.md) and [the BM plan](../docs/plans/BM.md).

## Claim-judge datasets

| Dataset | What it covers | Size | Where | Counts toward key results |
|---|---|---|---|---|
| `dev` | Planted errors in invented general news, en and pt | 80 articles | `claim_benchmark/cases.yaml` (committed) | No; used for tuning |
| `holdout` (D4) | Real articles: optical retail and POS (`en-07`–`en-17`), Brazilian business, retail and tax (`pt-06`–`pt-16`), and two-error articles | 278 articles from 30 sources | `claim_benchmark/holdout/` (git-ignored) | Yes, once, after sign-off |
| `public-a` | English grounded factuality: [LLM-AggreFact](https://huggingface.co/datasets/lytang/LLM-AggreFact) test, RAGTruth and Reveal | 150 claims | `artifacts/public_suite/` (git-ignored) | No; regression only |
| `public-b` | Portuguese entailment: [ASSIN 2](https://huggingface.co/datasets/nilc-nlp/assin2) test, entailment = supported, none = unsupported | 150 pairs | `artifacts/public_suite/` (git-ignored) | No; regression only |

Every dataset is checked against a hash before a run: `dev` and `holdout` against `claim_benchmark/rules.py`, public tiers against `public_suite/manifest.json`. Public samples are drawn with seed `20261111`, balanced across labels (and across subsets for Tier A); only their ids and SHA-256 are committed.

**Licenses.** LLM-AggreFact is CC BY-ND 4.0 and gated: accept its terms on Hugging Face and set `HF_TOKEN`. ASSIN 2's license is listed as unknown on Hugging Face; confirm before using results commercially. Holdout sources are U.S. federal works (public domain), Agência Brasil (CC BY 4.0) and Wikipedia (CC BY-SA 4.0). No dataset text is committed. Yale's LEME eye-health set was considered and dropped: it has no claim labels and a non-commercial license.

**Not included:** an eye-care tier. Optical terminology is covered by the holdout's optical articles.

## Running

```bash
uv run python -m evals.public_suite.loader --tier b          # cache Tier B
HF_TOKEN=... uv run python -m evals.public_suite.loader --tier a
OPENROUTER_API_KEY=... uv run python -m evals.claim_benchmark.harness --dataset public-b
OPENROUTER_API_KEY=... uv run python -m evals.claim_benchmark.harness --dataset dev
```

`--methods` defaults to `jev_judge` (the accepted benchmark winner from BM-6).

Reports go to `artifacts/claim_benchmark/<run>.json` with the git SHA, dataset hash, resolved model ids and measured cost. Offline tests run in CI with every PR (`pytest evals/`). The live public suite runs weekly and on demand in `.github/workflows/public-suite.yml`, which needs the `OPENROUTER_API_KEY` and, for Tier A, `HF_TOKEN` repository secrets. Each live run costs money; regression thresholds should be set after the first few weekly baselines.

## Holdout benchmark results (BM-6)

The definitive BM-6 holdout evaluation on 278 real articles across 3 runs (`3b2c8456`, `0a29e8af`, `8c8adb82`) resulted in **GO (`jev_judge`)**:

| Metric | `jev_judge` (Winner, Default) | `jev_cascade` | `per_claim_v2` | `plain_tuned` |
|---|---|---|---|---|
| Catch rate | **97.3%** | 98.6% | 99.1% | 99.7% |
| False pass rate | **1.6%** | 0.9% | 0.5% | 0.4% |
| Clean rejection | **0.0%** | 3.3% | 15.6% | 16.7% |
| False alarm rate | **0.0%** | 0.3% | 2.1% | 1.8% |
| Cost / article | **$0.00059** | $0.00114 | $0.00377 | $0.00075 |
| Jev precision | **99.6%** | 99.9% | N/A | N/A |
| Outcome | **GO (all W1-W9 pass)** | Fail W8 (locale gap) | Fail reference | Fail W1, W3 |

Decision tool:
```bash
uv run python -m evals.claim_benchmark.evaluate_kr artifacts/claim_benchmark/3b2c8456.json artifacts/claim_benchmark/0a29e8af.json artifacts/claim_benchmark/8c8adb82.json
```

## Metrics in every report

Overall and per locale:

| Metric | Meaning |
|---|---|
| `catch_rate` | Planted errors caught, counted per error |
| `false_pass_rate` | Articles with an error that were passed |
| `clean_reject_rate` | Clean articles rejected |
| `false_alarm_rate` | Correct sentences flagged |
| `tpr_single`, `tpr_multi` | Articles detected, split into one-error and two-error articles |
| `tnr_clean_articles`, `tnr_clean_sentences` | Clean articles passed; correct sentences left alone |
| `rogan_gladen` | θ = (p̂ + TNR − 1) / (TPR + TNR − 1), clipped to [0, 1]; null when TPR + TNR ≤ 1 |

p̂ is the share of articles a method flags, and TPR and TNR are article-level. On a labeled set, θ simply recovers the true share of erroneous articles. Its use is in production: apply a method's TPR and TNR from a benchmark to the flag rate on unlabeled customer drafts to estimate how many drafts really contain errors.

Jev methods also report escalation rate, the precision of claims Jev accepted, and a reliability table.

## Holdout process

1. `holdout/build_holdout.py` builds `cases.yaml`, `REVIEW.md` and `CHANGES.md` from drafts and sources.
2. `holdout/audit_holdout.py` checks every clean sentence and planted error against the full source with Sonnet 5.5, a model that is not under test, and keeps only quotes found verbatim in the source.
3. A person signs off on every label in `REVIEW.md`.
4. The holdout hash is set in `rules.py` and committed before its single run.
