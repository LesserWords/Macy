# F20 — Operations: deploy, backups, monitoring, retention

**Status:** Implemented (M0-03, M0-08, M2-06, M4-06, M5-01 verified locally/CI; live VPS rollout & M5-02 pilot pending external host/credentials) · **Milestone:** M0, M2, M4, M5 · **Plan items:** M0-03, M0-08, M2-06, M4-06, M5-01 · **ADRs:** 012, 019, 023, 024 · **OpenSpec capability:** `operations`

## User story
As the operator, the system deploys the same way everywhere, backs itself up, tells me when something fails, and deletes data it shouldn't keep.

## Scope
- In:
  - Dockerfile and Compose files;
  - staging (M2) and prod (M4/M5) Compose projects on the VPS;
  - Caddy;
  - GitHub Actions deploy;
  - nightly offsite `pg_dump` and a restore test;
  - Sentry;
  - heartbeat;
  - retention purge job;
  - LGPD user anonymisation;
  - runbook.
- Out: multi-host high availability, Kubernetes.

## Acceptance criteria
- **AC1** WHEN `docker compose up` runs locally, THEN the database, migrations, app and worker start healthy with no host-installed Python or Postgres.
- **AC2** WHEN a PR merges to `main` (from M2), THEN staging is deployed automatically and migrations run once.
- **AC3** WHEN a `v*` tag is pushed (from M5), THEN prod is deployed and the post-deploy smoke test (J1 read-only + health) passes. Otherwise the deploy is reported as failed.
- **AC4** WHEN the nightly backup runs, THEN a dump lands offsite; 14 days are kept; one documented restore into a scratch database succeeds.
- **AC5** WHEN an unhandled exception occurs, THEN Sentry receives it, tagged with `workspace_id` and `run_id`, with no secrets included.
- **AC6** WHEN a weekly run doesn't ping the heartbeat within its window, THEN an alert is sent.
- **AC7** WHEN the purge job runs, THEN data is deleted or nulled per ADR-024, and only by the `retention` role.
- **AC8** WHEN `cli anonymise-user <email>` runs, THEN the user's email and name are anonymised and their events keep only an opaque id.

## Data
All tables (retention); database roles

## Interfaces
- `compose.*.yaml`, `scripts/deploy.sh`, `scripts/backup.sh`, `scripts/post_deploy_smoke.py`
- `.github/workflows/{ci,e2e-live,deploy}.yml`
- `content-engine create-workspace`, `content-engine test-sentry`
- `cli purge`
- `cli anonymise-user`
- `docs/runbook.md`

## Tests
| Seam | Test | E2E |
|---|---|---|
| CI | `docker compose config` for every file; image builds; `shellcheck` + `deploy.yml` tests (`tests/test_compose_and_docker.py`, `tests/test_deploy_script.py`) | — |
| unit / integration | Purge job (`tests/test_retention.py`), Sentry/heartbeat scrubbing (`tests/test_observability.py`), post-deploy smoke checks (`tests/test_post_deploy_smoke.py`), live adapter blocking & worker loop (`tests/test_pipeline.py`, `tests/test_package.py`) | — |
| ops | Local `macy-prod` Compose rehearsal (deploy, failed-smoke rollback, `backup.sh --once` + alert webhook, `test-sentry`) | `scripts/post_deploy_smoke.py` (health + J1) |

## Dependencies
—
