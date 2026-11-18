# Proposal

## Why

Plan item **M1-03** (`docs/plans/M1.md`). Implement campaign settings validation, persistence, audit events, monthly cost estimation, and the bilingual Campaign Settings admin screen with offline E2E Journey J1. Features: F02, F17. ADRs: 004, 015, 016.

## What Changes

- Add `UnsupportedLocale` exception in `engine/integrations/registry.py` (re-exported in `engine/services.py` and `engine/contracts.py`) and supported-locale metadata (`SUPPORTED_LOCALES = ("pt-BR", "en-US")`) on plugins.
- Add `db.update_campaign()` and `db.list_workspace_integrations()` in `engine/db.py` with strict `workspace_id` scoping.
- Add `services.save_campaign()`, `services.get_active_campaign()`, `services.validate_cron_expression()`, and `services.estimate_monthly_cost()` in `engine/services.py`.
- Emit `settings.changed` event in `events` table containing `before` and `after` campaign snapshots on create and update.
- Build the bilingual Campaign Settings screen in `app.py` with live estimated monthly cost updates before saving, strategy scoring weights, cron schedule, and spend caps.
- Update `pt_BR` and `en_US` gettext catalogues in `locales/`.
- Add unit, property-based, and offline E2E Journey J1 tests (`tests/test_journey_j1_campaign_settings.py`).
- Out of scope: multiple active campaigns per locale in UI, scheduled future settings changes, budget guard enforcement at runtime (covered in M1-10).

## Capabilities

### Modified Capabilities
- `campaigns`: Add requirements and scenarios for plugin locale validation (AC2), run snapshot isolation (AC5), and pre-save monthly cost estimation (AC6).

## Impact

- New: `tests/test_journey_j1_campaign_settings.py`.
- Modified: `engine/db.py`, `engine/services.py`, `engine/integrations/registry.py`, `engine/integrations/fakes.py`, `engine/integrations/openrouter.py`, `app.py`, `locales/`, `tests/test_services.py`, `docs/features/F02-campaign-settings.md`, `docs/adr/ADR-015-locale-runtime-setting.md`.
