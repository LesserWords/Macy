## 1. Service validation, persistence, and audit events

- [x] 1.1 Write failing parametrised validation-matrix and service tests for `services.save_campaign()` covering AC1–AC5 (locale/market format, active plugin `UnsupportedLocale`, articles/week 1–20, cron validation, spend caps, scoring weights summing to 100, `settings.changed` before/after event, and run snapshot isolation) → verify: `uv run pytest tests/test_services.py` fails as expected
- [x] 1.2 Implement `UnsupportedLocale`, `db.update_campaign()`, `db.list_workspace_integrations()`, and `services.save_campaign()` with `settings.changed` audit event → verify: `uv run pytest tests/test_services.py` passes

## 2. Monthly cost estimation and Campaign Settings UI

- [x] 2.1 Write failing unit and Hypothesis tests for `services.estimate_monthly_cost()` against literal expected values (AC6) → verify: `uv run pytest tests/test_services.py` fails as expected
- [x] 2.2 Implement `services.estimate_monthly_cost()` and build the Campaign Settings screen in `app.py` with bilingual translations in `locales/` → verify: unit tests and `scripts/check_translations.py` pass

## 3. Offline E2E Journey J1 and documentation

- [x] 3.1 Write offline E2E Journey J1 test (`tests/test_journey_j1_campaign_settings.py`) covering saving pt-BR then en-US, volume=1, spend caps, live cost estimate update before save, unsupported locale plugin error, missing cap rejection, and UI language switching → verify: `uv run pytest tests/test_journey_j1_campaign_settings.py` passes
- [x] 3.2 Update `docs/features/F02-campaign-settings.md` and `docs/adr/ADR-015-locale-runtime-setting.md` and run all quality gates → verify: `scripts/check.sh` passes
