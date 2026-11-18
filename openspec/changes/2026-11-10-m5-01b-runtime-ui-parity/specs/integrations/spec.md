# integrations Delta Specification

## MODIFIED Requirements

### Requirement: Plugin settings and encrypted secrets management
The system SHALL configure plugins per port, render forms from the plugin's `Settings` JSON Schema (`SecretStr` as password inputs), validate settings and URLs (`safe_http`), test live connectivity (`healthcheck()`, rejecting empty or placeholder credentials), and Fernet-encrypt secrets without exposing raw secret values in UI, logs, services, or events.

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

#### Scenario: Placeholder credentials fail healthcheck before external calls
- **WHEN** an adapter is tested with empty credentials or placeholder strings (`test-openrouter-key`, `test-brave-key`, or `CHANGE_ME`)
- **THEN** `healthcheck()` raises a validation error naming the missing or placeholder credential and records `last_test_ok = False`

### Requirement: OpenRouter LLM gateway plugin
The system SHALL provide an OpenRouter LLM plugin implementing `LLMPort` with per-stage model selection reflected in `usage_ledger.model`, without runtime `"test-*"` key defaults, verifying credentials via authenticated `GET /key` in `healthcheck()`.

#### Scenario: OpenRouter passes LLM contract suite with recorded HTTP
- **WHEN** contract test suite executes against OpenRouter LLM adapter configured with a test credential under VCR playback
- **THEN** `healthcheck()` verifies key status and structured completion returns valid result dictionary

#### Scenario: Changing LLM model for a stage updates usage ledger model
- **WHEN** the LLM model for a stage is changed in workspace integration settings
- **THEN** the next stage execution uses the configured stage model and records it in `usage_ledger.model`

#### Scenario: OpenRouter rejects unconfigured or invalid keys on healthcheck
- **WHEN** `OpenRouterLLM.healthcheck()` executes without a configured API key or receives HTTP 401 from `/api/v1/key`
- **THEN** `healthcheck()` raises an error indicating unconfigured or unauthorized credentials without reporting healthy

## ADDED Requirements

### Requirement: Credentialed adapter healthcheck and Compose env forwarding parity
The system SHALL remove `"test-*"` runtime defaults from `BraveNewsSearch`, reject empty or placeholder credentials and perform authenticated `safe_request` probes in `BraveNewsSearch.healthcheck()` and `DataForSEOKeywordProvider.healthcheck()`, and forward `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` in `compose.prod.yaml` and `.env.{staging,prod}.example`.

#### Scenario: Brave and DataForSEO verify credentials cleanly in healthcheck
- **WHEN** `BraveNewsSearch.healthcheck()` or `DataForSEOKeywordProvider.healthcheck()` executes
- **THEN** empty or placeholder credentials fail immediately, valid credentials verify remote API status via `safe_request` (minimal search or `GET /v3/appendix/user_data`), and unauthorized responses fail healthcheck

#### Scenario: Compose and env templates forward DataForSEO fallback variables
- **WHEN** `compose.prod.yaml`, `.env.staging.example`, and `.env.prod.example` are inspected alongside `OPENROUTER_API_KEY` and `BRAVE_API_KEY`
- **THEN** `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` are defined in both `.env` examples and forwarded in `compose.prod.yaml` `x-app-env`
