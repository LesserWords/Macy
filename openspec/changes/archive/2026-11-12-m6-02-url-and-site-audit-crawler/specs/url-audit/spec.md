# url-audit Specification

## ADDED Requirements

### Requirement: Single-URL SEO and GEO audit with weighted scoring
The system SHALL fetch a public URL via `safe_http` and compute a 0–100 weighted audit score across `onpage`, `links`, `ai` (GEO), `security`, `usability`, `social`, and `performance` categories with `A+`–`F` letter grades and prioritized recommendations.

#### Scenario: Single-URL audit flags AI crawler blocks and missing metadata
- **WHEN** `audit_url` inspects a page whose `robots.txt` blocks `OAI-SearchBot` or whose HTML lacks `<meta name="description">`
- **THEN** it records category findings, reduces the `ai` or `onpage` score accordingly, and persists a workspace-scoped audit report

### Requirement: Bounded site crawl and multipage issue detection
The system SHALL crawl internal URLs up to a configurable `max_pages` cap (hard cap `500`), respecting `robots.txt` and `safe_http` SSRF rules, and detect broken internal/external links, redirect chains, duplicate titles/descriptions, and orphan pages.

#### Scenario: Site crawl detects broken link and orphan page
- **WHEN** `crawl_site` completes a bounded BFS crawl seeded from the start URL and `sitemap.py`
- **THEN** any `4xx`/`5xx` internal or external link and any sitemap URL with zero internal incoming links are reported in the crawl summary
