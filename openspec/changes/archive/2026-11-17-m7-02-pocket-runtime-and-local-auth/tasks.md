# Tasks: M7-02 — Path A: Macy Pocket Single-Container Runtime, Embedded Worker & Local Auth

## 1. Zero-.env Secret Bootstrap & Python Startup Migrations (Delivery KR: D1)
- [x] 1.1 Write failing tests in `tests/test_pocket_bootstrap.py` covering `ensure_pocket_secrets()` atomic key creation/reload (`0600`) under `POCKET_MODE=true`, non-Pocket fail-fast refusal, and `apply_pending_migrations()` parsing `-- migrate:up` blocks idempotently into `schema_migrations`.
- [x] 1.2 Implement `engine/bootstrap.py` and `engine/migrations.py`. Verify D1: `uv run pytest tests/test_pocket_bootstrap.py --no-cov` exits 0.

## 2. Built-In Local Admin Auth & Automatic OIDC Switch (Delivery KR: D2)
- [x] 2.1 Write failing tests in `tests/test_local_auth.py` covering `users.password_hash` storage, PBKDF2-HMAC-SHA256 (`600,000` iterations) hashing/constant-time verification, `5`/15m brute-force rate limiting, `AUTH_MODE=auto` routing (`local` vs `oidc`), and `MACY_TEST_USER_EMAIL` refusal in production.
- [x] 2.2 Add non-blocking `users.password_hash` SQL migration (`db/migrations/`), update `db/schema.sql`, implement local auth helpers in `engine/auth.py` + `engine/passwords.py` + `engine/services.py`, and render the local login card in `app.py`. Verify D2: `npx --yes squawk-cli db/migrations/*.sql && uv run pytest tests/test_local_auth.py --no-cov` exits 0.

## 3. Embedded Background Worker Supervisor (Delivery KR: D3)
- [x] 3.1 Write failing tests in `tests/test_embedded_worker.py` verifying `start_embedded_worker()` starts once across Streamlit reruns, acquires PostgreSQL `pg_try_advisory_lock`, invokes `run_worker_tick()`, and captures exceptions without crashing the UI process.
- [x] 3.2 Implement `start_embedded_worker()` in `engine/__main__.py` and wire `EMBEDDED_WORKER=true` into `app.py`. Verify D3: `uv run pytest tests/test_embedded_worker.py --no-cov` exits 0.

## 4. Pocket Compose Stack & CI Matrix Parity (Delivery KR: D4)
- [x] 4.1 Write failing Compose validation tests in `tests/test_compose_and_docker.py` asserting `docker compose -f compose.pocket.yaml config` succeeds with zero `.env` file (`POCKET_MODE=true`, `AUTO_MIGRATE=true`, `EMBEDDED_WORKER=true`, `macy_data:/data`, healthchecks on `db` and `macy`) while `compose.prod.yaml` retains strict security guarantees.
- [x] 4.2 Create `compose.pocket.yaml` and update `.github/workflows/ci.yml`. Verify D4: `docker compose -f compose.pocket.yaml config -q && uv run pytest tests/test_compose_and_docker.py --no-cov && sh scripts/check.sh` exits 0.
