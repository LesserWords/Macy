# F17 — Spend control and cost visibility

**Status:** Implemented · **Milestone:** M0 (ledger), M1 (guard), M4 (cost view) · **Plan items:** M0-07, M1-10, M4-07 · **ADRs:** 016, 023 · **OpenSpec capability:** `spend`

## User story
As an owner, I set monthly and per-run spending limits, and I can see what each run and each article cost, so I never get a surprise bill.

## Scope
- In:
  - `usage_ledger` writes for every LLM and paid API call;
  - budget guard before each call and at each stage boundary;
  - caps UI (in F02);
  - spend-to-date view;
  - cost per run and per article;
  - `budget.cap_reached` event.
- Out: billing, invoices.

## Acceptance criteria
- **AC1** WHEN any LLM or paid API call completes, THEN a `usage_ledger` row is written with vendor, model or endpoint, units or tokens, `cost_usd` (from `usage.cost` for OpenRouter, from the plugin's unit price otherwise), stage and `run_id`.
- **AC2** WHEN the next call would push the run's cost over `run_cap_usd`, THEN it is not made: the run becomes `blocked(cap_reached)` and a `budget.cap_reached` event is emitted.
- **AC3** WHEN the month's spend for the workspace reaches `monthly_cap_usd`, THEN new runs (scheduled or manual) are refused with a clear message.
- **AC4** WHEN a blocked run's cap is raised, THEN the run can be resumed from its current stage.
- **AC5** WHEN the owner opens the cost view, THEN spend-to-date against the caps, cost per run, and cost per published article are shown.
- **AC6** Pilot target: the cost per published article is under $5 and visible.

## Data
`usage_ledger`, `runs.cost_usd`, `campaigns` caps

## Interfaces
- `budget.check(run, estimate)`
- `budget.record(...)`
- `services.cost_summary()`

## Tests
| Seam | Test | E2E |
|---|---|---|
| core | Guard boundaries (Hypothesis on amounts; numeric exactness) | — |
| pipeline | Fake LLM with a fixed cost → blocked at the cap; resume after the cap is raised | — |
| UI | Caps set in J1; live suite aborts above its cap | J1, all live journeys |

## Dependencies
F02, M0-07
