# quality Specification

## Purpose
Evaluates drafts through deterministic rule checks and LLM rubric analysis, routing revisions to the responsible module.

## Requirements

### Requirement: Multi-stage quality evaluation and revision routing
The system SHALL execute deterministic checks prior to LLM evaluation and route findings to brief, write, or research stages.

#### Scenario: Deterministic check failure prevents LLM call
- **WHEN** a draft fails a critical deterministic check (such as missing claim sources or prohibited phrases)
- **THEN** a REVISION verdict is returned immediately without invoking the LLM evaluator

#### Scenario: LLM evaluation findings route to responsible module
- **WHEN** LLM evaluation generates findings with responsible modules
- **THEN** the pipeline routes the revision to brief, write, or research accordingly

### Requirement: Deterministic draft quality checks
The system SHALL evaluate article drafts with pure deterministic checks for fact claim provenance, source freshness, cited URL status, primary keyword placement (title/H1/meta), meta length, slug format, prohibited brand phrases, CTA intensity, FormatPack word-count bounds (check_word_count), and campaign language match (lingua-py for pt/en) prior to any LLM call.

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

### Requirement: Isolated LLM rubric evaluation and persisted quality reports
After deterministic checks pass, the system SHALL evaluate drafts with `prompts/evaluate.md` and the format weighted rubric without passing the writer's prompt or reasoning, log `usage_ledger` for stage `"evaluate"` (`reasoning_effort = "high"`), and persist a 1:1 `quality_reports` record per draft revision (`F14 AC2`, `AC6`, `ADR-002`).

#### Scenario: Isolated evaluator produces rubric-grounded QualityReport and usage record
- **WHEN** `quality.evaluate` or `services.evaluate_draft` evaluates a draft that passes critical deterministic checks
- **THEN** the evaluator loads `prompts/evaluate.md` and the format rubric, never passes `write_blog.md` instructions or writer reasoning in the LLM prompt/input, records a `usage_ledger` row with `stage = 'evaluate'` and `prompt_version`, and returns a `QualityReport` whose findings cite rubric criteria and valid `responsible_module` values (`research`, `brief`, or `write`)

#### Scenario: Known-bad drafts fail against calibrated rubric evaluation whereas stub fails red
- **WHEN** regression cases 6–7 grounded drafts and known-bad drafts (thin stub, hallucinated statistics, prohibited claims, or off-brief copy) are evaluated
- **THEN** a rubber-stamp stub evaluator erroneously passes known-bad drafts (red check), whereas `prompts/evaluate.md` + `quality.evaluate` passes valid grounded drafts and rejects known-bad drafts with non-PASS verdicts and targeted findings

### Requirement: Revision routing and maximum two-revision surfacing cap
The system SHALL route verdicts by `responsible_module` (`brief` → `revise_brief`, `write` → `revise_draft`, `research` or `REJECT` → reject draft and return opportunity to `'scored'` pool) and stop revising at `revision = 2`, surfacing failing drafts as-is with findings (`F14 AC3`, `AC4`, `F12 AC4`, `F13 AC3`).

#### Scenario: Routing matrix dispatches brief, write, and research findings accurately
- **WHEN** `services.evaluate_and_route_draft` processes evaluator outcomes for `PASS`, `REVISION(brief)`, `REVISION(write)`, and `REVISION(research)` / `REJECT`
- **THEN** `PASS` marks `drafts.status = 'pass'` and emits `draft.awaiting_approval`; `brief` creates brief `v+1` (preserving `v`) and drafts the next revision; `write` creates `revision+1` incorporating findings; and `research` / `REJECT` marks `drafts.status = 'reject'`, `quality_reports.verdict = 'reject'`, and returns the opportunity to `'scored'` with `decided_by = NULL` freeing the weekly approval slot

#### Scenario: Failing draft at revision 2 surfaces to owner without attempting revision 3
- **WHEN** a draft fails evaluation across two revision loops and reaches `revision = 2` with non-`PASS`, non-`research` findings
- **THEN** no `revision = 3` draft or extra revision brief is created, `quality_reports.verdict` records `'revision'` with full findings, `drafts.status` transitions to `'pass'` for Gate 2 review (`revision = 2` cannot move to `'revision'`), and `draft.awaiting_approval` is emitted with owner surfacing metadata

### Requirement: Runtime claim verification decoupled from benchmark harness
The runtime package `engine/quality/claims.py` SHALL verify factual claims against source text using deterministic numeric/quote guards and configured runtime `LLMPort` / `safe_http.safe_request` adapters (`ADR-026`) without importing `evals/` (`ADR-012`, `ADR-027`).

#### Scenario: Claim verification runs cleanly without evals directory
- **WHEN** `services.verify_claims` executes inside a minimal runtime container lacking `evals/` or `httpx2`
- **THEN** `engine/quality/claims.py` performs deterministic number/quote checks and runtime judge calls cleanly, returning structured verdicts (`supported`, `refuted`, `unverifiable`), confidence scores, and evidence quotes while logging usage

### Requirement: Extended deterministic content and readability checks
The system SHALL provide pure deterministic checks for primary keyword density (`0.5%` to `2.5%`), primary keyword presence in the first paragraph and `H2`–`H3` subheadings, subheading frequency (`>= 1` H2/H3 per 300 words), and readability metrics (Flesch Reading Ease / sentence and paragraph length).

#### Scenario: Keyword density outside ideal band reports structured finding
- **WHEN** `check_keyword_density` evaluates a draft whose normalized primary keyword density is below `0.5%` or above `2.5%`
- **THEN** it returns a `DeterministicCheckResult` with `density` metadata and a clear diagnostic without invoking any LLM call

### Requirement: JSON-LD schema generation and validation
The system SHALL generate and validate `Article` and `FAQPage` JSON-LD structured data for drafts, verifying required fields, nested `author`/`publisher` objects, and locale consistency.

#### Scenario: Article draft emits valid Article JSON-LD
- **WHEN** `generate_article_schema` runs for a `ContentDraft` with `BrandProfile` metadata
- **THEN** it produces a JSON-LD object with `@context = "https://schema.org"`, `@type = "Article"`, `headline`, `author`, `publisher`, and `inLanguage` passing `validate_schema`
