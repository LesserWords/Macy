# Design: M5-03 — MCP Server Foundation, Fail-Closed Auth, Services Boundary & CLI

**ADR:** [`docs/adr/ADR-027-mcp-server.md`](../../../docs/adr/ADR-027-mcp-server.md) · **Features:** `F01`, `F21` · **Branch target:** `m5-03-mcp-foundation-auth` (`<= ~400` non-test LOC)

## Context & Architecture Fit

Macy enforces strict separation between interfaces (`app.py`, `engine/cli.py`), domain/transactions (`engine/services.py`), and PostgreSQL SQL (`engine/db.py`). `M5-03` introduces `engine/mcp/` at the **interface layer** alongside `engine/cli.py`:

```
MCP Client (Antigravity / Claude / Cursor / Caddy HTTP)
        │  stdio (JSON-RPC 2.0)  OR  streamable-http (Bearer -> 401 fail-closed)
        ▼
engine/mcp/server.py  ──▶  engine/mcp/auth.py  ──▶  engine/mcp/errors.py
        │                        │
        ▼ (real mode)            ▼ (--mock, non-prod ONLY)
engine/services.py         engine/mcp/mock_store.py (acme-demo + Fake* doubles)
        │
        ▼
engine/db.py (PostgreSQL; FORBIDDEN to engine/mcp via importlinter)
```

## Module Breakdown (`<= ~400` non-test LOC)

1. **`pyproject.toml`**
   - Add `"mcp>=1.2.0"` to `[project].dependencies` and lock with `uv lock`.
   - Extend `[tool.importlinter.contracts]` with:
     ```toml
     [[tool.importlinter.contracts]]
     name = "MCP imports services and contracts only"
     type = "forbidden"
     source_modules = ["engine.mcp"]
     forbidden_modules = ["engine.db"]
     allow_indirect_imports = true
     ```
     Wait: `engine.mcp` imports `engine.services` which imports `engine.db`! Notice how existing rule lines 135–143 work in `pyproject.toml`: let's match `engine.cli`'s `ignore_imports` or check why `engine.cli` passes with `allow_indirect_imports = true`! Wait: `CLI` doesn't specify `ignore_imports`, wait — why did `engine.cli` pass `allow_indirect_imports = true`? Look closely at `engine/cli.py` and `pyproject.toml`: `importlinter` checks direct vs indirect paths — if `engine.services` imports `engine.db`, `allow_indirect_imports = true` means indirect imports THROUGH `engine.services`? NO, `allow_indirect_imports = true` means `engine.cli` -> `engine.services` -> `engine.db` is *allowed* (or Wait: in `import-linter`, `allow_indirect_imports` defaults to `false`, meaning indirect imports ARE forbidden, so setting `allow_indirect_imports = true` explicitly permits `engine.mcp -> engine.services -> engine.db` while STILL forbidding direct `engine.mcp -> engine.db` or `engine.mcp -> helper -> engine.db`? Let's verify against `pyproject.toml:143`: `engine.cli` uses `allow_indirect_imports = true`!). We'll configure `"MCP imports services and contracts only"` identically to `"CLI imports services and contracts only"` in `pyproject.toml`.

2. **`engine/services.py` (Foundation helpers added in `M5-03`)**
   - `list_accessible_workspaces(conn=None, *, user_id)` — returns workspace rows plus primary campaign locale and spend caps (`monthly_cap_usd`, `run_cap_usd`) for workspaces where `user_id` has membership.
   - `get_workspace_owner_identity(conn=None, *, workspace_id)` — resolves owner email/user_id cleanly through `services.py` (`env`-guarded against prod auto-fallback).
   - `get_workspace_status(conn=None, *, workspace_id, user_id)` — calls `require_member`, computes monthly spend vs cap (`db.get_monthly_spend`), active sources count, configured integration count, Gate 1 `'scored'` count, Gate 2 `'pass'` count, and latest run metadata.

3. **`engine/observability.py`**
   - Update `set_observability_tags(workspace_id=None, run_id=None, mcp_tool=None)` and `capture_exception(..., mcp_tool=None)` to tag Sentry scopes with `"mcp_tool"` when invoked from MCP. Token keys (`MCP_AUTH_TOKEN`) and `Bearer ...` headers are already scrubbed by `_is_sensitive_key` + `_BEARER_TOKEN_RE` (explicitly verified by unit tests).

4. **`engine/mcp/errors.py`**
   - Defines `MCPErrorCode` (`PERMISSION_DENIED`, `NOT_FOUND`, `INVALID_STATE`, `BUDGET_CAP_EXCEEDED`, `AUTH_REQUIRED`, `MOCK_FORBIDDEN`, `INTERNAL_ERROR`) and `MCPError` exception + safe exception-to-JSON translator that scrubs DSN/Bearer strings via `observability._scrub_string` (or clean helper) before returning tool errors.

5. **`engine/mcp/auth.py`**
   - Helper `is_non_local_environment()` (`APP_ENV` / `ENV` in `("staging", "production", "prod")`).
   - `resolve_http_bearer_actor(auth_header, env, *, tool_actor_email=None)` -> constant-time `hmac.compare_digest` check against `MCP_AUTH_TOKEN`, resolves pinned `MCP_ACTOR_EMAIL` via `services.get_user_by_email`, and raises `PERMISSION_DENIED` if `tool_actor_email` attempts to impersonate a different email.
   - `resolve_stdio_actor(conn, *, workspace_id=None, actor_email=None, actor_user_id=None, allow_owner_fallback=False)` -> resolves `actor_email` / `actor_user_id` -> `MACY_USER_EMAIL` -> `MACY_ACTOR_USER_ID` -> `MACY_TEST_USER_EMAIL` (raises `FakeIdentityForbiddenError` if `is_production_environment()` or `staging`), then `services.get_workspace_owner_identity` ONLY if `allow_owner_fallback` and non-prod.
   - Enforces `services.require_member(conn, workspace_id=ws_id, user_id=actor_id)` on every workspace operation.

6. **`engine/mcp/mock_store.py`**
   - Activated strictly when `mock=True` (via `--mock` CLI or non-prod `MACY_MCP_STANDALONE=1`). Raises `MockForbiddenError` if `APP_ENV`/`ENV` is `staging`/`production`/`prod`.
   - Provides seeded general `acme-demo` state (`owner@example.com` owner, sample secondary workspace `lumina` for cross-tenant refusal tests even in mock mode, campaign caps, `FakeLLM` / `FakeFeedSource` / `FakeKeywordProvider` / `FakePublisher`).

7. **`engine/mcp/server.py` & `engine/cli.py`**
   - Context manager `_protect_stdio_stream()`: redirects `sys.stdout` -> `sys.stderr` and configures root logging on `sys.stderr` so MCP `stdio` SDK writes exclusively to the real stdout file descriptor.
   - Creates `FastMCP("macy")` server (`create_mcp_server(mode=..., auth_config=...)`).
   - Wraps `streamable_http_app()` with Starlette/ASGI middleware:
     - Allows unauthenticated `GET /healthz` returning `200 {"status": "ok"}`.
     - Requires valid `Authorization: Bearer <MCP_AUTH_TOKEN>` on `/mcp*` returning HTTP `401` JSON otherwise.
   - Adds `content-engine mcp` subcommand in `engine/cli.py` with flags `--transport {stdio,streamable-http}`, `--host` (default `127.0.0.1`), `--port` (default `8765`), `--mock`, `--allow-owner-fallback`, and `--check`.

## Trade-offs & Mitigations

- **Bearer token per deployment vs per-user OAuth 2.1 DCR:** Matches Macy's current internal-team scale (1–2 operators, `ADR-014` trigger); isolates HTTP calls behind HTTPS Caddy + constant-time Bearer token + pinned `MCP_ACTOR_EMAIL` until self-serve external SaaS triggers OAuth 2.1 upgrade.
- **No silent DB->mock fallback:** Ensures container or Postgres misconfigurations alert loudly in logs and healthchecks instead of returning fake articles in production.
