# operations Delta Specification

## ADDED Requirements

### Requirement: Opt-in containerized MCP service behind Caddy with zero raw port exposure in production
The repository SHALL define an opt-in `mcp` Compose service (`profiles: ["mcp"]`) across `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml`, routing `/mcp*` via `Caddyfile` while exposing zero raw MCP host ports (`8765`) in `compose.prod.yaml`, isolating `MCP_AUTH_TOKEN` outside `x-app-env`, omitting `MACY_TEST_USER_EMAIL`, and providing a `/healthz` healthcheck (`F20`, `F21 AC9`, `ADR-012`, `ADR-027`).

#### Scenario: Compose matrix validates with and without MCP profile while keeping prod hardened
- **WHEN** `docker compose ... config` renders `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml` (both default and `--profile mcp`)
- **THEN** all configurations validate cleanly, `compose.prod.yaml` exposes zero `5432`, `8501`, or `8765` host ports, keeps `MCP_AUTH_TOKEN`, `AUTH_COOKIE_SECRET`, and `GOOGLE_CLIENT_SECRET` out of `x-app-env`, omits `MACY_TEST_USER_EMAIL`, and defines a healthcheck on `mcp`

### Requirement: Deploy script preflight, rollback, and smoke check coverage for MCP
`scripts/deploy.sh`, `scripts/post_deploy_smoke.py`, CI/deploy/live-E2E workflows, and `docs/runbook.md` SHALL support MCP preflight secret validation, targeted rollback of `mcp`, post-deploy `/mcp` `401`/authenticated smoke verification (`--check-mcp`), and operator key management procedures (`F20 AC2, AC3`, `F21 AC9, AC10`, `ADR-019`, `ADR-027`).

#### Scenario: Deploy preflight blocks placeholder MCP_AUTH_TOKEN when MCP is enabled
- **WHEN** `scripts/deploy.sh --preflight-only` executes with `MCP_ENABLED=true` and `MCP_AUTH_TOKEN` unset or equal to a `CHANGE_ME` placeholder
- **THEN** preflight exits non-zero before touching containers

#### Scenario: Post-deploy smoke verifies fail-closed 401 on unauthenticated MCP endpoint
- **WHEN** `scripts/post_deploy_smoke.py --check-mcp` runs against a target URL
- **THEN** it verifies unauthenticated `POST <url>/mcp` returns HTTP `401`, and verifies authenticated MCP `initialize` + `macy_list_workspaces` when a token is provided

#### Scenario: Local macy-prod rehearsal and rollback cover MCP service cleanly
- **WHEN** `scripts/deploy.sh` and `scripts/deploy.sh --rollback <prev_tag>` run locally against `macy-prod` with `COMPOSE_PROFILES=mcp`
- **THEN** `mcp` starts healthy behind Caddy, passes `post_deploy_smoke.py --check-mcp --insecure`, and rolls back cleanly alongside `app` and `worker` on smoke failure without touching migrations
