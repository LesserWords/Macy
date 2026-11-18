# Delta for spend

## MODIFIED Requirements

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
