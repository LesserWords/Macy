# Proposal: M7-02 — Path A: Macy Pocket Single-Container Runtime, Embedded Worker & Local Auth

**Plan Item:** M7-02 (Planned) · **Features:** F01, F18, F20, F22 · **ADRs:** ADR-011, ADR-012, ADR-013, ADR-030

## Why

Alongside Path B (`M7-01` stateless CLI/MCP), users who want the full persistent Admin UI, run history, and background scheduling currently face a steep operational barrier (`compose.yaml` + `compose.prod.yaml`):
1. `scripts/deploy.sh` requires **11 mandatory `.env` variables** (including manually generated Fernet keys and 3 separate Postgres passwords).
2. A separate `amacneil/dbmate` container and external `worker` container duplicate deployment complexity and memory usage.
3. **Google Cloud OAuth (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`)** is mandatory in staging/prod, blocking solo self-hosters and local evaluators who just want to log in with an email and password.

## What Changes

- **Zero-`.env` Persistent Secret Bootstrap (`engine/bootstrap.py`):**
  - When `POCKET_MODE=true`, auto-generate and persist `APP_ENCRYPTION_KEY` (Fernet) and `AUTH_COOKIE_SECRET` (64-hex) into `${MACY_DATA_DIR:-/data}/.macy_secrets.json` (`0600`) if unset; reload identically across restarts. Leave `compose.prod.yaml` strict fail-fast behavior intact when `POCKET_MODE` is false/unset.
- **Idempotent Python Startup Migrations (`engine/migrations.py`):**
  - Parse `-- migrate:up` SQL blocks from `db/migrations/*.sql` and apply unapplied versions into `schema_migrations` using `psycopg` when `AUTO_MIGRATE=true`, removing the mandatory `dbmate` container in Pocket mode.
- **Built-In Local Admin Auth (`db/migrations/`, `engine/auth.py`, `engine/services.py`, `app.py`):**
  - Non-blocking migration adding `password_hash TEXT NULL` to `users`.
  - Support `AUTH_MODE=local | oidc | auto` (`auto` uses local PBKDF2-HMAC-SHA256 password login when `GOOGLE_CLIENT_ID` is unset and Google OIDC when set) with constant-time verification and brute-force rate limiting (`5` failed attempts per `15m`).
- **Embedded Worker Supervisor (`engine/__main__.py`, `app.py`):**
  - When `EMBEDDED_WORKER=true`, run a background daemon thread inside `app` executing `run_worker_tick()` under a PostgreSQL session advisory lock (`pg_try_advisory_lock`) so no separate `worker` container is required in Pocket mode.
- **Pocket Compose Overlay (`compose.pocket.yaml`):**
  - Provide a zero-`.env` `compose.pocket.yaml` starting `db` + `macy` (with persistent `macy_data:/data` volume, `POCKET_MODE=true`, `AUTO_MIGRATE=true`, `EMBEDDED_WORKER=true`, `AUTH_MODE=auto`) in one `docker compose -f compose.pocket.yaml up -d` command.

## Capabilities

### New Capabilities
- None (extends `operations` and `access`).

### Modified Capabilities
- `operations`: Pocket single-container runtime (`compose.pocket.yaml`), auto-bootstrapped secrets, Python startup SQL migrations, and advisory-locked embedded worker supervisor.
- `access`: Built-in PBKDF2-HMAC-SHA256 local password authentication (`AUTH_MODE=local|oidc|auto`) with brute-force rate limiting and strict production protection against `MACY_TEST_USER_EMAIL`.

## Out of Scope

- Replacing PostgreSQL with SQLite inside `engine/db.py` (stateless zero-DB mode is delivered by Path B in `M7-01`).
- Altering `compose.prod.yaml`, `scripts/deploy.sh`, or multi-container VPS production hardening (`M5-01a`).
- Zero-key LLM/search/keyword adapters (`M7-03`) and the 3-step Setup Wizard (`M7-04`).

## Key Results: Delivered

All `D1`–`D4` must pass before `M7-02` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `engine/bootstrap.py` auto-generates and persists `APP_ENCRYPTION_KEY` + `AUTH_COOKIE_SECRET` (`0600`) when `POCKET_MODE=true` (reused across restarts), and `engine/migrations.py` applies pending `db/migrations/*.sql` idempotently when `AUTO_MIGRATE=true` | `uv run pytest tests/test_pocket_bootstrap.py --no-cov` exits `0` |
| **D2** | `users.password_hash` migration passes `squawk-cli`; PBKDF2-HMAC-SHA256 local auth enforces `5`/15m rate limiting; `AUTH_MODE=auto` routes cleanly between local password login and Google OIDC while `MACY_TEST_USER_EMAIL` remains forbidden in prod | `npx --yes squawk-cli db/migrations/*.sql && uv run pytest tests/test_local_auth.py --no-cov` exits `0` |
| **D3** | `start_embedded_worker()` runs `run_worker_tick()` in a single daemon thread guarded by PostgreSQL `pg_try_advisory_lock`, captures errors via Sentry/observability, and survives repeated Streamlit reruns | `uv run pytest tests/test_embedded_worker.py --no-cov` exits `0` |
| **D4** | `compose.pocket.yaml` renders cleanly with zero `.env` file (`docker compose -f compose.pocket.yaml config -q`), preserves `compose.prod.yaml` hardening, and passes all repo gates | `docker compose -f compose.pocket.yaml config -q && uv run pytest tests/test_compose_and_docker.py --no-cov && sh scripts/check.sh` exits `0` |

## Impact

- **Code:** `engine/bootstrap.py`, `engine/migrations.py`, `engine/auth.py`, `engine/__main__.py`, `engine/db.py`, `engine/services.py`, `app.py`
- **Infra & Migrations:** `compose.pocket.yaml`, `db/migrations/*_add_users_password_hash.sql`, `db/schema.sql`, `.github/workflows/ci.yml`
- **Tests:** `tests/test_pocket_bootstrap.py`, `tests/test_local_auth.py`, `tests/test_embedded_worker.py`, `tests/test_compose_and_docker.py`
