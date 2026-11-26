# brand Specification

## Purpose
Maintains brand profile, voice, capabilities, positioning, prohibited claims, and CTA rules with versioning.

## Requirements

### Requirement: Brand profile versioning and validation
The system SHALL maintain versioned brand profiles per workspace and validate prohibited regexes and CTA rules.

#### Scenario: Saving brand creates new version and keeps previous readable
- **WHEN** the owner saves brand profile changes
- **THEN** a new brand_profiles row with version+1 becomes current while prior versions remain readable

#### Scenario: Brief generation captures exact brand version
- **WHEN** an editorial brief is generated
- **THEN** it records the exact brand_profile_id and version used

#### Scenario: Invalid prohibited phrases regex is rejected
- **WHEN** brand profile contains an invalid regular expression in prohibited phrases
- **THEN** saving fails and names the invalid field

### Requirement: Single current brand profile invariant
The system SHALL demote the previous current brand profile and promote the new `version+1` brand profile within a single database transaction, and the `brand_one_current` partial unique index SHALL enforce at most one current version per workspace.

#### Scenario: Only one current brand version allowed per workspace
- **WHEN** the owner saves multiple brand profile versions or a concurrent insert attempts a second current row for the same workspace
- **THEN** only the newest version has `is_current = true`, all earlier versions have `is_current = false` and remain readable, and a second `is_current = true` row for the same workspace is rejected by the database unique index

### Requirement: Acme Demo brand seed command
The system SHALL provide a `seed-brand` CLI command and `fixtures/seed_example.yaml` fixture that seeds version 1 of the brand profile for a new workspace.

#### Scenario: Seed command creates version 1 from fixture
- **WHEN** `cli seed-brand` runs for a new workspace using `fixtures/seed_example.yaml`
- **THEN** version 1 of the brand profile is created as the current brand profile matching the fixture payload

### Requirement: Website URL and sitemap auto-seeder
The system SHALL provide `autoseed_from_url` in `engine/onboarding.py` to fetch a target URL and its sitemap strictly via `safe_http`, deterministically extracting metadata into `brand_profiles` (`v1`, `is_current=true`) and discovered pages into `content_inventory` with zero LLM calls, falling back to pack fixtures on network refusal (`ADR-031`, `F22 AC8`).

#### Scenario: Auto-seeder populates brand profile v1 and content inventory from public URL
- **WHEN** `autoseed_from_url` runs for a valid public website URL with an accessible sitemap
- **THEN** `brand_profiles` (`version=1`, `is_current=true`) and `content_inventory` rows are created with zero LLM API spend, while private or loopback URLs are refused by `safe_http`

### Requirement: Canonical brand identity fields and acyclic load-time YAML composition
The system SHALL support optional canonical identity fields (`brand_name`, `target_persona`, `canonical_domain`) on `BrandProfile` and acyclic load-time YAML `extends` fixture inheritance (`capabilities_add`, `capabilities_remove`, `prohibited_phrases_add`, `prohibited_claims_add`) resolved across cascading fixture roots and materialized into the workspace's own `brand_profiles` row with zero runtime parent links (`ADR-034`, `M11-01`).

#### Scenario: Child fixture composes core rules via extends and strips composition directives
- **WHEN** `config.load_brand_fixture_dict` resolves a child fixture declaring `extends: _base_core.yaml` alongside `capabilities_add`, `capabilities_remove`, `prohibited_phrases_add`, and `prohibited_claims_add`
- **THEN** shared parent fields are merged deterministically, removed items are excluded, composition directive keys are stripped before `BrandProfile.model_validate()`, and seeding persists a self-contained workspace snapshot

#### Scenario: Circular or missing extends reference fails cleanly
- **WHEN** a brand fixture declares a cyclic `extends` chain or names a non-existent parent file
- **THEN** loading raises `ValueError` naming the cycle or missing parent path
