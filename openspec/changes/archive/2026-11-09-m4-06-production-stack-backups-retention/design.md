# Design: M4-06 — Production stack, backups, retention

## 1. Production Stack & Local-to-VPS Parity (`ADR-012`)
- `compose.yaml` + `compose.prod.yaml` form the single production/staging topology. Environment differences live purely in `.env` (`.env.staging.example` vs `.env.prod.example`) and the mounted OIDC `secrets.toml` (`deploy/staging/secrets.toml` vs `deploy/prod/secrets.toml`).
- Neither `db` (5432) nor `app` (8501) publishes host ports in `compose.prod.yaml`; only `caddy` publishes HTTP/HTTPS ports (`CADDY_HTTP_PORT`, `CADDY_HTTPS_PORT`), enabling side-by-side execution of `macy-staging` and `macy-prod` both locally and on a single VPS.

## 2. Nightly Offsite Backups & 14-Day Retention (`F20 AC4`)
- `scripts/backup.sh` runs inside the `postgres:18-alpine` `backup` container:
  - Produces timestamped custom-format dumps (`macy_<POSTGRES_DB>_<UTC-timestamp>.dump`) via `pg_dump -Fc` into `BACKUP_DIR` (`/backups`) and verifies header integrity via `pg_restore --list`.
  - Copies each verified dump to `OFFSITE_BACKUP_DIR` (`/offsite`, which can be a mounted rclone/s3fs/SSHFS volume or local offsite target) and optionally executes `OFFSITE_SYNC_CMD` when configured.
  - Prunes `.dump` files older than `BACKUP_RETENTION_DAYS` (default `14`) in both `BACKUP_DIR` and `OFFSITE_BACKUP_DIR`.
  - Updates `/backups/.last_backup_ok` so the container healthcheck (`pg_isready` + marker file check) confirms both DB connectivity and backup health.
- `APP_ENCRYPTION_KEY` is never stored inside database dumps; `docs/runbook.md` documents backing it up separately in the operator's password manager / secret vault so encrypted `workspace_integrations.secrets` and `webhook_subscriptions.secret` remain decryptable after a restore.

## 3. Data Retention under the `retention` Role (`F20 AC7`, `ADR-024`)
- **Role enforcement:** `services.purge_expired_data()` connects via `RETENTION_DATABASE_URL` (or the provided connection) and checks `SELECT current_user` in PostgreSQL. If `current_user != 'retention'`, it raises `PermissionError` before executing any DML. Furthermore, `app` has `REVOKE UPDATE, DELETE ON events`, so only `retention` (or `owner`) has database-level permission to delete old `events`.
- **Least-privilege grant:** Migration `20261109000003_grant_usage_ledger_select_to_retention.sql` grants `SELECT ON usage_ledger TO retention` so `DELETE FROM llm_payloads USING usage_ledger` can inspect `usage_ledger.created_at` without granting `DELETE` on `usage_ledger`.
- **Retention windows (relative to `now` / `--now` frozen timestamp):**
  - `llm_payloads`: delete where `usage_ledger.created_at < now - 90 days` (`usage_ledger` rows remain intact).
  - `documents`: `UPDATE documents SET extracted_text = NULL, text_purged_at = now WHERE fetched_at < now - 365 days AND extracted_text IS NOT NULL` (title, URL, canonical_url, published_at, date_status, content_hash stay intact).
  - `webhook_deliveries`: delete where `status IN ('delivered', 'dead')` and `COALESCE(delivered_at, next_attempt_at) < now - 30 days`.
  - `events`: delete where `occurred_at < now - 730 days` (24 months), first deleting any referencing `webhook_deliveries` rows for those expired events to satisfy `webhook_deliveries_event_id_fkey`.

## 4. LGPD User Anonymisation (`F20 AC8`, `ADR-024`)
- `services.anonymise_user(email=...)` looks up the user by case-insensitive email, and in one transaction:
  - Replaces `users.email` with `deleted-<user_id>@anonymised.invalid` and sets `users.name = NULL`;
  - Deletes the user's rows from `memberships` so the anonymised account cannot access any workspace;
  - Scrubs personal data keys (`email`, `name`, `user_email`, `user_name`) from `events.payload` where `actor_user_id = user_id` or `entity_id = user_id` while keeping `events.actor_user_id` as an opaque UUID reference (`ADR-024`).
  - Note: Because `events` has `REVOKE UPDATE ON events FROM app`, the migration grants a `SECURITY DEFINER` helper function `anonymise_user_events(target_user_id uuid)` owned by `owner` and executable by `app`, or updates `users` and scrubs `events.payload` safely via that controlled function while keeping general `UPDATE, DELETE ON events` revoked from `app`.
