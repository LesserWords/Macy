# publishing Specification Delta

## ADDED Requirements

### Requirement: Content inventory sitemap import
The system SHALL import existing blog articles from XML sitemaps or sitemap index files into `publications` with `mode = 'live'`, `status = 'published'`, and unique `(workspace_id, url)` for idempotent re-runs.

#### Scenario: Sitemap import persists published articles idempotently
- **WHEN** `import-sitemap` is executed with a valid blog sitemap URL
- **THEN** each article URL, title, and publication date is stored in `publications`, and subsequent re-runs do not create duplicate rows
