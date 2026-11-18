# Tasks

## 1. Release Tagging (`v0.1.0`), Prod Deploy & Post-Deploy J1 Read-Only Smoke (`F20 AC3`)
- [x] 1.1 Write failing tests in `tests/test_package.py`, `tests/test_compose_and_docker.py`, and `tests/test_post_deploy_smoke.py` covering version `0.1.0`, `deploy.yml` `v*` production trigger/`PROD_DEPLOY_ENABLED`/`scripts/backup.sh` bundling/post-deploy smoke & rollback, and `scripts/post_deploy_smoke.py` health + read-only J1 checks (passing & failing cases).
- [x] 1.2 Update `engine/__init__.py`, `pyproject.toml`, `uv.lock`, `.github/workflows/deploy.yml`, and implement `scripts/post_deploy_smoke.py`.

## 2. Production Monitoring & Backup Failure Alerts (`F20 AC5`, `F20 AC6`)
- [x] 2.1 Write failing tests in `tests/test_observability.py`, `tests/test_cli.py`, and `tests/test_deploy_script.py` covering DSN/Bearer/query secret string scrubbing, `ensure_sentry_initialized()`, `capture_exception()` with `workspace_id`/`run_id`, `content-engine test-sentry`, `send_heartbeat(failed=True)` `/fail` ping, and `scripts/backup.sh` `BACKUP_HEARTBEAT_URL`/`BACKUP_ALERT_URL` on success and failure.
- [x] 2.2 Implement Sentry runtime wiring & enhanced scrubbing in `engine/observability.py`, `app.py`, `engine/cli.py`, weekly `/fail` heartbeat in `engine/pipeline.py`, and backup heartbeat/alert handling in `scripts/backup.sh`, `compose.prod.yaml`, `.env.prod.example`, and `.env.staging.example`.

## 3. Production Pipeline Integrations, Bootstrap CLI & Worker Loop Readiness
- [x] 3.1 Write failing tests in `tests/test_pipeline.py`, `tests/test_cli.py`, and `tests/test_package.py` covering live plugin resolution in `execute_run` (`blocked(integration_missing)` when `offline=False`/prod lacks `llm` or `news_search`, plus resume when configured), `services.create_workspace` / `content-engine create-workspace`, `cli run-week --workspace` parity with `crontab`, `services.run_week` compiling `report()`, and `engine.__main__.run_worker_tick()`.
- [x] 3.2 Implement `create_workspace` (`engine/services.py`, `engine/cli.py`), live adapter resolution & `integration_missing` blocking/resume (`engine/pipeline.py`, `engine/services.py`), `--workspace` flag on `cli run-week` (`engine/cli.py`), and `run_worker_tick()` (`engine/__main__.py`).

## 4. Docs Update, Local `macy-prod` Rehearsal & Quality Gates
- [x] 4.1 Rehearse `macy-prod` locally (deploy + `scripts/post_deploy_smoke.py` + rollback check + backup alert rehearsal + scrubbed Sentry verification) and update `docs/runbook.md`, `docs/features/F20-operations.md`, `docs/adr/ADR-019-git-ci-pr-workflow.md`, `docs/guides/developer-guide.md`, and `docs/plans/M5.md`.
- [x] 4.2 Run full `scripts/check.sh` suite (`ruff`, `mypy`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk`, `check_translations.py`, `pytest`, `diff-cover == 100%` vs `origin/dev`, `docker compose config`, `openspec validate --all --strict`).
