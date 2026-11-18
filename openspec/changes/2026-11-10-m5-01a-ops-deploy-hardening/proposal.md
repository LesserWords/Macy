# Proposal

**Plan Item:** M5-01a (Post-M5-01 Operations, Key Rotation & Deploy Hardening)
**Features:** F05, F06, F20
**ADRs:** ADR-008, ADR-012, ADR-019, ADR-024

## Why

A post-M5-01 maintainer and operator audit exposed silent runtime and operational failure modes across production deployment, key rotation, data retention, backups, and runbooks. Specifically, `content-engine rotate-key` ignores `webhook_subscriptions.secret`, `get_retention_connection()` falls back to application `DATABASE_URL` and fails role enforcement whenever `RETENTION_DATABASE_URL` is omitted, `deploy-prod` allows silent unauthenticated smoke bypass and invokes full `scripts/deploy.sh` (including new-bundle migrations) on rollback, and stale `deploy/*/secrets.toml.example` templates plus inaccurate `rclone`/bootstrap steps in `docs/runbook.md` can mislead operators into broken setups.

## What Changes

- **Atomic MultiFernet key rotation across integrations and webhooks:** Introduce `services.rotate_encryption_keys()` so a single database transaction re-encrypts both `workspace_integrations.secrets` (incrementing `key_version`) and `webhook_subscriptions.secret`, wired through `content-engine rotate-key` while preserving the `import-linter` contract (`engine.cli` -> `engine.services`, never `engine.db`).
- **Fail-fast retention role wiring:** Remove the `DATABASE_URL` fallback from `services.get_retention_connection()`, make `RETENTION_DB_PASSWORD` required in `.env.staging.example`, `compose.prod.yaml` (`${RETENTION_DB_PASSWORD:?...}`), and `scripts/deploy.sh` preflight, and reject default/placeholder `retention_secret` values consistently across staging and production.
- **Production smoke & rollback hardening:** Update `.github/workflows/deploy.yml` `deploy-prod` to fail fast unless `PROD_PLAYWRIGHT_STORAGE_STATE` is provided for `--require-auth` J1 verification (or explicit `PROD_SMOKE_ALLOW_UNAUTHENTICATED=true` opt-out is set), and add a migration-free rollback mode (`scripts/deploy.sh --rollback <tag>`) so failed production rollbacks pull/recreate `app` and `worker` at `PREV_TAG` and verify health without re-running `migrate` from the failed bundle.
- **Single `.env` OIDC baseline & template alignment (**BREAKING** for `deploy/*/secrets.toml.example` and `STREAMLIT_SECRETS_FILE` deploy check):** Remove `deploy/staging/secrets.toml.example`, `deploy/prod/secrets.toml.example`, and `STREAMLIT_SECRETS_FILE` branching in `scripts/deploy.sh`, aligning `.env.staging.example`, `.env.prod.example`, and `compose.prod.yaml` on `.env`-driven OIDC secret generation (`python -m engine.auth`), explicit non-`latest` release `APP_TAG` guidance in prod, and matching backup/retention variables.
- **Backup offsite sync boundary & runbook parity:** Clarify in `docs/runbook.md` and `docs/guides/environment-and-credentials.md` that `scripts/backup.sh` runs inside `postgres:18-alpine` (without `rclone`), so `rclone` remote syncing must run on the VPS host against `$OFFSITE_BACKUP_PATH`; reorder VPS first-time setup so `scripts/deploy.sh` initializes `db` + migrations before CLI workspace bootstrap (`create-workspace`, `add-owner`, `seed-brand`, `seed-sources`), fix `rotate-key` docs, and update cron examples to match runtime `content-engine run-week`.

## Out of Scope

- Adapter credential enforcement / live `healthcheck()` probes, OpenRouter model IDs, `run.blocked` webhook/UI parity, `code_version`/`pack_version` stamping, `run-stage` exit codes, `run-now --offline` worker race guard, UI offline mode/Dashboard cap/i18n, and `n8n-publish.md`/`user-guide.md` updates (covered by `2026-11-10-m5-01b-runtime-ui-parity`).
- CI `diff-cover` `push` comparison fix, `test_live_e2e_j0_j4.py` selector/journey strengthening, `compose.override.yaml` dev mounts, `db/schema.sql` regeneration, and `DATA-MODEL.md`/historical ADR/PRD cleanup (covered by `2026-11-10-m5-01c-ci-schema-doc-sync`).
- Bundling `supercronic` inside the worker container or altering `approvals` `campaigns.created_by` RBAC.

## Capabilities

### New Capabilities
- None (refines existing operational, integration, and webhook contracts).

### Modified Capabilities
- `operations`: Harden retention connection/password enforcement, remove legacy `deploy/*/secrets.toml.example` baseline, clarify container vs host-side `rclone` backup boundary, require authenticated prod J1 smoke by default with migration-free rollback, and align `docs/runbook.md` and `docs/guides/environment-and-credentials.md`.
- `integrations`: Extend MultiFernet key rotation to execute atomically across both `workspace_integrations.secrets` and `webhook_subscriptions.secret` via `services`.
- `webhooks`: Ensure stored `webhook_subscriptions.secret` ciphertexts rotate atomically alongside integrations while preserving Standard Webhooks HMAC-SHA256 signing compatibility.

## Key Results: Delivered

All `D1`–`D4` must pass before `M5-01a` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | Atomic MultiFernet key rotation re-encrypts both `workspace_integrations.secrets` (incrementing `key_version`) and `webhook_subscriptions.secret` in one transaction, rolls back both on failure, and rotated webhook secrets verify HMAC signatures under the new key alone | `uv run pytest tests/test_integrations.py tests/test_webhooks.py tests/test_cli.py -k rotate --no-cov && uv run lint-imports` exits `0` |
| **D2** | `services.get_retention_connection()` never falls back to `DATABASE_URL`; `RETENTION_DB_PASSWORD` is required across `.env.staging.example`, `compose.prod.yaml`, and `scripts/deploy.sh` preflight | `uv run pytest tests/test_deploy_script.py tests/test_compose_and_docker.py -k retention --no-cov` exits `0` |
| **D3** | Legacy `deploy/{staging,prod}/secrets.toml.example` and `STREAMLIT_SECRETS_FILE` deploy branching are removed; `.env` OIDC and `BACKUP_*`/`RETENTION_DB_PASSWORD` templates align across staging and prod | `docker compose --env-file .env.staging.example -f compose.yaml -f compose.prod.yaml config -q` and `--env-file .env.prod.example` exit `0`; placeholder preflight exits non-zero |
| **D4** | `scripts/deploy.sh --rollback <tag>` restores `app` + `worker` and runs smoke without running `migrate`; `.github/workflows/deploy.yml` enforces `PROD_PLAYWRIGHT_STORAGE_STATE` (or explicit `PROD_SMOKE_ALLOW_UNAUTHENTICATED=true`) | `shellcheck scripts/deploy.sh && uv run pytest tests/test_deploy_script.py tests/test_compose_and_docker.py --no-cov && sh scripts/check.sh` exits `0` |

## Impact

- **Code & Scripts:** `engine/integrations/config.py`, `engine/webhooks.py`, `engine/db.py`, `engine/services.py`, `engine/cli.py`, `scripts/deploy.sh`, `.github/workflows/deploy.yml`
- **Infra & Env Templates:** `compose.prod.yaml`, `.env.staging.example`, `.env.prod.example`, deletion of `deploy/staging/secrets.toml.example` and `deploy/prod/secrets.toml.example`
- **Tests:** `tests/test_deploy_script.py`, `tests/test_compose_and_docker.py`, `tests/test_cli.py`, `tests/test_webhooks.py` / `tests/test_integrations.py`, retention service tests
- **Docs:** `docs/runbook.md`, `docs/guides/environment-and-credentials.md`
