# spend Specification Delta (M4-07)

## ADDED Requirements

### Requirement: Cost summary and per-article spend visibility
The system SHALL provide `services.cost_summary` and an Admin UI cost view displaying current-month spend against `monthly_cap_usd`, per-run costs against `run_cap_usd`, and cost per published article compared against the `$5.00` pilot target (`F17 AC5, AC6`, `ADR-016`, `ADR-023`).

#### Scenario: Cost summary computes exact spend against caps, per-run cost, and per-published-article cost from usage ledger
- **WHEN** `services.cost_summary` is called for a workspace with literal `usage_ledger` entries across runs and published articles
- **THEN** it returns exact `Decimal` totals for `monthly_spend_usd`, `monthly_cap_usd`, per-run ledger costs, and per-published-article costs, and flags whether each published article is under the `$5.00` pilot target

#### Scenario: Admin UI renders cost view in pt-BR and en-US
- **WHEN** an authenticated workspace owner opens the Dashboard or Runs & Orchestration screen
- **THEN** spend-to-date against the caps, cost per run, and cost per published article (with the `< $5.00` pilot target) are displayed in the active UI locale
