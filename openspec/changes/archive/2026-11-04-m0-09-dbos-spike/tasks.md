## 1. Implement 2-stage toy pipeline spike for both approaches

- [x] 1.1 Implement Option A (PostgreSQL state machine with `FOR UPDATE SKIP LOCKED`, row statuses, failure isolation, resume) → verify: unit tests pass
- [x] 1.2 Implement Option B (DBOS Transact durable workflow with step isolation and resume) → verify: unit tests pass
- [x] 1.3 Implement unified test suite in `tests/test_spike_orchestration.py` validating failure isolation, resume, and active run constraint across both → verify: all tests pass

## 2. Compare metrics and resolve ADR-001

- [x] 2.1 Calculate lines of code, test ergonomics, operational footprint (system tables, migrations), and failure transparency
- [x] 2.2 Record evaluation table and update status to confirmed decision in `docs/adr/ADR-001-orchestration.md` and verify alignment in `docs/plans/M1.md` → verify: ADR-001 is finalized
