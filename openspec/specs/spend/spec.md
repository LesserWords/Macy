# spend Specification

## Purpose
Enforces per-run and monthly spend caps and provides exact cost accounting across LLM and API calls.

## Requirements

### Requirement: Budget guard enforcement and usage ledger recording
The system SHALL record exact monetary costs in the usage ledger, check budget limits before each paid call and at stage boundaries, transition runs to `blocked(cap_reached)` when the per-run cap is exceeded, refuse new runs when monthly spend cap is reached, and permit resuming runs once caps are raised.

#### Scenario: Call exceeding run cap blocks pipeline execution
- **WHEN** an estimated call cost would cause the run spend to exceed run_cap_usd
- **THEN** the call is blocked, run status changes to blocked(cap_reached), and budget.cap_reached event is emitted

#### Scenario: Accurate monetary accounting in usage ledger
- **WHEN** any paid API or LLM call completes
- **THEN** cost is recorded in usage_ledger with numeric exactness and token counts

#### Scenario: Monthly spend cap reached refuses new runs
- **WHEN** total spend in the current month for a workspace meets or exceeds monthly_cap_usd
- **THEN** triggering a new scheduled or manual run is refused with a clear cap-reached message

#### Scenario: Raising cap on blocked run enables resume
- **WHEN** a run blocked on cap_reached has its run_cap_usd raised
- **THEN** resuming the run unblocks it and execution continues from its current stage

### Requirement: LLM request and response payload persistence
The system SHALL persist debug request and response JSON payloads for LLM calls linked to usage ledger entries.

#### Scenario: LLM payload stored alongside ledger entry
- **WHEN** a structured LLM call completes
- **THEN** its request and response bodies are stored in llm_payloads referenced by usage_id

### Requirement: Cost summary and per-article spend visibility
The system SHALL provide `services.cost_summary` and an Admin UI cost view displaying current-month spend against `monthly_cap_usd`, per-run costs against `run_cap_usd`, and cost per published article compared against the `$5.00` pilot target (`F17 AC5, AC6`, `ADR-016`, `ADR-023`).

#### Scenario: Cost summary computes exact spend against caps, per-run cost, and per-published-article cost from usage ledger
- **WHEN** `services.cost_summary` is called for a workspace with literal `usage_ledger` entries across runs and published articles
- **THEN** it returns exact `Decimal` totals for `monthly_spend_usd`, `monthly_cap_usd`, per-run ledger costs, and per-published-article costs, and flags whether each published article is under the `$5.00` pilot target

#### Scenario: Admin UI renders cost view in pt-BR and en-US
- **WHEN** an authenticated workspace owner opens the Dashboard or Runs & Orchestration screen
- **THEN** spend-to-date against the caps, cost per run, and cost per published article (with the `< $5.00` pilot target) are displayed in the active UI locale

### Requirement: MCP generative tool spend preflight and ledger metering
The system SHALL invoke `services.preflight_mcp_spend` before any LLM or paid keyword/search operation triggered via MCP, meter exact USD spend to `usage_ledger`, and return structured MCP error `BUDGET_CAP_EXCEEDED` on cap breach without calling upstream vendors (`F17`, `F21 AC7`, `ADR-016`, `ADR-027`).

#### Scenario: Budget cap breach halts MCP tool before paid vendor calls
- **WHEN** a generative MCP tool (`macy_score_topics`, `macy_get_editorial_brief`, `macy_generate_draft`, `macy_verify_claims`, or `macy_evaluate_draft`) is invoked after reaching the workspace monthly or per-run spend cap
- **THEN** `services.preflight_mcp_spend` aborts prior to upstream LLM/API execution and returns `BUDGET_CAP_EXCEEDED`

#### Scenario: Completed generative MCP call records exact usage cost
- **WHEN** a generative MCP tool completes an LLM or keyword search operation within budget
- **THEN** `usage_ledger` records the exact `Decimal` USD spend and token counts scoped to `workspace_id` and `run_id`
