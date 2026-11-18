# Design: Runtime Integration Credibility, Pipeline/UI Parity, and Integration Guides (`m5-01b`)

## Context & Architectural Constraints

All runtime and Admin UI improvements in `m5-01b` must respect Macy's existing module boundaries (`import-linter`: `app.py` and `engine/cli.py` import only `engine.services` and `engine.contracts`), SSRF safeguards (`engine/safe_http.py`), offline test suites (`@pytest.mark.vcr` and Streamlit `AppTest`), `complexipy <= 15`, and `diff-cover = 100%` on `engine/` changes.

Verification against OpenRouter's live catalog (`https://openrouter.ai/api/v1/models`) confirmed `anthropic/claude-haiku-5.5` and `anthropic/claude-opus-5.5` are current valid catalog IDs with the pricing documented in `ADR-007`; therefore model defaults in `engine/integrations/openrouter.py` and `engine/llm.py` stay unchanged.

## Decisions

### Decision 1: Placeholder Rejection and Authenticated `safe_request` Healthchecks
- **Files:** `engine/integrations/openrouter.py`, `engine/integrations/brave.py`, `engine/integrations/dataforseo.py`, `engine/services.py`, `tests/test_llm.py`, `tests/test_integrations.py`, `tests/test_contract_suites.py`, `tests/test_journey_j8_integrations.py`, VCR cassettes in `tests/cassettes/test_contract_suites/`.
- **Default secrets removed:** Remove `"test-openrouter-key"` and `"test-brave-key"` string defaults from `OpenRouterLLM.Settings`, `OpenRouterLLM.__init__`, and `BraveNewsSearch.Settings`; read non-empty env fallbacks only (`None` / `SecretStr("")` when unset).
- **Placeholder rejection helper:** Reject empty/whitespace credentials and known non-production literals (`"test-openrouter-key"`, `"test-brave-key"`, or values starting with `"CHANGE_ME"`) in each adapter's `healthcheck()` and live API calls so unconfigured or placeholder credentials fail consistently even before sending HTTP traffic.
- **Lightweight authenticated remote probes via `safe_request`:**
  - `OpenRouterLLM.healthcheck()`: `validate_safe_url(self.settings.base_url)`, then `safe_request("GET", f"{self.settings.base_url.rstrip('/')}/key", headers={"Authorization": f"Bearer {token}"}, timeout=10.0)` and fail on `status_code >= 400`. Using OpenRouter's `/api/v1/key` endpoint checks key validity with zero LLM token usage. (Add `timeout_seconds` to `Settings` or pass `10.0`.)
  - `BraveNewsSearch.healthcheck()`: `validate_safe_url(self.settings.base_url)`, then `safe_request("GET", f"{self.settings.base_url}?{urlencode({'q': 'macy', 'count': '1'})}", headers={"Accept": "application/json", "X-Subscription-Token": token}, timeout=self.settings.timeout_seconds)` and fail on `status_code >= 400`.
  - `DataForSEOKeywordProvider.healthcheck()`: check `login, password = self._raw_credentials()`, reject empty/placeholder values, `validate_safe_url(self.settings.base_url)`, build `parts = urlsplit(self.settings.base_url)` → `f"{parts.scheme}://{parts.netloc}/v3/appendix/user_data"`, send `safe_request("GET", ...)` with Basic auth (`login:password`), and fail on `status_code >= 400`.
- **Offline test determinism:** Update `test_contract_suites.py` (or cassette interactions) so OpenRouter, Brave, and DataForSEO instantiate with explicit synthetic non-placeholder keys in tests and replay recorded `GET /key`, `GET ?q=macy&count=1`, and `GET /v3/appendix/user_data` interactions under `@pytest.mark.vcr`; patch `safe_request` (returning HTTP 200) alongside `resolve_and_validate_host` in `test_journey_j8_integrations.py` so UI `Test Connection` exercises the full real `healthcheck()` codepath offline.

### Decision 2: DataForSEO Env Var Forwarding Parity
- **Files:** `compose.prod.yaml`, `.env.staging.example`, `.env.prod.example`, `tests/test_compose_and_docker.py`.
- Add `DATAFORSEO_LOGIN: ${DATAFORSEO_LOGIN:-}` and `DATAFORSEO_PASSWORD: ${DATAFORSEO_PASSWORD:-}` under `x-app-env` in `compose.prod.yaml`, and add optional commented/empty template placeholders (`DATAFORSEO_LOGIN=`, `DATAFORSEO_PASSWORD=`) alongside `OPENROUTER_API_KEY` and `BRAVE_API_KEY` in `.env.staging.example` and `.env.prod.example`. Keep them optional (not in `scripts/deploy.sh` `REQUIRED_VARS`), matching UI-configured integration workflows.

### Decision 3: `run.blocked` Event Parity
- **Files:** `app.py`, `docs/guides/user-guide.md`, `docs/adr/ADR-017-webhooks.md`, `tests/test_webhooks.py`.
- Add `"run.blocked"` to `all_events` in `app.py:_render_webhook_form` (`options=all_events`).
- Document `run.blocked` in `docs/guides/user-guide.md` (§2 Step 5 event list) and `docs/adr/ADR-017-webhooks.md` (Decision bullet list alongside `run.completed` / `run.failed`).

### Decision 4: Recursive `prompts/` Hashing & Dynamic Run Versions
- **Files:** `engine/config.py`, `engine/scheduler.py`, `engine/__init__.py` (or `importlib.metadata`), `tests/test_config.py`, `tests/test_scheduler.py`.
- Change `prompts_path.glob("*.md")` → `prompts_path.rglob("*.md")` in `engine/config.py:compute_pack_version`.
- In `engine/scheduler.py`:
  - Resolve runtime `code_version` via `os.environ.get("APP_TAG") or metadata.version("macy-content-engine")` (falling back safely to `"0.1.0"` in editable/uninstalled imports via `engine.__version__`).
  - Resolve `workspace = db.get_workspace(conn, workspace_id=workspace_id)` in `trigger_campaign_run`, extract `industry_pack = str(workspace["industry_pack"])` and `format_slug = str((campaign.get("config") or {}).get("format") or "blog")`, and pass `pack_version = compute_pack_version(industry_pack, format_slug)` when `pack_version` is not explicitly supplied by the caller.

### Decision 5: `run-stage` Input Validation & Worker-Race-Free `run-now --offline`
- **Files:** `engine/pipeline.py`, `engine/services.py`, `engine/scheduler.py`, `engine/db.py`, `engine/cli.py`, `tests/test_pipeline.py`, `tests/test_cli.py`.
- **`re_run_failed_stage_items`:** Accept supported values `{"research", "score"}` explicitly; raise `ValueError(f"Unsupported stage '{stage}' for re-run; supported stages: research, score")` cleanly before exiting, causing `content-engine run-stage` to exit `1` and log the error. Keep helper modular so `complexipy` stays `<= 15`.
- **`run-now --offline` worker race guard:** Extend `db.create_run(..., status: str = "queued")` / `scheduler.trigger_campaign_run(..., initial_status: str = "queued")` / `services.run_now(..., offline: bool = False)` (or `initial_status="running"` when `args.offline` is True). When `initial_status="running"`, stamp `started_at=now()`, rely on `runs_one_active` (`status IN ('queued','running')`) for conflict prevention, and ensure `pipeline.execute_run` cleanly executes an already-`running` row when invoked synchronously in-process (`offline=True`). This prevents the background `worker` loop from claiming a newly inserted `queued` row in live mode before `_handle_run_now --offline` starts executing it.

### Decision 6: Admin UI Dev Offline Mode, Dynamic Dashboard Cap, and Translation Catalog
- **Files:** `app.py`, `locales/messages.pot`, `locales/pt_BR/LC_MESSAGES/messages.po`, `locales/pt_BR/LC_MESSAGES/messages.mo`, `locales/en_US/LC_MESSAGES/messages.po`, `locales/en_US/LC_MESSAGES/messages.mo`, `tests/test_i18n.py`, `tests/test_ui_shell_i18n.py`.
- Change `_use_offline_brief_mode` in `app.py` from `os.getenv("APP_ENV") != "test"` to `os.getenv("APP_ENV") not in {"test", "development"}` (so `staging`/`production` never enter offline mode, while `APP_ENV=development` cleanly enables fake brief + draft workflows when no `llm` integration and no `OPENROUTER_API_KEY` exist).
- Change `_render_dashboard` in `app.py` to query active campaign cap via `services.get_active_campaign(...)` / `services.cost_summary(...)` (`summary["monthly_cap_usd"]` when active campaign exists) instead of `150.00`, rendering `_("No active campaign")` if zero active campaigns exist in the workspace. Update `tests/test_ui_shell_i18n.py` to seed a campaign when testing locale-formatted currency caps and assert both dynamic campaign cap rendering and empty-campaign fallback.
- Wrap `_("Add New Source")`, `_("Opportunity")`, `_("No evidence recorded.")`, and `_("Draft")` cleanly in `_()`, extract/merge into `messages.pot` and `pt_BR` / `en_US` `.po`, compile both `.mo` binaries with Babel (`pybabel compile`), and verify `scripts/check_translations.py` exits `0`.

### Decision 7: Accurate `n8n-publish.md` and `user-guide.md`
- **Files:** `docs/guides/n8n-publish.md`, `docs/guides/user-guide.md`, `tests/test_publishing.py`.
- **`docs/guides/n8n-publish.md`:**
  - Replace `const rawBody = JSON.stringify($input.first().json.body)` with exact instructions enabling n8n Webhook **Options → Raw Body** (reading binary/raw UTF-8 payload buffer, e.g., `$input.first().binary.data` / raw body property) and explicitly warn why `JSON.stringify` breaks HMAC signatures when whitespace/key formatting differs from Python's exact JSON body.
  - Clarify non-`whsec_` secret normalization (`_normalize_webhook_secret` UTF-8-encodes plain secrets to base64 before Standard Webhooks signing), recommending `whsec_` base64 secrets verbatim across both Macy and `MACY_PUBLISH_SECRET`.
  - Document concrete `Idempotency-Key` (`<draft_id>:<destination>`) lookup/upsert logic in the n8n workflow.
- **`docs/guides/user-guide.md`:**
  - Clarify that Gate 2 **Approve** invokes publishing immediately if a `publisher` integration is configured, and explain when **Publish Now**, **Retry Publish**, and **Re-publish** appear.
  - Add a note under `markdown_export` explaining that inside Docker containers exports write to the container path unless a persistent volume mount (e.g., `./exports:/app/exports`) is mounted.

## Risks & Mitigations

- **Existing tests relying on `"test-openrouter-key"` / `"test-brave-key"`:** Audit and update all unit tests constructing `OpenRouterLLM()` or `BraveNewsSearch()` without explicit test credentials (`tests/test_llm.py`, `tests/test_contract_suites.py`, `tests/test_journey_j8_integrations.py`), verifying `healthcheck()` fails with no key and passes under mocked/VCR valid keys.
- **Streamlit UI import boundary & test execution:** Keep all UI cap lookups inside `engine/services.py` (never import `engine.db` from `app.py`) and verify `lint-imports` cleanly.
