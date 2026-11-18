# contracts Delta Specification

## MODIFIED Requirements

### Requirement: Git packs loader and hash calculation
The system SHALL load industry packs, format packs, and recursive prompt templates (`prompts/**/*.md`) from git directories, validate their structure against schemas, and compute a deterministic `pack_version` SHA-256 hash.

#### Scenario: Pack loading computes deterministic pack_version hash
- **WHEN** valid industry and format packs are loaded
- **THEN** `pack_version` returns a deterministic SHA-256 hash reflecting the content of the pack files

#### Scenario: Broken pack raises error naming file and field
- **WHEN** a pack file is corrupted or contains invalid fields
- **THEN** loader raises an exception identifying the specific file and invalid field

#### Scenario: Nested prompt markdown files affect pack_version
- **WHEN** a `.md` template inside a subdirectory under `prompts/` is added or modified
- **THEN** `compute_pack_version` includes that file deterministically in its SHA-256 digest

## ADDED Requirements

### Requirement: Dynamic run code_version and pack_version stamping
The system SHALL dynamically resolve `code_version` (from `APP_TAG` or package metadata / `engine.__version__`) and compute `pack_version` via `compute_pack_version` for the workspace's active industry pack and campaign format when `scheduler.trigger_campaign_run` creates a run row.

#### Scenario: Triggered run records dynamic code_version and computed pack_version
- **WHEN** `scheduler.trigger_campaign_run` inserts a run row without explicit version overrides
- **THEN** `runs.code_version` reflects the active runtime release/version metadata and `runs.pack_version` equals `compute_pack_version(workspace.industry_pack, campaign_format)` rather than static `"1.0.0"`
