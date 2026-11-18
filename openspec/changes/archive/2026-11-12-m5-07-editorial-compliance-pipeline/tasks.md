# Tasks

## 1. Format Sizing (`blog-short`, `blog`, `blog-long`), Deterministic `check_word_count` & `deterministic_only` Evaluation

- [x] 1.1 Write failing tests in `tests/test_quality_checks.py` and `tests/test_evaluator.py` verifying that `check_word_count` validates `body_md` word count against `FormatPack` (`min_words`..`max_words` for `blog-short`, `blog`, `blog-long`), fails when word count is below `min_words` or above `max_words` with `responsible_module = "write"`, and `evaluate_draft(..., deterministic_only=True)` executes all deterministic checks and persists a report with zero LLM calls — verify: `uv run pytest tests/test_quality_checks.py tests/test_evaluator.py -k "word_count or deterministic_only" --no-cov` fails before implementation
- [x] 1.2 Implement `check_word_count` in `engine/quality/checks.py`, wire format pack `min_words`/`max_words` into `run_all_checks` and `engine/quality/evaluate.py`, and add `deterministic_only: bool = False` support across `evaluate.py` and `services.evaluate_mcp_draft` — verify: `uv run pytest tests/test_quality_checks.py tests/test_evaluator.py --no-cov` and `uv run mypy` exit 0

## 2. `.md` (`python-frontmatter`) and `.pdf` (`pymupdf4llm`) Extraction & Parsing

- [x] 2.1 Write failing unit tests in `tests/test_writer.py` verifying that `extract_file_to_markdown` converts `.pdf` files via `pymupdf4llm` and reads `.md` files, and `parse_markdown_draft` parses YAML frontmatter (`python-frontmatter`) and Markdown H1/H2/FAQ/CTA blocks into a valid `ContentDraft` dictionary — verify: `uv run pytest tests/test_writer.py -k "extract_file or parse_markdown_draft" --no-cov` fails before implementation
- [x] 2.2 Add `python-frontmatter` and `pymupdf4llm` to `pyproject.toml` (`uv lock`) and implement `extract_file_to_markdown` and `parse_markdown_draft` in `engine/production/writer.py` — verify: `uv run pytest tests/test_writer.py -k "extract_file or parse_markdown_draft" --no-cov` and `uv run mypy` exit 0

## 3. Core Services (`ingest_external_research`, `import_external_draft`) & CLI (`content-engine import-draft`)

- [x] 3.1 Write failing integration tests in `tests/test_services.py` and `tests/test_cli.py` verifying `services.ingest_external_research` (SSRF check + `documents`/`research_records` upsert), `services.import_external_draft` (ad-hoc brief bootstrap when `brief_id` is omitted, `revision 0..2` when `brief_id` is provided, and `format_slug` selection), and CLI `content-engine import-draft <workspace> <file.md|.pdf> --evaluate --deterministic-only` — verify: `uv run pytest tests/test_services.py tests/test_cli.py -k "ingest_external_research or import_draft" --no-cov` fails before implementation
- [x] 3.2 Implement `services.ingest_external_research` and `services.import_external_draft` in `engine/services.py` and register `import-draft` in `engine/cli.py` — verify: `uv run pytest tests/test_services.py tests/test_cli.py -k "ingest_external_research or import_draft" --no-cov`, `uv run lint-imports`, and `uv run complexipy engine --max-complexity-allowed 15` exit 0
