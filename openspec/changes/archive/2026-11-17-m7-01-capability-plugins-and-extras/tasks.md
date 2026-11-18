# Tasks: M7-01 — Capability Plugin Registry, Composable Extras & Path B Zero-Docker Stateless CLI/MCP

## 1. Capability Plugin Protocol, Registry & Packaging Extras (Delivery KR: D1)
- [x] 1.1 Write failing tests in `tests/test_capabilities.py` for discovering all 4 built-in capability plugins (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`) via `content_engine.capabilities`, verifying `core-compliance.always_enabled=True`, and verifying optional extras separation in `pyproject.toml`.
- [x] 1.2 Implement `engine/capabilities/{__init__,ports,builtins,registry}.py` and register `content_engine.capabilities` entry points plus `[core]`, `[editorial]`, `[ui]`, `[audit-browser]`, and `[full]` extras in `pyproject.toml`. Verify D1: `uv run pytest tests/test_capabilities.py -k "discover or extras" --no-cov` exits 0.

## 2. Workspace Capability Switchboard (Delivery KR: D2)
- [x] 2.1 Write failing tests in `tests/test_capabilities.py` and `tests/test_tenant_scoping.py` for `services.get_enabled_capabilities` (backward-compatible default when unset), `services.set_enabled_capabilities` (owner authorization, `core-compliance` lock, invalid slug rejection, `workspace.capabilities_updated` event), and cross-workspace isolation.
- [x] 2.2 Implement `get_enabled_capabilities` and `set_enabled_capabilities` in `engine/services.py` and `engine/db.py` (`workspaces.config` migration). Verify D2: `uv run pytest tests/test_capabilities.py tests/test_tenant_scoping.py -k switchboard --no-cov` exits 0.

## 3. Dynamic UI, CLI & MCP Surface Filtering (Delivery KR: D3)
- [x] 3.1 Write failing tests in `tests/test_capabilities.py` and `tests/test_mcp_capabilities.py` verifying sidebar screen filtering in `app.py`, `content-engine capabilities list|enable|disable`, MCP tool filtering by workspace `enabled_capabilities`, and structured `CAPABILITY_DISABLED` error responses.
- [x] 3.2 Wire capability filtering into `app.py`, `engine/cli.py`, and `engine/mcp/`. Verify D3: `uv run pytest tests/test_capabilities.py tests/test_mcp_capabilities.py --no-cov && uv run lint-imports` exits 0.

## 4. Path B: Zero-Docker Stateless CLI & MCP Execution (Delivery KR: D4)
- [x] 4.1 Write failing tests in `tests/test_stateless_cli_mcp.py` (with `DATABASE_URL` unset and `psycopg.connect` forbidden) covering `content-engine audit <url> --stateless`, `content-engine audit <url> --crawl --stateless`, `content-engine import-draft <file> --stateless`, and `content-engine mcp --stateless` (`macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`).
- [x] 4.2 Implement `--stateless` / `MACY_STATELESS=true` stateless service paths for `core-compliance` and `seo-audit`. Verify D4: `uv run pytest tests/test_stateless_cli_mcp.py --no-cov && sh scripts/check.sh` exits 0.
