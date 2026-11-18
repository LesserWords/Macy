# admin-ui Specification Delta (M4-07)

## ADDED Requirements

### Requirement: End-to-end live journey coverage J0–J8 with budget guard
The system SHALL provide a Playwright live E2E suite covering journeys `J0` through `J8` (`J0` auth/isolation, `J1` campaign settings/i18n, `J2` sources, `J3` research/scoring report, `J4` Gate 1 topic approval & brief, `J5` draft & quality evaluation with `lingua` check, `J6` Gate 2 approval & draft-mode publishing, `J7` topic and draft rejection paths, `J8` integrations, Standard Webhooks delivery, and SSRF private-IP refusal) enforced by a per-run budget cap guard (`ADR-018`, `PRD §9.6`).

#### Scenario: Live E2E suite executes J0–J8 within the campaign spend cap
- **WHEN** the live E2E suite runs against the staging/Compose stack
- **THEN** journeys `J0` through `J8` pass and `BudgetGuard` verifies cumulative spend does not exceed the campaign cap
