# integrations Specification

## Purpose
Manages third-party plugin configurations, credentials encryption, and port bindings per workspace.

## Requirements

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

### Requirement: Port protocols and entry-point plugin discovery
The system SHALL define 7 typing.Protocol ports and discover plugins dynamically at runtime through standard Python entry points.

#### Scenario: Discover registered port plugins
- **WHEN** plugin registry discovers plugins for a port
- **THEN** registered plugins matching the port protocol are returned

#### Scenario: Fakes pass contract suites
- **WHEN** contract test suites execute against in-memory fake adapters
- **THEN** all contract assertions pass for each of the 7 ports

#### Scenario: MultiFernet key rotation preserves secret readability
- **WHEN** an encryption key rotation is executed with rotate-key
- **THEN** secrets are re-encrypted under the new primary key and remain decryptable

### Requirement: OpenRouter LLM gateway plugin
The system SHALL provide an OpenRouter LLM plugin implementing the LLMPort protocol and supporting per-stage model selection reflected in `usage_ledger.model`.

#### Scenario: OpenRouter passes LLM contract suite with recorded HTTP
- **WHEN** contract test suite executes against OpenRouter LLM adapter
- **THEN** healthcheck succeeds and structured completion returns valid result dictionary

#### Scenario: Changing LLM model for a stage updates usage ledger model
- **WHEN** the LLM model for a stage is changed in workspace integration settings
- **THEN** the next stage execution uses the configured stage model and records it in `usage_ledger.model`

### Requirement: Research source trials and plugin evaluation
The system SHALL evaluate candidate research source technologies against quantitative keep/drop criteria, recording decisions and evidence in architecture records and tech radar.

#### Scenario: RSSHub route evaluation for site_search sources
- **WHEN** RSSHub routes are evaluated against the 13 seed `site_search` sources in the optical retail industry pack
- **THEN** if fewer than 5 sources yield dated items, the `rsshub` profile is removed from Docker Compose and the drop decision is recorded in TECH-RADAR and ADR-003

#### Scenario: GDELT DOC 2.0 NewsSearch plugin evaluation with rate limiting against Brave
- **WHEN** the GDELT DOC 2.0 plugin is evaluated with a 1 request per 5 seconds rate limit over a 14-day window against Brave Search News for `pt-BR`
- **THEN** if unique relevant optical industry stories are below 20%, the plugin is dropped from production entry points and the empirical decision is recorded in TECH-RADAR and ADR-003

### Requirement: Docling PDF extractor trial evaluation
The system SHALL evaluate a candidate `docling` `ExtractorPort` plugin scoped exclusively to `application/pdf` documents in an optional Compose image/profile against 3 seed-source PDF fixtures, live open-PDF availability across `data` sources, monthly PDF brief volume (`>= 3 briefs/month`), and the single-VPS resource ceiling (`ADR-012`).

#### Scenario: Docling PDF Extractor evaluation across 3 seed data PDF fixtures and VPS criteria
- **WHEN** the `docling` `ExtractorPort` plugin is tested on 3 statistical PDF fixtures (IBGE, The Vision Council, public Euromonitor summary) and evaluated against live seed source PDF accessibility and the `2 vCPU / 4 GB RAM / 40 GB disk` VPS budget
- **THEN** if fewer than 3 seed `data` sources publish un-gated statistical PDFs, `< 3` briefs/month require PDF extraction, or the container footprint compromises the shared VPS memory budget, the `docling` Compose profile and production entry point are removed/omitted while the trial plugin, fixtures, comparator, and drop rationale are preserved in `spikes/trials/` and recorded in `TECH-RADAR.md` and `ADR-003`

### Requirement: OpenRouter structured calls use JSON-schema mode with a visible fallback
The OpenRouter LLM plugin SHALL request structured output through JSON-schema `response_format` rather than a forced tool call, and `structured_call` SHALL log a warning and include the response schema whenever it falls back to JSON mode.

#### Scenario: Structured completion requests JSON-schema output
- **WHEN** the OpenRouter adapter executes a structured completion
- **THEN** the request carries `response_format` of type `json_schema` and no `tool_choice`

#### Scenario: Fallback is visible and carries the schema
- **WHEN** the Instructor call fails and `structured_call` falls back to JSON mode
- **THEN** a warning is logged, the fallback request includes the response JSON Schema, and a validated result is returned

### Requirement: Universal OpenAI-compatible and local Ollama LLM plugins
The system SHALL provide `openai_compat` and `ollama` plugins implementing `LLMPort` registered under `macy.llm`, supporting structured `instructor` outputs, per-stage model selection, SSRF validation on custom base URLs, and `$0.000000` cost tracking for local Ollama runs (`ADR-031`, `F22 AC7`).

#### Scenario: Local Ollama LLM completes structured prompt with zero USD spend
- **WHEN** `ollama` executes `complete(prompt, schema=...)` against a configured local Ollama endpoint
- **THEN** it returns a validated structured response matching `schema` and records `$0.000000` cost in `usage_ledger`

### Requirement: One-click integration stack presets
The system SHALL provide `services.apply_integration_preset` supporting `pocket_free`, `local_ollama`, and `pro_cloud` presets to configure workspace port bindings atomically (`ADR-031`, `F22 AC7`).

#### Scenario: Applying pocket_free preset binds zero-key plugins across workspace ports
- **WHEN** a workspace owner applies the `pocket_free` preset via `services.apply_integration_preset`
- **THEN** `news_search` (`gnews_rss`), `keyword_provider` (`autocomplete`), `feed_source` (`rss`), `extractor` (`trafilatura`), and `publisher` (`markdown_export`) are persisted for the workspace and `integration.preset_applied` is logged
