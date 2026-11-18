# Proposal

## Why

Plan item **M1-05** (`docs/plans/M1.md`). Implement trusted source validation and persistence (`services.save_source()`, `services.list_sources()`), seed the 18 general trusted sources from PRD §5.8 via `packs/industries/general/sources.yaml` and `cli seed-sources`, and build the bilingual Sources admin screen with offline E2E Journey J2. Features: F04. ADRs: 003, 022.

## What Changes

- Add `db.create_source()`, `db.update_source()`, `db.get_source()`, `db.list_sources()`, and `db.upsert_pack_source()` in `engine/db.py` with strict `workspace_id` scoping.
- Add `services.save_source()`, `services.list_sources()`, `services.set_source_enabled()`, and `services.seed_sources()` in `engine/services.py` validating RSS/Atom feeds via `safe_http`, `site_search` domain requirement, required tier (`1..3`) and role (`news`, `company`, `data`, `management`), and domain uniqueness (`UNIQUE NULLS NOT DISTINCT (workspace_id, domain)`), emitting `settings.changed` audit events (`entity_type="source"`).
- Populate all 18 PRD §5.8 general trusted sources in `packs/industries/general/sources.yaml` and add `cli seed-sources` in `engine/cli.py`.
- Build the bilingual Sources screen in `app.py` (list, add, edit, enable/disable, and role descriptions in both `pt-BR` and `en-US`) and update `locales/`.
- Add unit/service validation-matrix tests, seed count tests, and offline E2E Journey J2 (`tests/test_journey_j2_sources.py`).
- Out of scope: live 30-day seed verification run (`cli verify-sources`, covered in M1-12), research stage polling of enabled sources (covered in M1-07/M1-08/M1-10).

## Capabilities

### Modified Capabilities
- `sources`: Expand `Source registration and validation` with scenarios for `site_search` domain requirement (AC2), required tier/role and UI role descriptions (AC3), and source disabling (AC4), and add requirement for seeding the 18 PRD §5.8 sources (AC5).

## Impact

- New: `tests/test_journey_j2_sources.py`.
- Modified: `packs/industries/general/sources.yaml`, `engine/db.py`, `engine/services.py`, `engine/cli.py`, `app.py`, `locales/`, `tests/test_services.py`, `docs/features/F04-source-management.md`, `docs/adr/ADR-022-source-model.md`.
