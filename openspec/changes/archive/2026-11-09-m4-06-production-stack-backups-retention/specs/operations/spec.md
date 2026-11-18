# operations Specification Delta (M4-06)

## ADDED Requirements

### Requirement: Production Compose baseline and isolated environment configuration
The repository SHALL provide `.env.prod.example` and `deploy/prod/secrets.toml.example` so the production stack (`macy-prod`) runs with the exact same `compose.yaml` + `compose.prod.yaml` topology locally and on a VPS, with its own `.env`, database, subdomain, and OIDC redirect URI, without exposing the database port (`F20 AC1, AC4`, `ADR-012`).

#### Scenario: Production Compose configuration renders cleanly and exposes only Caddy
- **WHEN** `docker compose --env-file .env.prod.example -f compose.yaml -f compose.prod.yaml config` is executed
- **THEN** it renders cleanly with `APP_ENV=production`, `COMPOSE_PROJECT_NAME=macy-prod`, no published database port (`5432`), and healthchecks on every long-running service

### Requirement: Nightly offsite database backups with 14-day retention
The `backup` service SHALL run a nightly `pg_dump` of the database, copy verified dumps to offsite storage, prune backups older than 14 days, and support restoring into a scratch database with matching row counts (`F20 AC4`, `ADR-012`).

#### Scenario: Backup script creates dump, syncs offsite, and prunes files older than 14 days
- **WHEN** `scripts/backup.sh --once` runs against a database and backup directories containing dumps both newer and older than 14 days
- **THEN** a new custom-format dump is written and synced to the offsite directory, dumps older than 14 days are deleted, and recent dumps are kept

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
