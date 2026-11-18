# Proposal: Public Benchmark Suite, Detection Metrics and Holdout Audit

**Plan Item:** BM-5 support ([docs/plans/BM.md](../../../docs/plans/BM.md)) · **Features:** F14 · **ADRs:** ADR-026

## Why

The private holdout (D4) covers optical retail, Brazilian business topics and multi-error articles, but it is small and hand-built. Public, already-labeled sets give a repeatable regression check that needs no manual review, before the single-use holdout is run. Reports also lack per-article detection rates split by single and multiple errors, and an estimate that corrects observed flag rates for the judge's error rates. Finally, the holdout needs an audit before sign-off: planted errors may be supported deeper in the full source, clean sentences need their exact supporting passage, and the closing advice sentences should be factual so the holdout measures faithfulness only.

## What Changes

- **Holdout audit (before freeze):** a model that is not a candidate (Sonnet 5.5) checks every planted error and clean sentence against the full source and must quote a verbatim passage, verified in code. Flagged items are corrected by hand, and closing advice sentences are replaced with sourced facts. The review sheet shows exact passages, and a change table lists every modified line for human sign-off. The audit model must not be one of the methods under test, so the clean-up cannot remove exactly the items those methods get wrong.
- **Public suite (`evals/public_suite/`):** fixed-seed, label-stratified samples cached under `artifacts/` and never committed, with sample ids and SHA-256 committed in a manifest:
  - Tier A: 150 claims from `lytang/LLM-AggreFact` test, subsets RAGTruth and Reveal (gated, CC BY-ND 4.0, needs `HF_TOKEN`).
  - Tier B: 150 pairs from `nilc-nlp/assin2` test; entailment means supported, none means unsupported.
  - Tier C (Yale LEME) is dropped: no claim labels, and a non-commercial license.
- Each sample becomes a one-sentence article, so every existing method runs unchanged.
- **Metrics in every report:** article-level TPR for single-error and multi-error articles, TNR for clean articles and clean sentences, and the Rogan-Gladen estimate θ = (p̂ + TNR − 1) / (TPR + TNR − 1).
- **Automation:** offline tests in CI; a manual and weekly GitHub workflow runs the public suite and uploads reports.

## Out of Scope

Frozen key-result thresholds, the decision rule, and holdout scoring are unchanged. No public-suite result counts toward a key result.

## Impact

`evals/claim_benchmark/harness.py`, new `evals/public_suite/`, `evals/README.md`, `.github/workflows/public-suite.yml`. Audit spend about US$0.70 from the US$5 BM budget; public-suite runs are budgeted separately.
