# Proposal: M4-02 — Deterministic checks

## Why
Before spending tokens on the LLM rubric evaluator (F14), every article draft must pass free, deterministic code checks covering claim provenance, evidence freshness, fetch health, SEO metadata placement and sizing, URL slug format, brand prohibited phrases, funnel CTA intensity limits, and campaign language matching (`lingua-py`, `pt`/`en` only) (`F14 AC1`, `AC5`, `ADR-015`, `ADR-025`).

## What Changes
- **Deterministic Check Functions (`engine/quality/checks.py`):**
  - Implement all nine deterministic checks as composable pure functions returning `DeterministicCheckResult` with explicit severity (`critical` vs `major`), `responsible_module`, detail message, and metadata:
    1. `check_fact_claims_have_source` (`critical`, `write`)
    2. `check_sources_fresh` (`critical`, `research`)
    3. `check_cited_urls_fetched_ok` (`critical`, `research`)
    4. `check_primary_keyword_placement` (`major`, `write` — reuses `engine.seo.normalize_keyword`)
    5. `check_meta_length` (`major`, `write`)
    6. `check_slug_format` (`critical`, `write`)
    7. `check_no_prohibited_phrases` (`critical`, `write`)
    8. `check_cta_intensity` (`critical`, `write`)
    9. `check_language_matches` (`critical`, `write` — reuses `engine.research.detect_language` powered by `lingua-py` restricted to `pt`/`en`)
- **Aggregated Runner & Deterministic Pre-Gate (`engine/quality/checks.py`, `engine/quality/evaluate.py`, `engine/quality/__init__.py`):**
  - Implement `quality.checks.run(draft, ...)` returning `DeterministicReport` (`verdict="REVISION"` when any critical check fails, `"PASS"` otherwise, plus `.to_quality_report()`).
  - Add deterministic-first evaluation gate `quality.evaluate(...)` ensuring any critical deterministic failure short-circuits immediately to `QualityReport(verdict="REVISION", ...)` with zero LLM calls (`F14 AC1`, `AC5`).
- **Verification & Documentation (`tests/test_quality_checks.py`, `docs/adr/ADR-025-supporting-libraries.md`, `docs/features/F13-article-writing.md`, `docs/features/F14-quality-evaluation.md`):**
  - Add literal unit tests for all nine checks, Hypothesis property tests for slug, meta length, and primary keyword placement rules, and spy-verified tests proving `0` LLM calls on critical failure including language mismatch.
