## ADDED Requirements

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
