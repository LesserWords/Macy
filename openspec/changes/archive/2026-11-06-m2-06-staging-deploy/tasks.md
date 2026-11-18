# Tasks: Staging on the VPS

## 1. Image and Compose
- [x] 1.1 Dockerfile ships `app.py`, `locales/`, `packs/`, `prompts/`; `app` runs Streamlit with healthcheck; `worker` healthcheck; `APP_ENV` wired.
- [x] 1.2 `compose.prod.yaml`: Caddy (ports, healthcheck), pinned images via `APP_IMAGE`/`APP_TAG`, OIDC secrets mount, backup healthcheck.
- [x] 1.3 `Caddyfile` hardening (compression, security headers).
- [x] 1.4 `.env.example`, `.env.staging.example`, `deploy/staging/secrets.toml.example`, `.gitignore` rules.

## 2. Deploy automation
- [x] 2.1 `scripts/deploy.sh` with preflight, one-shot migrate, health check, rollback.
- [x] 2.2 `.github/workflows/deploy.yml` (GHCR build, SSH deploy, gated by `STAGING_DEPLOY_ENABLED`).
- [x] 2.3 CI: staging compose validation and shellcheck.

## 3. Docs and tests
- [x] 3.1 `docs/runbook.md`.
- [x] 3.2 Tests in `tests/test_compose_and_docker.py` and `tests/test_deploy_script.py`.
- [x] 3.3 Local rehearsal: staging stack up on baselines, HTTPS health through Caddy, DB port unpublished, broken-migration and rollback drills.
- [x] 3.4 Update ADR-012, ADR-019, F20 and README; run all quality gates.
