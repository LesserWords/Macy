# Proposal: M4-01 — Writer and claim provenance

## Why
Once an approved opportunity has an `EditorialBrief` (F12), the pipeline needs to synthesize a full-length blog draft (`ContentDraft`) in the campaign's language and brand voice where every factual assertion traces back to an existing workspace `documents` row (`F13`, `ADR-002`, `ADR-010`).

## What Changes
- **Database Schema (`db/migrations/20261109000001_create_drafts_claims_and_quality_reports.sql`):**
  - Add `drafts` (`revision BETWEEN 0 AND 2`, `UNIQUE (brief_id, revision)`, composite FK `(workspace_id, brief_id)`), `draft_claims` (`CHECK (kind <> 'fact' OR document_id IS NOT NULL)`, composite FKs `(workspace_id, draft_id)` and `(workspace_id, document_id)`), and `quality_reports` (1:1 per draft revision).
- **Writer Prompt (`prompts/write_blog.md`):**
  - Author versioned markdown prompt instructing structured `ContentDraft` output (`title`, `meta_description`, `slug`, `body_md`, `faq`, `cta`, `claims[]`), strict `fact` claim `source_id` provenance from brief/evidence documents, locale/brand compliance, and revision finding resolution.
- **Regression Evaluation Suite (`evals/cases/case_06.yaml`, `evals/cases/case_07.yaml`, `evals/test_regression.py`):**
  - Extend regression cases 6–7 (`pt-BR` and `en-US`) with writer expectations (`min_words`, `min_claims`, `min_fact_claims`, `require_primary_keyword_in_title_h1_meta`, `slug_pattern`) and add red/green writer regression tests.
- **Production Writer Step (`engine/production/writer.py`, `engine/production/__init__.py`, `engine/db.py`, `engine/services.py`, `engine/integrations/fakes.py`):**
  - Implement `production.write(brief, findings=None, ...)` and pure helper `write_draft_with_prompt(...)` calling `llm.structured_call(stage="write", model_cls=ContentDraft, reasoning_effort="high", prompt_version=...)`.
  - Persist `drafts` (`revision = 0..2`) and `draft_claims` atomically in a single transaction, recording LLM spend in `usage_ledger` (`stage="write"`, `prompt_version`) and emitting `draft.created`.
