# operations Specification

## Purpose
Provides unified container images, multi-environment Docker Compose topologies (dev, test, prod), safe database migration automation with dbmate and squawk, and operational infrastructure.

## Requirements

### Requirement: Unified container image
The repository SHALL provide a multi-stage Dockerfile that builds a single minimal container image using uv and a non-root user for both application and worker runtimes.

#### Scenario: Image builds and imports engine
- **WHEN** the Docker image is built
- **THEN** it succeeds and running `python -c "import engine"` within the container exits 0

### Requirement: Declarative Compose environments
The repository SHALL provide `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml` covering local development, offline E2E, and production deployment topologies.

#### Scenario: Local development stack starts healthy
- **WHEN** `docker compose up -d` executes
- **THEN** the `db`, `migrate`, `app`, and `worker` services initialize without errors, with healthchecks passing and no host-installed Python or Postgres required

#### Scenario: Compose configurations validate cleanly in CI
- **WHEN** `docker compose config` runs across all Compose file combinations (`compose.yaml`, with override, with e2e, and with prod)
- **THEN** all configurations parse without errors

### Requirement: Safe SQL migrations with squawk
The repository SHALL lint all migration files in `db/migrations/*.sql` with squawk in pre-commit, `scripts/check.sh`, and CI.

#### Scenario: Safe migration passes squawk
- **WHEN** migration files adhere to non-blocking DDL rules
- **THEN** squawk exits with status 0

#### Scenario: Dangerous DDL is blocked
- **WHEN** a migration contains unsafe DDL such as non-concurrent index creation on an existing table
- **THEN** squawk flags the violation and exits non-zero

### Requirement: Safe outbound HTTP requests (safe_http)
The system SHALL validate all outbound user-supplied URLs and refuse requests to private, loopback, link-local, and metadata ranges, enforcing HTTPS in production, a 10s timeout, a 1 MB response cap, and blocking cross-host redirects.

#### Scenario: Refuse private, loopback, and cloud metadata addresses
- **WHEN** an outbound HTTP request targets a private IP, loopback, or metadata service (`169.254.169.254`, `127.0.0.1`, `10.0.0.0/8`, `::1`)
- **THEN** the request is refused with an unsafe URL error before any connection is made

#### Scenario: Enforce HTTPS in production
- **WHEN** an outbound HTTP request is made using plain HTTP while in production environment
- **THEN** the request is refused requiring HTTPS

#### Scenario: Enforce response size cap and block cross-host redirects
- **WHEN** a remote server redirects to a different host or returns more than 1 MB of response data
- **THEN** the request is aborted

### Requirement: Sentry exception tracking with secret scrubbing
The system SHALL initialize Sentry automatically across application, pipeline worker, and CLI runtimes when `SENTRY_DSN` is configured, capture unhandled and pipeline-isolated exceptions tagged with `workspace_id` and `run_id`, scrub all sensitive dict keys and inline credential patterns (DSN passwords, Bearer tokens, API keys, webhook secrets, sensitive query params) before sending, and provide `content-engine test-sentry` to verify end-to-end delivery (`F20 AC5`, `ADR-023`).

#### Scenario: Scrubber strips credentials and authorization headers
- **WHEN** an exception event contains API keys, passwords, Authorization headers, DSN userinfo passwords, or inline secret strings
- **THEN** the `before_send` hook scrubs all sensitive values while preserving non-sensitive token metrics

#### Scenario: Exceptions tagged with workspace and run identifiers
- **WHEN** an exception occurs within an active pipeline execution or UI render while `SENTRY_DSN` is configured
- **THEN** the event is tagged with `workspace_id` and (when active) `run_id` and captured via `capture_exception`

#### Scenario: Forced Sentry verification CLI flushes a test exception
- **WHEN** `content-engine test-sentry` executes with `SENTRY_DSN` set
- **THEN** it initializes Sentry, captures and flushes a forced test exception, and exits 0 (or exits non-zero when `SENTRY_DSN` is unset)

### Requirement: Heartbeat run monitoring
The system SHALL support pinging a healthcheck heartbeat URL upon run completion (`HEARTBEAT_URL`) and `${HEARTBEAT_URL}/fail` upon failure to monitor scheduled weekly run execution (`F20 AC6`, `ADR-023`).

#### Scenario: Ping heartbeat on run completion and no-op when unset
- **WHEN** a pipeline run completes or skips when unset
- **THEN** a heartbeat ping is dispatched to the configured URL, or safely skipped if no URL is set

#### Scenario: Failed run pings failure endpoint while skipped run alerts after weekly window
- **WHEN** a pipeline run fails while `HEARTBEAT_URL` is configured
- **THEN** a GET request is sent to `${HEARTBEAT_URL}/fail` instead of resetting the weekly success window

### Requirement: Staging deploys automatically with one-shot migrations
The repository SHALL provide a deploy workflow and deploy script that, on merge to `main`, build and publish the image, run database migrations exactly once before replacing running services, health-check the result, and restore the previous app version when the post-deploy check fails.

#### Scenario: Failing migration leaves the previous version running
- **WHEN** a deploy runs with a migration that fails
- **THEN** the deploy exits non-zero and the previously running `app` and `worker` containers are not recreated

#### Scenario: Unhealthy release is rolled back
- **WHEN** the post-deploy smoke test fails
- **THEN** the previous app image tag is redeployed and the deploy exits non-zero

#### Scenario: Placeholder secrets are rejected
- **WHEN** the deploy preflight finds a missing, world-readable or placeholder-valued environment file
- **THEN** it exits non-zero before any container is touched

### Requirement: Staging stack exposes only HTTPS and no database port
The staging Compose combination SHALL publish only the reverse proxy ports, SHALL NOT publish the database port, SHALL give every long-running service a healthcheck, SHALL pin third-party images, and SHALL NOT pass the fake test identity variable to application containers.

#### Scenario: Staging configuration is safe by construction
- **WHEN** `docker compose -f compose.yaml -f compose.prod.yaml config` is rendered
- **THEN** only `caddy` publishes ports, no service sets `MACY_TEST_USER_EMAIL`, and every long-running service defines a healthcheck

### Requirement: Application container serves the admin UI
The runtime image SHALL contain the Streamlit entry point, locale catalogues, industry packs and prompts, and the `app` service SHALL run Streamlit with a healthcheck.

#### Scenario: App service reports healthy
- **WHEN** the `app` service starts
- **THEN** `/_stcore/health` responds `ok` and the container reports healthy

### Requirement: Operator runbook
The repository SHALL document deploy, rollback, logs, restart and first-time VPS preparation in `docs/runbook.md`.

#### Scenario: Runbook covers operational procedures
- **WHEN** an operator opens `docs/runbook.md`
- **THEN** it contains the deploy, rollback, logs, restart and VPS preparation procedures

### Requirement: Production Compose baseline and isolated environment configuration
The repository SHALL provide `.env.prod.example` and `deploy/prod/secrets.toml.example` so the production stack (`macy-prod`) runs with the exact same `compose.yaml` + `compose.prod.yaml` topology locally and on a VPS, with its own `.env`, database, subdomain, and OIDC redirect URI, without exposing the database port (`F20 AC1, AC4`, `ADR-012`).

#### Scenario: Production Compose configuration renders cleanly and exposes only Caddy
- **WHEN** `docker compose --env-file .env.prod.example -f compose.yaml -f compose.prod.yaml config` is executed
- **THEN** it renders cleanly with `APP_ENV=production`, `COMPOSE_PROJECT_NAME=macy-prod`, no published database port (`5432`), and healthchecks on every long-running service

### Requirement: Nightly offsite database backups with 14-day retention
The `backup` service SHALL run a nightly `pg_dump` of the database, copy verified dumps to offsite storage, prune backups older than 14 days, include `scripts/backup.sh` in release deployment bundles, ping `BACKUP_HEARTBEAT_URL` on success, clear `.last_backup_ok` and notify `${BACKUP_HEARTBEAT_URL}/fail` / `BACKUP_ALERT_URL` on failure, and support restoring into a scratch database with matching row counts (`F20 AC4`, `ADR-012`).

#### Scenario: Backup script creates dump, syncs offsite, and prunes files older than 14 days
- **WHEN** `scripts/backup.sh --once` runs against a database and backup directories containing dumps both newer and older than 14 days (with optional `BACKUP_HEARTBEAT_URL`)
- **THEN** a new custom-format dump is written and synced to the offsite directory, dumps older than 14 days are deleted, `.last_backup_ok` is updated, `BACKUP_HEARTBEAT_URL` is pinged if set, and recent dumps are kept

#### Scenario: Failed backup clears health marker and fires failure alerts
- **WHEN** `pg_dump` or verification fails during `scripts/backup.sh`
- **THEN** `${BACKUP_DIR}/.last_backup_ok` is removed, `${BACKUP_HEARTBEAT_URL}/fail` is pinged if set, and a JSON error payload is POSTed to `BACKUP_ALERT_URL` if set

#### Scenario: Dump restores cleanly into a scratch database with matching row counts
- **WHEN** a backup dump produced by `scripts/backup.sh` is restored into a scratch PostgreSQL database via `pg_restore`
- **THEN** the restore exits 0 and table row counts in the restored database match the source database

### Requirement: Nightly data retention purge under the retention role
The system SHALL provide `content-engine purge` (`services.purge_expired_data`) that executes exclusively under the `retention` database role and purges expired data according to `ADR-024`: `llm_payloads` after 90 days, `documents.extracted_text` set to `NULL` (with `text_purged_at` stamped and metadata retained) after 365 days, `delivered` or `dead` `webhook_deliveries` after 30 days, and `events` after 24 months (`F20 AC7`).

#### Scenario: Purge deletes and nulls expired data per ADR-024 while keeping fresh data and facts
- **WHEN** `content-engine purge` runs as the `retention` role against seeded data with frozen timestamps spanning fresh and expired windows
- **THEN** `llm_payloads` older than 90 days are deleted while `usage_ledger` rows remain, `documents.extracted_text` older than 365 days is set to `NULL` with `text_purged_at` populated and `title`/`url`/`published_at`/`content_hash` preserved, `delivered` and `dead` `webhook_deliveries` older than 30 days are deleted while `pending`/`retrying` stay, and `events` older than 24 months are deleted

#### Scenario: Purge refuses execution when not connected as the retention role
- **WHEN** `services.purge_expired_data` or `content-engine purge` is invoked using the `app` role connection
- **THEN** it raises a permission error and modifies 0 rows

### Requirement: LGPD user anonymisation command
The system SHALL provide `content-engine anonymise-user <email>` (`services.anonymise_user`) that anonymises the user's `email` and `name`, removes workspace memberships, and scrubs PII fields from `events.payload` while preserving `actor_user_id` as an opaque UUID (`F20 AC8`, `ADR-024`).

#### Scenario: Anonymising a user scrubs email and name while preserving opaque event actor IDs
- **WHEN** `content-engine anonymise-user <email>` runs for an existing user
- **THEN** the user's `email` is replaced with an opaque `deleted-<id>@anonymised.invalid` address, `name` is set to `NULL`, their `memberships` are removed, and their `events` rows retain `actor_user_id` with personal email/name keys scrubbed from `payload`

### Requirement: Production deploys on release tags with J1 read-only smoke test and rollback
The repository SHALL define release version `0.1.0` and configure `.github/workflows/deploy.yml` so pushing a `v*` tag builds the release image, deploys `/opt/macy/prod` (`environment: production`), executes `scripts/post_deploy_smoke.py` (`/_stcore/health` plus read-only J1 Playwright verification) against `PROD_APP_URL`, marks the deploy as failed if any check fails, and rolls back to the previous deployed tag (`F20 AC3`, `ADR-012`, `ADR-019`).

#### Scenario: Release tag deploy executes smoke test and rolls back on failure
- **WHEN** a `v*` tag triggers `deploy-prod` and the post-deploy `J1 read-only + health` smoke test fails
- **THEN** the workflow restores the previous image tag via `scripts/deploy.sh` and reports the job as failed

#### Scenario: Post-deploy smoke verifier rejects failing health or UI errors and performs read-only J1 checks
- **WHEN** `scripts/post_deploy_smoke.py` runs against a target URL
- **THEN** it fails non-zero when `/_stcore/health` does not return `ok` or Streamlit renders `[data-testid="stException"]`, and visits Campaign Settings read-only without invoking Save

### Requirement: Opt-in containerized MCP service behind Caddy with zero raw port exposure in production
The repository SHALL define an opt-in `mcp` Compose service (`profiles: ["mcp"]`) across `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml`, routing `/mcp*` via `Caddyfile` while exposing zero raw MCP host ports (`8765`) in `compose.prod.yaml`, isolating `MCP_AUTH_TOKEN` outside `x-app-env`, omitting `MACY_TEST_USER_EMAIL`, and providing a `/healthz` healthcheck (`F20`, `F21 AC9`, `ADR-012`, `ADR-027`).

#### Scenario: Compose matrix validates with and without MCP profile while keeping prod hardened
- **WHEN** `docker compose ... config` renders `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml` (both default and `--profile mcp`)
- **THEN** all configurations validate cleanly, `compose.prod.yaml` exposes zero `5432`, `8501`, or `8765` host ports, keeps `MCP_AUTH_TOKEN`, `AUTH_COOKIE_SECRET`, and `GOOGLE_CLIENT_SECRET` out of `x-app-env`, omits `MACY_TEST_USER_EMAIL`, and defines a healthcheck on `mcp`

### Requirement: Deploy script preflight, rollback, and smoke check coverage for MCP
`scripts/deploy.sh`, `scripts/post_deploy_smoke.py`, CI/deploy/live-E2E workflows, and `docs/runbook.md` SHALL support MCP preflight secret validation, targeted rollback of `mcp`, post-deploy `/mcp` `401`/authenticated smoke verification (`--check-mcp`), and operator key management procedures (`F20 AC2, AC3`, `F21 AC9, AC10`, `ADR-019`, `ADR-027`).

#### Scenario: Deploy preflight blocks placeholder MCP_AUTH_TOKEN when MCP is enabled
- **WHEN** `scripts/deploy.sh --preflight-only` executes with `MCP_ENABLED=true` and `MCP_AUTH_TOKEN` unset or equal to a `CHANGE_ME` placeholder
- **THEN** preflight exits non-zero before touching containers

#### Scenario: Post-deploy smoke verifies fail-closed 401 on unauthenticated MCP endpoint
- **WHEN** `scripts/post_deploy_smoke.py --check-mcp` runs against a target URL
- **THEN** it verifies unauthenticated `POST <url>/mcp` returns HTTP `401`, and verifies authenticated MCP `initialize` + `macy_list_workspaces` when a token is provided

#### Scenario: Local macy-prod rehearsal and rollback cover MCP service cleanly
- **WHEN** `scripts/deploy.sh` and `scripts/deploy.sh --rollback <prev_tag>` run locally against `macy-prod` with `COMPOSE_PROFILES=mcp`
- **THEN** `mcp` starts healthy behind Caddy, passes `post_deploy_smoke.py --check-mcp --insecure`, and rolls back cleanly alongside `app` and `worker` on smoke failure without touching migrations

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
