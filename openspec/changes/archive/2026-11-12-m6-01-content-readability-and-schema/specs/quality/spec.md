# quality Delta Specification

## ADDED Requirements

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
