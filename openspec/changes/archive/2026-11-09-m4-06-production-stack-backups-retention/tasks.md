# Tasks

## 1. Production Stack Baselines, Backup Script & Compose Wiring (M4-06 Steps 1 & 2)
- [x] 1.1 Write failing tests in `tests/test_compose_and_docker.py` and `tests/test_deploy_script.py` asserting `.env.prod.example`, `deploy/prod/secrets.toml.example`, `scripts/backup.sh` (`--once`, 14-day retention pruning, offsite copy), and `compose.prod.yaml` backup service configuration.
- [x] 1.2 Create `.env.prod.example` (`APP_ENV=production`, `COMPOSE_PROJECT_NAME=macy-prod`), `deploy/prod/secrets.toml.example` (prod OIDC redirect URI), and `scripts/backup.sh`, and wire `compose.prod.yaml`, `scripts/deploy.sh`, and `.github/workflows/ci.yml` (`compose-config`) to validate the prod baseline and backup script.
- [x] 1.3 Run a live Docker rehearsal of the backup and restore flow against PostgreSQL 18 (`scripts/backup.sh --once` -> offsite dump -> `pg_restore` into `macy_restore_scratch` -> compare row counts) and record the verification in `docs/runbook.md` and `docs/plans/M4.md`.

## 2. Nightly Retention Purge & LGPD User Anonymisation (M4-06 Step 3)
- [x] 2.1 Write failing tests in `tests/test_retention.py` and `tests/test_cli.py` covering `F20 AC7` (90d `llm_payloads`, 365d `documents.extracted_text` nulled with metadata kept, 30d `delivered`/`dead` `webhook_deliveries`, 24m `events`, and refusal when run as `app` role instead of `retention` role) and `F20 AC8` (`cli anonymise-user <email>` anonymises `users.email` and `users.name`, removes memberships, and keeps only opaque IDs in `events`).
- [x] 2.2 Add migration `db/migrations/20261109000003_retention_and_privacy.sql` granting `SELECT ON usage_ledger TO retention` and providing `anonymise_user_events(uuid)` (`SECURITY DEFINER`) so `events` remains append-only for `app` while allowing PII scrubbing on user deletion.
- [x] 2.3 Implement `db.purge_expired_data()`, `db.anonymise_user()`, `services.purge_expired_data()`, `services.anonymise_user()`, CLI commands `content-engine purge` and `content-engine anonymise-user`, and nightly purge entry in `scheduler.generate_crontab()`.

## 3. Documentation, OpenSpec Sync & Quality Gates
- [x] 3.1 Update `docs/runbook.md`, `docs/features/F20-operations.md`, `docs/adr/ADR-012-docker-compose-and-hosting.md`, `docs/adr/ADR-024-retention-and-privacy.md`, and `docs/plans/M4.md`.
- [x] 3.2 Sync OpenSpec specs (`operations`, `access`) and pass all quality gates (`ruff`, `mypy`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk`, `check_translations.py`, `pytest`, `diff-cover == 100%`, `openspec validate --all --strict`).
