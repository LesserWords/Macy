# F22 — Macy Pocket (Path A Container & Path B Zero-Docker CLI/MCP), Modular Capability Plugins & Guided Setup

**Status:** Done · **Milestone:** M7 · **Plan items:** M7-01, M7-02, M7-03, M7-04 · **ADRs:** ADR-029, ADR-030, ADR-031 · **OpenSpec capability:** `capabilities`

## User story
As an operator, solo creator, or developer installing Macy, I want both **Path B** (a lightweight zero-Docker, zero-database CLI/MCP package via `uvx` for instant SEO/GEO audits, bounded crawls, and draft compliance checks) and **Path A** (a zero-config containerized stack with local login, zero-key default integrations, modular capability toggles, and a guided URL setup wizard) so that I can deploy and use only the parts of Macy I need in under two minutes.

## Scope
- In:
  - **Path B (Zero-Docker Stateless CLI & MCP Package — `M7-01`):** Composable packaging extras (`macy[core]`, `macy[editorial]`, `macy[ui]`, `macy[audit-browser]`, `macy[full]`), `CapabilityPlugin` registry (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), and `--stateless` / `MACY_STATELESS=true` execution mode decoupling `audit` (single-URL + `--crawl`), `import-draft` (deterministic checks + JSON-LD schema), and `stdio` MCP (`macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`) from `engine/db.py` so `uvx --from macy[core] macy ...` runs with zero Docker and zero PostgreSQL.
  - **Path A (Zero-Config Pocket Container Runtime & Guided Onboarding — `M7-02`–`M7-04`):** `compose.pocket.yaml`, auto-bootstrapped `${MACY_DATA_DIR:-/data}/.macy_secrets.json` (`0600`), Python startup SQL migrations (`engine/migrations.py`), embedded worker supervisor (`EMBEDDED_WORKER=true` with `pg_try_advisory_lock`), built-in Local Admin Auth (`AUTH_MODE=local|oidc|auto` with PBKDF2-HMAC-SHA256 + rate limiting), zero-key default adapters (`ollama`/`openai_compat`, `gnews_rss`, `autocomplete`), workspace capability switchboard (`workspaces.config.enabled_capabilities`), dynamic UI/CLI/MCP surface filtering, and the bilingual 3-step First-Boot Setup Wizard with website URL + sitemap auto-seeding (`engine/onboarding.py`, `macy init`).
- Out:
  - Removing or weakening the multi-container `compose.prod.yaml` + Google OIDC + `dbmate` VPS production path (`ADR-012`, `ADR-013`).
  - Full SQLite rewrite of the 4,251-line multi-tenant PostgreSQL editorial repository (`engine/db.py`).

## Acceptance criteria
- **AC1 (Capability Plugin Discovery & Workspace Switchboard):** WHEN `discover_capabilities()` runs or `services.set_enabled_capabilities` updates a workspace, THEN `core-compliance`, `seo-audit`, `editorial-pipeline`, and `route-monitors` are discovered via `macy.capabilities`, `core-compliance` remains always enabled, and workspace configuration persists cleanly.
- **AC2 (Dynamic UI, CLI & MCP Surface Filtering):** WHEN a workspace disables a capability (e.g., `editorial-pipeline`), THEN `app.py` hides its sidebar screens, `engine/mcp/` omits its tools from discovery and returns `CAPABILITY_DISABLED` on direct invocation, and `Integrations` marks its ports as optional.
- **AC3 (Path B — Zero-Docker Stateless CLI & MCP Execution for Audit, Crawl & Compliance):** WHEN `macy audit <url> [--crawl] --stateless`, `macy import-draft <file> --stateless`, or `macy mcp --stateless` (`macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`) executes without `DATABASE_URL` or a running Postgres instance, THEN the operation completes all deterministic compliance, JSON-LD schema, single-URL SEO/GEO audit, and bounded BFS crawl checks with zero database queries and exits `0`.
- **AC4 (Path A — Pocket Zero-Env Secret Bootstrap & Python Startup Migrations):** WHEN the runtime starts with `POCKET_MODE=true` and no `.env` secrets, THEN `engine/bootstrap.py` creates `${MACY_DATA_DIR}/.macy_secrets.json` (`0600`) with valid Fernet and cookie keys, and `engine/migrations.py` applies all pending `db/migrations/*.sql` files idempotently without a `dbmate` container.
- **AC5 (Path A — Local Admin Auth & Automatic OIDC Switch):** WHEN `AUTH_MODE=local` (or `auto` without `GOOGLE_CLIENT_ID`), THEN users sign in with email and PBKDF2-HMAC-SHA256 password hash (rate-limited after 5 failed attempts in 15 minutes); WHEN `GOOGLE_CLIENT_ID` is configured, THEN Google OIDC sign-in remains active.
- **AC6 (Path A — Embedded Worker Supervisor):** WHEN `EMBEDDED_WORKER=true` is set on the `app` container, THEN a supervised background worker thread polls queued runs and webhook deliveries under a Postgres advisory lock (`pg_try_advisory_lock`) without requiring a separate `worker` container.
- **AC7 (Path A — Zero-Key & Local Adapters + Stack Presets):** WHEN a workspace applies the `pocket_free` or `local_ollama` preset, THEN `gnews_rss` (`NewsSearchPort`), `autocomplete` (`KeywordProviderPort`, `is_estimate=True`), and `ollama`/`openai_compat` (`LLMPort`) pass healthchecks and execute research, keyword lookup, and structured completions without OpenRouter, Brave, or DataForSEO keys.
- **AC8 (Path A — First-Boot Setup Wizard & URL Auto-Seeding):** WHEN Macy starts with zero workspaces (in UI or via `macy init --url https://example.com`), THEN the 3-step Setup Wizard creates the owner account, enables selected capabilities, applies the chosen integration preset, fetches the target URL + sitemap via `safe_http` to auto-seed `brand_profiles` (`v1`) and `content_inventory`, and lands on the active workspace dashboard.

## Data
- `workspaces.config` (`JSONB`): stores `enabled_capabilities: list[str]` (backwards-compatible default = all 4 built-in capabilities).
- `users`: non-blocking migration adding `password_hash TEXT NULL` for local auth (`AUTH_MODE=local|auto`).
- `${MACY_DATA_DIR:-/data}/.macy_secrets.json` (`0600`): stores auto-generated `APP_ENCRYPTION_KEY` and `AUTH_COOKIE_SECRET` in Pocket mode.

## Interfaces
- `engine/capabilities/{__init__,ports,registry}.py`
- `engine/bootstrap.py`, `engine/migrations.py`, `engine/auth.py`, `engine/__main__.py`
- `engine/integrations/{openai_compat,gnews_rss,autocomplete_keywords}.py`
- `engine/onboarding.py`, `engine/services.py`, `engine/cli.py`, `engine/mcp/`, `app.py`
- `compose.pocket.yaml`, `pyproject.toml`, `locales/{pt_BR,en_US}/LC_MESSAGES/messages.po`

## Tests
| Seam | Test | E2E journey |
|---|---|---|
| Capability Registry, Switchboard, Filtering & Path B Stateless CLI/MCP | `tests/test_capabilities.py`, `tests/test_stateless_cli_mcp.py`, `tests/test_mcp_capabilities.py` | Path B stateless smoke |
| Pocket Bootstrap, Python Migrations, Local Auth, Embedded Worker & Compose | `tests/test_pocket_bootstrap.py`, `tests/test_local_auth.py`, `tests/test_embedded_worker.py`, `tests/test_compose_and_docker.py` | — |
| Zero-Key Adapters & Stack Presets | `tests/test_zero_key_llm_adapters.py`, `tests/test_gnews_rss.py`, `tests/test_autocomplete_keywords.py`, `tests/test_integration_presets.py` | — |
| Onboarding, URL Auto-Seed & Bilingual Setup Wizard | `tests/test_onboarding.py`, `scripts/check_translations.py` | `tests/e2e/test_j_pocket_onboarding.py` (`J-POCKET`) |

## Dependencies
`F01`, `F03`, `F04`, `F05`, `F07`, `F08`, `F11`, `F13`, `F14`, `F18`, `F19`, `F20`, `F21`, and `M6-01`–`M6-03`.
