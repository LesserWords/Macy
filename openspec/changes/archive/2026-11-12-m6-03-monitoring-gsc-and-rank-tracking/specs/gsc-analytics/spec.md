# gsc-analytics Specification

## ADDED Requirements

### Requirement: Google Search Console sync and diagnostic insights
The system SHALL integrate Google Search Console Search Analytics and 12-hour-cached URL Inspection via `AnalyticsSourcePort` using encrypted per-workspace credentials, and compute striking-distance (`position 4..20`), traffic decay (`7d`/`28d`), and query cannibalization diagnostics.

#### Scenario: Striking-distance analyzer surfaces high-impression low-CTR queries
- **WHEN** GSC query data includes a query at average position `4..20` with above-median impressions and below-benchmark CTR
- **THEN** `gsc_insights` ranks it as a top candidate for snippet/meta optimization or internal linking

### Requirement: Scheduled URL monitoring and optional rank tracking
The system SHALL support scheduled URL re-audits with TLS certificate expiry checks and report diffing that emit Standard Webhooks on score drops, plus an optional `RankTrackerPort` recording API spend in `usage_ledger`.

#### Scenario: Score drop emits Standard Webhook event
- **WHEN** a scheduled URL monitor detects a score drop exceeding the configured threshold or a TLS certificate expiring soon
- **THEN** it enqueues an outbound event in `events` for `webhook_deliveries`
