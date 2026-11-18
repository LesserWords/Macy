# Proposal: M5-08 — MCP "Modo A" Agent Tools & Strands Agents (`strands-agents`) Autonomous Runner

**Plan Item:** M5-08 ([docs/plans/M5.md](../../../docs/plans/M5.md)) · **Features:** F07, F12, F13, F14, F15, F21 · **ADRs:** ADR-002, ADR-013, ADR-014, ADR-016, ADR-025, ADR-027

## Why

Once `M5-07` establishes the core services (`services.ingest_external_research`, `services.import_external_draft`, `check_word_count`, and `deterministic_only` evaluation), Macy's MCP server (`engine/mcp/`) needs to expose these capabilities as first-class MCP tools so external AI agents can operate in **"Modo A" (Agent-Driven Research, Writing & Self-Correction against Macy's Editorial Compliance Pipeline)**:

1. **Interactive AI Clients (Antigravity, Claude Code, Cursor):** Need `macy_ingest_research_documents` and `macy_import_draft` (plus `format_slug`, `funnel_stage`, and `deterministic_only` options on existing tools) so an interactive assistant can search the web, read local `.md`/`.pdf` files, submit drafts in the desired size (`blog-short`, `blog`, `blog-long`), run zero-cost compliance checks (`deterministic_only=True`), iteratively fix findings (`revision 0..2`), and hand off to Gate 2.
2. **Headless Autonomous Execution via Strands Agents (`strands-agents`):** While `ADR-002` keeps Macy's internal `engine/` deterministic and agent-free, operators also want an option to run "Modo A" unattended from a script or CLI. **Strands Agents SDK (`https://strandsagents.com/`, `strands-agents`)** is lightweight, model-agnostic (OpenRouter/Anthropic/OpenAI/Gemini), and natively consumes MCP servers via `MCPClient` and `MCPAgentTool` without adding database schemas or heavy background containers.

## What Changes

- **MCP "Modo A" Tools (`engine/mcp/tools_research.py`, `engine/mcp/tools_editorial.py`, `engine/mcp/server.py`, `engine/mcp/mock_store.py`):**
  - `macy_ingest_research_documents`: Register agent-gathered research documents (`url`, `title`, `extracted_text`, `published_at`, `summary`, `facts`) into workspace `documents` and `research_records`, returning `document_id` UUIDs for FK-anchored `draft_claims`.
  - `macy_import_draft`: Import raw Markdown, local `.md`/`.pdf` file paths (`stdio` mode), or structured `ContentDraft` fields with `format_slug` (`"blog-short" | "blog" | "blog-long"`), `funnel_stage` (`"tofu" | "mofu" | "bofu"`), optional `brief_id` (bootstrapping an ad-hoc brief when omitted, or appending `revision 0..2` when provided), and optional inline `sources`.
  - Extend `macy_get_editorial_brief` and `macy_generate_draft` with `format_slug` and `funnel_stage` parameters, and extend `macy_evaluate_draft` with `deterministic_only: bool = False`.
- **Headless Strands Agents MCP Runner (`scripts/compliance_agent.py`, `pyproject.toml`, `docs/MCP.md`):**
  - Add optional dependency group `[project.optional-dependencies] agent = ["strands-agents>=0.1.0"]` in `pyproject.toml` (keeping core runtime container lean while enabling `uv run --extra agent python scripts/compliance_agent.py`).
  - Implement `scripts/compliance_agent.py` using `strands.Agent` + `strands.tools.mcp.MCPClient` connected over `stdio` (or `streamable-http`) to `content-engine mcp` to autonomously execute the Modo A compliance loop (`read brand -> ingest sources -> import draft -> evaluate(deterministic_only=True) -> self-correct revision 1..2 -> evaluate(full rubric) -> stop at Gate 2`).
  - Document both interactive (Antigravity/Claude) and headless (`strands-agents`) Modo A workflows in `docs/MCP.md`.

## Capabilities

### New Capabilities
- None (extends `mcp-server`).

### Modified Capabilities
- `mcp-server`: Add `macy_ingest_research_documents` and `macy_import_draft` tools, add `format_slug`/`funnel_stage` selection on briefing/drafting tools, add `deterministic_only` mode on `macy_evaluate_draft`, and provide the `strands-agents` external MCP Mode A runner (`scripts/compliance_agent.py` + `docs/MCP.md`).

## Out of Scope

- Embedding `strands-agents`, LangChain, or CrewAI inside `engine/` core modules (`ADR-002` and `lint-imports` strictly keep `engine/` deterministic and agent-free).
- Bypassing Gate 2 campaign-creator owner approval (`_validate_topic_approver`) or campaign spend caps (`ADR-013`, `ADR-016`).

## Impact

- **Dependencies:** Optional `[project.optional-dependencies] agent = ["strands-agents>=0.1.0"]` in `pyproject.toml`
- **MCP Server:** `engine/mcp/{tools_research,tools_editorial,server,mock_store}.py`
- **Scripts & Docs:** `scripts/compliance_agent.py`, `docs/MCP.md`, `docs/plans/M5.md`, `docs/features/F21-mcp-server.md`
- **Tests:** `tests/test_mcp_research_gate1.py`, `tests/test_mcp_editorial_gate2.py`, `tests/test_tenant_scoping.py`
