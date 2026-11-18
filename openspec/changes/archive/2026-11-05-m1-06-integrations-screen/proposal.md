# Proposal

## Why

Plan item **M1-06** (`docs/plans/M1.md`). Implement the Integrations admin screen and supporting service functions so workspace owners can configure plugins per port via forms generated from each plugin's `Settings` JSON Schema (`SecretStr` → password field; secrets masked after save), test connections (`healthcheck()` storing `last_test_ok` and `last_tested_at`), configure LLM models per stage, and guarantee strict secret hygiene across UI state, logs, services, and events. Features: F05. ADRs: 005, 007, 008.

## What Changes

- Upgrade secret fields in `OpenRouterLLM.Settings` (`api_key`) and `fakes.py` (`api_key`, `auth_token`) to `pydantic.SecretStr` so JSON Schema exports `"format": "password"` and secrets are masked on representation.
- Add `db.get_workspace_integration()`, `db.upsert_workspace_integration()`, and `db.update_workspace_integration_test_status()` in `engine/db.py` with strict `workspace_id` scoping.
- Add `services.extract_settings_field_specs()`, `services.list_integrations()`, `services.save_integration()`, and `services.test_integration()` in `engine/services.py` enforcing membership, validating URL fields via `safe_http.validate_safe_url()` (F05 AC7), separating non-secret `settings` from Fernet-encrypted `secrets`, recording `last_test_ok`/`last_tested_at`, and emitting `settings.changed` audit events (`entity_type="integration"`) with masked secrets.
- Build the bilingual Integrations screen in `app.py` rendering schema-driven widgets (`password`, `select`, `number`, `checkbox`, `stage_models`, `json`, `text`), masked secret indicators after save, Test connection button, and LLM model-per-stage selectors (`research`, `score`, `brief`, `write`, `evaluate`).
- Add unit/service tests and offline E2E Journey (`tests/test_journey_j8_integrations.py`) proving schema-to-widget mapping, `test_integration()` (`last_test_ok`), LLM model-per-stage routing in `usage_ledger.model`, SSRF URL refusal, and zero secret leakage in page state, logs, or events.

## Capabilities

### Modified Capabilities
- `integrations`: Add scenarios for JSON Schema form generation with `SecretStr` password inputs (AC1), secret hygiene across UI/logs/services/events (AC2), Test connection recording `last_test_ok` and `last_tested_at` (AC3), per-stage LLM model routing into `usage_ledger.model` (AC5), and SSRF URL refusal via `safe_http` on save or test (AC7).

## Impact

- New: `tests/test_journey_j8_integrations.py`.
- Modified: `engine/integrations/openrouter.py`, `engine/integrations/fakes.py`, `engine/integrations/config.py`, `engine/db.py`, `engine/services.py`, `engine/llm.py`, `app.py`, `locales/`, `tests/test_integrations.py`, `tests/test_services.py`, `docs/features/F05-integrations-management.md`, `docs/adr/ADR-008-user-configured-integrations.md`.
