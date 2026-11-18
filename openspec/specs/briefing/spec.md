# briefing Specification

## Purpose
Generates structured editorial briefs with angle, audience, search intent, outline, and source-backed required facts.

## Requirements

### Requirement: Editorial brief synthesis from approved opportunity
The system SHALL generate an `EditorialBrief` linking the current brand profile (`brand_profile_id` and `brand_version`), target audience, keyword snapshot (with explicit `is_estimate` indicators), outline, CTA goal, and verified evidence facts referencing workspace `documents`.

#### Scenario: Brief creation grounds required facts in evidence
- **WHEN** an approved opportunity is processed for brief generation
- **THEN** a new brief row (version 1) is persisted in `briefs`, its `payload` validates against `EditorialBrief`, its `brand_profile_id` and `brand_version` match the workspace's active brand profile, and every `required_facts[].source_id` resolves to an existing document ID in the same workspace

#### Scenario: Revision routing produces new brief version
- **WHEN** the quality evaluator or revision service routes a `REVISION` finding to the brief stage for an approved opportunity
- **THEN** a `version + 1` row is created in `briefs` incorporating the evaluator findings into synthesis while previous brief versions remain unmodified and readable

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

### Requirement: PDF statistical extraction trial (M3-03)
The system SHALL evaluate PDF statistical table extraction across seed evidence (`data`) sources during M3 and either retain an optional PDF extractor for `application/pdf` or record the trial as dropped with empirical justification (F12 AC5).

#### Scenario: Docling PDF extraction trial evaluates accuracy, seed source availability, and resource fit
- **WHEN** the M3-03 Docling trial executes against general seed evidence sources (`IBGE`, `The Vision Council`, `Euromonitor`) using the optional `docling-serve-cpu` container and compares extracted tables against ground truth and operational criteria
- **THEN** the trial records both fixture-level extraction accuracy (`3/3` PDFs) and operational rejection criteria (`1/3` un-gated open PDF seed sources, `< 3 briefs/month`, `7.05 GiB` image / `1.60 GiB` RSS RAM on a `4 GB` VPS), documenting the Drop verdict cleanly in `TECH-RADAR.md` and `ADR-003`
