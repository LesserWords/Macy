# Proposal: M4-06 — Production stack, backups, retention

## Why
Before entering the M4 exit gate and M5 production pilot, the production Compose stack (`macy-prod`), automated nightly database backups with 14-day retention (`F20 AC4`, `ADR-012`), nightly data retention purge under the least-privilege `retention` database role (`F20 AC7`, `ADR-024`), and LGPD user anonymisation (`F20 AC8`, `ADR-024`) must be ready and verified locally in Docker Compose with the exact same topology used on a VPS.

## What Changes
1. **Production Compose Baseline & Shared/Isolated Topology (`.env.prod.example`, `deploy/prod/secrets.toml.example`, `compose.prod.yaml`, `scripts/deploy.sh`, `docs/runbook.md`):**
   - Add `.env.prod.example` (`APP_ENV=production`, `COMPOSE_PROJECT_NAME=macy-prod`) and `deploy/prod/secrets.toml.example` (with the production Google OIDC `redirect_uri`).
   - Update `scripts/deploy.sh` to apply both `APP_DB_PASSWORD` (for `app`) and `RETENTION_DB_PASSWORD` (for `retention`, when set) after migrations.
   - Document running both `macy-staging` and `macy-prod` on a single VPS or locally on Docker Compose with isolated `.env`, database volumes, and ports/domains.
2. **Nightly Backup Service & Restore Verification (`scripts/backup.sh`, `compose.prod.yaml`, `docs/runbook.md`):**
   - Implement `scripts/backup.sh` (`--once` and nightly loop mode) that creates compressed PostgreSQL dumps (`pg_dump -Fc`), syncs to offsite storage (local/mounted offsite path or `OFFSITE_SYNC_CMD` / `rclone`), and prunes dumps older than `BACKUP_RETENTION_DAYS` (default `14`) in both local and offsite directories (`F20 AC4`).
   - Wire `backup` service in `compose.prod.yaml` to run `scripts/backup.sh` with mounted `/backups` and `/offsite` volumes and a healthcheck verifying recent backup integrity.
   - Document separate manual backup of `APP_ENCRYPTION_KEY` and execute a full Docker PostgreSQL dump → scratch database restore verification, recording table row counts in `docs/runbook.md` and `docs/plans/M4.md`.
3. **Retention Purge & LGPD User Anonymisation (`db/migrations/20261109000003_grant_usage_ledger_select_to_retention.sql`, `engine/db.py`, `engine/services.py`, `engine/cli.py`):**
   - Grant `SELECT ON usage_ledger TO retention` so the `retention` role can join `usage_ledger.created_at` when purging expired `llm_payloads`.
   - Implement `db.purge_expired_data()` and `services.purge_expired_data()` (restricted strictly to the `retention` role via `current_user = 'retention'`) to:
     - Delete `llm_payloads` older than 90 days (keeping `usage_ledger` rows);
     - Set `documents.extracted_text = NULL` and stamp `text_purged_at` for documents older than 365 days (keeping title, URL, dates, and hash);
     - Delete `delivered` or `dead` `webhook_deliveries` older than 30 days;
     - Delete `events` older than 24 months (along with dependent `webhook_deliveries`).
   - Implement `db.anonymise_user()` and `services.anonymise_user()` to anonymise a user's `email` (`deleted-<user_id>@anonymised.invalid`), set `name = NULL`, delete `memberships`, and scrub user PII keys (`email`, `name`, `user_email`, `user_name`) from `events.payload` while preserving `actor_user_id` as an opaque UUID (`F20 AC8`).
   - Add `content-engine purge [--now <ISO-8601>]` and `content-engine anonymise-user <email>` CLI commands and schedule nightly `content-engine purge` in `scheduler.generate_crontab()`.

## Capabilities Affected
- `operations`
- `access`
