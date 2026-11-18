# Tasks

## 1. Service Gate 2 Decisions, Events & Declined Story Exclusion (M4-04 Step 1)
- [x] 1.1 Write failing tests in `tests/test_draft_review.py` and `tests/test_tenant_scoping.py` for `services.decide_draft` (approver check `F15 AC5`, guarded `pass -> approved|declined`, `draft.decided` event with `publish_queued`, concurrent two-connection race), `draft.awaiting_approval` queue surfacing (`F15 AC1`), declined story exclusion from future `report()` (`F15 AC4`), review bundle details, and strict `workspace_id` isolation.
  → verify: `uv run pytest tests/test_draft_review.py tests/test_tenant_scoping.py` failed red before implementation (`AttributeError: module 'engine.services' has no attribute 'decide_draft'`).
- [x] 1.2 Implement `engine/db.py` (`decide_draft`, `list_drafts_for_review`, `list_latest_drafts_for_run`, `list_draft_revision_history_for_opportunity`, `get_run_usage_breakdown`, declined-story `is_rejected_story` update) and `engine/services.py` (`decide_draft`, `list_draft_reviews`, `get_draft_review_bundle`, `produce_and_evaluate_draft`, `latest_draft` on `report()`).
  → verify: all Gate 2 service, concurrency, declined-story, and tenant-scoping tests pass green (`tests/test_draft_review.py`, `tests/test_tenant_scoping.py`).

## 2. Bilingual Draft Review Screen & i18n Catalogs (M4-04 Step 2 & Step 3)
- [x] 2.1 Write failing bilingual offline E2E tests in `tests/test_journey_j5_j6_j7_draft_review.py` covering J5 (evaluated draft surfaced with rendered Markdown, claim → source table with title/URL/date/`date_status`, findings, revision history, and cost), J6 (approve flow in `pt-BR` and `en-US`), and J7 (decline path with note & report exclusion).
  → verify: E2E J5–J7 tests failed red prior to `app.py` Draft Review screen implementation.
- [x] 2.2 Implement Draft Review screen (`"draft_review"`) and approved-brief draft trigger/link in `app.py`, update `locales/messages.pot` and `pt_BR`/`en_US` `.po`/`.mo` catalogs via `pybabel`, and verify `scripts/check_translations.py`.
  → verify: offline E2E J5–J7 (`tests/test_journey_j5_j6_j7_draft_review.py`) and translation completeness checks (`scripts/check_translations.py`) pass green (`F15 AC1–AC5`, `F19`).

## 3. Quality Gates, Coverage & Documentation Sync
- [x] 3.1 Verify full quality suite (`ruff`, `mypy`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk`, `pytest`, `diff-cover == 100%` against `origin/dev`, `openspec validate --all --strict`).
- [x] 3.2 Update `docs/features/F15-draft-review-gate.md` status (`Done`) and interfaces/test mappings.
