# Proposal: Runtime Integration Credibility, Pipeline/UI Parity, and Integration Guides

**Plan Item:** M5-01b (post-M5-01 release audit follow-up) · **Features:** F02, F05, F06, F07, F11, F15, F16, F18, F19 · **ADRs:** ADR-001, ADR-002, ADR-005, ADR-007, ADR-008, ADR-013, ADR-014, ADR-017, ADR-021

## Why

An end-to-end maintainer and operator review of the M5-01 baseline (`3fe745f`) uncovered several runtime, CLI, Admin UI, translation, and integration-guide gaps that can mislead operators or fail silently in production:

1. **Shallow adapter `healthcheck()` and placeholder key defaults:** `OpenRouterLLM` and `BraveNewsSearch` still fall back to `"test-openrouter-key"` / `"test-brave-key"` in runtime `Settings`, and neither OpenRouter, Brave, nor DataForSEO (`engine/integrations/dataforseo.py` lacks credential checks altogether) performs an authenticated remote connectivity probe on `healthcheck()`. Clicking **Test Connection** in the UI can report green with invalid or missing API credentials. (Catalog verification against live `https://openrouter.ai/api/v1/models` confirmed `anthropic/claude-haiku-5.5` and `anthropic/claude-opus-5.5` are current valid catalog IDs and should remain unchanged.)
2. **Compose / `.env` credential forwarding mismatch:** `compose.prod.yaml` (`x-app-env`) and `.env.{staging,prod}.example` forward `OPENROUTER_API_KEY` and `BRAVE_API_KEY` but omit `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` even though `DataForSEOKeywordProvider._raw_credentials()` and `docs/guides/environment-and-credentials.md` document them.
3. **`run.blocked` webhook subscription and doc drift:** `engine/pipeline.py` emits `run.blocked`, yet `app.py`'s Webhooks selector, `docs/guides/user-guide.md`, and `docs/adr/ADR-017-webhooks.md` omit `run.blocked`.
4. **Static run version stamping and non-recursive prompt hashing:** `engine/scheduler.py` stamps hardcoded `CURRENT_CODE_VERSION = "0.1.0"` and `CURRENT_PACK_VERSION = "1.0.0"` on every run instead of resolving runtime version and computing `compute_pack_version`, while `engine/config.py` scans `prompts/` non-recursively with `glob("*.md")`.
5. **CLI `run-stage` silent no-op and `run-now --offline` worker race:** `re_run_failed_stage_items` returns `[]` with exit code `0` for unknown `--stage` values instead of failing fast, and `macy run-now --offline` commits a `queued` run before executing locally—creating a race with the background polling worker (`engine/__main__.py`).
6. **Admin UI dev parity, hardcoded cap, i18n literals, and guide pitfalls:** `_use_offline_brief_mode` in `app.py` only allows `APP_ENV=test` (blocking `APP_ENV=development`), `_render_dashboard` hardcodes `\$150.00` instead of reading the active campaign cap, four UI fallback strings bypass `_()` translation, `docs/guides/n8n-publish.md` uses `JSON.stringify($input.first().json.body)` (which breaks HMAC verification against raw wire bytes) and blurs non-`whsec_` base64 transformation, and `docs/guides/user-guide.md` omits immediate publish on Gate 2 approval and container export volume notes.

## What Changes

- Remove runtime `"test-*"` API key defaults from `OpenRouterLLM` and `BraveNewsSearch`, reject empty/placeholder credentials across OpenRouter, Brave, and DataForSEO, add lightweight authenticated `safe_request` healthcheck probes (`GET /api/v1/key`, Brave minimal query, DataForSEO `GET /v3/appendix/user_data`), update VCR cassettes / UI mocks cleanly, and forward `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` in `compose.prod.yaml` and `.env.{staging,prod}.example`.
- Expose `run.blocked` in `app.py`'s Webhooks event selector, `docs/guides/user-guide.md`, and `docs/adr/ADR-017-webhooks.md`.
- Switch `compute_pack_version` to recursive `prompts_path.rglob("*.md")`, and dynamically stamp `runs.code_version` and `runs.pack_version` in `engine/scheduler.py`.
- Validate `--stage` cleanly in `re_run_failed_stage_items` / `services.run_stage` so invalid stages raise `ValueError` and exit non-zero, and execute `macy run-now --offline` atomically as an already-claimed `running` run so the polling worker never hijacks an offline CLI execution.
- Extend `_use_offline_brief_mode` to allow `APP_ENV=development` (still rejecting `staging` / `production`), replace the hardcoded `\$150.00` cap on Dashboard with the active campaign's `monthly_cap_usd` (or translated `"No active campaign"` notice), wrap untranslated literals (`"Add New Source"`, `"Opportunity"`, `"No evidence recorded."`, `"Draft"`) in `_()` across `app.py` and `locales/`, and update `docs/guides/n8n-publish.md` and `docs/guides/user-guide.md`.

## Capabilities

### Modified Capabilities
- `integrations`: Real credentialed healthchecks without `"test-*"` runtime defaults, plus `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD` Compose forwarding parity.
- `webhooks`: `run.blocked` exposed cleanly across Admin UI subscription options and documentation.
- `runs`: Invalid `--stage` non-zero error enforcement and worker-race-free `run-now --offline` execution.
- `contracts`: Recursive `prompts/` hashing and dynamic `code_version` / `pack_version` stamping on run creation.
- `admin-ui`: Translation catalog completeness for remaining literals, `development` offline brief/draft fallback, and dynamic Dashboard campaign monthly cap.
- `publishing`: Validated raw-body HMAC signature guidance in `n8n-publish.md` and Gate 2 auto-publish + container export volume accuracy in `user-guide.md`.

## Key Results: Delivered

All `D1`–`D6` must pass before `M5-01b` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `OpenRouterLLM`, `BraveNewsSearch`, and `DataForSEOKeywordProvider` reject empty/`"test-*"`/`"CHANGE_ME"` keys, perform authenticated `safe_request` probes on `healthcheck()`, and `compose.prod.yaml` + `.env.{staging,prod}.example` forward `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` | `uv run pytest tests/test_llm.py tests/test_integrations.py tests/test_contract_suites.py tests/test_journey_j8_integrations.py tests/test_compose_and_docker.py --no-cov` exits `0` |
| **D2** | `run.blocked` is selectable in `app.py:_render_webhook_form`, persists/delivers when a run blocks, and is documented in `user-guide.md` and `ADR-017` | `uv run pytest tests/test_webhooks.py -k blocked --no-cov` exits `0` |
| **D3** | `compute_pack_version()` hashes nested `prompts/**/*.md` via `rglob("*.md")`, and `scheduler.trigger_campaign_run()` dynamically stamps runtime `code_version` and computed `pack_version` | `uv run pytest tests/test_config.py tests/test_scheduler.py --no-cov && uv run lint-imports` exits `0` |
| **D4** | `macy run-stage` exits non-zero (`ValueError`) on invalid stages, and `macy run-now <ws> --offline` claims the run as `running` before execution so `run_worker_tick` never races | `uv run pytest tests/test_pipeline.py tests/test_cli.py tests/test_services.py -k "run_stage or run_now" --no-cov` exits `0` |
| **D5** | `_use_offline_brief_mode` allows `APP_ENV=development` (rejecting `staging`/`production`), Dashboard displays active campaign `monthly_cap_usd`, and all UI literals pass `check_translations.py` with `0` untranslated/fuzzy strings | `uv run python scripts/check_translations.py && uv run pytest tests/test_i18n.py tests/test_ui_shell_i18n.py tests/test_cost_view.py --no-cov` exits `0` |
| **D6** | `docs/guides/n8n-publish.md` raw-body HMAC + `Idempotency-Key` examples and `docs/guides/user-guide.md` Gate 2 / export volume docs pass doc-contract tests and full repo gates | `uv run pytest tests/test_publishing.py --no-cov && sh scripts/check.sh` exits `0` |

## Out of Scope

- Cross-table key rotation, `RETENTION_DB_PASSWORD` enforcement, `.env` OIDC template cleanup, and production authenticated smoke/rollback hardening (covered in `2026-11-10-m5-01a-ops-deploy-hardening`).
- CI `diff-cover` workflow fix, Playwright live E2E selectors/assertions, Compose dev bind mounts, `db/schema.sql` regeneration, and `DATA-MODEL.md`/milestone doc sync (covered in `2026-11-10-m5-01c-ci-schema-doc-sync`).
- Multi-owner approval RBAC relaxation beyond the MVP creator-only invariant (`ADR-013`).
