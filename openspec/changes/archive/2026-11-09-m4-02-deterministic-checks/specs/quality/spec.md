## ADDED Requirements

### Requirement: Deterministic draft quality checks
The system SHALL evaluate article drafts with pure deterministic checks for fact claim provenance, cited source freshness, cited URL fetch status, primary keyword placement in title/H1/meta, meta description length, URL slug format, prohibited brand phrase regex patterns, CTA intensity limits per funnel stage, and campaign language match (`lingua-py` restricted to `pt` and `en`) prior to invoking any LLM evaluator.

#### Scenario: Language mismatch fails deterministically without LLM evaluation
- **WHEN** a draft's detected language from `lingua-py` (`pt` or `en`) differs from the campaign locale's base language
- **THEN** `quality.checks.run` marks the language check as failed critically, returns `verdict = "REVISION"`, and `quality.evaluate` invokes zero LLM calls

#### Scenario: Pure SEO rules uphold property invariants for slug, meta length, and keyword placement
- **WHEN** `check_slug_format`, `check_meta_length`, and `check_primary_keyword_placement` evaluate generated inputs across Hypothesis property tests
- **THEN** valid kebab-case slugs (`^[a-z0-9]+(?:-[a-z0-9]+)*$`), bounded meta descriptions (`110..160` characters), and normalized keyword occurrences across `title`, H1, and `meta_description` pass deterministically while violations fail with explicit diagnostics
