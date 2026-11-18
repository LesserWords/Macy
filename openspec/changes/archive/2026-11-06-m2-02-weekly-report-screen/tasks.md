# Tasks: Weekly Report Screen and Recommendations (M2-02)

## 1. Database & Queries
- [x] 1.1 Add `get_opportunity_report_items` in `engine/db.py` joining `opportunities`, `research_records`, and `documents`.
- [x] 1.2 Unit test opportunity report query with joins and tenant isolation in `tests/test_db.py`.

## 2. Service Layer & Events
- [x] 2.1 Implement `services.report` in `engine/services.py` with top 10 limit, top $N = \max(3, \text{articles\_per\_week})$ recommendations, `already_covered` flag, and date integrity checks (F09 AC3, AC4).
- [x] 2.2 Emit `topics.awaiting_approval` event idempotently upon report generation (F09 AC5).
- [x] 2.3 Add comprehensive tests in `tests/test_services.py` and `tests/test_report.py`.

## 3. UI Screen & Localization
- [x] 3.1 Add Weekly Report page in `app.py` with bilingual strings.
- [x] 3.2 Extract and compile gettext translations for `pt-BR` and `en-US` (`messages.po` / `messages.mo`).

## 4. E2E Offline Journey & Verification
- [x] 4.1 Implement Journey J3 in `tests/test_journey_j3_weekly_report.py`.
- [x] 4.2 Verify all quality gates pass (100% diff-cover, >=90% branch coverage, ruff, mypy, complexipy <=15, vulture, lint-imports, jscpd, squawk, openspec validate).
