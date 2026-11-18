# research Delta Specification

## ADDED Requirements

### Requirement: Zero-key Google News RSS search plugin
The system SHALL provide a `gnews_rss` plugin implementing `NewsSearchPort` registered under `content_engine.news_search`, executing topic and `site:<domain>` queries via `safe_http` + `defusedxml` across `pt-BR`/`BR` and `en-US`/`US` with zero external API keys (`ADR-031`, `F22 AC7`).

#### Scenario: Google News RSS plugin performs domain-scoped search and rejects unsupported locales
- **WHEN** `gnews_rss` searches for `site:example.com` with `locale="pt-BR"` and `market="BR"`
- **THEN** it returns parsed `ResearchRecord` items at `$0` spend, and raises `UnsupportedLocale` when invoked with an unmapped locale such as `fr-FR`
