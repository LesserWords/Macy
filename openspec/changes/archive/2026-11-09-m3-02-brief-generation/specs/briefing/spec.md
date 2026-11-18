# briefing Specification Delta

## MODIFIED Requirements

### Requirement: Editorial brief synthesis from approved opportunity
The system SHALL generate an `EditorialBrief` linking the current brand profile (`brand_profile_id` and `brand_version`), target audience, keyword snapshot (with explicit `is_estimate` indicators), outline, CTA goal, and verified evidence facts referencing workspace `documents`.

#### Scenario: Brief creation grounds required facts in evidence
- **WHEN** an approved opportunity is processed for brief generation
- **THEN** a new brief row (version 1) is persisted in `briefs`, its `payload` validates against `EditorialBrief`, its `brand_profile_id` and `brand_version` match the workspace's active brand profile, and every `required_facts[].source_id` resolves to an existing document ID in the same workspace

#### Scenario: Revision routing produces new brief version
- **WHEN** the quality evaluator or revision service routes a `REVISION` finding to the brief stage for an approved opportunity
- **THEN** a `version + 1` row is created in `briefs` incorporating the evaluator findings into synthesis while previous brief versions remain unmodified and readable

## ADDED Requirements

### Requirement: Evidence lookup from data and management sources
The system SHALL gather supporting statistics from enabled `data` and `management` sources matching the workspace industry pack and campaign locale, attaching extracted pages into `documents` before brief synthesis.

#### Scenario: Evidence lookup attaches extracted data documents
- **WHEN** brief generation executes with enabled `data` or `management` sources in the workspace
- **THEN** relevant evidence articles are queried, extracted into workspace `documents`, and supplied as candidate `document_id` references for `required_facts`

### Requirement: Strict workspace document provenance enforcement
The system SHALL validate every `required_facts[].source_id` in code before saving a brief and reject missing, malformed, or cross-tenant `document_id` values.

#### Scenario: Invalid or cross-tenant document ID is rejected
- **WHEN** the structured LLM response returns a `required_facts[].source_id` that is malformed, missing, or belongs to a different workspace
- **THEN** brief persistence is aborted with a validation error and no row is written to `briefs`

### Requirement: Brief keyword snapshot and estimate visibility
The system SHALL resolve target keyword metrics via `seo.keywords_for`, store the resolved keyword snapshot in `briefs.keywords`, and display keyword volumes or explicit estimate badges in the Weekly Report UI after Gate 1 approval.

#### Scenario: Approved topic displays brief and keyword volumes or estimate labels
- **WHEN** an owner approves a topic in a workspace with a current brand profile
- **THEN** the rendered brief displays target keywords with search volumes or `"Estimated"` flags and cites verified evidence documents
