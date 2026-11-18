# admin-ui Specification

## Purpose
Provides the Streamlit administrative interface with bilingual support (pt-BR and en-US) and user locale persistence.

## Requirements

### Requirement: Bilingual admin UI with persistent user locale
The system SHALL present UI labels in Portuguese (Brazil) or English according to user preference, stored in the database.

#### Scenario: Switching UI language updates labels and persists
- **WHEN** a user selects a different UI language in the interface
- **THEN** interface reloads with translated labels and preference is stored in users.ui_locale

#### Scenario: UI locale is independent of campaign content language
- **WHEN** a user views an en-US campaign in a pt-BR UI session
- **THEN** admin chrome is in Portuguese while campaign content and outputs remain in English

### Requirement: Translation catalog completeness gate
The system SHALL verify via automated CI checks that all user-facing UI message strings are fully translated in both `pt_BR` and `en_US` message catalogues with zero fuzzy entries.

#### Scenario: Untranslated string fails CI gate
- **WHEN** a message catalog contains untranslated or fuzzy strings
- **THEN** the translation check script exits with a non-zero status

### Requirement: Locale-aware date and currency formatting
The system SHALL format dates and monetary amounts according to the active UI locale while retaining USD currency units.

#### Scenario: Format dates and currency for pt-BR and en-US
- **WHEN** displaying dates and currency in pt-BR
- **THEN** dates use DD/MM/YYYY and monetary amounts use comma decimal separators
- **WHEN** displaying dates and currency in en-US
- **THEN** dates use MM/DD/YYYY and monetary amounts use period decimal separators

### Requirement: End-to-end live journey coverage J0–J8 with budget guard
The system SHALL provide a Playwright live E2E suite covering journeys `J0` through `J8` (`J0` auth/isolation, `J1` campaign settings/i18n, `J2` sources, `J3` research/scoring report, `J4` Gate 1 topic approval & brief, `J5` draft & quality evaluation with `lingua` check, `J6` Gate 2 approval & draft-mode publishing, `J7` topic and draft rejection paths, `J8` integrations, Standard Webhooks delivery, and SSRF private-IP refusal) enforced by a per-run budget cap guard (`ADR-018`, `PRD §9.6`).

#### Scenario: Live E2E suite executes J0–J8 within the campaign spend cap
- **WHEN** the live E2E suite runs against the staging/Compose stack
- **THEN** journeys `J0` through `J8` pass and `BudgetGuard` verifies cumulative spend does not exceed the campaign cap

### Requirement: Capability-filtered sidebar and integration surfaces
The Admin UI (`app.py`) SHALL dynamically filter sidebar navigation tabs and required integration port prompts based on the active workspace's `enabled_capabilities` (`ADR-029`, `F22 AC2`).

#### Scenario: Disabling editorial-pipeline hides editorial screens from sidebar navigation
- **WHEN** an authenticated workspace user views the Admin UI for a workspace with only `["core-compliance", "seo-audit"]` enabled
- **THEN** sidebar navigation displays **Dashboard**, **SEO & GEO Audit**, and workspace settings while omitting **Weekly Report**, **Draft Review**, **Campaigns**, and **Sources**

### Requirement: Bilingual 3-step first-boot setup wizard and J-POCKET E2E
The Admin UI (`app.py`) SHALL display a bilingual (`pt-BR`/`en-US`) 3-step First-Boot Setup Wizard whenever `list_workspaces()` is empty, allowing users to create an owner account, toggle capability plugins, select an integration stack preset, and auto-seed from a website URL, covered by offline E2E journey `J-POCKET` (`ADR-031`, `F22 AC8`).

#### Scenario: J-POCKET offline E2E completes 3-step onboarding and runs a filtered UI audit
- **WHEN** `tests/e2e/test_j_pocket_onboarding.py` launches the UI against an empty database with `AUTH_MODE=local` and completes Steps 1–3 selecting `core-compliance` + `seo-audit`, `pocket_free`, and a mock website URL
- **THEN** the workspace dashboard loads with editorial sidebar tabs hidden, runs a single-URL audit cleanly with zero `[data-testid="stException"]` errors, and all wizard strings pass `check_translations.py`
