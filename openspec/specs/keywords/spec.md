# keywords Specification

## Purpose
Retrieves and caches search volume, CPC, and intent metrics from keyword providers.

## Requirements

### Requirement: Keyword data retrieval and caching
The system SHALL look up search volume and keyword metrics with 30-day caching, idempotently normalized terms, and spend tracking.

#### Scenario: Cached keyword lookup avoids external paid call
- **WHEN** keyword data was fetched within the last 30 days for the same locale, market, and normalized keyword
- **THEN** cached metrics are returned without triggering a paid external API call

#### Scenario: Uncached keyword query records ledger expense
- **WHEN** a new keyword query is made to the provider
- **THEN** usage ledger records the unit cost and response metrics

#### Scenario: Missing metrics marked as estimate
- **WHEN** the keyword provider returns no search metrics for a term
- **THEN** the keyword record is marked with `is_estimate=true` and search volume 0 without inventing fake numbers

### Requirement: Keyword normalization
The system SHALL normalize search terms by trimming whitespace, lowercasing, and removing diacritics/accents in an idempotent operation.

#### Scenario: Idempotent normalization of accented terms
- **WHEN** a keyword such as "Óculos de Sol" is normalized
- **THEN** it produces "oculos de sol", and re-normalizing the output yields the exact same string

### Requirement: DataForSEO provider and locale mapping
The system SHALL provide a DataForSEO plugin that maps supported locales and markets (`pt-BR`/`br`, `en-US`/`us`) to vendor location and language codes, verifies connectivity, and raises `UnsupportedLocale` for unmapped locales.

#### Scenario: Supported locale lookup succeeds
- **WHEN** keyword metrics are queried for `pt-BR` and `br`
- **THEN** the request maps to Brazil location code 2076 and Portuguese language code "pt"

#### Scenario: Unsupported locale is rejected
- **WHEN** keyword metrics are queried for an unmapped locale such as `fr-FR`
- **THEN** `UnsupportedLocale` is raised

### Requirement: Zero-key autocomplete keyword estimator plugin
The system SHALL provide an `autocomplete` plugin implementing `KeywordProviderPort` registered under `content_engine.keyword_provider`, retrieving autocomplete keyword suggestions via `safe_http`, respecting the 30-day `keyword_cache` and `UnsupportedLocale`, and marking all returned `KeywordData` rows with `is_estimate=True` (`ADR-031`, `F22 AC7`).

#### Scenario: Autocomplete provider marks unbacked keyword suggestions as estimates
- **WHEN** `autocomplete` looks up terms for `pt-BR`/`BR` without paid keyword volume metrics
- **THEN** it returns normalized `KeywordData` suggestions with `is_estimate=True` and `search_volume=0` while caching results for 30 days
