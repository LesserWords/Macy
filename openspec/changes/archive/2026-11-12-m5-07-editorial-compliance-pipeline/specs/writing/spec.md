# writing Delta Specification

## ADDED Requirements

### Requirement: External Markdown/PDF and agent-authored draft import with format sizing
The system SHALL support importing ready-made `.md` (`python-frontmatter`) and `.pdf` (`pymupdf4llm`) files or structured `ContentDraft` payloads (`services.import_external_draft`, `extract_file_to_markdown`, and `parse_markdown_draft`) pinned to a selected `FormatPack` (`blog-short`, `blog`, or `blog-long`), bootstrapping an ad-hoc `EditorialBrief` when `brief_id` is omitted and appending revisions `0..2` when `brief_id` is provided (`F12`, `F13`, `F21 AC11`, `ADR-025`).

#### Scenario: Parsing raw Markdown with YAML frontmatter or PDF into a valid ContentDraft payload
- **WHEN** `extract_file_to_markdown` and `parse_markdown_draft` receive a `.pdf` or `.md` file containing H1/H2 sections, optional YAML frontmatter (`title`, `slug`, `meta_description`, `primary_keyword`, `format_slug`), and optional FAQ/CTA blocks
- **THEN** `pymupdf4llm` and `python-frontmatter` extract and normalize a valid `ContentDraft` payload with kebab-case `slug`, bounded `meta_description` (`110..160` characters), structured `faq` and `cta`, and `claims` linked to supplied `document_id`s

#### Scenario: Importing draft revisions respects the two-revision cap
- **WHEN** `services.import_external_draft` is called repeatedly for the same `brief_id`
- **THEN** revisions `0`, `1`, and `2` are persisted in `drafts` with `draft_claims`, and attempting a fourth import (`revision = 3`) is rejected cleanly
