## ADDED Requirements

### Requirement: Orchestration architecture validation (ADR-001 spike)
The system SHALL evaluate and confirm the pipeline orchestration architecture using a comparative spike testing failure isolation, resume capability, and active run constraints against the same PostgreSQL database.

#### Scenario: Comparative spike across state machine and durable workflow
- **WHEN** the 2-stage toy pipeline is executed under failure, resume, and concurrency conditions
- **THEN** both implementations demonstrate failure isolation and idempotent resume, and the evaluation matrix confirms the production orchestrator
