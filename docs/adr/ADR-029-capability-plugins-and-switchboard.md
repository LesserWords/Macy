# ADR-029: Two-Tier Plugin Architecture — Capability Plugins, Workspace Switchboard & Stateless Mode

**Status:** Accepted · **Date:** 2026-11-17 · **Deciders:** Project Maintainer
**Related:** features F01, F05, F19, F21, F22 · ADR-005, ADR-014, ADR-027, ADR-028

## Context
Macy currently supports vendor adapter plugins via `typing.Protocol` ports and `importlib.metadata` entry points (`content_engine.<port>`, `ADR-005`), while product capabilities (`audit`, `crawl`, `monitors`, `research`, `strategy`, `production`, `quality`) are bundled in a single runtime where all 10 Admin UI screens (`app.py`), 20 CLI commands (`engine/cli.py`), and 17 MCP tools (`engine/mcp/`) are always exposed. Users who only want deterministic `.md`/PDF compliance or CMS-agnostic SEO/GEO audits must still install every dependency (`pymupdf4llm`, `lingua-language-detector`, `trafilatura`, `streamlit`, `psycopg`) and run PostgreSQL even for stateless single-URL audits or draft checks.

## Options considered
| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. Two-tier plugin architecture (`CapabilityPlugin` registry + per-workspace switchboard + optional pip extras + stateless CLI/MCP mode)** (chosen) | Medium | `$0` | High — one codebase powers both a 1-command CLI/MCP tool and the full editorial suite |
| B. Split Macy into separate repositories (`macy-audit`, `macy-compliance`, `macy-editorial`) | High | High maintenance | Poor — duplicates `engine/contracts.py`, `safe_http.py`, and MCP server wiring |
| C. Hard-coded UI flags only | Low | `$0` | Incomplete — does not filter MCP tools, CLI commands, required ports, or pip dependencies |

## Decision
1. **Tier 1 — Capability Plugins (`content_engine.capabilities` entry point group):**
   - Introduce `CapabilityPlugin` (`Protocol` in `engine/capabilities/ports.py`) declaring `slug`, `title_msgid`, `always_enabled`, `required_ports`, `optional_ports`, `ui_screens`, `cli_commands`, and `mcp_tools`.
   - Ship 4 built-in capability plugins registered in `pyproject.toml`:
     - `core-compliance` (`always_enabled=True`): deterministic quality checks (`engine/quality/checks.py`) and 5 JSON-LD generators (`engine/production/schema.py`). Requires `0` external ports.
     - `seo-audit`: 7-category single-URL SEO+GEO auditor (`engine/audit/`) and bounded BFS crawler (`engine/crawl/`). Optional ports: `browser_audit`, `pagespeed`.
     - `editorial-pipeline`: sourced research, scoring, Gate 1, keyword brief, writer with `draft_claims` FKs, LLM evaluator, Gate 2, and publishing. Required ports: `llm`, `feed_source`, `extractor`.
     - `route-monitors`: scheduled URL/TLS regression monitors (`engine/monitors/`), hybrid GSC insights (`analytics_source`), and `RankTrackerPort`.
2. **Workspace Capability Switchboard (`workspaces.config.enabled_capabilities`):**
   - Persist `enabled_capabilities: list[str]` in `workspaces.config` (defaulting to all 4 capabilities on existing workspaces for backward compatibility; `core-compliance` is always enforced).
   - **Dynamic Surface Filtering:** `app.py` renders only sidebar screens belonging to `enabled_capabilities`; `engine/mcp/` exposes only tools belonging to `enabled_capabilities` (returning structured `CAPABILITY_DISABLED` error if a disabled tool is invoked directly); `Integrations` screen highlights only ports required by enabled capabilities.
3. **Path B — Zero-Docker Stateless CLI/MCP Package (`uvx macy[core]`) & Composable Extras:**
   - Group optional heavy dependencies under extras (`[core]`, `[editorial]`, `[ui]`, `[audit-browser]`, `[full]`).
   - Support `--stateless` (or automatic fallback when `MACY_STATELESS=true`) to decouple `core-compliance` and `seo-audit` from `engine/db.py` across **both CLI (`content-engine audit <url> [--crawl] --stateless`, `content-engine import-draft <file> --stateless`) and `stdio` MCP (`content-engine mcp --stateless` exposing `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`)**, executing with zero PostgreSQL or Docker required and installable via `uvx --from macy[core] content-engine ...`.

## Consequences
- Easier: Users can run `uvx macy audit <url>` without Docker or Postgres, or toggle off editorial screens in the UI when using Macy purely as an SEO/GEO auditor.
- Harder: UI navigation (`app.py`) and MCP tool registration (`engine/mcp/`) must query the capability registry rather than relying on static lists.

## Revisit when
Third-party capability plugins require custom database tables outside `db/migrations/` (introduce per-capability migration namespaces if needed).

## Action items
- [ ] `M7-01` — Implement `engine/capabilities/`, workspace capability switchboard, dynamic UI/MCP/CLI filtering, composable extras, and `--stateless` audit/compliance execution.
