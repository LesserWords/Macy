# Proposal: Staging on the VPS (S1)

## Why
F20 AC2 requires every merge to `main` to deploy staging automatically with migrations run once (ADR-012, ADR-019, scope change S1). No VPS, domain or OAuth credentials exist yet, so everything that can be built and proven locally is delivered now, driven only by environment baselines (`.env.example`, `.env.staging.example`). The VPS-specific steps (public HTTPS, port scan from outside, OAuth sign-in, first real deploy) are recorded as pending in the plan.

## What Changes
1. **Image actually serves the UI:** the runtime image now contains `app.py`, `locales/`, `packs/` and `prompts/`; the `app` service runs Streamlit with a real healthcheck (`/_stcore/health`); the `worker` gets a healthcheck. `APP_ENV` is passed to containers (the engine reads `APP_ENV`, not `ENVIRONMENT`).
2. **Staging/prod overlay (`compose.prod.yaml`):** Caddy with configurable ports and healthcheck, images pinned through `APP_IMAGE`/`APP_TAG`, Streamlit OIDC secrets mounted read-only from a git-ignored file, no published database port, every long-running service has a healthcheck.
3. **Baseline env files:** `.env.example` (dev) and `.env.staging.example` (staging) document every variable compose consumes; real files stay git-ignored.
4. **`scripts/deploy.sh`:** preflight (env file present, mode 600, required variables set, placeholder secrets rejected), pull, migrate once before touching the running app, recreate, health-check, smoke test, automatic rollback of the app to the previous tag.
5. **`.github/workflows/deploy.yml`:** on merge to `main`, build and push to GHCR, then SSH deploy using a short-lived token; gated by the repository variable `STAGING_DEPLOY_ENABLED` so merges stay green until the VPS exists.
6. **`docs/runbook.md`:** deploy, rollback, logs, restart, first-time VPS preparation.
7. **CI:** `compose-config` validates the staging combination and runs shellcheck on the deploy script.

## Capabilities Affected
- `operations`
