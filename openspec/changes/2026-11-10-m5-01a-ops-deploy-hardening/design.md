# Design

**Change:** `2026-11-10-m5-01a-ops-deploy-hardening` · See [proposal.md](proposal.md) for motivation and scope.

## Context

1. `engine/cli.py:_handle_rotate_key` currently calls `engine/integrations/config.py:rotate_workspace_integration_keys()`, leaving `webhook_subscriptions.secret` (raw UTF-8 `whsec_...` token encrypted via `Fernet.encrypt`) unrotated. Meanwhile, `pyproject.toml` `import-linter` forbids `engine.cli -> engine.db`.
2. `engine/services.py:get_retention_connection()` falls back from `RETENTION_DATABASE_URL` to `DATABASE_URL` (connected as `app`) before `DEFAULT_RETENTION_DATABASE_URL`, causing `_execute_purge_expired_data` to raise `PermissionError` whenever `.env` sets `DATABASE_URL` without `RETENTION_DATABASE_URL`. In parallel, `.env.staging.example` omits `RETENTION_DB_PASSWORD`, and `compose.prod.yaml` silently defaults `RETENTION_DB_PASSWORD` to `retention_secret`.
3. `deploy/staging/secrets.toml.example`, `deploy/prod/secrets.toml.example`, and `STREAMLIT_SECRETS_FILE` in `scripts/deploy.sh` remain from pre-M4-07 manual config, duplicating `.env`-driven `/tmp/macy/secrets.toml` generation (`python -m engine.auth`).
4. `scripts/backup.sh` runs inside `postgres:18-alpine` (which does not include `rclone`), while `docs/runbook.md` and `docs/guides/environment-and-credentials.md` suggest `OFFSITE_SYNC_CMD="rclone ..."` and list bootstrap commands (`create-workspace`/`add-owner`) without first initializing `db` + migrations or documenting `--no-deps` consistently.
5. `.github/workflows/deploy.yml` `deploy-prod` silently skips `--require-auth` on `post_deploy_smoke.py` when `PROD_PLAYWRIGHT_STORAGE_STATE` is empty, and invokes full `sh scripts/deploy.sh` on failure rollback (re-running `migrate` from the failed bundle).

## Goals / Non-Goals

**Goals:**
- Make encryption key rotation single-transaction across `workspace_integrations` and `webhook_subscriptions` behind `engine.services` without breaking `import-linter`.
- Guarantee `macy purge` connects under `RETENTION_DATABASE_URL` (never `DATABASE_URL`) and enforce non-default `RETENTION_DB_PASSWORD` in deploy preflight and Compose prod/staging overlays.
- Consolidate OIDC configuration exclusively in `.env` by removing `deploy/*/secrets.toml.example` and `STREAMLIT_SECRETS_FILE` preflight logic.
- Harden production smoke auth gating and make `--rollback <tag>` migration-free.
- Align `docs/runbook.md`, `docs/guides/environment-and-credentials.md`, `.env.staging.example`, and `.env.prod.example`.

**Non-Goals:**
- Runtime plugin healthchecks, OpenRouter model catalogs, `run.blocked` webhook/UI parity (`2026-11-10-m5-01b-runtime-ui-parity`).
- CI `diff-cover`, `db/schema.sql`, Compose dev mounts, or historical ADR cleanup (`2026-11-10-m5-01c-ci-schema-doc-sync`).
- Database schema migrations (neither `webhook_subscriptions` rotation nor strict `RETENTION_DATABASE_URL` resolution requires DDL changes).

## Decisions

### Decision 1: Service-Layer Atomic Key Rotation via `engine.services.rotate_encryption_keys`
- **Choice:** Keep DB row fetch/update primitives in `engine/db.py` (for both `workspace_integrations` and `webhook_subscriptions`), reuse `integrations.config.rotate_secrets()` (which calls `MultiFernet.rotate(raw_bytes)` directly on Fernet tokens — valid for JSON-encoded `workspace_integrations.secrets` and raw UTF-8 `whsec_...` `webhook_subscriptions.secret` alike), parameterize `_execute_rotate_keys(..., commit: bool = True)`, expose `webhooks.rotate_subscription_secrets(..., commit: bool = True)`, and wrap both inside one `with managed_conn.transaction():` block in `services.rotate_encryption_keys()`. Update `engine/cli.py:_handle_rotate_key` to invoke `services.rotate_encryption_keys()`.
- **Why:** Guarantees all-or-nothing commit semantics across both tables without adding a `key_version` migration on `webhook_subscriptions` and naturally honors the `engine.cli -> engine.services` `import-linter` contract.
- **Alternative Considered:** Orchestrating two separate calls from `engine/cli.py`. Rejected because partial failure leaves `workspace_integrations` rotated under `<new>` while `webhook_subscriptions` remains on `<old>`, causing `InvalidToken` errors once `<old>` is removed from `APP_ENCRYPTION_KEY`.

### Decision 2: Strict `RETENTION_DATABASE_URL` Lookup & Mandatory `RETENTION_DB_PASSWORD`
- **Choice:** Change `services.get_retention_connection()` from `database_url or os.environ.get("RETENTION_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_RETENTION_DATABASE_URL` to `database_url or os.environ.get("RETENTION_DATABASE_URL") or DEFAULT_RETENTION_DATABASE_URL`. Require `${RETENTION_DB_PASSWORD:?set RETENTION_DB_PASSWORD}` in `compose.prod.yaml`, add `RETENTION_DB_PASSWORD=CHANGE_ME_retention_role_password` in `.env.staging.example`, add `RETENTION_DB_PASSWORD` to `REQUIRED_VARS` in `scripts/deploy.sh`, and update `tests/test_deploy_script.py` `_make_env_file`.
- **Why:** Fail-fast at config render and deploy preflight prevents silent fallback to `app` credentials (`PermissionError` on first scheduled purge).
- **Alternative Considered:** Rewriting `DATABASE_URL` username/password to `retention` in Python. Rejected because string-munging DSNs breaks when hosts, SSL modes, or credentials differ and obscures misconfiguration.

### Decision 3: Single `.env` OIDC Source (Delete `deploy/*/secrets.toml.example`)
- **Choice:** Delete `deploy/staging/secrets.toml.example`, `deploy/prod/secrets.toml.example`, and the `STREAMLIT_SECRETS_FILE` branch in `scripts/deploy.sh`; update `tests/test_compose_and_docker.py` (assert obsolete files are absent) and `tests/test_deploy_script.py`. Keep `.streamlit/config.toml` `[secrets]` search list (`/tmp/macy/secrets.toml`, `.streamlit/secrets.toml`) untouched so manual local development overrides still work without operational ambiguity.
- **Why:** Removing duplicate templates eliminates the primary onboarding trap where operators hand-edit `secrets.toml` instead of `.env`.
- **Alternative Considered:** Keeping `deploy/*/secrets.toml.example` with deprecation banners. Rejected because maintainers still have to validate placeholders in `deploy.sh` and tests, adding dead maintenance surface.

### Decision 4: Dedicated `scripts/deploy.sh --rollback <tag>` & Default-Strict Production J1 Auth Smoke
- **Choice:**
  - Add `--rollback <tag>` submode to `scripts/deploy.sh`: validates `ENV_FILE` mode/existence and Docker/curl binaries, sets `APP_TAG=<tag>`, pulls `app` and `worker` (unless `SKIP_PULL=1`), recreates only `app` and `worker` (`--no-build --wait --wait-timeout`), and runs `smoke` (`/ _stcore/health`), skipping `db`, `migrate`, `caddy`, `backup`, and role password mutations.
  - Update `.github/workflows/deploy.yml` `deploy-prod` rollback step to execute `sh scripts/deploy.sh --rollback "$PREV_TAG"`, and fail fast in the post-deploy smoke step if `PROD_PLAYWRIGHT_STORAGE_STATE` (or `PLAYWRIGHT_STORAGE_STATE`) is empty unless `vars.PROD_SMOKE_ALLOW_UNAUTHENTICATED == 'true'`.
- **Why:** Forward-only schema migrations already applied (or partially tested) by a newer release MUST NOT be re-invoked from the newer release bundle during rollback; likewise, silent degradation of `deploy-prod` smoke from authenticated J1 to health-only hides broken OIDC or UI regressions.
- **Alternative Considered:** Downloading the previous Git tag's archive during rollback. Rejected as slower, fragile with deleted tags/detached SHAs, and unnecessary when forward-only SQL migrations remain compatible per `docs/runbook.md`.

### Decision 5: Explicit Env Template Defaults & Host-Side `rclone` Boundary
- **Choice:**
  - Set `APP_TAG=CHANGE_ME_release_tag` in `.env.prod.example` (never mutable `latest` in prod baseline), add `RETENTION_DB_PASSWORD` + `BACKUP_RETENTION_DAYS`/`BACKUP_INTERVAL_SECONDS`/`OFFSITE_BACKUP_PATH`/`OFFSITE_SYNC_CMD` consistently to `.env.staging.example`, and clarify `DOMAIN`/`CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (`80`/`443` default with `8080`/`8443` commented for side-by-side local rehearsal) in `.env.prod.example`.
  - Document in `docs/runbook.md` and `docs/guides/environment-and-credentials.md` that `OFFSITE_SYNC_CMD` executes inside `postgres:18-alpine` (`sh`/POSIX utilities only), while external tools like `rclone` run via host cron/systemd timer syncing `$OFFSITE_BACKUP_PATH`. Reorder VPS bootstrap so `scripts/deploy.sh` runs before `dc run --rm --no-deps app macy create-workspace/add-owner/seed-brand/seed-sources`.

## Risks / Trade-offs

- [Making `RETENTION_DB_PASSWORD` required breaks existing local `.env` files lacking that key] → Document upgrade note in `docs/runbook.md` and `scripts/deploy.sh` preflight error (`RETENTION_DB_PASSWORD is empty or missing`).
- [Requiring `PROD_PLAYWRIGHT_STORAGE_STATE` blocks initial `v*` production tag push until OIDC session JSON is captured] → Document two-step first-release option (`PROD_SMOKE_ALLOW_UNAUTHENTICATED=true` variable or capture session during staging/initial deploy, then remove variable) explicitly in `docs/runbook.md`.
- [Removing `deploy/*/secrets.toml.example` breaks any external script passing `STREAMLIT_SECRETS_FILE`] → Search repo for references (none outside `scripts/deploy.sh` and unit tests) and note removal in PR release notes.

## Migration Plan

1. Deploy updated code and `compose.prod.yaml` / `scripts/deploy.sh` to staging (`dev -> main`), verifying `RETENTION_DB_PASSWORD` is present in `/opt/macy/staging/.env`.
2. Re-run `macy rotate-key` test/drill on staging when rotating keys so both `workspace_integrations` and `webhook_subscriptions` re-encrypt cleanly.
3. For initial production tag before OIDC session state capture, set `PROD_SMOKE_ALLOW_UNAUTHENTICATED=true`, deploy `v*`, capture `PROD_PLAYWRIGHT_STORAGE_STATE`, and unset `PROD_SMOKE_ALLOW_UNAUTHENTICATED`.

## Open Questions

- None blocking implementation; minor preference on whether `.env.prod.example` defaults `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` to `80`/`443` (VPS first, commented `8080`/`8443` for local side-by-side) vs `8080`/`8443` can be adjusted without affecting specs.
