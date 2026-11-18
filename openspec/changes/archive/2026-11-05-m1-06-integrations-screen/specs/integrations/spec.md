## MODIFIED Requirements

### Requirement: Plugin settings and encrypted secrets management
The system SHALL configure plugins per port, render forms from the plugin's `Settings` JSON Schema (`SecretStr` as password inputs), validate settings and URLs (`safe_http`), test connectivity (`healthcheck()`), and store secrets using Fernet encryption without ever exposing secret values in UI, logs, services, or events.

#### Scenario: Render form from plugin Settings JSON Schema
- **WHEN** the owner selects a plugin for a port
- **THEN** field specifications are derived from its `Settings` JSON Schema with `SecretStr` (`format: password`) mapped to password inputs and `models_by_stage` mapped to per-stage model selectors

#### Scenario: Secrets are encrypted and never exposed
- **WHEN** integration credentials are saved
- **THEN** non-secret fields are stored in `settings`, secrets are Fernet-encrypted in `secrets`, and neither UI state, logs, `services` return values, nor `events` payloads ever contain raw secret values

#### Scenario: Test connection runs healthcheck and stores status
- **WHEN** Test connection is triggered for a configured port
- **THEN** the plugin's `healthcheck()` executes and `last_test_ok` and `last_tested_at` are persisted in `workspace_integrations`

#### Scenario: Missing required integration blocks pipeline run
- **WHEN** a pipeline stage requires an unconfigured port
- **THEN** the run becomes blocked with an integration missing status naming the port and an audit event is emitted

#### Scenario: Refusing private or metadata URLs in integration settings
- **WHEN** a URL setting in an integration points to a private, loopback, link-local, or cloud metadata address
- **THEN** saving or testing the integration is refused by `safe_http`

### Requirement: OpenRouter LLM gateway plugin
The system SHALL provide an OpenRouter LLM plugin implementing the LLMPort protocol and supporting per-stage model selection reflected in `usage_ledger.model`.

#### Scenario: OpenRouter passes LLM contract suite with recorded HTTP
- **WHEN** contract test suite executes against OpenRouter LLM adapter
- **THEN** healthcheck succeeds and structured completion returns valid result dictionary

#### Scenario: Changing LLM model for a stage updates usage ledger model
- **WHEN** the LLM model for a stage is changed in workspace integration settings
- **THEN** the next stage execution uses the configured stage model and records it in `usage_ledger.model`
