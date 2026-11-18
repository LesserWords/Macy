# Design: M5-07 — Editorial Compliance Pipeline, `.md`/PDF Import (`python-frontmatter` + `pymupdf4llm`) & Format Sizing

## Context & Architectural Constraints

All enhancements in `M5-07` preserve Macy's core invariants:
- **Deterministic Core (`ADR-002`):** `engine/` remains 100% deterministic and agent-free.
- **Services Boundary (`ADR-014`):** `engine/cli.py` imports only `engine.services` and `engine.contracts` (`lint-imports`).
- **4 GB VPS Budget (`ADR-012`, `TECH-RADAR.md`):** Unlike `docling-serve-cpu` (`7.05 GiB` disk, `1.60 GiB` RAM, dropped in `M3-03`), **`pymupdf4llm`** (~25 MB CPU wheel) and **`python-frontmatter`** run in-process with negligible memory overhead.
- **Quality Gates:** `complexipy <= 15` per function, `jscpd = 0%` duplication, `diff-cover = 100%`, and `>= 90%` total branch coverage.

---

## Decisions

### Decision 1: Adopt `python-frontmatter` & `pymupdf4llm` for `.md` and `.pdf` Draft Extraction
- **Files:** `pyproject.toml`, `engine/production/writer.py`, `tests/test_writer.py`.
- **`extract_file_to_markdown(file_path: Path) -> str`:**
  - Enforces a safety ceiling (`MAX_IMPORT_BYTES = 10 * 1024 * 1024` / `MAX_PDF_PAGES = 50`) to protect the `4 GB` VPS memory budget (`ADR-012`) against oversized files.
  - For `.pdf` files: validates page count via PyMuPDF and calls `pymupdf4llm.to_markdown(str(file_path))` to extract structured GitHub-Flavored Markdown (preserving headings, bullet lists, and tables).
  - For `.md` / `.markdown` / `.txt` files: reads UTF-8 text directly.
- **`parse_markdown_draft(raw_md: str, default_sources: list[str] | None = None) -> dict[str, Any]`:**
  - Uses `frontmatter.loads(raw_md)` (`python-frontmatter`) to separate optional YAML metadata (`title`, `slug`, `meta_description`, `primary_keyword`, `format_slug`, `funnel_stage`, `faq`, `cta`, `claims`) from `post.content`.
  - When metadata keys are omitted in frontmatter, derives `title` from the first `# H1` heading, normalizes `slug` to kebab-case (`<= 100` chars), extracts a `110..160` character `meta_description` from the opening paragraph, parses `## FAQ` / `## Perguntas Frequentes` pairs, and synthesizes stage-aligned `CTA` and `claims`. Leaves `primary_keyword` as `None` unless explicitly supplied in frontmatter or arguments (avoiding false-positive failures in `check_primary_keyword_placement` on pure compliance audits).

### Decision 2: Core Research Ingestion & Draft Import Services (`services.ingest_external_research` & `services.import_external_draft`)
- **Files:** `engine/services.py`, `engine/cli.py`, `tests/test_cli.py`.
- **`services.ingest_external_research`:**
  - Validates each item's `url` via `safe_http.validate_safe_url`, normalizes via `research.canonicalize_url`, upserts into `documents` (`fetch_status = "ok"`) and `research_records` (`record_status = "extracted"`), and returns `document_id` UUIDs for FK-anchored `draft_claims`.
  - When `run_id` is omitted, inserts any ad-hoc `runs` row directly with `status = 'completed'` (`started_at = now()`, `finished_at = now()`, `stage = 'research'`) so it **never** collides with the `runs_one_active` partial unique index (`WHERE status IN ('queued', 'running')`) if a scheduled/manual run is active.
- **`services.import_external_draft`:**
  - Accepts `workspace_id`, `user_id`, optional `brief_id`, `body_md` (or `file_path`), `format_slug` (`"blog-short" | "blog" | "blog-long"`, default `"blog"`), `funnel_stage` (`"tofu" | "mofu" | "bofu"`, default `"mofu"`), and optional inline `sources`.
  - **When `brief_id` is `None`:**
    - Inserts an ad-hoc `runs` row directly with `status = 'completed'` (`started_at = now()`, `finished_at = now()`, `stage = 'write'`), avoiding `runs_one_active` collisions.
    - Because `opportunities.record_id` is `NOT NULL` with `FOREIGN KEY (workspace_id, record_id) REFERENCES research_records(workspace_id, id)` (`db/schema.sql:174, 852`), if no `sources` were supplied, creates a synthetic anchor `document` (`url="urn:macy:imported-draft:<uuid>"`, `fetch_status="ok"`, `date_status="verified"`) and `research_record` before inserting the `'approved'` `Opportunity` and `v1` `EditorialBrief` (pinned to the workspace's latest `brand_profile` and requested `format_slug`), then inserts `revision = 0` in `drafts` + `draft_claims`.
  - **When `brief_id` is provided:** Appends `revision + 1` (up to `revision = 2`) under `brief_id`.
- **CLI `content-engine import-draft`:**
  - `content-engine import-draft <workspace> <file_path> [--format blog-short|blog|blog-long] [--funnel-stage tofu|mofu|bofu] [--brief-id <uuid>] [--evaluate] [--deterministic-only]`.

### Decision 3: Format Sizing (`blog-short`, `blog`, `blog-long`), `check_word_count` & `deterministic_only` Mode
- **Files:** `packs/formats/{blog-short,blog,blog-long}/format.yaml`, `engine/quality/checks.py`, `engine/quality/evaluate.py`, `tests/test_quality_checks.py`, `tests/test_evaluator.py`.
- **`DeterministicRunOptions` Extension (`engine/quality/checks.py`):**
  - Add `min_words: int = 800` and `max_words: int = 2500` fields to `DeterministicRunOptions` (populated from `FormatPack` in `evaluate.py`), preserving `checks.run()`'s existing signature and keeping `complexipy <= 15`.
- **`check_word_count`:** Counts words in `draft.body_md` and checks `min_words <= word_count <= max_words` against the active `FormatPack` (`blog-short`: 400–800, `blog`: 800–2500, `blog-long`: 1800–3500). Under-length drafts (`word_count < min_words`) fail with `severity = "critical"` (so thin stubs always trigger `verdict = "REVISION"`), while over-length drafts (`word_count > max_words`) fail with `severity = "major"` and `responsible_module = "write"`.
- **`deterministic_only=True`:** Executes all 10 deterministic checks (`fact_sources`, `source_freshness`, `cited_urls_fetched`, `primary_keyword_placement`, `meta_length`, `slug_format`, `prohibited_phrases`, `cta_intensity`, `language_match`, `word_count`), triggers `verdict = "REVISION"` whenever `all_passed` is `False` (so both critical and major compliance findings block rubber-stamping), and persists a `QualityReport` (`evaluator_model = "deterministic-only"`) with `$0.00` LLM spend.
