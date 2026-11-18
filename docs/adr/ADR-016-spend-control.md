# ADR-016: Spend control — user-set caps, enforced from a usage ledger

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F17, F18, ADR-007 · DATA-MODEL `usage_ledger`

## Context
Costs grow with the articles/week setting and the models the user picks. You decided the limits are set by the user. Paid APIs (DataForSEO, Brave) count toward the limits as well as the LLM.

## Options considered
| Option | Fit |
|---|---|
| **A. Required monthly cap and per-run cap per campaign; every billable call written to `usage_ledger`; guard checked before each call and at stage boundaries** | Hard limit, auditable, per customer |
| B. Rely on provider-side limits only | Coarse, not per campaign, and not visible in the app |

## Decision
A.
- **Settings:** `campaigns.monthly_cap_usd` and `run_cap_usd` are required, with no default; the run cap must be ≤ the monthly cap.
- **Ledger:** LLM cost comes from OpenRouter's `usage.cost`. API cost comes from each plugin's per-unit price (a `Settings` field, so the user can edit it).
- **Per-run cap reached:** the run moves to `blocked(cap_reached)` and can be resumed; a `budget.cap_reached` event is sent.
- **Monthly cap reached:** new runs are refused.
- **UI:** shows spend-to-date against both caps, and an estimate for the chosen volume.
- **Staging:** live E2E uses the same mechanism.

## Consequences
- Easier: there are no surprise bills, and cost per article is known.
- Harder: a cap that is set too low blocks runs. The UI shows the estimate before saving.

## Revisit when
Billing is introduced. The ledger is already the metering source.

## Action items
- [x] M0-07: ledger writes from the LLM helper
- [x] M1-10: budget guard + caps UI
- [x] M4-07: cost view per article
