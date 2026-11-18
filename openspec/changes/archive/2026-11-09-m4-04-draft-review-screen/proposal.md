# Proposal: M4-04 — Draft review screen (gate 2)

## Why
Evaluated article drafts that pass quality checks or reach the maximum 2-revision cap (`M4-03`, `F14`) must surface cleanly for human editorial review (`F15`, Gate 2) alongside source provenance, evaluator findings, revision history, and spend visibility so the campaign creator can approve for publishing (`M4-05`) or decline with feedback (`ADR-013`, `ADR-014`).

## What Changes
- **Database Queries & Declined-Story Exclusion (`engine/db.py`):**
  - Add tenant-scoped `decide_draft()` with guarded atomic `WHERE status = 'pass'` transition setting `approved` / `declined`, `decided_by`, `decided_at`, and `decision_note`.
  - Add tenant-scoped review queries: `list_drafts_for_review()`, `list_latest_drafts_for_run()`, `list_draft_revision_history_for_opportunity()`, and `get_run_usage_breakdown()`.
  - Update `get_opportunity_report_items()` so opportunities whose drafts reached `declined` mark matching stories/clusters as rejected and exclude them from future recommendations (`F15 AC4`).
- **Service Layer Gate 2 Decisions & Review Bundle (`engine/services.py`):**
  - Implement `decide_draft()` enforcing campaign creator (`created_by`) + `'owner'` role (`ADR-013`, `F15 AC5`), guarded `pass -> approved|declined` transition, atomic `draft.decided` event emission with `publish_queued` flag (`F15 AC3`, `AC4`).
  - Implement `list_draft_reviews()`, `get_draft_review_bundle()`, and `produce_and_evaluate_draft()` plus idempotent `draft.awaiting_approval` surfacing checks (`F15 AC1`) and attach `latest_draft` metadata in `report()`.
- **Bilingual Draft Review Screen (`app.py`, `locales/`):**
  - Add `"draft_review": _("Draft Review")` screen rendering awaiting-review & recently-decided drafts, full Markdown article preview, claim → source provenance table (title, clickable URL, publication date, `date_status`), deterministic checks + evaluator findings, cross-brief revision history (`0..2`), per-stage cost breakdown, and Approve/Decline controls with optional note (`F15 AC2`, `F19`).
  - Surface quick "Write & Evaluate Draft" / "Open Draft Review" controls on approved briefs in Weekly Report.
  - Extract, update, translate (`pt-BR` & `en-US`), and compile gettext catalogs cleanly (`scripts/check_translations.py`).
- **Tests & Offline E2E Journeys J5–J7 (`tests/`):**
  - Service/DB unit, concurrency, declined-story exclusion, and tenant-isolation tests (`tests/test_draft_review.py`, `tests/test_tenant_scoping.py`).
  - Offline E2E Playwright/AppTest journeys J5 (evaluated draft surfaced), J6 (approve flow), and J7 part 2 (decline with note & exclusion) in `tests/test_journey_j5_j6_j7_draft_review.py`.
