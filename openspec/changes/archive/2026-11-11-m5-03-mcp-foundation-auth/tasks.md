# Tasks: M5-03 — MCP Server Foundation, Fail-Closed Auth, Services Boundary & CLI

**PR:** `M5-03` (branch `m5-03-mcp-foundation-auth`, `<= ~400` non-test LOC per `ADR-019`) · **Plan:** [docs/plans/M5.md](../../../docs/plans/M5.md)

## 1. Boundary Contract, Dependencies & Foundation Services

- [x] 1.1 Write failing boundary and service tests in `tests/test_mcp_auth.py` and `tests/test_observability.py` covering `services.list_accessible_workspaces`, `services.get_workspace_status`, `services.get_workspace_owner_identity`, Sentry `mcp_tool` tag emission, and `MCP_AUTH_TOKEN` / `Bearer` scrubbing — verify: `uv run pytest tests/test_mcp_auth.py tests/test_observability.py --no-cov` fails before implementation
- [x] 1.2 Add `mcp>=1.2.0` to `pyproject.toml`, run `uv lock`, and add `"MCP imports services and contracts only"` (`source_modules = ["engine.mcp"]`, `forbidden_modules = ["engine.db"]`) under `[tool.importlinter.contracts]` — verify: `uv run python -c "import mcp"` exits 0 and `uv run lint-imports` parses config cleanly
- [x] 1.3 Add `list_accessible_workspaces`, `get_workspace_owner_identity`, and `get_workspace_status` in `engine/services.py` (using existing `db.py` queries with strict `require_member`), and add optional `mcp_tool` tagging to `set_observability_tags` / `capture_exception` in `engine/observability.py` — verify: `uv run mypy && uv run lint-imports` pass and service/observability unit tests turn green

## 2. Auth Guard, Error Redaction, Stdio Framing & Mock Store

- [x] 2.1 Write failing protocol and security tests in `tests/test_mcp_server.py` and `tests/test_mcp_auth.py` covering `stdio` `initialize` capabilities without corrupt stdout frames, HTTP `/healthz` (`200 ok`), HTTP `/mcp` fail-closed `401` on missing/empty/wrong Bearer token, pinned `MCP_ACTOR_EMAIL` rejection of spoofed `actor_email` args, `FakeIdentityForbiddenError` in prod, explicit `--allow-owner-fallback` gating, and refusal of `--mock` / `MACY_MCP_STANDALONE=1` in `staging`/`production`/`prod` or DB-outage silent fallback — verify: `uv run pytest tests/test_mcp_server.py tests/test_mcp_auth.py --no-cov` fails before implementation
- [x] 2.2 Implement `engine/mcp/__init__.py`, `engine/mcp/errors.py` (structured `MCPErrorCode` + secret scrubbing), `engine/mcp/auth.py` (constant-time `hmac.compare_digest` Bearer check, 3-tier non-prod `stdio` resolution, strict production guards), and `engine/mcp/mock_store.py` (seeded `acme-demo` + secondary `lumina` demo tenant) — verify: `uv run pytest tests/test_mcp_auth.py --no-cov` passes and `uv run lint-imports` confirms zero `engine.db` imports
- [x] 2.3 Implement `engine/mcp/server.py` (FastMCP factory, `_protect_stdio_stream` stdout->stderr guard, `streamable-http` Bearer middleware + `/healthz` route) — verify: `uv run pytest tests/test_mcp_server.py --no-cov` passes including real subprocess `stdio` handshake

## 3. CLI Entrypoint, Docs Parity & Full Delivery Gates

- [x] 3.1 Write failing CLI tests in `tests/test_cli.py` for `content-engine mcp --help`, `--check --mock`, non-zero exit when DB is unreachable without `--mock`, and non-zero exit for `--mock` when `APP_ENV=production` — verify: `uv run pytest tests/test_cli.py -k mcp --no-cov` fails before registration
- [x] 3.2 Register `mcp` subcommand (`--transport`, `--host`, `--port`, `--mock`, `--allow-owner-fallback`, `--check`) in `engine/cli.py` — verify: `uv run pytest tests/test_cli.py -k mcp --no-cov` passes
- [x] 3.3 Run complete `M5-03` delivery verification gates (`sh scripts/check.sh`, confirming `ruff`, `mypy --strict`, `complexipy <= 15`, `vulture >= 60`, `lint-imports`, `jscpd = 0%`, `gitleaks`, `squawk`, `pytest >= 90%` branch, `diff-cover = 100%`, `openspec validate --all --strict`) and confirm non-test LOC <= ~400 (`git diff --stat`) — verify: `sh scripts/check.sh` exits 0

## Scenario ↔ Test Mapping

| Spec Scenario | Test Assertion |
|---|---|
| Clean stdio handshake advertises protocol capabilities | `tests/test_mcp_server.py::test_stdio_handshake_and_clean_stdout_framing` |
| Streamable HTTP health endpoint responds cleanly | `tests/test_mcp_server.py::test_http_healthz_endpoint` |
| Explicit mock mode boots demo store in non-production | `tests/test_mcp_server.py::test_mock_mode_initializes_demo_store` |
| Mock mode refused in staging/prod; DB outage fails cleanly | `tests/test_mcp_auth.py::test_mock_refused_in_staging_and_prod`, `tests/test_cli.py::test_mcp_check_fails_when_db_down_without_mock` |
| HTTP rejects missing/empty/wrong Bearer & incomplete startup | `tests/test_mcp_auth.py::test_http_bearer_fail_closed`, `test_http_startup_refuses_missing_token` |
| Pinned transport identity rejects spoofed tool arguments | `tests/test_mcp_auth.py::test_http_pinned_actor_rejects_spoofed_tool_arg` |
| Stdio resolves tiers & blocks fake identity in prod | `tests/test_mcp_auth.py::test_stdio_three_tier_resolution_and_fake_id_block` |
| Non-member refused; Sentry tags & scrubs tokens | `tests/test_mcp_auth.py::test_non_member_permission_denied`, `tests/test_observability.py::test_mcp_tool_tag_and_token_scrub` |
| `engine.mcp` cannot import `engine.db` | `uv run lint-imports` in `scripts/check.sh` |

## Workflow follow-up

- Squash-merge `m5-03-mcp-foundation-auth` PR into `main` once CI `check`, `test`, `openspec-validate`, and `compose-config` pass.
- Archive `2026-11-11-m5-03-mcp-foundation-auth` with `openspec archive` to promote the core `mcp-server` capability spec into `openspec/specs/mcp-server/spec.md`.
