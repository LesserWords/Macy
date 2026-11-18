# writing Specification

## Purpose
Drafts authoritative articles in the campaign locale and brand voice, tracing every factual claim to verified sources.

## Requirements

### Requirement: Article generation with claim provenance
The system SHALL generate article drafts (`ContentDraft`) from an `EditorialBrief` in the campaign's locale and pinned brand voice, persist each claim in `draft_claims` with database-enforced `document_id` provenance for all `fact` claims, support revisions `0` through `2` incorporating evaluator findings, and record LLM usage in `usage_ledger` with stage `write` and `prompt_version`.

#### Scenario: Draft creation at revision 0 from editorial brief
- **WHEN** `production.write(brief)` is called for a finalized brief
- **THEN** a draft at `revision = 0` is persisted in `drafts` with `status = 'draft'`, its `payload` validates against `ContentDraft`, and `usage_ledger` records a row with `stage = 'write'` and the `write_blog.md` `prompt_version`

#### Scenario: Draft claims enforce factual provenance
- **WHEN** an article draft is written and persisted
- **THEN** every claim in `ContentDraft.claims` is stored in `draft_claims` ordered by `idx`, every `fact` claim references an existing workspace `documents.id`, and any `fact` claim with `document_id IS NULL` or a cross-tenant `document_id` is rejected by the database (`SQLSTATE 23514` / `23503`)

#### Scenario: Revision counter capped at two attempts
- **WHEN** `production.write(brief, findings)` is called for a revision after quality evaluation
- **THEN** the evaluator's findings are included in the writer input, a new draft row with `revision + 1` is persisted up to `revision = 2`, and attempting `revision = 3` is rejected by the database `CHECK (revision BETWEEN 0 AND 2)` constraint (`SQLSTATE 23514`)

### Requirement: External Markdown/PDF and agent-authored draft import with format sizing
The system SHALL support importing ready-made `.md` (`python-frontmatter`) and `.pdf` (`pymupdf4llm`) files or structured `ContentDraft` payloads (`services.import_external_draft`, `extract_file_to_markdown`, and `parse_markdown_draft`) pinned to a selected `FormatPack` (`blog-short`, `blog`, or `blog-long`), bootstrapping an ad-hoc `EditorialBrief` when `brief_id` is omitted and appending revisions `0..2` when `brief_id` is provided (`F12`, `F13`, `F21 AC11`, `ADR-025`).

#### Scenario: Parsing raw Markdown with YAML frontmatter or PDF into a valid ContentDraft payload
- **WHEN** `extract_file_to_markdown` and `parse_markdown_draft` receive a `.pdf` or `.md` file containing H1/H2 sections, optional YAML frontmatter (`title`, `slug`, `meta_description`, `primary_keyword`, `format_slug`), and optional FAQ/CTA blocks
- **THEN** `pymupdf4llm` and `python-frontmatter` extract and normalize a valid `ContentDraft` payload with kebab-case `slug`, bounded `meta_description` (`110..160` characters), structured `faq` and `cta`, and `claims` linked to supplied `document_id`s

#### Scenario: Importing draft revisions respects the two-revision cap
- **WHEN** `services.import_external_draft` is called repeatedly for the same `brief_id`
- **THEN** revisions `0`, `1`, and `2` are persisted in `drafts` with `draft_claims`, and attempting a fourth import (`revision = 3`) is rejected cleanly
