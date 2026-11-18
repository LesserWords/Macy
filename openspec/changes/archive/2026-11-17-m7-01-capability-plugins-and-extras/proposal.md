# Proposal: M7-01 — Capability Plugin Registry, Composable Extras & Path B Zero-Docker Stateless CLI/MCP

**Plan Item:** M7-01 (Planned) · **Features:** F05, F19, F21, F22 · **ADRs:** ADR-005, ADR-014, ADR-027, ADR-028, ADR-029

## Why

Macy already abstracts external vendors behind `typing.Protocol` ports (`ADR-005`), yet its product capabilities (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`) remain tightly coupled at three layers:
1. **Packaging & Runtime:** `pyproject.toml` installs heavy editorial dependencies (`pymupdf4llm`, `lingua-language-detector`, `trafilatura`, `streamlit`, `psycopg`) unconditionally.
2. **Surface Exposure:** Every workspace displays all 10 Admin UI sidebar tabs and exposes all 17 MCP tools regardless of whether the user only wants SEO/GEO audits or Markdown compliance.
3. **Database Requirement (**Path B** blocker):** Deterministic compliance (`import-draft`), single-URL audits (`audit`), bounded crawls (`audit --crawl`), and `stdio` MCP tools (`macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`) cannot currently run without a live PostgreSQL database.

## What Changes

- **Capability Plugin Protocol & Registry (`engine/capabilities/`):**
  - Define `CapabilityPlugin` (`Protocol` in `engine/capabilities/ports.py`) and entry-point discovery (`content_engine.capabilities`) in `engine/capabilities/registry.py`.
  - Ship 4 built-in capability plugins: `core-compliance` (`always_enabled=True`), `seo-audit`, `editorial-pipeline`, and `route-monitors`.
- **Composable Packaging Extras (`pyproject.toml`):**
  - Organize dependencies into `[core]`, `[editorial]`, `[ui]`, `[audit-browser]`, and `[full]` extras while preserving default backward compatibility for existing Docker builds.
- **Workspace Capability Switchboard (`engine/services.py`, `engine/db.py`):**
  - Persist `enabled_capabilities` inside `workspaces.config` (backward-compatible default = all 4 built-in capabilities), refusing attempts to disable `core-compliance` or unknown slugs and logging `workspace.capabilities_updated`.
- **Dynamic UI, CLI & MCP Surface Filtering (`app.py`, `engine/cli.py`, `engine/mcp/`):**
  - Filter Admin UI sidebar screens, `Integrations` port hints, and workspace-scoped MCP tool lists dynamically; enforce `CAPABILITY_DISABLED` on direct calls to disabled MCP tools; add `content-engine capabilities list|enable|disable`.
- **Path B — Zero-Docker Stateless CLI & MCP Mode (`--stateless` / `MACY_STATELESS=true`):**
  - Decouple `audit` (single-URL + `--crawl`), `import-draft`, and `content-engine mcp --stateless` (`macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`) from `engine/db.py` so users can run `uvx --from macy[core] content-engine ...` with zero PostgreSQL and zero Docker.

## Capabilities

### New Capabilities
- `capabilities`: Capability plugin discovery, per-workspace feature switchboard, and Path B stateless CLI/MCP execution.

### Modified Capabilities
- `admin-ui`: Dynamic sidebar and integrations port filtering driven by `enabled_capabilities`.
- `mcp-server`: Capability-scoped MCP tool exposure, `CAPABILITY_DISABLED` guard, and `--stateless` stdio execution mode.

## Out of Scope

- Single-container Pocket bootstrap, Python migrations, local PBKDF2 auth, and embedded worker (covered in `M7-02`).
- Zero-key adapters (`ollama`, `gnews_rss`, `autocomplete`) and stack presets (covered in `M7-03`).
- First-boot 3-step Setup Wizard and URL auto-seeding (covered in `M7-04`).

## Key Results: Delivered

All `D1`–`D4` must pass before `M7-01` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `discover_capabilities()` returns all 4 built-in `CapabilityPlugin` definitions (`core-compliance` with `always_enabled=True`) and `pyproject.toml` defines `[core]`, `[editorial]`, `[ui]`, `[audit-browser]`, `[full]` extras | `uv run pytest tests/test_capabilities.py -k "discover or extras" --no-cov` exits `0` |
| **D2** | `services.get_enabled_capabilities` and `services.set_enabled_capabilities` persist workspace selections in `workspaces.config`, enforce owner authorization, protect `core-compliance`, and emit `workspace.capabilities_updated` | `uv run pytest tests/test_capabilities.py tests/test_tenant_scoping.py -k switchboard --no-cov` exits `0` |
| **D3** | Admin UI sidebar, `content-engine capabilities`, and `engine/mcp/` dynamically filter screens/tools per `enabled_capabilities`, and direct calls to disabled MCP tools return `CAPABILITY_DISABLED` | `uv run pytest tests/test_capabilities.py tests/test_mcp_capabilities.py --no-cov && uv run lint-imports` exits `0` |
| **D4** | **Path B:** `content-engine audit <url> [--crawl] --stateless`, `content-engine import-draft <file> --stateless`, and `content-engine mcp --stateless` execute `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, and `macy_import_draft` with `DATABASE_URL` unset and zero DB calls, passing all repo gates | `uv run pytest tests/test_stateless_cli_mcp.py --no-cov && sh scripts/check.sh` exits `0` |

## Impact

- **Code:** `engine/capabilities/{__init__,ports,registry}.py`, `engine/services.py`, `engine/db.py`, `engine/cli.py`, `engine/mcp/{server,errors,tools_editorial,tools_audit}.py`, `app.py`, `pyproject.toml`
- **Tests:** `tests/test_capabilities.py`, `tests/test_mcp_capabilities.py`, `tests/test_stateless_cli_mcp.py`, `tests/test_tenant_scoping.py`
