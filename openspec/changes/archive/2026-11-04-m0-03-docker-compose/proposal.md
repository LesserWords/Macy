## Why

Plan item **M0-03** (docs/plans/M0.md). Containerise the application and worker into a single multi-stage Docker image, set up Compose configurations for dev, CI/offline E2E, and production, provide PostgreSQL with dbmate migrations runner, and wire SQL migration linting via squawk into pre-commit and CI. Features: F20 (AC1). ADRs: 011, 012.

## What Changes

- Multi-stage `Dockerfile` using `uv` and non-root user (`appuser`), shared between app and worker.
- `compose.yaml` (base stack: `db` pinned to postgres 18.x, `migrate` with dbmate one-shot, `app` Streamlit placeholder, `worker` placeholder, and optional `rsshub` profile).
- `compose.override.yaml` (local dev bind-mounts, hot reload, published DB port).
- `compose.e2e.yaml` (CI offline E2E with test mock services: `webhook-receiver` and fake OIDC placeholder).
- `compose.prod.yaml` (production skeleton: `caddy`, `backup` container, `unless-stopped` restart policy, pinned images).
- `db/migrations/` skeleton directory.
- Wire `squawk` SQL linter into `.pre-commit-config.yaml`, `scripts/check.sh`, and GitHub Actions CI (`ci.yml` `compose-config` and `squawk` checks).
- Update `README.md` documenting `docker compose up` as the single local setup command.
- Add test suite `tests/test_compose_and_docker.py` validating compose configurations and Dockerfile structure.

Out of scope: actual business schema tables (M0-04), staging deployment to VPS (M2-06), and offsite backups runner implementation (M4-06).

## Impact

- New: `Dockerfile`, `.dockerignore`, `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, `compose.prod.yaml`, `db/migrations/.gitkeep`, `tests/test_compose_and_docker.py`.
- Modified: `scripts/check.sh`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `README.md`, `docs/adr/ADR-011-postgres-and-migrations.md`, `docs/adr/ADR-012-docker-compose-and-hosting.md`.
