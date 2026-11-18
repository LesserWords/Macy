# Proposal: M5-01 — Production release, monitoring & workspace readiness

## Why
Before beginning the 4-week M5-02 production pilot (`docs/plans/M5.md`), Macy must bump to release `0.1.0` (`v0.1.0`), support automated production deployment on `v*` git tags with a post-deploy read-only `J1 + health` smoke test and rollback (`F20 AC3`, `ADR-012`, `ADR-019`), wire production monitoring end to end (runtime Sentry capture & secret scrubbing + `test-sentry`, weekly heartbeat `/fail` signalling, backup failure alert + heartbeat) (`F20 AC5`, `F20 AC6`, `ADR-023`), and close operational runtime gaps for unattended production runs and workspace onboarding (`create-workspace` CLI, live plugin resolution in `execute_run`, `crontab`/`run-week` CLI parity, and worker loop execution).

## What Changes
1. **Release Tagging (`v0.1.0`), Prod Deploy & Post-Deploy Smoke (`pyproject.toml`, `engine/__init__.py`, `uv.lock`, `.github/workflows/deploy.yml`, `scripts/post_deploy_smoke.py`):**
   - Bump package version to `0.1.0`.
   - Extend `.github/workflows/deploy.yml` with `push.tags: ['v*']` (`build-prod`, `deploy-prod` under `environment: production`, gated by `PROD_DEPLOY_ENABLED == 'true'`), include `scripts/backup.sh` in both staging and prod release bundles, execute `scripts/post_deploy_smoke.py` (`/_stcore/health` + Playwright J1 read-only verification) against `PROD_APP_URL`, and roll back to the previous deployed image tag on failure while keeping the workflow job failed.
2. **Production Monitoring (`engine/observability.py`, `engine/pipeline.py`, `engine/cli.py`, `app.py`, `scripts/backup.sh`, `compose.prod.yaml`, `.env.prod.example`, `.env.staging.example`):**
   - Enhance `scrub_sensitive_data()` / `_scrub_value()` to redact inline DSN passwords, Bearer tokens, and sensitive query parameters/keys in strings as well as dict keys.
   - Add `ensure_sentry_initialized()` and `capture_exception(..., workspace_id, run_id)` and wire them into `app.py`, `engine/pipeline.py`, `engine/cli.py`, and `engine/__main__.py`.
   - Add `content-engine test-sentry` CLI command to send and flush a controlled test exception tagged with `workspace_id` / `run_id`.
   - Update `send_heartbeat(..., failed=bool)` and `_finish_run()` so failed runs ping `${HEARTBEAT_URL}/fail` while completed runs reset the weekly `HEARTBEAT_URL` window.
   - Update `scripts/backup.sh`, `compose.prod.yaml`, `.env.prod.example`, and `.env.staging.example` with `BACKUP_HEARTBEAT_URL` (success and `/fail` pings) and `BACKUP_ALERT_URL` JSON failure webhooks plus `.last_backup_ok` removal on failure.
3. **Production Runtime & Bootstrap Readiness (`engine/services.py`, `engine/pipeline.py`, `engine/cli.py`, `engine/__main__.py`):**
   - Add `services.create_workspace()` and `content-engine create-workspace <slug> [--name ...] [--pack ...] [--timezone ...]`.
   - Resolve workspace integrations (`plugin_config`) for non-offline/production pipeline runs (`RSSFeedSource` / `TrafilaturaExtractor` defaults for `feed_source`/`extractor`, `blocked(integration_missing)` when `llm`/`news_search` is unconfigured), support `offline` flag propagation from `services.run_week` & `services.run_stage`, call `report()` after `run_week` completion, and align `cli run-week` with `crontab` `--workspace` output.
   - Implement `engine.__main__.run_worker_tick()` to process queued runs (`process_next_queued_run`) and pending webhooks (`deliver_due`).

## Capabilities Affected
- `operations`
- `access`
- `runs`
