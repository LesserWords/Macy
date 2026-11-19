# Environment Variables & External Credentials Guide

This guide explains **every environment variable, external API credential, monitoring endpoint, and GitHub CI/CD secret** used by **Macy**, and exactly **where to generate or obtain each value**.

> **Related docs:**
> - Admin UI setup & workflow → [User & Editorial Guide](user-guide.md)
> - CLI reference & local testing → [Developer & CLI Guide](developer-guide.md)
> - AI client & HTTP MCP setup → [MCP Guide](../MCP.md)
> - VPS deployment, cron & backups → [Operations Runbook](../runbook.md)

---

## 1. Where Configuration Lives (`.env` vs Admin UI vs GitHub)

Macy splits configuration across three places by design:

1. **Host env file (`.env` on VPS, `.env.staging` / `.env.prod` locally, never committed):** Database passwords, `APP_ENCRYPTION_KEY`, Google OIDC credentials, Caddy public domain/ports, backup settings, Sentry/heartbeat/alert URLs, and optional API fallbacks.
2. **Encrypted Postgres DB (**Admin UI → Integrations**):** Per-workspace plugin choices and vendor credentials (`openrouter`, `brave`, `dataforseo`, `publisher`, `rss`, `trafilatura`), encrypted at rest with `APP_ENCRYPTION_KEY`.
   - **Important:** Setting `OPENROUTER_API_KEY`, `BRAVE_API_KEY`, or `DATAFORSEO_*` in `.env` alone does **not** activate those plugins in pipeline runs—you must still save the integration in **Integrations** (or `macy integrations set`). Those env vars act only as fallbacks when a saved integration omits the secret, or for CLI/test utilities (`pytest -m eval`, `scripts/spike_openrouter.py`).
3. **GitHub Repository Variables & Environment Secrets:** Used only by `.github/workflows/deploy.yml` and `.github/workflows/e2e-live.yml` to build images, SSH onto the VPS, and run post-deploy smoke/E2E tests.

---

## 2. Environment Identity, Public Routing & Registry

Used in `.env.staging`, `.env.prod`, `infra/compose.prod.yaml`, and `infra/Caddyfile`.

| Variable | Purpose | Where to Get / What to Set |
|---|---|---|
| `APP_ENV` | Controls strict prod rules: refuses `MACY_TEST_USER_EMAIL`, requires HTTPS in `safe_http`, forces `mode="draft"` in `staging`, and blocks missing `llm`/`news_search` plugins (`integration_missing`). | Set `development` (local dev), `test` (offline E2E), `staging`, or `production`. |
| `COMPOSE_PROJECT_NAME` | Isolates Docker containers, networks, and named volumes (`<project>_db_data`). | Set `macy-staging` or `macy-prod` so staging and prod never collide on one host. |
| `DOMAIN` | Hostname Caddy serves and requests a Let's Encrypt TLS certificate for. | Point an **A / AAAA** record at your DNS provider (Cloudflare, Route 53, Registro.br, etc.) to your VPS public IP (e.g., `staging.yourdomain.com`, `macy.yourdomain.com`). Use `localhost` for local testing. |
| `CADDY_HTTP_PORT`, `CADDY_HTTPS_PORT` | Host ports bound to Caddy (`80`/`443` for standard VPS, or `8080`/`8443` when running staging + prod side by side or on localhost). | Choose open host ports; standard VPS deployments use `80` and `443`. |
| `APP_IMAGE`, `APP_TAG` | Container image pulled by `scripts/deploy.sh` from GitHub Container Registry (**GHCR**). | GitHub repo → **Packages**. Format must be **lowercase**: `ghcr.io/<github-owner-lowercase>/macy`. `APP_TAG` is `latest`, `prod`, `<git-sha>`, or release tag (`v0.1.0`). |
| `SMOKE_URL` | Health endpoint probed over HTTPS at the end of `scripts/deploy.sh`. | Set `https://<DOMAIN>/_stcore/health` (or `https://localhost:8443/_stcore/health` for local prod testing). |

---

## 3. PostgreSQL & Encryption Keys (Generated Locally)

Never sourced from a third-party service—generate distinct random values per environment and never commit them.

| Variable | Purpose | How to Generate |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER` | Database name (`macy` or `macy_prod`) and schema-owner PostgreSQL role (`owner`). | Keep template defaults unless customizing database naming. |
| `POSTGRES_PASSWORD` | Password for the `owner` role (migrations and `pg_dump` backups). | Run `openssl rand -hex 32` (or Python `secrets.token_hex(32)`). Avoid URL-reserved characters (`@`, `:`, `/`, `?`, `#`) or use hex strings. |
| `APP_DB_PASSWORD` | Password for the least-privilege `app` role (refused if left as `app_secret`). Applied by `scripts/deploy.sh` after migrations. | Generate a distinct 64-char hex string (`openssl rand -hex 32`). |
| `RETENTION_DB_PASSWORD` | Password for the least-privilege `retention` role used by nightly `macy purge` (refused in prod if left as `retention_secret`). | Generate a distinct 64-char hex string (`openssl rand -hex 32`). |
| `APP_ENCRYPTION_KEY` | 32-byte URL-safe base64 **Fernet** key encrypting `workspace_integrations.secrets` and `webhook_subscriptions.secret` (supports comma-separated `<new>,<old>` during rotation). | Run `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. **Back this up in your password vault separately from DB dumps.** |
| `DATABASE_URL`, `RETENTION_DATABASE_URL` | Full PostgreSQL DSNs built automatically inside Compose from the passwords above. | Set directly only when running CLI commands, migrations, or `pytest` outside Compose on your host. |

---

## 4. Google OIDC Authentication (Google Cloud Console)

Required for `APP_ENV=staging` and `production` (`engine/auth.py` renders `/tmp/macy/secrets.toml` on startup).

| Variable | Purpose | Where to Get / How to Configure |
|---|---|---|
| `AUTH_COOKIE_SECRET` | Signs Streamlit's encrypted session cookie (`st.login`). | Generate locally with `openssl rand -hex 32`. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 Web Client credentials used by `st.login("google")`. | See step-by-step Google Cloud Console instructions below. |
| `AUTH_REDIRECT_URI` | Optional explicit callback URL override. | Leave **empty** to auto-derive `https://<DOMAIN>[:<CADDY_HTTPS_PORT>]/oauth2callback`. |
| `GOOGLE_SERVER_METADATA_URL` | Google OIDC discovery endpoint. | Keep default `https://accounts.google.com/.well-known/openid-configuration`. |
| `MACY_TEST_USER_EMAIL` | Local dev/test bypass email (`development` / `test` only). | Set only in local dev; **never** set in staging or production (`FakeIdentityForbiddenError` refuses it). |
| `MACY_STREAMLIT_SECRETS_PATH` | Optional override output path for generated `secrets.toml`. | Defaults cleanly to `/tmp/macy/secrets.toml`. |

### Step-by-step: Creating `GOOGLE_CLIENT_ID` & `GOOGLE_CLIENT_SECRET`
1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a project.
2. Open **APIs & Services → OAuth consent screen** (or **Google Auth Platform → Branding / Audience**):
   - Choose **Internal** (if all users share your Google Workspace domain) or **External** (add your operator/editor emails under **Test users** while in Testing mode, or publish the app).
   - Ensure non-sensitive scopes include `openid`, `email`, and `profile`.
3. Open **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
4. Select **Application type: Web application**.
5. Under **Authorised redirect URIs**, add the exact callback URL for each environment:
   - Production VPS (`443`): `https://macy.<yourdomain>/oauth2callback`
   - Staging VPS (`443`): `https://staging.<yourdomain>/oauth2callback`
   - Local prod/Caddy test (`8443`): `https://localhost:8443/oauth2callback`
6. Click **Create** and copy **Client ID** and **Client Secret** into `.env` (`.env.staging` / `.env.prod`). Remember every signing user must also exist via `macy add-owner <workspace-slug> <google-email>`.

---

## 5. External AI, Search, SEO & Publisher Credentials

Configure these inside the Admin UI (**Integrations**) once logged in; optional env vars below serve as defaults or test-runner inputs.

| Service / Port | Env Var (Fallback / Evals) | Where to Obtain Credentials |
|---|---|---|
| **OpenRouter** (`llm` port → `openrouter`) | `OPENROUTER_API_KEY` *(also used directly by `pytest -m eval` & `scripts/spike_openrouter.py`)* | Sign in at [OpenRouter](https://openrouter.ai/) → **Keys** (`openrouter.ai/keys`) → **Create Key** (recommended: set a spending limit and configure privacy/data retention matching your chosen `data_policy`: `deny` or `allow`). |
| **Brave Search API** (`news_search` port → `brave`) | `BRAVE_API_KEY` | Sign in at [Brave Search API Dashboard](https://api-dashboard.search.brave.com/) → subscribe to a Search plan → **API Keys → Add API key** (copy the `X-Subscription-Token`). |
| **DataForSEO** (`keyword_provider` port → `dataforseo`) | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | Sign in at [DataForSEO](https://app.dataforseo.com/) → **API Access** (`app.dataforseo.com/api-access`) → copy **API Login** (account email) and the generated **API Password** *(not your portal login password)*. Used for Google Ads search volume (`/v3/keywords_data/google_ads/search_volume/live`). |
| **Universal OpenAI / Ollama** (`llm` port → `openai_compat`, `ollama`) | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Zero-key local `ollama` defaults to `http://localhost:11434/v1` (`llama3.1:8b`); `openai_compat` connects any OpenAI-compatible provider (OpenAI, Groq, Together, LM Studio, vLLM). |
| **Google News RSS / Autocomplete** (`news_search` → `gnews_rss`, `keyword_provider` → `autocomplete`) | None required | Zero-key built-in defaults for `pocket_free` and `local_ollama` stack presets. |
| **n8n / CMS Webhook** (`publisher` port → `http_webhook`) | Configured in UI (`endpoint_url`, `signing_secret`) | In **n8n**, create a **Webhook** trigger node set to **Respond: Using 'Respond to Webhook' Node** and copy its Production URL (`https://...`); generate `signing_secret` with `python -c "import os, base64; print('whsec_' + base64.b64encode(os.urandom(32)).decode())"` and put the same value in n8n (`MACY_PUBLISH_SECRET`). See [n8n CMS Publishing Guide](n8n-publish.md). |
| **RSS / Trafilatura** (`feed_source`, `extractor`) | None required | Built-in open-web plugins; configure optional `user_agent` or `timeout_seconds` in UI **Integrations**. |

---

## 6. Monitoring, Heartbeats & Backup Storage

Configured in `.env` on staging/prod VPS deployments (`ADR-023`, `F20 AC4–AC6`).

| Variable | Purpose | Where to Get / How to Configure |
|---|---|---|
| `SENTRY_DSN` | Sends runtime UI, CLI, and worker exceptions (scrubbed of secrets and tagged with `workspace_id`/`run_id`) to Sentry. Verify with `macy test-sentry`. | Sign in at [Sentry](https://sentry.io/) → **Projects → Create Project → Python** → **Settings → Client Keys (DSN)** → copy the HTTPS DSN (`https://<key>@o<org>.ingest.sentry.io/<id>`). |
| `HEARTBEAT_URL` | Dead-man's-switch URL pinged on `run.completed` (`GET <url>`) and on `run.failed`/`blocked` (`GET <url>/fail`). Production enforces HTTPS. | Create a **7-day period** (+ 1 day grace) check on [Healthchecks](https://healthchecks.io/), **Better Stack Heartbeats**, or a self-hosted **Uptime Kuma** Push monitor and paste its Ping URL without a trailing `/`. |
| `BACKUP_HEARTBEAT_URL` | Daily check pinged by `scripts/backup.sh` on verified dump (`GET <url>`) or failure (`GET <url>/fail`). | Create a **1-day period** (+ 2 hour grace) check in Healthchecks / Better Stack / Uptime Kuma and paste its Ping URL. |
| `BACKUP_ALERT_URL` | Webhook receiving a JSON `POST` (`{"service":"backup","db":...,"error":...}`) whenever `scripts/backup.sh` fails. | Create an **Incoming Webhook** in **Slack** (`api.slack.com/apps`), **Discord** (*Server Settings → Integrations → Webhooks*), or an **n8n** Webhook trigger URL. |
| `OFFSITE_BACKUP_PATH`, `OFFSITE_SYNC_CMD` | Host directory bound to `/offsite` inside `backup` container, plus optional post-copy sync command (e.g., `rclone`). | Point `OFFSITE_BACKUP_PATH` to a separate mounted disk / SSHFS / S3 mount, or set `OFFSITE_SYNC_CMD` (e.g., `rclone copy "$OFFSITE_FILE" remote:macy-backups/`) using an `rclone` remote/credentials configured on the host. |
| `BACKUP_RETENTION_DAYS`, `BACKUP_INTERVAL_SECONDS` | Local + offsite dump retention (`14` days) and backup sleep interval (`86400` s = 24 h). | Keep defaults in production. |

---

## 7. GitHub Actions CI/CD & E2E Secrets/Variables

Set under **GitHub Repo → Settings → Secrets and variables → Actions** (repository **Variables** tab or **Environments**: `staging`, `production`).

| Name | GitHub Scope | Purpose & Where to Obtain |
|---|---|---|
| `STAGING_DEPLOY_ENABLED`, `PROD_DEPLOY_ENABLED`, `LIVE_E2E_ENABLED` | Repository Variables | Set to `"true"` to enable `deploy.yml` staging/prod jobs and nightly `e2e-live.yml` runs cleanly once infra is ready. |
| `STAGING_SSH_HOST`, `PROD_SSH_HOST` | Environment Secrets (`staging` / `production`) | Public IPv4/IPv6 or DNS hostname of your VPS from your cloud provider dashboard. |
| `STAGING_SSH_USER`, `PROD_SSH_USER` | Environment Secrets | Linux deploy user (e.g., `deploy`) belonging to the `docker` group on the VPS. |
| `STAGING_SSH_PORT`, `PROD_SSH_PORT` | Environment Variables (optional) | SSH daemon port if not `22`. |
| `STAGING_SSH_KEY`, `PROD_SSH_KEY` | Environment Secrets | Private SSH key generated locally (`ssh-keygen -t ed25519 -f macy_deploy`); add the matching `.pub` key to `/home/deploy/.ssh/authorized_keys` on the VPS. |
| `STAGING_SSH_KNOWN_HOSTS`, `PROD_SSH_KNOWN_HOSTS` | Environment Secrets | Run `ssh-keyscan -t ed25519 <vps-host>` locally and verify the SSH fingerprint against your VPS provider's console before pasting. |
| `STAGING_APP_URL`, `PROD_APP_URL` | Variable or Environment Variable/Secret | Base HTTPS URL of the deployed app (e.g., `https://macy.<yourdomain>`) used by `e2e-live.yml` and `scripts/post_deploy_smoke.py`. |
| `PLAYWRIGHT_STORAGE_STATE`, `PROD_PLAYWRIGHT_STORAGE_STATE` | Environment / Repo Secret (optional for prod J1 auth smoke) | Authenticated browser session JSON from logging into Google OIDC once (`uv run playwright open --save-storage=auth.json https://<DOMAIN>` → paste JSON file contents). Re-capture when the session cookie expires. *(Use `PLAYWRIGHT_STORAGE_STATE_PATH` when running `pytest -m live_e2e` from a local file.)* |
| `REQUIRE_LIVE_SERVER` | Runtime Env (`"true"` in `e2e-live.yml`) | Causes `tests/test_live_e2e_j0_j4.py` to fail fast instead of skipping if `STAGING_APP_URL` is unreachable. |

---

## 8. Worker, Test & Local Rehearsal Flags (Internal)

| Variable | Default / Accepted Values | Where Used |
|---|---|---|
| `KEEP_ALIVE`, `WORKER_POLL_INTERVAL_SECONDS` | `"true"`, `15.0` | `engine.__main__`: keeps Compose `worker` polling queued runs and webhooks every `15 s`. |
| `SKIP_PULL`, `SMOKE_INSECURE` | `0` (`1` for local rehearsal) | `scripts/deploy.sh`: uses locally built image tags instead of pulling from GHCR, and allows Caddy's self-signed localhost TLS cert. |
| `ALLOW_PLACEHOLDER_SECRETS`, `SKIP_ENV_MODE_CHECK`, `ENV_FILE` | `0`, `0`, `.env` | `scripts/deploy.sh`: local/CI preflight overrides only (never use placeholder secrets on a real host). |
| `HYPOTHESIS_PROFILE`, `DIFF_COVER_BASE` | `ci`, `origin/dev` | `tests/conftest.py` / `scripts/check.sh`: deterministic CI property-test profile and base branch for `100%` diff coverage. |

---

## 9. Pocket Mode, Local Auth & Stateless Path B (`M7`, `ADR-029`–`ADR-031`)

Used automatically by `infra/compose.pocket.yaml` or `--stateless` CLI/MCP invocations:

| Variable | Default / Accepted Values | Purpose |
|---|---|---|
| `POCKET_MODE` | `"false"` (`"true"` in `infra/compose.pocket.yaml`) | Enables persistent `.macy_secrets.json` generation inside `MACY_DATA_DIR` (`APP_ENCRYPTION_KEY`, `AUTH_COOKIE_SECRET`). |
| `AUTH_MODE` | `"auto"` / `"local"` / `"oidc"` | Selects UI authentication: `auto` uses Google OIDC if `GOOGLE_CLIENT_ID` is set, otherwise PBKDF2 local login. |
| `AUTO_MIGRATE` | `"false"` (`"true"` in `infra/compose.pocket.yaml`) | Runs Python SQL migrations from `db/migrations/` on app startup (`engine/migrations.py`). |
| `MIGRATION_DATABASE_URL` | Optional owner DSN | Database URL used by startup migrations when distinct from `DATABASE_URL`. |
| `EMBEDDED_WORKER` | `"false"` (`"true"` in `infra/compose.pocket.yaml`) | Runs the advisory-locked background worker supervisor thread inside the UI container. |
| `MACY_DATA_DIR` | `/data` | Directory holding `.macy_secrets.json` (`0600`). |
| `MACY_STATELESS` | `"false"` (`"true"` or `--stateless`) | Enables zero-DB Path B CLI/MCP compliance & audit execution. |
