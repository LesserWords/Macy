# Proposal: M5-07 — Editorial Compliance Pipeline, `.md`/PDF Import (`python-frontmatter` + `pymupdf4llm`) & Format Sizing

**Plan Item:** M5-07 ([docs/plans/M5.md](../../../docs/plans/M5.md)) · **Features:** F07, F12, F13, F14, F15 · **ADRs:** ADR-002, ADR-009, ADR-014, ADR-025, ADR-026

## Why

To operate as a flexible **Editorial Quality & Compliance Pipeline ("Esteira de Qualidade e Compliance Editorial")**, Macy's core engine (`engine/`) and CLI need three foundational capabilities before exposing them to external AI agents in `M5-08`:

1. **External Research Ingestion for FK-Grounded Claims:** `draft_claims` enforces `CHECK (kind <> 'fact' OR document_id IS NOT NULL)` with a composite foreign key to `documents(workspace_id, id)`. When an editor or external agent gathers sources outside Macy's internal RSS/Brave loop, `engine/services.py` needs a service (`services.ingest_external_research`) to validate URLs via `safe_http`, deduplicate by canonical URL, and persist verified `documents` and `research_records` rows.
2. **Direct `.md` & `.pdf` Draft Import (`python-frontmatter` + `pymupdf4llm`):** Currently, drafts can only be created by calling Macy's internal LLM writer (`write_blog.md`) against an existing `brief_id`. Operators cannot import a ready-made `.md` or `.pdf` file via CLI (`content-engine import-draft <file.md|.pdf>`) or service API (`services.import_external_draft`) for compliance auditing and iterative improvement (`revision 0..2`). While `Docling` was dropped in `M3-03` due to its `7.05 GiB` image and `1.60 GiB` RAM footprint on a `4 GB` VPS (`TECH-RADAR.md`), **`pymupdf4llm`** (~25 MB wheel, zero ML/GPU dependencies) and **`python-frontmatter`** convert PDFs and YAML-frontmatter Markdown files cleanly on CPU in milliseconds.
3. **Format Size Enforcement (`blog-short`, `blog`, `blog-long`) & Zero-Cost Compliance Mode (`deterministic_only`):** Although `packs/formats/` defines `blog-short` (400–800 words), `blog` (800–2500 words), and `blog-long` (1800–3500 words), `engine/quality/checks.py` lacks a deterministic `check_word_count` check, and `evaluate_draft` lacks a `deterministic_only=True` flag to run instant `$0.00` compliance audits before invoking the paid LLM rubric evaluator.

## What Changes

- **Adopt `python-frontmatter` & `pymupdf4llm` (`pyproject.toml`, `ADR-025`, `TECH-RADAR.md`):**
  - Use `python-frontmatter` in `parse_markdown_draft` (`engine/production/writer.py`) to parse YAML frontmatter (`title`, `slug`, `meta_description`, `primary_keyword`, `format_slug`, `funnel_stage`, `sources`) and Markdown sections/FAQ/CTA.
  - Use `pymupdf4llm.to_markdown` in `extract_file_to_markdown` (`engine/production/writer.py`) to convert local `.pdf` files (including multi-column layouts and tables) into GitHub-Flavored Markdown on CPU without heavy ML containers.
- **Core Services (`services.ingest_external_research` & `services.import_external_draft`) & CLI (`content-engine import-draft`):**
  - `services.ingest_external_research`: Upsert external documents into `documents` and `research_records` with `safe_http` validation and return `document_id` UUIDs.
  - `services.import_external_draft`: Import `.md`/`.pdf` or structured `ContentDraft` payloads; auto-bootstrap an ad-hoc `run` $\rightarrow$ `approved opportunity` $\rightarrow$ `EditorialBrief` (pinned to `format_slug` in `{"blog-short", "blog", "blog-long"}` and `funnel_stage` in `{"tofu", "mofu", "bofu"}`) when `brief_id` is omitted, or append `revision 0..2` when `brief_id` is provided.
  - Add `content-engine import-draft <workspace> <file.md|.pdf> [--format blog-short|blog|blog-long] [--funnel-stage tofu|mofu|bofu] [--brief-id <uuid>] [--evaluate] [--deterministic-only]` to `engine/cli.py`.
- **Deterministic Word-Count Check (`check_word_count`) & `deterministic_only` Mode (`engine/quality/checks.py`, `engine/quality/evaluate.py`):**
  - Add `check_word_count(draft, min_words=..., max_words=...)` to `engine/quality/checks.py` and wire `FormatPack.min_words`..`max_words` into `run_all_checks`.
  - Add `deterministic_only: bool = False` to `evaluate_draft` and `services.evaluate_mcp_draft` to execute all deterministic checks and persist a `QualityReport` (`evaluator_model = "deterministic-only"`) with zero LLM calls.

## Capabilities

### New Capabilities
- None (extends existing capabilities).

### Modified Capabilities
- `writing`: Support importing external `.md` (`python-frontmatter`) and `.pdf` (`pymupdf4llm`) files or structured `ContentDraft` payloads (`services.import_external_draft`, `content-engine import-draft`) with ad-hoc brief bootstrapping and `FormatPack` sizing (`blog-short`, `blog`, `blog-long`).
- `quality`: Add deterministic `check_word_count` enforcing `FormatPack` `min_words`..`max_words` bounds and support `deterministic_only` evaluation mode with zero LLM spend.

## Out of Scope

- MCP tool exposure (`macy_ingest_research_documents`, `macy_import_draft`) and headless `strands-agents` MCP runner — covered in `M5-08` (`2026-11-12-m5-08-mcp-agent-mode-strands`).
- Heavy ML/GPU PDF containers (`docling-serve-cpu` remains dropped per `M3-03` in `TECH-RADAR.md`).

## Impact

- **Dependencies:** `python-frontmatter`, `pymupdf4llm` added to `pyproject.toml`
- **Engine:** `engine/quality/checks.py`, `engine/quality/evaluate.py`, `engine/production/writer.py`, `engine/services.py`, `engine/cli.py`
- **Docs:** `docs/plans/M5.md`, `docs/architecture/TECH-RADAR.md`, `docs/adr/ADR-025-supporting-libraries.md`
- **Tests:** `tests/test_quality_checks.py`, `tests/test_evaluator.py`, `tests/test_writer.py`, `tests/test_cli.py`
