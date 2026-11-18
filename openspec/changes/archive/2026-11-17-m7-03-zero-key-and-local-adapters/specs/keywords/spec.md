# keywords Delta Specification

## ADDED Requirements

### Requirement: Zero-key autocomplete keyword estimator plugin
The system SHALL provide an `autocomplete` plugin implementing `KeywordProviderPort` registered under `content_engine.keyword_provider`, retrieving autocomplete keyword suggestions via `safe_http`, respecting the 30-day `keyword_cache` and `UnsupportedLocale`, and marking all returned `KeywordData` rows with `is_estimate=True` (`ADR-031`, `F22 AC7`).

#### Scenario: Autocomplete provider marks unbacked keyword suggestions as estimates
- **WHEN** `autocomplete` looks up terms for `pt-BR`/`BR` without paid keyword volume metrics
- **THEN** it returns normalized `KeywordData` suggestions with `is_estimate=True` and `search_volume=0` while caching results for 30 days
