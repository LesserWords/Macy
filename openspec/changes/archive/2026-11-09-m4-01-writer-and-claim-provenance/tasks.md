# Tasks

## 1. Schema Migration & Writer Prompt (Step 1)
- [x] 1.1 Write failing schema constraint and tenant-scoping tests in `tests/test_schema_constraints.py` and `tests/test_tenant_scoping.py` for `drafts`, `draft_claims` (`fact` without `document_id` rejected with `SQLSTATE 23514`, cross-tenant `document_id` rejected with `SQLSTATE 23503`, `revision = 3` rejected with `SQLSTATE 23514`), and `quality_reports`.
  → verify: `uv run pytest tests/test_schema_constraints.py -k drafts` fails red before migration.
- [x] 1.2 Add `db/migrations/20261109000001_create_drafts_claims_and_quality_reports.sql`, apply migration, and author `prompts/write_blog.md`.
  → verify: `npx --yes squawk-cli db/migrations/*.sql` is clean and schema constraint tests pass green.

## 2. Writer Regression Assertions for Cases 6–7 (Step 2)
- [x] 2.1 Write failing writer regression tests in `evals/test_regression.py` and extend `evals/cases/case_06.yaml` and `evals/cases/case_07.yaml` with writer expectations (`write_expected`).
  → verify: `uv run pytest evals/test_regression.py -k write` fails red against a stub before writer implementation.

## 3. Production Writer Step & Service Wiring (Step 3)
- [x] 3.1 Write failing unit and integration tests in `tests/test_writer.py` covering `F13 AC1–AC3` and `AC5` (revision 0 creation, `draft_claims` persistence and DB rejection of sourceless/invalid facts, revision 1–2 with evaluator findings and revision 3 cap rejection, `usage_ledger` row with `stage='write'` and `prompt_version`).
  → verify: `uv run pytest tests/test_writer.py` fails red before implementation.
- [x] 3.2 Implement `engine/production/writer.py` (`production.write`, `write_draft_with_prompt`), `engine/production/__init__.py`, `FakeLLM` support for `ContentDraft` in `engine/integrations/fakes.py`, database helpers in `engine/db.py`, and service methods in `engine/services.py`.
  → verify: `uv run pytest tests/test_writer.py evals/test_regression.py tests/test_schema_constraints.py tests/test_tenant_scoping.py` and all quality gates pass green.
