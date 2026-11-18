# Design: M4-02 — Deterministic checks

## Context
`F14` mandates a two-layer quality system where free deterministic code checks run prior to LLM rubric evaluation (`M4-03`). Reusing existing repo primitives (`engine.seo.normalize_keyword` for accent/case-folded SEO keyword matching and `engine.research.detect_language` for `lingua-py` `pt`/`en` detection) avoids duplication while keeping `engine/quality` acyclic in `import-linter`.

## Architecture & Pure Check Rules
1. **Pure Function Contracts (`engine/quality/checks.py`):**
   - Each pure check returns `DeterministicCheckResult(check, passed, severity, responsible_module, detail, metadata)` and converts failures cleanly via `.to_finding()` into `Finding`.
   - Critical blockers (`severity="critical"`) cause immediate `verdict="REVISION"` without calling an LLM:
     - `fact_sources`: every `fact` claim in `draft.claims` must carry a non-empty `source_id`, and when `evidence_documents` is provided, every cited `source_id` must exist in `evidence_documents`.
     - `source_freshness`: every cited document present in `evidence_documents` must not have `date_status == "unverified"`, must expose a valid `published_at` timestamp, and must fall within `[reference_time - timedelta(days=max_age_days), reference_time + timedelta(hours=24)]` (default `max_age_days = 90`).
     - `cited_urls_fetched`: every evidence document cited via `claims[].source_id` (or referenced by URL inside `body_md`) must have `fetch_status == "ok"`.
     - `slug_format`: `draft.slug` must match `^[a-z0-9]+(?:-[a-z0-9]+)*$` with length `1..max_length` (default `100`).
     - `prohibited_phrases`: no regex pattern in `BrandProfile.prohibited_phrases` may match (`re.IGNORECASE`) across `title`, `meta_description`, `slug`, `body_md`, `faq`, `cta`, or `claims[].text`.
     - `cta_intensity`: CTA intensity detected across `draft.cta` (and hard-sell CTA imperatives in `body_md`) classified on the scale `none < low < medium < high` must be `<= CampaignConfig.cta_intensity_limits[FUNNEL_STAGE]` (default `{"TOFU": "low", "MOFU": "medium", "BOFU": "high"}`).
     - `language_match`: `lingua-py` restricted to `pt` / `en` (`engine.research.detect_language`) evaluated on normalized draft prose must equal the base BCP-47 tag (`pt` or `en`) of the campaign locale (`F14 AC5`, `ADR-025`).
   - Non-critical search optimization checks (`severity="major"`, aligning with `packs/formats/blog/rubric.yaml`) attach `Finding` entries when violated without blocking LLM evaluation when critical checks pass:
     - `primary_keyword_placement`: `normalize_keyword(primary_keyword)` appears on word boundaries in `title`, the markdown H1 (`# ...`) of `body_md`, and `meta_description`.
     - `meta_length`: stripped `draft.meta_description` length falls in `[110, 160]` (configurable via `min_length`, `max_length`).
2. **Pre-Gate Short-Circuit (`quality.checks.run` & `quality.evaluate`):**
   - `quality.checks.run(draft, ...)` aggregates all check outcomes into `DeterministicReport(verdict, all_passed, critical_passed, checks, findings)` with `.to_quality_report()`.
   - `quality.evaluate(...)` executes `checks.run(...)` first; when `verdict == "REVISION"`, it returns `QualityReport(verdict="REVISION", findings=...)` immediately before invoking any LLM port or rubric evaluator (`M4-03` extends the passing path with `prompts/evaluate.md` and `rubric.yaml`).
