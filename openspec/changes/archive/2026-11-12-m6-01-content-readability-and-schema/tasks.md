# Tasks: M6-01 — Content Readability, Keyword Density, JSON-LD Schema & Modular AI Rewrite

## 1. Extended Deterministic Keyword & Subheading Checks (Delivery KR: D1)
- [x] 1.1 Write failing unit & Hypothesis property tests in `tests/test_quality_checks.py` for `check_keyword_density` (`0.5%–2.5%`), `check_keyword_in_first_paragraph`, `check_keyword_in_headings`, and `check_subheading_density` (`>= 1` H2/H3 per 300 words).
- [x] 1.2 Implement the pure check functions in `engine/quality/checks.py` and wire them into `DeterministicRunOptions`. Verify D1: `uv run pytest tests/test_quality_checks.py -k "density or first_paragraph or headings or subheading" --no-cov` exits 0 with zero LLM calls.

## 2. Bilingual Tiered Readability (`en-US` + Martins et al. `pt-BR`) (Delivery KR: D2)
- [x] 2.1 Write failing tests in `tests/test_quality_checks.py` covering `en-US` Flesch (`206.835`), `pt-BR` Martins, Ghiraldelo, Nunes, & Oliveira Jr. (`248.835`), short-draft skip (`< 100` words), and tiered severity (`>= 60` pass, `40..59.9` `minor`, `< 40` `major`).
- [x] 2.2 Implement `count_syllables` (`en`/`pt`) and `check_readability` in `engine/quality/checks.py`. Verify D2: `uv run pytest tests/test_quality_checks.py -k readability --no-cov` exits 0.

## 3. Deterministic JSON-LD Generator & Validator (Delivery KR: D3)
- [x] 3.1 Write failing tests in `tests/test_schema.py` for generating and validating `Article`, `FAQPage`, `Product`, `LocalBusiness`, and `HowTo` JSON-LD (including `@graph` flattening and locale match).
- [x] 3.2 Implement `engine/production/schema.py` with zero LLM or `engine/quality/` dependencies. Verify D3: `uv run pytest tests/test_schema.py --no-cov && uv run lint-imports` exits 0.

## 4. Modular Seovela-Style LLM Rewrite Modes (Delivery KR: D4)
- [x] 4.1 Write failing tests in `tests/test_rewrite.py` and `tests/test_mcp_editorial_gate2.py` for `improve`, `simplify`, and `seo_optimize` using `FakeLLM` + `usage_ledger` spend tracking, asserting zero coupling to `engine/quality/`.
- [x] 4.2 Implement `engine/production/rewrite.py`, `prompts/rewrite_*.md`, and optional MCP/export integration. Verify D4: `uv run lint-imports && uv run mypy && uv run pytest tests/test_rewrite.py tests/test_mcp_editorial_gate2.py --no-cov && sh scripts/check.sh` exits 0.
