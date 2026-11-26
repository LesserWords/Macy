# Developer & CLI Guide

This guide covers local development, the `macy` CLI reference, quality gates, testing suites, and extending Macy (plugins, packs, prompts, translations, and migrations).

> **Related docs:**
> - Product & workflow overview → [README.md](../../README.md) · [User & Editorial Guide](user-guide.md)
> - AI client & HTTP MCP setup → [MCP Guide](../MCP.md)
> - Env variables & external API keys → [Environment Variables & External Credentials Guide](environment-and-credentials.md)
> - Database schema & design invariants → [DATA-MODEL.md](../architecture/DATA-MODEL.md)
> - VPS operations, backups, and retention → [Operations Runbook](../runbook.md)
> - Architecture decisions & OpenSpec workflow → [ADRs](../adr/README.md) · [Delivery Plan](../plans/README.md)

---

## 1. Architecture Boundaries

Macy is structured as a modular monolith enforced by `import-linter` (`pyproject.toml`):

```text
app.py (Streamlit UI) ──┐
engine/cli.py (CLI)   ──┼──► engine/services.py (membership checks, transactions, audit events)
engine/mcp/ (MCP)     ──┤          │
                        │          ▼
                        │    domain modules: research · strategy · seo · audit · crawl · monitors · production · quality · pipeline · scheduler · budget · webhooks
                        │          │  (depend on engine/contracts.py + ports.py, never vendor adapters)
                        │          ▼
                        └──► engine/db.py (all SQL, every query scoped by workspace_id)
```

Key architectural rules:
- **No SQL outside `engine/db.py`** and every tenant query requires `workspace_id` (`tests/test_tenant_scoping.py` parses every SQL query in `engine/db.py`).
- **UI, CLI & MCP import `engine.services` and `engine.contracts` only** (`lint-imports` rejects `engine.db` imports in `engine.cli` and `engine.mcp`).
- **Contracts (`engine/contracts.py`):** Typed Pydantic v2 models (`ResearchRecord`, `Opportunity`, `EditorialBrief`, `ContentDraft`, `QualityReport`, `PublishRequest`, `PublishedContent`, `BrandProfile`, `CampaignConfig`, `KeywordData`, `AuditCheckResult`, `CategoryScore`, `CrawlConfig`, `PageAuditReport`, `SiteAuditReport`), each carrying `schema_version` where persisted.
- **Outbound HTTP (`engine/safe_http.py`):** All user-configured URLs (feeds, webhooks, `http_webhook` publisher) go through SSRF validation (DNS resolution check refusing private/loopback/link-local/metadata IPs, 10 s timeout, 1 MB cap, no cross-host redirects, HTTPS required in production).

---

## 2. Environment, Auth & Compose Overlays

| File | Purpose |
|---|---|
| `infra/compose.yaml` | Base stack: `db` (`postgres:18-alpine`), `migrate` (`dbmate`), `app` (`streamlit run app.py` on `:8501`), `worker` (`python -m engine`), opt-in `mcp` (`profiles: ["mcp"]` on `:8765`) |
| `infra/compose.pocket.yaml` | Single-app Pocket stack: `db` + `app` (`POCKET_MODE=true`, `AUTO_MIGRATE=true`, `EMBEDDED_WORKER=true`, `AUTH_MODE=auto`, persistent `macy_data` `/data`, zero `.env`), plus optional `--profile local-llm` (`ollama`) |
| `infra/compose.override.yaml` | Local dev overlay: exposes `5432`, `8501`, and `127.0.0.1:8765` (for `--profile mcp`), sets `APP_ENV=development`, bind-mounts `engine/`, `app.py`, `.streamlit/` |
| `infra/compose.e2e.yaml` | CI/local offline E2E: sets `APP_ENV=test`, exposes test DB/MCP (`55432`/`8765`), adds `webhook-receiver` (`:9000`) and `fake-oidc` (`:8080`) |
| `infra/compose.prod.yaml` | Staging/prod overlay: adds `caddy` (`80`/`443` routing `/mcp*` -> `mcp:8765`) + `backup` (`pg_dump` → `/offsite`), requires `.env` secrets (`${VAR:?}`), zero raw host ports on DB/UI/MCP |

### Authentication Modes (`engine/auth.py`, `engine/passwords.py`)
- **Non-production (`APP_ENV=development` or `test`):** Set `MACY_TEST_USER_EMAIL=<email>` to authenticate as that user without Google OIDC or local password. Strictly refused with `FakeIdentityForbiddenError` if `APP_ENV` / `ENV` is `production` or `prod`.
- **Local PBKDF2 Auth (`AUTH_MODE=local`, or `auto` without `GOOGLE_CLIENT_ID`):** Email + password login verified against `users.password_hash` (`pbkdf2_sha256$600000$...`), rate-limited after 5 failed attempts in 15 minutes (`RateLimitExceededError`).
- **Google OIDC (`AUTH_MODE=oidc`, or `auto` with `GOOGLE_CLIENT_ID`):** At container startup, `python -m engine.auth --output /tmp/macy/secrets.toml` renders Streamlit `[auth]` secrets from `AUTH_COOKIE_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and optional `AUTH_REDIRECT_URI` (or derives `https://<DOMAIN>[:<CADDY_HTTPS_PORT>]/oauth2callback`).

### Running & Testing Live Locally
- **Rebuild after image changes:** After `Dockerfile`, `pyproject.toml`, or `fixtures/` changes, run `docker compose up -d --build` so running `app` and `worker` containers pick up the updated image.
- **Dev vs Prod adapter parity:** In `compose.override.yaml` (`APP_ENV=development`), configuring plugins in **Integrations** (OpenRouter, Brave, DataForSEO, Publisher) executes real vendor API calls, while unconfigured ports still fall back to `Fake*` adapters. To enforce `staging`/`production` parity (`RSSFeedSource`/`TrafilaturaExtractor` defaults + `blocked(integration_missing)` if `llm`/`news_search` is absent + Caddy TLS/Google OIDC), run the `compose.prod.yaml` overlay locally (`SKIP_PULL=1 SMOKE_INSECURE=1 sh scripts/deploy.sh`, see [Operations Runbook](../runbook.md#two-projects-on-one-host)).
- **Worker vs Scheduled Cron:** `worker` (`python -m engine`) polls queued UI `Run now` jobs and outbound webhooks every `WORKER_POLL_INTERVAL_SECONDS` (default `15 s`), whereas automatic `schedule_cron` execution and nightly `02:00 UTC` `purge` require wiring external/host cron or Supercronic from `macy crontab`.

---

## 3. Complete CLI Reference (`macy`)

Invoke via `docker compose exec app macy <command>` or locally via `uv run macy <command>` (equivalent to `python -m engine.cli`).

### Workspace, Brand & Source Bootstrap

| Command | Arguments & Flags | Description |
|---|---|---|
| `init` | `[--email] [--password] [--slug] [--name] [--capabilities] [--preset] [--site-url] [--non-interactive] [--json]` | One-command first-boot provisioning: creates owner, workspace, capabilities, preset (`pocket_free`/`local_ollama`/`pro_cloud`), and optional URL auto-seed. |
| `create-workspace` | `<slug> --name <name> [--pack general] [--timezone America/Sao_Paulo]` | Validates slug/pack/IANA timezone and provisions a `workspaces` row. |
| `add-owner` | `<workspace> <email>` | Creates/fetches user by email, assigns `owner` membership, emits `membership.created`. *(Workspace slug must already exist via `create-workspace`.)* |
| `seed-brand` | `<workspace> [--fixture <path>]` | Seeds `BrandProfile` from YAML (defaults to `fixtures/seed_example.yaml`; see [Brand Fixtures Guide](brand-fixtures-guide.md)). Requires an existing workspace owner. |
| `seed-sources` | `[--pack <slug>]` | Loads shared sources (`workspace_id IS NULL`) from `packs/industries/<pack>/sources.yaml` (default: `general`). |
| `verify-sources` | `[--pack <slug>] [--days 30] [--offline]` | Probes all pack sources over the lookback window and prints `records_found`, `dated_pct`, and status (`ok` / `disabled` / `no_records` / `error`). |
| `import-sitemap` | `<url> [--workspace <slug-or-id>] [--fetch-titles]` | Idempotently imports existing blog URLs from a sitemap XML/index (or local file) into `publications` (`mode='live'`) so reports flag already-covered topics. |
| `import-draft` | `--file <path.md\|.pdf> [--workspace <slug-or-id>] [--format blog\|blog-short\|blog-long] [--brief-id <uuid>] [--topic <str>] [--primary-keyword <str>] [--deterministic-only] [--stateless] [--json]` | Parses Markdown/frontmatter or PDF into a `ContentDraft` and runs compliance evaluation (`--stateless` runs zero-DB Path B check + JSON-LD generation). |
| `adapt-draft` | `--file <path.md\|.pdf> [--markdown <text>] [--workspace <slug-or-id>] [--brand-fixture <path>] [--industry-pack <slug>] [--format blog\|blog-short\|blog-long] [--max-similarity 0.65] [--deterministic-only] [--offline] [--stateless] [--output <out.md>] [--json]` | Adapts a draft by value to a target brand/persona with `check_adaptation_uniqueness` (`max_similarity <= 0.65`), staging at Gate 2 in DB mode or running zero-DB in `--stateless` mode (`ADR-034`). |
| `audit` | `<url> [--crawl] [--max-pages <n>] [--plan free\|paid] [--stateless] [--json]` | Runs a 7-category single-URL SEO+GEO audit or a bounded BFS site crawl (`free` `20`, `paid` `50`, hard cap `200`; `--stateless` runs with zero DB). |

### Pipeline Runs, Scheduling & Webhooks

| Command | Arguments & Flags | Description |
|---|---|---|
| `run-week` | `[<workspace> \| --workspace <slug-or-id>] [--campaign <name-or-id>] [--user-id <uuid>] [--offline] [--window-days 7]` | Triggers a run, executes research + scoring synchronously, and compiles the weekly report on completion. In live/production mode (`offline=False` or `APP_ENV=staging\|production`), missing `llm`/`news_search` integrations block cleanly (`blocked_reason='integration_missing'`). |
| `run-now` | `<workspace> [--campaign <name-or-id>] [--user-id <uuid>] [--offline]` | Queues a manual run (`run_now`) for the background worker loop (`python -m engine --worker` / `KEEP_ALIVE=true`). Passing `--offline` executes the queued run immediately with fake adapters. |
| `run-stage` | `<run-or-record-uuid> [--stage <stage>] [--offline]` | Re-runs failed items for a `research_record` UUID or resumes/re-runs a `blocked`/`failed` run stage (`research`, `score`) cleanly with configured live adapters (pass `--offline` to force fakes). |
| `crontab` | `[--workspace <slug-or-id>]` | Generates a Supercronic crontab emitting `CRON_TZ=<workspace-tz> <schedule_cron> macy run-week --workspace ...` plus daily `02:00 UTC` `macy purge`. |
| `webhooks-deliver` | `[--batch-size 10]` | Dispatches due `pending` / retrying `webhook_deliveries` rows using Standard Webhooks HMAC-SHA256 signing and `safe_http`. |

### Integrations, Encryption & Observability

| Command | Arguments & Flags | Description |
|---|---|---|
| `plugins list` | — | Lists all discovered entry-point plugins across the 7 ports. |
| `capabilities` | `{list,enable,disable} <slug> [--workspace-id \| --workspace] [--user-id]` | Lists or toggles workspace capability plugins (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`). |
| `integrations set` | `--workspace-id <uuid> --port <port> --plugin <name> --user-id <uuid> [--settings '<json>'] [--secrets '<json>']` | Configures a workspace integration programmatically and encrypts secrets via Fernet. |
| `integrations preset` | `{list,apply} [<preset_name>] [--workspace-id <uuid>] [--user-id <uuid>] [--json]` | Lists or atomically applies `pocket_free`, `local_ollama`, or `pro_cloud` stack presets. |
| `rotate-key` | `--current-keys <k1,k2> --new-key <k_new> [--workspace-id <uuid>]` | Re-encrypts `workspace_integrations.secrets` and `webhook_subscriptions.secret` with `new_key` using `MultiFernet`. |
| `test-sentry` | — | Sends and flushes a synthetic test exception via `SENTRY_DSN` to verify Sentry connectivity and scrubbing (`F20 AC5`). |
| `mcp` | `[--transport stdio\|streamable-http] [--host 127.0.0.1] [--port 8765] [--mock] [--allow-owner-fallback] [--stateless] [--check]` | Starts the Macy MCP server (see [MCP Guide](../MCP.md)). `--stateless` runs zero-DB Path B `stdio` mode. `--mock` and `--allow-owner-fallback` are forbidden in `staging`/`production`/`prod`. `--check` validates config without starting a listener. |

### Data Retention & LGPD Privacy (`ADR-024`)

| Command | Arguments & Flags | Description |
|---|---|---|
| `purge` | `[--now <iso-utc>] [--workspace-id <uuid>]` | Enforces retention under the least-privilege `retention` role (`RETENTION_DATABASE_URL` / `DATABASE_URL`): deletes `llm_payloads` > 90d, nulls `documents.extracted_text` > 365d, deletes `delivered`/`dead` `webhook_deliveries` > 30d, deletes `events` > 24mo. |
| `anonymise-user` | `<email>` | LGPD user deletion: replaces email with `deleted-<user_id>@anonymised.invalid`, clears `name`, deletes `memberships`, and scrubs PII keys from `events.payload` while keeping opaque `actor_user_id`. |

---

## 4. Quality Gates & Testing

### Full Gate Runner (`scripts/check.sh`)
Run all CI quality gates locally before opening a PR:

```sh
sh scripts/check.sh
```

`scripts/check.sh` enforces:
1. **Lint & Format:** `uv run ruff check .` and `uv run ruff format --check .` (includes McCabe `C901 ≤ 10`, Pylint complexity/branch/arg limits, Bandit `S` rules).
2. **Strict Typing:** `uv run mypy` (`strict = true` across `app.py`, `engine`, `evals`, `scripts`, `spikes`, `tests`).
3. **Cognitive Complexity:** `uv run complexipy engine --max-complexity-allowed 15`.
4. **Dead Code:** `uv run vulture` (against `vulture_whitelist.py`).
5. **Architecture Boundaries:** `uv run lint-imports` (no cycles in `engine`; `engine.cli` cannot import `engine.db`).
6. **Duplication:** `npx --yes jscpd@4.0.5 .`.
7. **Migration Safety:** `npx --yes squawk-cli --no-error-on-unmatched-pattern db/migrations/*.sql`.
8. **Unit, Contract & Offline E2E Tests:** `uv run pytest` (default `addopts` excludes `-m 'eval'` and `-m 'live_e2e'`, requires `≥ 90%` branch coverage on `engine`).
9. **Diff Coverage:** `uv run diff-cover coverage.xml --fail-under=100` against `origin/dev` / `origin/main`.
10. **Hotspots & OpenSpec:** `scripts/hotspots.py` and `npx --yes @fission-ai/openspec@1.16.0 validate --all --strict --no-interactive`.

### Running Specific Test Suites

- **Fast offline suite (requires running Postgres `db` + `migrate`):**
  ```sh
  docker compose up -d db
  docker compose run --rm migrate up
  uv run pytest
  ```
- **Translation gate (0 untranslated / 0 fuzzy in `pt_BR` & `en_US`):**
  ```sh
  uv run python scripts/check_translations.py
  ```
- **Offline E2E stack (`infra/compose.e2e.yaml`):**
  ```sh
  docker compose -f infra/compose.yaml -f infra/compose.e2e.yaml --profile mcp up -d --wait
  uv run pytest tests/test_journey_*.py
  ```
- **Live E2E Journeys J0–J8 (Playwright against running app, budget-capped by `BudgetGuard`):**
  ```sh
  uv run playwright install chromium
  STAGING_APP_URL=http://localhost:8501 REQUIRE_LIVE_SERVER=true uv run pytest tests/test_live_e2e_j0_j4.py -m live_e2e --tracing retain-on-failure --screenshot only-on-failure
  ```
- **LLM Regression Evals (Cases 01–07 in `evals/cases/`, costs OpenRouter credits when `OPENROUTER_API_KEY` is set):**
  ```sh
  uv run pytest evals/test_regression.py -m eval
  ```

---

## 5. Extending Macy

### Adding a New Integration Plugin (`ADR-005`, `ADR-008`, `ADR-028`)
1. Implement the matching `Protocol` in [engine/integrations/ports.py](../../engine/integrations/ports.py) (`LLMPort`, `FeedSourcePort`, `NewsSearchPort`, `ExtractorPort`, `KeywordProviderPort`, `PublisherPort`, `AnalyticsSourcePort`, `BrowserAuditPort`, `PageSpeedPort`, or `RankTrackerPort`).
2. Define a nested `class Settings(BaseModel)` with `model_config = ConfigDict(extra="forbid")`. Mark sensitive credentials as `SecretStr` so the Admin UI renders password inputs and encrypts them automatically.
3. Implement `healthcheck(self) -> None` (called by **Test Connection** in the UI) and declare `SUPPORTED_LOCALES: tuple[str, ...]` (raising `UnsupportedLocale` if a requested locale cannot be served).
4. Register entry-point plugins under their `[project.entry-points."macy.<port>"]` group in [pyproject.toml](../../pyproject.toml) (e.g., `gsc = "engine.integrations.gsc:GSCAnalyticsSource"` under `macy.analytics_source`). *(Breaking change in `M9` / [ADR-033](../adr/ADR-033-macy-package-cli-and-tooling-identity.md): legacy pre-M9 plugin group names are no longer loaded.)*
5. Optional heavy extras in [pyproject.toml](../../pyproject.toml) (`uv sync --extra agent` for `scripts/compliance_agent.py`, `uv sync --extra audit-browser` for Playwright + `axe-core`) keep the default 4 GB VPS runtime lean.
6. Parametrize and pass the shared contract suite in [tests/test_contract_suites.py](../../tests/test_contract_suites.py). Zero edits to core pipeline modules are needed.

### Editing Config Packs & Prompts
- **Industry packs (`packs/industries/<slug>/`):** Loaded by `engine.config.load_industry_pack()` across the 3-layer overlay search path (`MACY_CONTEXT_DIR` → Pocket `/data/context` → built-in `packs/`). Built-in OSS packs include `general/` (default) and `b2b-saas/` (`profile.yaml`, `sources.yaml`, `audience.md`, `challenges.md`, `exclusions.md`, `terminology.md`). Place custom or proprietary packs and brand fixtures in git-ignored `./context/` without modifying tracked files.
- **Format packs (`packs/formats/<slug>/`):** Loaded by `engine.config.load_format_pack()`. Includes `blog/` (`800–2500` words), `blog-short/` (`400–800`), and `blog-long/` (`1800–3500`), each defining `format.yaml` and `rubric.yaml`.
- **Prompts (`prompts/*.md`):** `extract_research.md`, `score.md`, `brief.md`, `write_blog.md`, `rewrite_draft.md`, `evaluate.md`. Each prompt is SHA-256 hashed at load time so `usage_ledger` and `opportunities`/`briefs`/`drafts` track `prompt_version`.
- Whenever modifying `packs/` or `prompts/`, run the regression suite (`pytest -m eval`) and inspect `evals/cases/case_01.yaml`–`case_07.yaml`.

### Updating UI Translations (`F19`, `ADR-014`)
1. Wrap every user-visible string in `app.py` / `engine/auth.py` with `_("...")` from `engine.i18n`.
2. Extract, update, and compile Babel catalogs:
   ```sh
   uv run pybabel extract -F babel.cfg -o locales/messages.pot .
   uv run pybabel update -i locales/messages.pot -d locales -l pt_BR
   uv run pybabel update -i locales/messages.pot -d locales -l en_US
   # Fill translations in locales/{pt_BR,en_US}/LC_MESSAGES/messages.po (no fuzzy flags)
   uv run pybabel compile -d locales
   ```
3. Verify with `uv run python scripts/check_translations.py`.

### Adding Database Migrations (`ADR-010`, `ADR-011`)
1. Create a forward/backward migration file in `db/migrations/` (using `dbmate` `-- migrate:up` / `-- migrate:down`).
2. Preserve multi-workspace invariants: `workspace_id NOT NULL`, composite foreign keys `(workspace_id, id)`, exact `numeric(12,6)` for money, guarded status transitions, and non-blocking DDL.
3. Verify with `npx --yes squawk-cli db/migrations/*.sql`, update committed `db/schema.sql`, and cover constraints in `tests/test_schema_constraints.py` and tenant filters in `tests/test_tenant_scoping.py`.
