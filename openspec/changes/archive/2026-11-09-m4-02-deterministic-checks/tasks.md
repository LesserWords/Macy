# Tasks

## 1. Pure Deterministic Check Functions (Step 1)
- [x] 1.1 Write failing literal and Hypothesis property tests in `tests/test_quality_checks.py` for all 9 pure deterministic check functions (`check_fact_claims_have_source`, `check_sources_fresh`, `check_cited_urls_fetched_ok`, `check_primary_keyword_placement`, `check_meta_length`, `check_slug_format`, `check_no_prohibited_phrases`, `check_cta_intensity`, `check_language_matches`).
  → verify: `uv run pytest tests/test_quality_checks.py` fails red (`ModuleNotFoundError: No module named 'engine.quality'`) before implementation; scenarios in `specs/quality/spec.md` map 1:1 to `test_check_*` and `test_hypothesis_*` tests.
- [x] 1.2 Implement `engine/quality/checks.py` with all 9 pure check functions reusing `engine.seo.normalize_keyword` and `engine.research.detect_language`.
  → verify: Step 1 literal and Hypothesis property tests in `tests/test_quality_checks.py` pass green (`10 passed`).

## 2. Runner & Deterministic Pre-Gate (Step 2)
- [x] 2.1 Add failing tests in `tests/test_quality_checks.py` for `quality.checks.run(draft)` and `quality.evaluate(...)` confirming critical deterministic failures (including `F14 AC5` language mismatch) return `verdict="REVISION"` with `0` LLM calls verified by a spy adapter.
  → verify: Step 2 tests fail red (`10 failed, 11 passed`: `AttributeError` for `checks.run` and `quality.evaluate`) before runner/pre-gate wiring.
- [x] 2.2 Implement `quality.checks.run(...)`, `engine/quality/evaluate.py` deterministic pre-gate, and `engine/quality/__init__.py` exports; update `docs/adr/ADR-025-supporting-libraries.md`, `docs/features/F13-article-writing.md`, and `docs/features/F14-quality-evaluation.md`.
  → verify: full `pytest` suite (`500 passed`, 99.51% branch coverage), Hypothesis `ci` profile, `ruff`, `mypy` (101 files), `complexipy` (`<= 14`), `vulture`, `lint-imports` (2 kept), `jscpd` (0 clones), `diff-cover` (100% across 292 lines), `check_translations.py`, and `openspec validate --all --strict --no-interactive` pass green.
