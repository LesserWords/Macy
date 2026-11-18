# Proposal: Add Jev-only Judge as an Outcome Candidate

**Plan Item:** BM-5 prerequisite ([docs/plans/BM.md](../../../docs/plans/BM.md)) · **Features:** F14 · **ADRs:** ADR-026 · **Amends:** [2026-11-10-claim-judge-jev-cascade](../2026-11-10-claim-judge-jev-cascade/proposal.md)

## Why

The frozen decision rule lets only `jev_cascade` produce GO. On the development set (3 runs at `56ad755`), `jev_judge`, where Jev alone judges every extracted claim, matched or beat every method: catch 100%, false pass 0%, clean rejection 0%, US$0.00045 per article, against the cascade's 8.3% clean rejection and US$0.00059. If the holdout confirms this, the simplest and cheapest design could not be chosen. No holdout result has been seen, so the rule can still change without bias.

## What Changes

The decision rule in the amended proposal is replaced by:

- **Candidates:** `jev_judge` and `jev_cascade`, each measured as the mean of 3 holdout runs.
- **A candidate passes** when W1 to W9 all pass for it. W4 and W5 compare it with `per_claim_v2`. W6 is the precision of the claims it accepted: for `jev_judge`, every claim it labeled supported; for `jev_cascade`, every claim Jev accepted without escalation.
- **GO:** at least one candidate passes. Build on the passing candidate with the lower measured cost per article; on a tie, `jev_judge`, because it has fewer parts.
- **GO WITHOUT JEV:** no candidate passes, and `per_claim_v2` passes W1, W2, W3, W7, W8 and W9. Ship Haiku-only.
- **STOP:** no method passes W1 to W3.

Thresholds, the holdout requirements, budget and every other key result are unchanged.

## Risk

Two candidates instead of one slightly raise the chance that one passes by luck. W9 (stability across 3 runs) and the single-use holdout limit this. This change is disclosed in the final report.

## Capabilities

### Modified Capabilities

- `claim-benchmark`: outcome candidates.

## Out of Scope

Any threshold, method, prompt or dataset change. `per_claim_v2` remains the cost and quality reference.

## Impact

`evals/claim_benchmark/rules.py` gains `JEV_CANDIDATES`; ADR-026 and `docs/plans/BM.md` updated. The BM-1 freeze is superseded by this change's commit as the ancestor every holdout run must descend from.
