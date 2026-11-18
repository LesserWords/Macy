# quality Delta Specification

## MODIFIED Requirements

### Requirement: Deterministic draft quality checks
The system SHALL evaluate article drafts with pure deterministic checks for fact claim provenance, cited source freshness, cited URL fetch status, primary keyword placement in title/H1/meta, meta description length, URL slug format, prohibited brand phrase regex patterns, CTA intensity limits per funnel stage, article word count bounds (`check_word_count` against the brief's `FormatPack.min_words`..`max_words`), and campaign language match (`lingua-py` restricted to `pt` and `en`) prior to invoking any LLM evaluator.

#### Scenario: Language mismatch fails deterministically without LLM evaluation
- **WHEN** a draft's detected language from `lingua-py` (`pt` or `en`) differs from the campaign locale's base language
- **THEN** `quality.checks.run` marks the language check as failed critically, returns `verdict = "REVISION"`, and `quality.evaluate` invokes zero LLM calls

#### Scenario: Pure SEO rules uphold property invariants for slug, meta length, and keyword placement
- **WHEN** `check_slug_format`, `check_meta_length`, and `check_primary_keyword_placement` evaluate generated inputs across Hypothesis property tests
- **THEN** valid kebab-case slugs (`^[a-z0-9]+(?:-[a-z0-9]+)*$`), bounded meta descriptions (`110..160` characters), and normalized keyword occurrences across `title`, H1, and `meta_description` pass deterministically while violations fail with explicit diagnostics

#### Scenario: Word count outside FormatPack bounds fails deterministically
- **WHEN** `check_word_count` evaluates a draft whose `body_md` word count falls below `min_words` (`severity = "critical"`) or exceeds `max_words` (`severity = "major"`) for the active `FormatPack` (`blog-short`: 400–800, `blog`: 800–2500, `blog-long`: 1800–3500)
- **THEN** `check_word_count` fails with `responsible_module = "write"` and metadata reporting `word_count`, `min_words`, and `max_words`

#### Scenario: Deterministic-only evaluation audits compliance with zero LLM cost
- **WHEN** `quality.evaluate.evaluate_draft` or `services.evaluate_mcp_draft` is called with `deterministic_only=True`
- **THEN** all deterministic checks execute, any failed critical or major check (`all_passed == False`) returns `verdict = "REVISION"`, and a `QualityReport` (`evaluator_model = "deterministic-only"`) is persisted without invoking any LLM call
