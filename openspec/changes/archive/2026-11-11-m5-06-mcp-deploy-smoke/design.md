# Design: M5-06 — MCP Deployment Integration, Smoke, Offline/Live E2E & Operator Docs

**ADR:** [`docs/adr/ADR-027-mcp-server.md`](../../../docs/adr/ADR-027-mcp-server.md) · **Plan Item:** `M5-06` (`<= ~400` non-test LOC)

## Container & Compose Topology (`ADR-012`)

Reuses the single `content-engine` image (`Dockerfile` already includes `engine/`, omits `evals/`, runs non-root `appuser`):

```
Internet HTTPS (:443)
        │
     [caddy] ──(/mcp*)──▶ [mcp:8765] (profiles: ["mcp"], internal network only)
        │                      │
        └──(/*)──▶ [app:8501]  └──▶ engine.services ──▶ [db:5432]
```

1. **`compose.yaml`**
   - Add service `mcp`:
     - `<<: *engine-service`
     - `profiles: ["mcp"]` (opt-in until operator enables `MCP_ENABLED=true` / `COMPOSE_PROFILES=mcp`)
     - `command: ["python", "-m", "engine.cli", "mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8765"]`
     - `healthcheck:` Python `urllib.request.urlopen('http://localhost:8765/healthz', timeout=3)`
     - Environment: inherits `*engine-env` plus optional `MCP_AUTH_TOKEN`, `MCP_ACTOR_EMAIL`.
2. **`compose.override.yaml` & `compose.e2e.yaml`**
   - Dev override: bind `./engine:/app/engine:ro` and `127.0.0.1:8765:8765` on `mcp` when `--profile mcp` is used.
   - E2E overlay: set `APP_ENV=test`, test `MCP_AUTH_TOKEN`, `MCP_ACTOR_EMAIL=owner@example.test`, health dependency on `db` + `migrate`.
3. **`compose.prod.yaml` & `Caddyfile`**
   - Hardening invariants checked by `tests/test_compose_and_docker.py`:
     - `mcp:` has `build: !reset null`, `pull_policy: missing`, inherits `<<: *app-env` plus explicit `MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:-}` and `MCP_ACTOR_EMAIL: ${MCP_ACTOR_EMAIL:-}`.
     - `x-app-env` **never** includes `MCP_AUTH_TOKEN` (nor `GOOGLE_CLIENT_SECRET` / `AUTH_COOKIE_SECRET`) so `app` and `worker` never see MCP tokens.
     - Zero host `ports:` published by `mcp` (`8765` is internal Compose network only).
   - `Caddyfile`: route `handle /mcp* { reverse_proxy mcp:8765 }` before `handle { reverse_proxy app:8501 }`.
4. **`.env.staging.example`, `.env.prod.example`, `scripts/deploy.sh`, `scripts/post_deploy_smoke.py`**
   - `.env.*.example`: add `MCP_ENABLED=false`, `COMPOSE_PROFILES=`, `MCP_AUTH_TOKEN=CHANGE_ME_mcp_bearer_token`, `MCP_ACTOR_EMAIL=`.
   - `scripts/deploy.sh`:
     - When `MCP_ENABLED=true` (or `COMPOSE_PROFILES` contains `mcp`), preflight verifies `MCP_AUTH_TOKEN` is non-empty, not `CHANGE_ME*`, and `MCP_ACTOR_EMAIL` is set; exports `COMPOSE_PROFILES=mcp`.
     - `pull`, `up -d`, and `rollback` manage `mcp` cleanly alongside `app worker` without re-running `migrate` during `--rollback`.
   - `scripts/post_deploy_smoke.py`:
     - Add `--check-mcp`: `POST <base_url>/mcp` without auth MUST return HTTP `401`; when `MCP_AUTH_TOKEN` is set in smoke env, performs `initialize` + `macy_list_workspaces` read-only call and asserts HTTP `200`.
5. **CI/CD & Docs**
   - `.github/workflows/ci.yml`: extend `compose-config` job with `--profile mcp` config checks + `deploy.sh --preflight-only` rejection when `MCP_ENABLED=true` + placeholder token.
   - `.github/workflows/deploy.yml`: pass `--check-mcp` to `post_deploy_smoke.py` when `vars.MCP_ENABLED == 'true'`.
   - `.github/workflows/e2e-live.yml`: include `tests/test_live_e2e_mcp.py` when `MCP_AUTH_TOKEN` secret is present.
   - Author `docs/MCP.md`, update `docs/runbook.md` & `docs/guides/developer-guide.md`.
