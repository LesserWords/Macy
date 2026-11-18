# spend Delta Specification

## ADDED Requirements

### Requirement: MCP generative tool spend preflight and ledger metering
The system SHALL invoke `services.preflight_mcp_spend` before any LLM or paid keyword/search operation triggered via MCP, meter exact USD spend to `usage_ledger`, and return structured MCP error `BUDGET_CAP_EXCEEDED` on cap breach without calling upstream vendors (`F17`, `F21 AC7`, `ADR-016`, `ADR-027`).

#### Scenario: Budget cap breach halts MCP tool before paid vendor calls
- **WHEN** a generative MCP tool (`macy_score_topics`, `macy_get_editorial_brief`, `macy_generate_draft`, `macy_verify_claims`, or `macy_evaluate_draft`) is invoked after reaching the workspace monthly or per-run spend cap
- **THEN** `services.preflight_mcp_spend` aborts prior to upstream LLM/API execution and returns `BUDGET_CAP_EXCEEDED`

#### Scenario: Completed generative MCP call records exact usage cost
- **WHEN** a generative MCP tool completes an LLM or keyword search operation within budget
- **THEN** `usage_ledger` records the exact `Decimal` USD spend and token counts scoped to `workspace_id` and `run_id`
