# Tasks: Topic Approval Gate 1 (M2-03)

## 1. Database Queries & Transitions
- [x] 1.1 Implement `decide_opportunity`, `count_approved_opportunities`, `expire_previous_opportunities`, and story rejection lookup in `engine/db.py`.
- [x] 1.2 Add DB query unit tests in `tests/test_db.py`.

## 2. Service Layer & Business Logic
- [x] 2.1 Implement `services.decide_topic` in `engine/services.py` with creator authorization, run locking, volume capping, guarded transition, and `topic.decided` event emission (F10 AC1–AC4).
- [x] 2.2 Wire previous run opportunity expiry into run execution lifecycle (F10 AC6).
- [x] 2.3 Exclude rejected stories from future recommendations in `services.report` (F10 AC5).
- [x] 2.4 Add service unit and integration tests including two-connection race test in `tests/test_topic_approval.py`.

## 3. UI Screen & Localization
- [x] 3.1 Update `app.py` opportunity card with decision note and Approve / Reject action buttons.
- [x] 3.2 Extract and compile gettext translations for `pt-BR` and `en-US` (`messages.po` / `messages.mo`).
- [x] 3.3 Validate zero untranslated or fuzzy strings with `scripts/check_translations.py`.

## 4. E2E Journeys & Quality Gates
- [x] 4.1 Implement offline E2E Journey J4 (approve path → brief queued) and Journey J7 part 1 (reject path → not re-recommended) in `tests/test_journey_j4_topic_approval.py`.
- [x] 4.2 Verify all quality gates pass (100% diff-cover on `engine/`, pytest, ruff, mypy, complexipy <= 15, vulture, lint-imports, jscpd, squawk, openspec validate).
