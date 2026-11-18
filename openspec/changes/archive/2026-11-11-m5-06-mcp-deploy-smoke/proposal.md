# Proposal: M5-06 — MCP Deployment Integration, Smoke, Offline/Live E2E & Operator Docs

**Plan Item:** M5-06 ([docs/plans/M5.md](../../../docs/plans/M5.md)) · **Features:** F20, F21 · **ADRs:** ADR-012, ADR-018, ADR-019, ADR-023, ADR-027

## Why

Once all MCP tools, resources, and fail-closed guards work locally (`M5-03`–`M5-05`), the MCP server must deploy through Macy’s exact container stack (`compose.yaml`, `compose.prod.yaml`, `Caddyfile`), release scripts (`scripts/deploy.sh`), post-deploy verification (`scripts/post_deploy_smoke.py`), CI config/E2E gates, and operator runbooks without regressing security invariants verified in `tests/test_compose_and_docker.py` (no exposed DB/app/MCP host ports in prod, secrets kept out of `x-app-env`, non-empty preflight secrets, and healthchecks on every service).

## What Changes

- **Docker Compose & Caddy Routing:**
  - Add opt-in `mcp` service (`profiles: ["mcp"]`) in `compose.yaml` reusing the single runtime image (`content-engine mcp --transport streamable-http --host 0.0.0.0 --port 8765`), with `/healthz` Docker healthcheck and scoped `MCP_AUTH_TOKEN`/`MCP_ACTOR_EMAIL` env vars (explicitly excluded from shared `x-app-env` in `compose.prod.yaml`).
  - Configure `compose.override.yaml` (dev hot-reload mount + `127.0.0.1:8765`), `compose.e2e.yaml` (test MCP HTTP service), and `compose.prod.yaml` (zero published host ports on `mcp`).
  - Update `Caddyfile` to route `/mcp*` -> `mcp:8765` while routing all other traffic -> `app:8501`.
- **Environment Baselines, Deploy & Smoke Verification:**
  - Add `MCP_ENABLED`, `COMPOSE_PROFILES`, `MCP_AUTH_TOKEN`, `MCP_ACTOR_EMAIL` placeholders/defaults to `.env.staging.example` and `.env.prod.example`.
  - Update `scripts/deploy.sh` preflight (reject missing/`CHANGE_ME` `MCP_AUTH_TOKEN` and empty `MCP_ACTOR_EMAIL` when `MCP_ENABLED=true`), rollout (`pull` / `up -d mcp` when enabled), and `--rollback` to restore `mcp` cleanly alongside `app` and `worker`.
  - Extend `scripts/post_deploy_smoke.py` with `--check-mcp` to verify unauthenticated `/mcp` returns `401` and optional authenticated `initialize` + `macy_list_workspaces` succeeds read-only.
- **CI, Offline E2E & Nightly Live E2E:**
  - Extend `.github/workflows/ci.yml` `compose-config` matrix (validating `--profile mcp` configs + preflight failure checks), `.github/workflows/deploy.yml` (`--check-mcp` gate on prod deploy when `vars.MCP_ENABLED == 'true'`), and `.github/workflows/e2e-live.yml` (`tests/test_live_e2e_mcp.py`).
  - Add offline E2E HTTP MCP roundtrip test (`tests/test_journey_mcp_e2e.py`) and live staging smoke suite (`tests/test_live_e2e_mcp.py`).
- **Operator & Client Documentation:** Create `docs/MCP.md` (Antigravity, Claude Desktop, Claude Code, Cursor JSON snippets) and update `docs/runbook.md` & `docs/guides/developer-guide.md`.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `mcp-server`: Add client setup guide, offline container HTTP E2E journey, and nightly live E2E MCP suite.
- `operations`: Integrate `mcp` service across Compose overlays, Caddy `/mcp*` routing, `scripts/deploy.sh` preflight/rollout/rollback, `scripts/post_deploy_smoke.py --check-mcp`, CI/CD workflows, and `docs/runbook.md`.

## Out of Scope

- Changing `evals/` packaging or bundling `evals/` into `Dockerfile`.
- Public unauthenticated `/mcp` endpoints or per-user OAuth 2.1 DCR (`ADR-027` revisit trigger).

## Impact

- **Infra & Scripts:** `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, `compose.prod.yaml`, `Caddyfile`, `.env.staging.example`, `.env.prod.example`, `scripts/deploy.sh`, `scripts/post_deploy_smoke.py`
- **CI/CD Workflows:** `.github/workflows/{ci,deploy,e2e-live}.yml`
- **Tests:** `tests/test_compose_and_docker.py`, `tests/test_deploy_script.py`, `tests/test_post_deploy_smoke.py`, `tests/test_journey_mcp_e2e.py`, `tests/test_live_e2e_mcp.py`
- **Docs:** `docs/MCP.md`, `docs/runbook.md`, `docs/guides/developer-guide.md`
