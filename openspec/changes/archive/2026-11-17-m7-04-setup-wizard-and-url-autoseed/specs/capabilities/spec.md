# capabilities Delta Specification

## ADDED Requirements

### Requirement: Atomic onboarding bootstrap and init CLI (Path A)
The system SHALL provide `services.bootstrap_onboarding` and `content-engine init` to atomically provision the initial owner account, workspace, `enabled_capabilities`, and integration preset when no workspaces exist, refusing unauthenticated execution once a workspace is present (`ADR-031`, `F22 AC8`).

#### Scenario: Bootstrap onboarding creates owner, workspace, capabilities, and preset atomically
- **WHEN** `services.bootstrap_onboarding` or `content-engine init` runs against an empty database
- **THEN** the owner user, workspace, membership, selected `enabled_capabilities`, and integration preset commit in a single transaction, and subsequent unauthenticated calls are rejected
