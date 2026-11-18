# Proposal

## Why

Plan item **M0-09** (docs/plans/M0.md). Conduct the architectural spike comparing hand-written PostgreSQL state machine orchestration vs DBOS Transact workflows on the same PostgreSQL backend to resolve the decision in ADR-001. Features: F18. ADRs: 001.

## What Changes

- Implement a 2-stage toy pipeline (`research` -> `score`) with two distinct execution engines on PostgreSQL:
  1. **Option A (State Machine)**: PostgreSQL row status updates with `FOR UPDATE SKIP LOCKED`, guarded state transitions, per-item failure isolation, and resume logic.
  2. **Option B (DBOS Workflows)**: DBOS Transact durable workflows (`@DBOS.workflow`, `@DBOS.step`), checkpointing execution steps into PostgreSQL tables.
- Run a unified test suite against both engines verifying:
  - Full end-to-end multi-item execution
  - Failure isolation (failure of one item does not halt or fail other items)
  - Resumption (re-running a blocked/failed run skips already-completed items)
  - Single active run constraint per campaign
- Document comparative metrics in `ADR-001` (lines of code, testing ergonomics, operational complexity, schema transparency, upgrade requirements) and record the final decision.
- Align M1-10 scope with the final architecture choice.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `runs`: Add requirement and scenario for orchestration architecture spike validation (ADR-001).

## Impact

- New: `spikes/orchestration/` (or `engine/spikes/orchestration/`), `tests/test_spike_orchestration.py`.
- Modified: `docs/adr/ADR-001-orchestration.md`, `docs/plans/M1.md`, `openspec/specs/runs/spec.md`.
