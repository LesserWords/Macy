## MODIFIED Requirements

### Requirement: Budget guard enforcement and usage ledger recording
The system SHALL record exact monetary costs in the usage ledger and halt runs exceeding configured spend caps.

#### Scenario: Call exceeding run cap blocks pipeline execution
- **WHEN** an estimated call cost would cause the run spend to exceed run_cap_usd
- **THEN** the call is blocked, run status changes to blocked(cap_reached), and budget.cap_reached event is emitted

#### Scenario: Accurate monetary accounting in usage ledger
- **WHEN** any paid API or LLM call completes
- **THEN** cost is recorded in usage_ledger with numeric exactness and token counts

## ADDED Requirements

### Requirement: LLM request and response payload persistence
The system SHALL persist debug request and response JSON payloads for LLM calls linked to usage ledger entries.

#### Scenario: LLM payload stored alongside ledger entry
- **WHEN** a structured LLM call completes
- **THEN** its request and response bodies are stored in llm_payloads referenced by usage_id
