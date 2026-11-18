# Tasks: M5-06 — MCP Deployment Integration, Smoke, Offline/Live E2E & Operator Docs

**PR:** `M5-06` (branch `m5-06-mcp-deploy-smoke`, `<= ~400` non-test LOC per `ADR-019`) · **Plan:** [docs/plans/M5.md](../../../docs/plans/M5.md)

## 1. Compose Topology, Caddy Routing & Env Baselines

- [x] 1.1 Write failing infra tests in `tests/test_compose_and_docker.py` asserting: `compose.yaml` defines `mcp` (`profiles: ["mcp"]` + `/healthz`), `compose.prod.yaml` exposes zero `5432`/`8501`/`8765` host ports and excludes `MCP_AUTH_TOKEN` from `x-app-env` + omits `MACY_TEST_USER_EMAIL`, `Caddyfile` proxies `/mcp*` -> `mcp:8765`, and `.env.{staging,prod}.example` include `MCP_ENABLED`/`COMPOSE_PROFILES`/`MCP_AUTH_TOKEN`/`MCP_ACTOR_EMAIL` — verify: `uv run pytest tests/test_compose_and_docker.py --no-cov` fails before Compose updates
- [x] 1.2 Update `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, `compose.prod.yaml`, `Caddyfile`, `.env.staging.example`, and `.env.prod.example` — verify: `docker compose --env-file .env.prod.example -f compose.yaml -f compose.prod.yaml --profile mcp config -q` and dev/E2E configs exit 0, and `tests/test_compose_and_docker.py` passes

## 2. Deploy Script, Rollback, Smoke Check & CI Workflows

- [x] 2.1 Write failing tests in `tests/test_deploy_script.py` and `tests/test_post_deploy_smoke.py` covering `MCP_ENABLED=true` placeholder rejection (`CHANGE_ME`), `mcp` rollout & migration-free `--rollback`, and `post_deploy_smoke.py --check-mcp` (`401` on unauthenticated `/mcp`, `200` on authenticated `initialize` + `macy_list_workspaces`) — verify: `uv run pytest tests/test_deploy_script.py tests/test_post_deploy_smoke.py --no-cov` fails before script updates
- [x] 2.2 Update `scripts/deploy.sh`, `scripts/post_deploy_smoke.py`, `.github/workflows/ci.yml` (`compose-config` matrix + preflight check), and `.github/workflows/deploy.yml` (`--check-mcp` gate on prod deploy) — verify: `shellcheck scripts/deploy.sh scripts/backup.sh` and `uv run pytest tests/test_deploy_script.py tests/test_post_deploy_smoke.py --no-cov` exit 0

## 3. Offline E2E, Live E2E & Operator Documentation

- [x] 3.1 Write failing container E2E test `tests/test_journey_mcp_e2e.py` and staging live E2E test `tests/test_live_e2e_mcp.py` (`@pytest.mark.live_e2e`), and wire `tests/test_live_e2e_mcp.py` into `.github/workflows/e2e-live.yml` — verify: `uv run pytest tests/test_journey_mcp_e2e.py --no-cov` fails until E2E wiring completes
- [x] 3.2 Run `tests/test_journey_mcp_e2e.py` against `compose.yaml` + `compose.e2e.yaml` (`COMPOSE_PROFILES=mcp`, real Postgres + `webhook-receiver`) covering HTTP MCP discovery -> Gate 1 -> draft -> claim check -> Gate 2 -> webhook publish — verify: Compose offline E2E passes cleanly with zero `evals/` imports
- [x] 3.3 Write `docs/MCP.md` (client setup for Antigravity, Claude Desktop, Claude Code, Cursor) and update `docs/runbook.md` + `docs/guides/developer-guide.md` (`MCP_AUTH_TOKEN` generation, port non-exposure check, rotation, rollback), plus rehearse local `macy-prod` deploy -> `post_deploy_smoke.py --check-mcp --insecure` -> failed-smoke rollback -> full `sh scripts/check.sh` — verify: local `macy-prod` rehearsal, rollback, and `sh scripts/check.sh` exit 0 (`100%` diff-cover, `>= 90%` branch, OpenSpec valid)

## Scenario ↔ Test Mapping

| Spec Scenario | Test Assertion |
|---|---|
| Compose matrix validates & prod remains hardened | `tests/test_compose_and_docker.py::test_compose_prod_mcp_isolation_and_healthcheck` |
| Deploy preflight blocks placeholder `MCP_AUTH_TOKEN` | `tests/test_deploy_script.py::test_deploy_preflight_rejects_mcp_placeholder` |
| Post-deploy smoke checks fail-closed `401` + auth session | `tests/test_post_deploy_smoke.py::test_post_deploy_smoke_mcp_auth_and_401` |
| Offline container E2E completes HTTP MCP workflow | `tests/test_journey_mcp_e2e.py` |
| Nightly live E2E validates staging `/mcp` | `tests/test_live_e2e_mcp.py` (`e2e-live.yml`) |
| Local `macy-prod` rehearsal, rollback & operator docs | Manual/automated `deploy.sh` + `--rollback` rehearsal + `docs/MCP.md` / `docs/runbook.md` |

## Workflow follow-up

- Squash-merge `m5-06-mcp-deploy-smoke` PR into `main`, verify staging deploy + nightly `e2e-live.yml` green, tag `v0.2.0` to deploy `macy-prod`, and archive `2026-11-11-m5-06-mcp-deploy-smoke` via `openspec archive`.
