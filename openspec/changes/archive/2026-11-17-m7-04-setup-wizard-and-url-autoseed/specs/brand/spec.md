# brand Delta Specification

## ADDED Requirements

### Requirement: Website URL and sitemap auto-seeder
The system SHALL provide `autoseed_from_url` in `engine/onboarding.py` to fetch a target URL and its sitemap strictly via `safe_http`, deterministically extracting metadata into `brand_profiles` (`v1`, `is_current=true`) and discovered pages into `content_inventory` with zero LLM calls, falling back to pack fixtures on network refusal (`ADR-031`, `F22 AC8`).

#### Scenario: Auto-seeder populates brand profile v1 and content inventory from public URL
- **WHEN** `autoseed_from_url` runs for a valid public website URL with an accessible sitemap
- **THEN** `brand_profiles` (`version=1`, `is_current=true`) and `content_inventory` rows are created with zero LLM API spend, while private or loopback URLs are refused by `safe_http`
