# operations Delta Specification

## MODIFIED Requirements

### Requirement: Operator runbook
The repository SHALL document deploy, migration-free rollback, logs, restart, key rotation, retention purge, host-side offsite backup sync, and ordered VPS bootstrap (stack migrations before workspace/owner seeding) in `docs/runbook.md` and `docs/guides/environment-and-credentials.md`.

#### Scenario: Runbook covers operational procedures
- **WHEN** an operator opens `docs/runbook.md` or `docs/guides/environment-and-credentials.md`
- **THEN** bootstrap steps run `scripts/deploy.sh` (`db` + `migrate`) before `dc run --rm --no-deps app content-engine create-workspace/add-owner/seed-brand/seed-sources`, OIDC setup reflects `.env` rendering without removed `deploy/*/secrets.toml.example` files, `rotate-key` documents `"<new>,<old>"` ordering across integrations and webhooks, `purge` documents `RETENTION_DATABASE_URL`, and `rclone` offsite sync is documented as a host-side step rather than an in-container `OFFSITE_SYNC_CMD`

### Requirement: Production Compose baseline and isolated environment configuration
The repository SHALL align `.env.staging.example` and `.env.prod.example` on `.env`-driven OIDC rendering without `deploy/*/secrets.toml.example` or `STREAMLIT_SECRETS_FILE` deploy preflight checks, enforcing non-default `RETENTION_DB_PASSWORD`, explicit release-tag `APP_TAG` guidance, backup variables, and zero exposed database ports (`F20 AC1, AC4`, `ADR-012`).

#### Scenario: Production Compose configuration renders cleanly and exposes only Caddy
- **WHEN** `docker compose --env-file .env.prod.example -f compose.yaml -f compose.prod.yaml config` is executed
- **THEN** it renders cleanly with `APP_ENV=production`, `COMPOSE_PROJECT_NAME=macy-prod`, no published database port (`5432`), healthchecks on every long-running service, and `.env.staging.example` / `.env.prod.example` share consistent `RETENTION_DB_PASSWORD` and `BACKUP_*` variables

#### Scenario: Obsolete secrets.toml templates are absent and deploy relies on .env
- **WHEN** `scripts/deploy.sh --preflight-only` runs or repository layout is checked
- **THEN** `deploy/staging/secrets.toml.example` and `deploy/prod/secrets.toml.example` are absent, and `scripts/deploy.sh` validates `.env` OIDC variables without reading `STREAMLIT_SECRETS_FILE`

### Requirement: Nightly offsite database backups with 14-day retention
The `backup` service SHALL run nightly `pg_dump`, verify archives, copy to `/offsite`, prune dumps older than 14 days, maintain `.last_backup_ok`, dispatch success/failure heartbeats and `BACKUP_ALERT_URL` payloads, support POSIX `OFFSITE_SYNC_CMD` inside the container, document host-side remote sync (such as `rclone`) on `$OFFSITE_BACKUP_PATH`, and restore with matching row counts (`F20 AC4`, `ADR-012`).

#### Scenario: Backup script creates dump, syncs offsite, and prunes files older than 14 days
- **WHEN** `scripts/backup.sh --once` runs against a database and backup directories containing dumps both newer and older than 14 days (with optional `BACKUP_HEARTBEAT_URL`)
- **THEN** a new custom-format dump is written and synced to the offsite directory, dumps older than 14 days are deleted, `.last_backup_ok` is updated, `BACKUP_HEARTBEAT_URL` is pinged if set, and recent dumps are kept

#### Scenario: Failed backup clears health marker and fires failure alerts
- **WHEN** `pg_dump` or verification fails during `scripts/backup.sh`
- **THEN** `${BACKUP_DIR}/.last_backup_ok` is removed, `${BACKUP_HEARTBEAT_URL}/fail` is pinged if set, and a JSON error payload is POSTed to `BACKUP_ALERT_URL` if set

#### Scenario: Dump restores cleanly into a scratch database with matching row counts
- **WHEN** a backup dump produced by `scripts/backup.sh` is restored into a scratch PostgreSQL database via `pg_restore`
- **THEN** the restore exits 0 and table row counts in the restored database match the source database

#### Scenario: Remote object-storage sync boundary reflects postgres-alpine container tools
- **WHEN** an operator configures remote offsite replication (such as `rclone`) following `docs/runbook.md` and `docs/guides/environment-and-credentials.md`
- **THEN** documentation specifies running `rclone` on the VPS host targeting `$OFFSITE_BACKUP_PATH` rather than invoking missing container binaries via `OFFSITE_SYNC_CMD`

### Requirement: Nightly data retention purge under the retention role
The system SHALL run `content-engine purge` (`services.purge_expired_data`) connecting strictly via `RETENTION_DATABASE_URL` (never falling back to `DATABASE_URL`), require non-default `RETENTION_DB_PASSWORD` in staging/prod Compose and `scripts/deploy.sh`, and purge expired `llm_payloads`, `documents.extracted_text`, `webhook_deliveries`, and `events` per `ADR-024` under `retention` (`F20 AC7`).

#### Scenario: Purge deletes and nulls expired data per ADR-024 while keeping fresh data and facts
- **WHEN** `content-engine purge` runs as the `retention` role against seeded data with frozen timestamps spanning fresh and expired windows
- **THEN** `llm_payloads` older than 90 days are deleted while `usage_ledger` rows remain, `documents.extracted_text` older than 365 days is set to `NULL` with `text_purged_at` populated and `title`/`url`/`published_at`/`content_hash` preserved, `delivered` and `dead` `webhook_deliveries` older than 30 days are deleted while `pending`/`retrying` stay, and `events` older than 24 months are deleted

#### Scenario: Purge refuses execution when not connected as the retention role
- **WHEN** `services.purge_expired_data` or `content-engine purge` is invoked using the `app` role connection
- **THEN** it raises a permission error and modifies 0 rows

#### Scenario: Retention connection never falls back to application DATABASE_URL
- **WHEN** `DATABASE_URL` is present in the environment and `RETENTION_DATABASE_URL` is unset
- **THEN** `services.get_retention_connection()` ignores `DATABASE_URL`, connects using `DEFAULT_RETENTION_DATABASE_URL` (or fails cleanly when invalid), and `compose.prod.yaml` / `scripts/deploy.sh` reject missing or placeholder `RETENTION_DB_PASSWORD`

### Requirement: Production deploys on release tags with J1 read-only smoke test and rollback
The repository SHALL configure `.github/workflows/deploy.yml` so release tags deploy `/opt/macy/prod` (`environment: production`), require authenticated J1 `scripts/post_deploy_smoke.py` checks (`--require-auth`) unless explicitly opted out, fail on health or UI errors, and roll back `app` and `worker` to the previous tag without re-running new-bundle migrations (`F20 AC3`, `ADR-012`, `ADR-019`).

#### Scenario: Release tag deploy executes smoke test and rolls back on failure
- **WHEN** a `v*` tag triggers `deploy-prod` and `scripts/post_deploy_smoke.py` fails with a non-empty previous tag
- **THEN** the workflow invokes targeted rollback (`scripts/deploy.sh --rollback <prev_tag>`) to pull/recreate `app` and `worker` at `<prev_tag>` and verify `/_stcore/health` without executing `migrate` from the failed bundle, and reports the job as failed

#### Scenario: Release tag deploy enforces authenticated J1 smoke unless explicitly bypassed
- **WHEN** `deploy-prod` executes `scripts/post_deploy_smoke.py`
- **THEN** it fails fast when `PROD_PLAYWRIGHT_STORAGE_STATE` is empty unless `PROD_SMOKE_ALLOW_UNAUTHENTICATED` is `"true"`, and passes `--require-auth` whenever `PROD_PLAYWRIGHT_STORAGE_STATE` is provided

#### Scenario: Post-deploy smoke verifier rejects failing health or UI errors and performs read-only J1 checks
- **WHEN** `scripts/post_deploy_smoke.py` runs against a target URL
- **THEN** it fails non-zero when `/_stcore/health` does not return `ok` or Streamlit renders `[data-testid="stException"]`, and visits Campaign Settings read-only without invoking Save
