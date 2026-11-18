# Tasks

## 1. MCP "Modo A" Research Ingestion (`macy_ingest_research_documents`)

- [x] 1.1 Write failing tests in `tests/test_mcp_research_gate1.py` and `tests/test_tenant_scoping.py` verifying that `macy_ingest_research_documents` persists agent-supplied documents and extracted facts into `documents` and `research_records` (both Postgres and `--mock`), rejects SSRF/private URLs, deduplicates by canonical URL, and denies cross-tenant callers with `PERMISSION_DENIED` — verify: `uv run pytest tests/test_mcp_research_gate1.py -k "ingest_research" --no-cov` fails before implementation
- [x] 1.2 Expose `macy_ingest_research_documents` across `engine/mcp/tools_research.py`, `engine/mcp/server.py`, and `engine/mcp/mock_store.py` delegating strictly to `services.ingest_external_research` — verify: `uv run pytest tests/test_mcp_research_gate1.py -k "ingest_research" --no-cov` and `uv run lint-imports` exit 0

## 2. MCP Draft Import (`macy_import_draft`), Format Sizing & `deterministic_only` Evaluation

- [x] 2.1 Write failing tests in `tests/test_mcp_editorial_gate2.py` verifying `macy_import_draft` (standalone import with `brief_id=None`, iterative agent revision `0..2` with `brief_id`, `format_slug` in `{"blog-short", "blog", "blog-long"}`, and `file_path` refusal on `streamable-http`), `format_slug`/`funnel_stage` on `macy_get_editorial_brief`, and `macy_evaluate_draft(..., deterministic_only=True)` — verify: `uv run pytest tests/test_mcp_editorial_gate2.py -k "import_draft or deterministic_only" --no-cov` fails before implementation
- [x] 2.2 Implement `macy_import_draft` and add `format_slug`, `funnel_stage`, and `deterministic_only` parameters across `engine/mcp/tools_editorial.py`, `engine/mcp/server.py`, and `engine/mcp/mock_store.py` — verify: `uv run pytest tests/test_mcp_editorial_gate2.py --no-cov`, `uv run mypy`, and `uv run complexipy engine --max-complexity-allowed 15` exit 0

## 3. End-to-End "Modo A" Compliance Loop & Headless Strands Agents (`strands-agents`) Runner

- [x] 3.1 Write failing integration tests in `tests/test_mcp_editorial_gate2.py` exercising the complete "Modo A" agent loop (`macy_ingest_research_documents` $\rightarrow$ `macy_import_draft` with under-length draft $\rightarrow$ `macy_evaluate_draft(deterministic_only=True)` returning `REVISION(word_count)` $\rightarrow$ `macy_import_draft` `revision = 1` $\rightarrow$ `macy_evaluate_draft` returning `PASS` $\rightarrow$ `macy_decide_draft("approved")`) and unit tests for `scripts/compliance_agent.py` CLI argument/prompt wiring — verify: `uv run pytest tests/test_mcp_editorial_gate2.py -k "modo_a" --no-cov` fails before implementation
- [x] 3.2 Add optional `[project.optional-dependencies] agent = ["strands-agents>=0.1.0"]` to `pyproject.toml`, implement `scripts/compliance_agent.py` (`MCPClient` + `Agent` connecting to `content-engine mcp`), and document interactive (Antigravity/Claude Code) and headless (`strands-agents`) Modo A workflows in `docs/MCP.md` — verify: `uv run pytest tests/test_mcp_editorial_gate2.py --no-cov` and `npx --yes @fission-ai/openspec@1.16.0 validate --all --strict --no-interactive` exit 0
