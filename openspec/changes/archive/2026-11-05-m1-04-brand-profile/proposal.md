# Proposal

## Why

Plan item **M1-04** (`docs/plans/M1.md`). Implement versioned brand profile persistence and validation in the service layer, build the bilingual Brand Profile screen in the Admin UI with per-field validation errors, and add the Acme Demo brand seed fixture and `seed-brand` CLI command. Features: F03. ADRs: 009, 010, 014.

## What Changes

- Tighten `BrandProfile` validation in `engine/contracts.py` (non-empty `voice`, `positioning`, and list entries alongside regex compilation on `prohibited_phrases`).
- Add `db.get_current_brand_profile()`, `db.get_brand_profile()`, `db.list_brand_profiles()`, `db.save_brand_profile()`, and `db.get_workspace_owner()` in `engine/db.py` with strict `workspace_id` scoping and single-transaction version promotion/demotion.
- Add `services.save_brand()`, `services.current_brand()`, and `services.seed_brand()` in `engine/services.py` with `settings.changed` audit events (`entity_type="brand_profile"`).
- Build the bilingual Brand Profile screen in `app.py` showing current version and per-field validation errors on invalid input, and update `locales/pt_BR` and `locales/en_US` catalogues.
- Add `fixtures/seed_example.yaml` and the `seed-brand` subcommand in `engine/cli.py`.
- Add unit, database constraint, CLI, and offline E2E Journey J1 brand tests.
- Out of scope: multiple brands per workspace, approval workflow for brand changes, brief `brand_profile_id` recording (AC2, covered in M3-02).

## Capabilities

### Modified Capabilities
- `brand`: Add requirements and scenarios for single-transaction version demotion (`brand_one_current` partial unique index), per-field validation errors on the Brand Profile screen, and seeding version 1 from `fixtures/seed_example.yaml` via `cli seed-brand` (AC4).

## Impact

- New: `fixtures/seed_example.yaml`.
- Modified: `engine/contracts.py`, `engine/db.py`, `engine/services.py`, `engine/cli.py`, `app.py`, `locales/`, `tests/test_contracts.py`, `tests/test_services.py`, `tests/test_journey_j1_campaign_settings.py`, `docs/features/F03-brand-profile.md`.
