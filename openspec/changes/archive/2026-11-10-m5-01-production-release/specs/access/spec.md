# access Specification Delta (M5-01)

## ADDED Requirements

### Requirement: CLI workspace provisioning
The system SHALL provide `services.create_workspace` and `content-engine create-workspace <slug>` so operators can provision a production or staging workspace with its industry pack and timezone without executing raw SQL.

#### Scenario: Create workspace CLI provisions workspace idempotently or rejects duplicates cleanly
- **WHEN** `content-engine create-workspace acme-demo --name "Acme Demo" --pack general` runs
- **THEN** the workspace row exists with slug `acme-demo` and the configured industry pack and timezone
