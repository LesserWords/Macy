# ADR-030: Macy Pocket Single-Container Runtime, Embedded Worker, Python Migrations & Local Auth

**Status:** Accepted · **Date:** 2026-11-17 · **Deciders:** Project Maintainer
**Related:** features F01, F18, F20, F22 · ADR-001, ADR-011, ADR-012, ADR-013

## Context
Alongside the zero-Docker stateless CLI/MCP package (**Path B**, `ADR-029`), users who want the full persistent Admin UI, background queue, and multi-stage editorial history currently face the production VPS topology (`compose.yaml` + `compose.prod.yaml`, `ADR-012`), which runs 6–7 containers (`db`, `migrate`, `app`, `worker`, `caddy`, `backup`, `mcp`), requires 11 mandatory `.env` variables before preflight passes (`scripts/deploy.sh`), and mandates Google Cloud OAuth (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ADR-013`) before first login. For solo operators, small teams, local evaluators, and single-container PaaS targets (Railway, Fly.io, Render, Coolify), this multi-container ceremony and external Google Cloud setup are major adoption barriers.

## Options considered
| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. Path A — Zero-Config Containerized Pocket Runtime (`compose.pocket.yaml` & single-container deploy): auto-bootstrapped `/data` secrets, Python startup SQL migrations, embedded worker daemon, and built-in Local Admin Auth (`AUTH_MODE=local\|oidc\|auto`)** (chosen) | Medium | `$0` | High — zero `.env` needed to boot; preserves strict `compose.prod.yaml` VPS topology and complements Path B (`ADR-029`) |
| B. Replace PostgreSQL with SQLite across all 4,251 lines of `engine/db.py` immediately | High | `$0` | Risky — rewrites `pg_trgm`, `FOR UPDATE SKIP LOCKED`, and composite FKs all at once |
| C. Keep mandatory Google OIDC and 6 containers for all users | Low | `$0` | Poor — blocks plug-and-play self-hosting |

## Decision
Implement **Path A — Zero-Config Containerized Pocket Runtime** (`POCKET_MODE=true`, `AUTO_MIGRATE=true`, `EMBEDDED_WORKER=true`, `AUTH_MODE=local|oidc|auto`) supporting both `docker compose -f compose.pocket.yaml up -d` and single-container deployment:

1. **Auto-Bootstrapped Persistent Secrets (`engine/bootstrap.py`):**
   - When `POCKET_MODE=true` and `APP_ENCRYPTION_KEY` or `AUTH_COOKIE_SECRET` are unset in the environment, generate cryptographically strong keys (`Fernet.generate_key()` and `secrets.token_hex(32)`) and persist them atomically to `${MACY_DATA_DIR:-/data}/.macy_secrets.json` with `0600` permissions.
   - Standard production (`compose.prod.yaml`, `POCKET_MODE` unset/false) continues to fail fast on missing `.env` variables.
2. **Idempotent Python Startup Migrations (`engine/migrations.py`):**
   - Parse `-- migrate:up` blocks from `db/migrations/*.sql` and apply unapplied versions into `schema_migrations` using `psycopg` on startup when `AUTO_MIGRATE=true` (default in Pocket mode), eliminating the separate `amacneil/dbmate` container in `compose.pocket.yaml`.
3. **Embedded Background Worker (`EMBEDDED_WORKER=true`):**
   - Spawn `run_worker_loop()` (`engine/__main__.py`) as a supervised background daemon guarded by a PostgreSQL session advisory lock (`pg_try_advisory_lock`) inside the `app` container so only one worker tick runs even across Streamlit reruns.
4. **Built-in Local Admin Auth (`AUTH_MODE=local | oidc | auto`):**
   - Add `password_hash TEXT NULL` to `users` via non-blocking migration.
   - When `AUTH_MODE=local` (or `AUTH_MODE=auto` and `GOOGLE_CLIENT_ID` is unset), authenticate users via email + password verified against PBKDF2-HMAC-SHA256 (`hashlib.pbkdf2_hmac`, 600,000 iterations, constant-time `hmac.compare_digest`) with in-memory/DB rate limiting (`5` failed attempts per `15m`).
   - When `GOOGLE_CLIENT_ID` is configured (`AUTH_MODE=oidc` or `auto`), use Google OIDC (`st.login("google")`, `ADR-013`).

## Consequences
- Easier: `docker compose -f compose.pocket.yaml up -d` starts the entire platform in 2 containers (`db` + `macy`) with zero `.env` setup and zero Google Cloud account required.
- Harder: Two auth paths (`local` and `oidc`) must be covered in unit and E2E tests.

## Revisit when
Embedded SQLite/PGlite support is needed for full multi-run editorial history without a `postgres:18-alpine` container.

## Action items
- [ ] `M7-02` — Implement `engine/bootstrap.py`, `engine/migrations.py`, local password auth in `engine/auth.py`, embedded worker supervisor, and `compose.pocket.yaml`.
