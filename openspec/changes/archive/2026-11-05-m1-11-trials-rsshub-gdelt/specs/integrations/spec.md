# Spec Delta

## ADDED Requirements

### Requirement: Research source trials and plugin evaluation
The system SHALL evaluate candidate research source technologies against quantitative keep/drop criteria, recording decisions and evidence in architecture records and tech radar.

#### Scenario: RSSHub route evaluation for site_search sources
- **WHEN** RSSHub routes are evaluated against the 13 seed `site_search` sources in the optical retail industry pack
- **THEN** if fewer than 5 sources yield dated items, the `rsshub` profile is removed from Docker Compose and the drop decision is recorded in TECH-RADAR and ADR-003

#### Scenario: GDELT DOC 2.0 NewsSearch plugin evaluation with rate limiting against Brave
- **WHEN** the GDELT DOC 2.0 plugin is evaluated with a 1 request per 5 seconds rate limit over a 14-day window against Brave Search News for `pt-BR`
- **THEN** if unique relevant optical industry stories are below 20%, the plugin is dropped from production entry points and the empirical decision is recorded in TECH-RADAR and ADR-003
