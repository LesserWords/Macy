# operations Specification Delta (M5-01)

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Production deploys on release tags with J1 read-only smoke test and rollback
The repository SHALL define release version `0.1.0` and configure `.github/workflows/deploy.yml` so pushing a `v*` tag builds the release image, deploys `/opt/macy/prod` (`environment: production`), executes `scripts/post_deploy_smoke.py` (`/_stcore/health` plus read-only J1 Playwright verification) against `PROD_APP_URL`, marks the deploy as failed if any check fails, and rolls back to the previous deployed tag (`F20 AC3`, `ADR-012`, `ADR-019`).

#### Scenario: Release tag deploy executes smoke test and rolls back on failure
- **WHEN** a `v*` tag triggers `deploy-prod` and the post-deploy `J1 read-only + health` smoke test fails
- **THEN** the workflow restores the previous image tag via `scripts/deploy.sh` and reports the job as failed

#### Scenario: Post-deploy smoke verifier rejects failing health or UI errors and performs read-only J1 checks
- **WHEN** `scripts/post_deploy_smoke.py` runs against a target URL
- **THEN** it fails non-zero when `/_stcore/health` does not return `ok` or Streamlit renders `[data-testid="stException"]`, and visits Campaign Settings read-only without invoking Save
