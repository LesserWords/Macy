# operations Delta Specification

## ADDED Requirements

### Requirement: Zero-config Pocket single-container runtime (Path A)
The repository SHALL provide `compose.pocket.yaml` and runtime bootstrap modules (`engine/bootstrap.py`, `engine/migrations.py`, and embedded worker supervision in `engine/__main__.py`) so that setting `POCKET_MODE=true`, `AUTO_MIGRATE=true`, and `EMBEDDED_WORKER=true` starts Macy with zero pre-configured `.env` secrets, zero separate `migrate` or `worker` containers, and persistent `/data` key storage while leaving `compose.prod.yaml` fail-fast guarantees unchanged (`ADR-030`, `F22 AC4, AC6`).

#### Scenario: Pocket mode generates persistent secrets and applies SQL migrations on startup
- **WHEN** the Pocket runtime boots with `POCKET_MODE=true`, `AUTO_MIGRATE=true`, and an empty `${MACY_DATA_DIR}` directory
- **THEN** `ensure_pocket_secrets()` atomically writes valid `APP_ENCRYPTION_KEY` and `AUTH_COOKIE_SECRET` into `${MACY_DATA_DIR}/.macy_secrets.json` (`0600`), reuses those exact keys on subsequent boots, and `apply_pending_migrations()` executes pending `-- migrate:up` SQL migrations idempotently into `schema_migrations`

#### Scenario: Embedded worker runs under a PostgreSQL advisory lock
- **WHEN** `EMBEDDED_WORKER=true` is enabled in the application process
- **THEN** `start_embedded_worker()` spawns at most one daemon thread guarded by `pg_try_advisory_lock` to run `run_worker_tick()` without blocking UI startup

#### Scenario: Pocket Compose renders with zero .env file
- **WHEN** `docker compose -f compose.pocket.yaml config -q` executes in a clean environment without `.env`
- **THEN** the configuration validates cleanly with `db` and `macy` healthchecks and persistent `macy_data:/data` volume
