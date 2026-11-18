# tooling Specification Delta

## ADDED Requirements

### Requirement: Live E2E test suite and nightly workflow
The repository SHALL provide a live E2E test suite executing journeys J0 through J4 against staging, with a budget guard fixture that aborts the suite if spend exceeds the campaign cap, 1 retry, failure trace and screenshot capture, and a nightly GitHub Actions workflow (`e2e-live.yml`).

#### Scenario: Live E2E suite verifies user journeys J0 through J4
- **WHEN** the live E2E suite runs against an active application instance
- **THEN** it executes authentication (J0), campaign configuration and language toggle (J1), source management (J2), pipeline execution with report generation (J3), and topic approval (J4)

#### Scenario: Budget overrun aborts the test suite
- **WHEN** the cumulative spend during a live test run exceeds the campaign spend cap
- **THEN** the budget guard fixture raises an error and fails the suite immediately

#### Scenario: Forced failure retains trace and screenshot artefacts
- **WHEN** a test in the live E2E suite encounters a failure
- **THEN** Playwright retains trace archives and screenshots in the results directory for diagnostic upload

#### Scenario: Workflow provides nightly cron and manual dispatch
- **WHEN** `.github/workflows/e2e-live.yml` is parsed
- **THEN** it defines a scheduled cron at 03:00 UTC and a `workflow_dispatch` trigger with artifact upload on failure
