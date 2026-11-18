# sources Specification

## Purpose
Manages trusted news, data, company, and management sources per workspace and pack.

## Requirements

### Requirement: Source registration and validation
The system SHALL validate feed URLs, site search domains, tier, role, and domain uniqueness across pack and workspace sources, display role descriptions in the admin UI, and allow owners to edit or disable sources.

#### Scenario: Feed source URL must parse and fetch
- **WHEN** a feed source is added
- **THEN** the feed URL is fetched via safe HTTP and verified to parse, or saving fails

#### Scenario: Site search source requires a valid domain
- **WHEN** a `site_search` source is saved without a valid domain
- **THEN** saving fails with a validation error naming the domain requirement

#### Scenario: Tier and role are required and role descriptions are shown in UI
- **WHEN** a source is saved without a valid tier (`1`, `2`, or `3`) or role (`news`, `company`, `data`, `management`)
- **THEN** saving fails, and the Sources UI displays localized help text describing each role

#### Scenario: Source can be disabled by the owner
- **WHEN** an owner disables a source in the Sources screen
- **THEN** the source's `enabled` flag is persisted as `false` so subsequent runs skip it

#### Scenario: Duplicate domain in same scope is rejected
- **WHEN** a second source is added with a domain already registered in the workspace or global scope
- **THEN** the second source is rejected by unique constraint

### Requirement: Optical retail 18 seed sources
The system SHALL seed all 18 general trusted sources defined in PRD §5.8 from `packs/industries/general/sources.yaml` as shared pack sources (`workspace_id IS NULL`) via `cli seed-sources`.

#### Scenario: Seed command loads all 18 sources with expected role and tier counts
- **WHEN** `cli seed-sources` runs for the `general` pack
- **THEN** all 18 shared sources exist with `workspace_id IS NULL`, comprising 7 `news`, 6 `company`, 4 `data`, and 1 `management` sources across 5 Tier-1, 10 Tier-2, and 3 Tier-3 entries

### Requirement: Seed source verification run and exit gate
The system SHALL verify all seed sources against live or mock feeds and search queries over a verification window (default 30 days) via `cli verify-sources`, generating a verification report (records found, dated %, errors), reclassifying or disabling failing sources with explanatory notes, and verifying the 14-day M1 exit gate.

#### Scenario: CLI verify-sources generates per-source verification report
- **WHEN** `cli verify-sources --days 30` runs for an industry pack
- **THEN** every shared pack source is queried across its fetch mode and a summary report is returned reporting records found, percentage with verified publication dates, and fetch status

#### Scenario: Failing source is reclassified or disabled with explanatory notes
- **WHEN** a seed source yields zero dated records over the 30-day verification window
- **THEN** the source is either reclassified or marked `enabled: false` with explanatory notes recorded in the pack configuration so no seed source is left unexplained

#### Scenario: M1 exit gate verification for pt-BR and en-US over 14 days
- **WHEN** a 14-day research run executes for both pt-BR and en-US campaigns and 20 sample records are inspected
- **THEN** the run stores deduplicated research records with verified publication dates, validating the M1 exit gate
