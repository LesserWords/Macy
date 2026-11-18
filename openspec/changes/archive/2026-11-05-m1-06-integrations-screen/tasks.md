# Tasks

## 1. Schema-to-Widget Mapping & SecretStr Plugin Settings
- [x] 1.1 Write failing unit tests in `tests/test_integrations.py` for `services.extract_settings_field_specs()` covering every field type used by MVP plugins (`SecretStr` -> `password`, `int` -> `number`, `float` -> `number`, `str` -> `text`, `Literal`/`enum` -> `select`, `models_by_stage` -> `stage_models`, `dict` -> `json`).
- [x] 1.2 Upgrade secret fields (`api_key`, `auth_token`) in `OpenRouterLLM.Settings` and `fakes.py` to `SecretStr` and implement `services.extract_settings_field_specs()`.

## 2. Integration Save, Test Connection, Stage Model Selection & SSRF Guard
- [x] 2.1 Write failing service tests in `tests/test_services.py` covering `save_integration()` (Fernet encryption, preserving existing secrets when blank, SSRF URL refusal via `safe_http`, `settings.changed` audit event with masked secrets), `test_integration()` (`healthcheck()` storing `last_test_ok` and `last_tested_at`), and per-stage LLM model selection reflected in `usage_ledger.model` (AC2, AC3, AC5, AC7).
- [x] 2.2 Implement `db.get_workspace_integration()`, `db.upsert_workspace_integration()`, `db.update_workspace_integration_test_status()` in `engine/db.py` and `services.list_integrations()`, `services.save_integration()`, `services.test_integration()` in `engine/services.py`, plus workspace LLM resolution in `engine/llm.py`.

## 3. Integrations Screen & Secret Hygiene E2E
- [x] 3.1 Write failing offline E2E test (`tests/test_journey_j8_integrations.py`) covering plugin configuration, masked secrets after save, Test connection (`last_test_ok`), LLM model-per-stage update, SSRF URL refusal, and verifying the planted secret string never appears in page state/elements, logs, or `events` rows.
- [x] 3.2 Implement `_render_integrations()` in `app.py`, update `pt_BR` and `en_US` translation catalogs, compile `.mo` files, and verify all quality gates pass.
