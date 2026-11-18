# integrations Delta Specification

## ADDED Requirements

### Requirement: Universal OpenAI-compatible and local Ollama LLM plugins
The system SHALL provide `openai_compat` and `ollama` plugins implementing `LLMPort` registered under `content_engine.llm`, supporting structured `instructor` outputs, per-stage model selection, SSRF validation on custom base URLs, and `$0.000000` cost tracking for local Ollama runs (`ADR-031`, `F22 AC7`).

#### Scenario: Local Ollama LLM completes structured prompt with zero USD spend
- **WHEN** `ollama` executes `complete(prompt, schema=...)` against a configured local Ollama endpoint
- **THEN** it returns a validated structured response matching `schema` and records `$0.000000` cost in `usage_ledger`

### Requirement: One-click integration stack presets
The system SHALL provide `services.apply_integration_preset` supporting `pocket_free`, `local_ollama`, and `pro_cloud` presets to configure workspace port bindings atomically (`ADR-031`, `F22 AC7`).

#### Scenario: Applying pocket_free preset binds zero-key plugins across workspace ports
- **WHEN** a workspace owner applies the `pocket_free` preset via `services.apply_integration_preset`
- **THEN** `news_search` (`gnews_rss`), `keyword_provider` (`autocomplete`), `feed_source` (`rss`), `extractor` (`trafilatura`), and `publisher` (`markdown_export`) are persisted for the workspace and `integration.preset_applied` is logged
