# contracts Specification

## Purpose
Defines Pydantic v2 domain contracts, git packs loader with hash-based pack versioning, and service layer boundary enforcement.

## Requirements

### Requirement: Pydantic domain contracts with schema versioning
The system SHALL provide typed Pydantic models for core domain contracts (`ResearchRecord`, `Opportunity`, `EditorialBrief`, `ContentDraft`, `QualityReport`, `PublishRequest`, `PublishedContent`, `BrandProfile`, `CampaignConfig`, `KeywordData`), each carrying an integer `schema_version`.

#### Scenario: Validation of CampaignConfig requires scoring weights to sum to 100
- **WHEN** a `CampaignConfig` is instantiated or validated
- **THEN** if the scoring weights do not sum to 100, validation fails with a `ValidationError`

#### Scenario: Validation of BrandProfile validates prohibited phrases and CTA rules
- **WHEN** a `BrandProfile` is validated
- **THEN** invalid regular expressions in prohibited phrases are rejected, and CTA rules are checked

#### Scenario: Domain contracts serialize and deserialize with schema version
- **WHEN** a contract model is serialized to JSON or dict and deserialized
- **THEN** `schema_version` is preserved and all fields validate

### Requirement: Git packs loader and hash calculation
The system SHALL load industry packs, format packs, and prompts from git directories, validate their structure against schemas, and compute a deterministic `pack_version` hash.

#### Scenario: Pack loading computes deterministic pack_version hash
- **WHEN** valid industry and format packs are loaded
- **THEN** `pack_version` returns a deterministic SHA-256 hash reflecting the content of the pack files

#### Scenario: Broken pack raises error naming file and field
- **WHEN** a pack file is corrupted or contains invalid fields
- **THEN** loader raises an exception identifying the specific file and invalid field

### Requirement: Service layer membership check and architectural boundaries
The system SHALL provide a `services.py` entry point checking user membership, and enforce that application and CLI code only access `services` and `contracts`.

#### Scenario: require_member raises PermissionError when user lacks membership
- **WHEN** `require_member` is called for a user who does not have an active membership in the workspace
- **THEN** a `PermissionError` is raised

#### Scenario: Direct DB imports outside service layer violate architectural boundaries
- **WHEN** `app.py` or `cli.py` attempts to import `engine.db`
- **THEN** import-linter contract check fails
