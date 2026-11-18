## MODIFIED Requirements

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
