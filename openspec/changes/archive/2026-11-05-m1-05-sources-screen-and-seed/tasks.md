## 1. Source service validation, feed parsing via safe_http, and domain uniqueness

- [x] 1.1 Write failing parametrised validation-matrix and service tests for `services.save_source()` and `services.list_sources()` covering AC1–AC4 and AC7 (feed fetch and XML parse via `safe_http`, `site_search` domain requirement, required tier and role, disabling a source, and duplicate domain rejection for both workspace and shared pack scopes) → verify: `uv run pytest tests/test_services.py -k "source"` fails as expected
- [x] 1.2 Implement source queries in `engine/db.py` and `services.save_source()`, `services.list_sources()`, and `services.set_source_enabled()` in `engine/services.py` → verify: `uv run pytest tests/test_services.py -k "source"` passes

## 2. 18 PRD §5.8 seed sources and CLI seed-sources command

- [x] 2.1 Write failing unit and CLI tests for `packs/industries/general/sources.yaml`, `services.seed_sources()`, and `cli seed-sources` asserting literal counts per role (`news=7`, `company=6`, `data=4`, `management=1`), tier (`1=5`, `2=10`, `3=3`), and locale (`pt-BR=7`, `en-US=11`) (AC5) → verify: `uv run pytest tests/test_services.py -k "seed_sources"` fails as expected
- [x] 2.2 Populate the 18 PRD §5.8 sources in `packs/industries/general/sources.yaml` and implement `services.seed_sources()` and `cli seed-sources` in `engine/cli.py` → verify: `uv run pytest tests/test_services.py -k "seed_sources"` passes

## 3. Bilingual Sources UI screen, offline E2E Journey J2, and documentation

- [x] 3.1 Write failing offline E2E Journey J2 tests (`tests/test_journey_j2_sources.py`) covering listing sources, localized role help text in `pt-BR` and `en-US`, adding a feed source (validated via `safe_http`), rejecting an invalid feed or missing domain, editing a source, and disabling a source (AC1–AC4) → verify: `uv run pytest tests/test_journey_j2_sources.py` fails as expected
- [x] 3.2 Build `_render_sources()` in `app.py` with role help descriptions and update `locales/pt_BR` and `locales/en_US` catalogues, update `docs/features/F04-source-management.md` and `docs/adr/ADR-022-source-model.md`, and run all quality gates → verify: `scripts/check.sh` passes
