# admin-ui Delta Specification

## ADDED Requirements

### Requirement: Bilingual 3-step first-boot setup wizard and J-POCKET E2E
The Admin UI (`app.py`) SHALL display a bilingual (`pt-BR`/`en-US`) 3-step First-Boot Setup Wizard whenever `list_workspaces()` is empty, allowing users to create an owner account, toggle capability plugins, select an integration stack preset, and auto-seed from a website URL, covered by offline E2E journey `J-POCKET` (`ADR-031`, `F22 AC8`).

#### Scenario: J-POCKET offline E2E completes 3-step onboarding and runs a filtered UI audit
- **WHEN** `tests/e2e/test_j_pocket_onboarding.py` launches the UI against an empty database with `AUTH_MODE=local` and completes Steps 1–3 selecting `core-compliance` + `seo-audit`, `pocket_free`, and a mock website URL
- **THEN** the workspace dashboard loads with editorial sidebar tabs hidden, runs a single-URL audit cleanly with zero `[data-testid="stException"]` errors, and all wizard strings pass `check_translations.py`
