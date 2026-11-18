## 1. Brand profile versioning, single-transaction demotion, and current_brand service

- [x] 1.1 Write failing unit and service tests for `services.save_brand()`, `services.current_brand()`, `BrandProfile` field validation, single-transaction version increment (`version+1`), previous version readability, and `brand_one_current` partial unique index enforcement (AC1, AC3) → verify: `uv run pytest tests/test_services.py tests/test_contracts.py` fails as expected
- [x] 1.2 Implement tightened `BrandProfile` validators in `engine/contracts.py`, brand profile queries in `engine/db.py`, and `services.save_brand()` / `services.current_brand()` in `engine/services.py` → verify: `uv run pytest tests/test_services.py tests/test_contracts.py` passes

## 2. Brand Profile Admin UI screen and bilingual translations

- [x] 2.1 Write failing UI and offline E2E J1 tests for the Brand Profile screen (editing and saving v1 -> v2, displaying per-field validation errors when `prohibited_phrases` contains an invalid regex or required fields are blank) (AC1, AC3) → verify: `uv run pytest tests/test_journey_j1_campaign_settings.py` fails as expected
- [x] 2.2 Build `_render_brand_profile()` in `app.py` with per-field validation error messages and update `pt_BR` and `en_US` translation catalogues in `locales/` → verify: `uv run pytest tests/test_journey_j1_campaign_settings.py` and `scripts/check_translations.py` pass

## 3. Acme Demo brand seed fixture, CLI command, and documentation

- [x] 3.1 Write failing CLI and service tests for `fixtures/seed_example.yaml` and `cli seed-brand` creating version 1 for a new workspace (AC4) → verify: `uv run pytest tests/test_services.py` fails as expected
- [x] 3.2 Create `fixtures/seed_example.yaml`, implement `services.seed_brand()` and `cli seed-brand` in `engine/cli.py`, update `docs/features/F03-brand-profile.md`, and run all quality gates → verify: `scripts/check.sh` passes
