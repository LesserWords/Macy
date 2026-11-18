# Proposal: M6-01 — Content Readability, Keyword Density, GEO Citability & JSON-LD Schema

**Plan Item:** M6-01 (Proposed) · **Features:** F13, F14, F15, F21 · **ADRs:** ADR-002, ADR-006, ADR-015, ADR-025

## Why

Macy currently validates draft keyword placement (`title`, `H1`, `meta_description`), meta length, slug format, word count, and language (`engine/quality/checks.py`), but lacks deeper on-page content quality signals needed for blogs and landing pages:

1. **Keyword & Structure Depth:** Missing checks for keyword density (`0.5%–2.5%`), keyword presence in the first paragraph and `H2–H3` subheadings, and subheading frequency (`>= 1` H2/H3 per 300 words).
2. **Readability & Citability (GEO):** No deterministic readability check (Flesch Reading Ease / sentence & paragraph length) or AI-citability check (BLUF intro, quotable specifics).
3. **Structured Data (JSON-LD):** Drafts do not emit or validate `Article` / `FAQPage` JSON-LD schema alongside Markdown + frontmatter.

## Source Logic Absorbed (License-Safe)

- **Seovela (`GPL-3.0`, algorithms reimplemented cleanly):**
  - `class-keyword-analyzer.php`: keyword density `(occurrences / total_words) * 100` (target `0.5%–2.5%`), first-paragraph check, H1–H3 heading check, title-start bonus.
  - `class-readability-analyzer.php`: Flesch Reading Ease (`206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)`, warn `< 60`, fail `< 40`), average sentence length (`<= 20` words, `< 25%` long sentences), paragraph length (`<= 150` words), subheading ratio (`>= 1` per 300 words).
  - `modules/schema/`: `Article`, `FAQPage`, `HowTo`, `LocalBusiness`, `Person`, `Product` JSON-LD templates.
- **seo-audit-skill (`MIT`, `seo-audit-full/scripts/check-schema.py`):**
  - `_flatten_schemas` (`@graph` unwrapping), `_validate_schema` (required vs. recommended fields, nested object checks like `author.name`, `publisher.name`), and `_lang_matches` (`pt-BR` / `en-US` consistency).
- **zens-ink-seo-package (`MIT`, `skills/geo-readiness/SKILL.md`):**
  - `content_qc` citability pattern (BLUF direct answer near top, concrete numbers/facts) and `llms_gen` factual description formatting.

## What Changes

- **Deterministic Quality Checks (`engine/quality/checks.py`):**
  - Add `check_keyword_density`, `check_keyword_in_first_paragraph`, `check_keyword_in_headings`, `check_subheading_density`, and `check_readability`.
  - **Bilingual Readability:** Use standard Flesch Reading Ease for `en-US` and the **Martins, Ghiraldelo, Nunes, and Oliveira Jr.** Flesch adaptation for `pt-BR`. Use tiered severity: `minor` (advisory) when `40 <= score < 60`, escalating to `major` (triggers `REVISION`) when `score < 40`.
  - Wire into `run()` with configurable options in `DeterministicRunOptions`.
- **JSON-LD Schema Generator & Validator (`engine/production/schema.py`):**
  - Generate and validate `Article`, `FAQPage`, `Product`, `LocalBusiness`, and `HowTo` JSON-LD schemas (zero LLM dependency).
- **Modular LLM Rewrite Modes (`engine/production/rewrite.py`):**
  - Independent Seovela-style rewrite actions (`improve`, `simplify`, `seo_optimize`) using `LLMPort` + `usage_ledger`, completely decoupled from `engine/quality/` so deterministic QA and LLM rewrites operate independently.
- **Publisher / Export & MCP (`engine/integrations/markdown_export.py`, `engine/mcp/tools_editorial.py`):**
  - Optionally include `schema_json_ld` in exported frontmatter/metadata and return content-score breakdown in `macy_evaluate_draft`.

## Capabilities

### New Capabilities
- `schema-generation`: Deterministic JSON-LD generation and validation (`Article`, `FAQPage`).

### Modified Capabilities
- `quality`: Extended deterministic on-page keyword, subheading, and readability checks.
- `writing`: Draft artifacts can carry validated JSON-LD structured data.

## Out of Scope

- Live URL crawling and multi-page site audits (covered in `M6-02`).
- WordPress-specific meta boxes, Yoast/RankMath importers, or PHP plugins.
- GSC / SERP rank tracking (covered in `M6-03`).

## Key Results: Delivered

All `D1`–`D4` must pass before `M6-01` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `check_keyword_density` (`0.5%–2.5%`), `check_keyword_in_first_paragraph`, `check_keyword_in_headings`, and `check_subheading_density` (`>= 1` H2/H3 per 300 words) pass deterministic unit + Hypothesis property tests with zero LLM calls | `uv run pytest tests/test_quality_checks.py -k "density or first_paragraph or headings or subheading" --no-cov` exits `0` |
| **D2** | `check_readability` computes `en-US` Flesch (`206.835`) and `pt-BR` Martins, Ghiraldelo, Nunes, & Oliveira Jr. (`248.835`), skips drafts `< 100` words, and enforces tiered severity (`>= 60` pass, `40..59.9` `minor`, `< 40` `major`) | `uv run pytest tests/test_quality_checks.py -k readability --no-cov` exits `0` |
| **D3** | `engine/production/schema.py` generates and validates `Article`, `FAQPage`, `Product`, `LocalBusiness`, and `HowTo` JSON-LD (`@graph` flattening + locale match) with zero imports from `engine/quality/` or LLM modules | `uv run pytest tests/test_schema.py --no-cov && uv run lint-imports` exits `0` |
| **D4** | `engine/production/rewrite.py` runs `improve`, `simplify`, and `seo_optimize` via `LLMPort` + `usage_ledger` spend tracking with zero coupling to `engine/quality/`, and all repo gates pass | `uv run pytest tests/test_rewrite.py tests/test_mcp_editorial_gate2.py --no-cov && sh scripts/check.sh` exits `0` |

## Decisions Locked (2026-11-12)

1. **Tiered Readability:** `40 <= score < 60` emits `minor` (advisory); `score < 40` escalates to `major` (blocks `PASS`).
2. **`pt-BR` Flesch:** Implements the **Martins, Ghiraldelo, Nunes, and Oliveira Jr.** Portuguese Flesch formula (`248.835 - 1.015*(W/S) - 84.6*(Syl/W)`).
3. **Schema Scope:** Includes `Article`, `FAQPage`, `Product`, `LocalBusiness`, and `HowTo`.
4. **Modular Split:** Deterministic checks/schemas and Seovela-style LLM rewrite modes (`improve`, `simplify`, `seo_optimize`) are built as independent modules with zero cross-dependency.
