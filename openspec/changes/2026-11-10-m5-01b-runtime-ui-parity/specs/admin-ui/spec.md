# admin-ui Delta Specification

## MODIFIED Requirements

### Requirement: Translation catalog completeness gate
The system SHALL wrap all user-facing labels and fallback literals in `app.py` with `_()` and verify via automated CI checks that all UI message strings are completely translated in `pt_BR` and `en_US` `.po`/`.mo` catalogs with zero fuzzy entries.

#### Scenario: Untranslated string fails CI gate
- **WHEN** a message catalog contains untranslated or fuzzy strings
- **THEN** the translation check script exits with a non-zero status

#### Scenario: Previously hardcoded UI fallback literals render localized in both languages
- **WHEN** an editor opens Sources, Weekly Report, Draft Review, or Dashboard in `pt-BR` and `en-US`
- **THEN** strings `"Add New Source"`, `"Opportunity"`, `"No evidence recorded."`, and `"Draft"` resolve through gettext and `scripts/check_translations.py` exits 0

## ADDED Requirements

### Requirement: Development-mode offline brief and draft execution fallback
The system SHALL allow `_use_offline_brief_mode` in `app.py` to enable offline fake brief generation and draft execution when `APP_ENV` is `test` or `development` without a configured `llm` integration or `OPENROUTER_API_KEY`, while strictly prohibiting offline fallback in `staging` and `production`.

#### Scenario: Development mode generates briefs offline when no LLM integration is configured
- **WHEN** `APP_ENV=development` and neither a workspace `llm` integration nor `OPENROUTER_API_KEY` is configured
- **THEN** clicking **Generate Brief** or **Write & Evaluate Draft** runs cleanly with fake adapters in local development, whereas `APP_ENV=staging` or `production` never enters offline fallback

### Requirement: Dynamic campaign monthly cap in Dashboard overview
The system SHALL display the workspace's active campaign `monthly_cap_usd` in `_render_dashboard` (or a translated indicator when no campaign is active) instead of a hardcoded `\$150.00` placeholder.

#### Scenario: Dashboard displays active campaign monthly cap dynamically
- **WHEN** an active campaign has `monthly_cap_usd = 250.00`
- **THEN** the Dashboard renders `$250.00` (locale-formatted) rather than a hardcoded `$150.00`, and handles workspaces without campaigns gracefully
