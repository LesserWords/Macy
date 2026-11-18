# Proposal: M5-03 — MCP Server Foundation, Fail-Closed Auth, Services Boundary & CLI

**Plan Item:** M5-03 ([docs/plans/M5.md](../../../docs/plans/M5.md)) · **Features:** F01, F21 · **ADRs:** ADR-006, ADR-009, ADR-013, ADR-014, ADR-019, ADR-020, ADR-023, ADR-027

## Why

External AI clients (Antigravity, Claude Desktop, Claude Code, Cursor) need an authenticated, tenant-isolated MCP interface to Macy. An earlier unimplemented draft (`openspec/changes/mcp-server/`) referenced non-existent `engine/services.py` functions, bypassed the `ADR-014` `import-linter` boundary by importing `engine.db`, permitted silent fallbacks to mock data and `get_workspace_owner`, and exceeded the `ADR-019` `<= ~400` non-test lines/PR size ceiling.

This change (`M5-03`) establishes the audited MCP server foundation: dual `stdio` + `streamable-http` FastMCP transport, fail-closed authentication and production mock bans, strict `engine.mcp` -> `engine.services`/`engine.contracts` architectural boundaries, Sentry telemetry/redaction, and the `content-engine mcp` CLI entrypoint.

## What Changes

- **Architecture & Documentation:** Add accepted-format ADR draft [`docs/adr/ADR-027-mcp-server.md`](../../../docs/adr/ADR-027-mcp-server.md), feature spec [`docs/features/F21-mcp-server.md`](../../../docs/features/F21-mcp-server.md), and split milestone slices `M5-03`–`M5-06` in `docs/plans/M5.md`.
- **Dependency & Boundary Contract:** Add `mcp>=1.2.0` to `pyproject.toml` and `uv.lock`; add `importlinter` forbidden contract `"MCP imports services and contracts only"` (`engine.mcp` -> never `engine.db`) matching CLI/UI (`ADR-014`).
- **Service Layer Foundation (`engine/services.py`):** Expose `list_accessible_workspaces`, `get_workspace_status`, and non-production `get_workspace_owner_identity` so MCP handlers remain pure thin adapters with zero direct DB imports.
- **Core MCP Package (`engine/mcp/`):** Implement `errors.py` (structured `MCPError` codes without secret leakage), `auth.py` (fail-closed HTTP Bearer validation via `hmac.compare_digest` + pinned `MCP_ACTOR_EMAIL`; 3-tier `stdio` identity resolution rejecting `MACY_TEST_USER_EMAIL` in `staging`/`production`/`prod` and gating owner fallback behind explicit non-prod `--allow-owner-fallback`), `mock_store.py` (isolated general demo store refused in `staging`/`prod` and never auto-triggered on DB outage), and `server.py` (stdout->stderr pipe guard for clean `stdio` JSON-RPC framing + HTTP `/healthz` & `/mcp` mount).
- **Observability & CLI:** Extend `engine/observability.py` with `mcp_tool` Sentry tagging and Bearer/`MCP_AUTH_TOKEN` scrubbing; register `content-engine mcp` (`--transport`, `--host`, `--port`, `--mock`, `--allow-owner-fallback`, `--check`) in `engine/cli.py`.

## Capabilities

### New Capabilities
- `mcp-server`: Core MCP server lifecycle, clean `stdio` JSON-RPC framing, HTTP `streamable-http` `/healthz` & fail-closed Bearer auth, non-production `--mock` isolation, workspace membership checks, Sentry `mcp_tool` tagging, and CLI entrypoint.

### Modified Capabilities
- `tooling`: Enforce `import-linter` boundary preventing `engine.mcp` from importing `engine.db` (`ADR-014`).

## Out of Scope

- Research, scoring, and Gate 1 topic tools (`macy_research_topic`, `macy_score_topics`, `macy_list_topics_awaiting_approval`, `macy_decide_topic`) — deferred to `M5-04`.
- Editorial briefing, drafting, runtime claim verification (`engine/quality/claims.py`), Gate 2 review, publishing, spend preflight guards, MCP resources, and prompts — deferred to `M5-05`.
- Docker Compose service definitions, `Caddyfile` routing, `scripts/deploy.sh`, `scripts/post_deploy_smoke.py`, workflow CI/CD edits, and client setup docs (`docs/MCP.md`, `docs/runbook.md`) — deferred to `M5-06`.

## Impact

- **Dependencies & Tooling:** `pyproject.toml`, `uv.lock`
- **Engine:** `engine/services.py`, `engine/observability.py`, `engine/cli.py`, new `engine/mcp/{__init__,errors,auth,mock_store,server}.py`
- **Tests:** `tests/test_mcp_auth.py`, `tests/test_mcp_server.py`, `tests/test_cli.py`, `tests/test_observability.py`
- **Docs:** `docs/adr/ADR-027-mcp-server.md`, `docs/features/F21-mcp-server.md`, `docs/plans/M5.md`
