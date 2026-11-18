# admin-ui Delta Specification

## ADDED Requirements

### Requirement: Capability-filtered sidebar and integration surfaces
The Admin UI (`app.py`) SHALL dynamically filter sidebar navigation tabs and required integration port prompts based on the active workspace's `enabled_capabilities` (`ADR-029`, `F22 AC2`).

#### Scenario: Disabling editorial-pipeline hides editorial screens from sidebar navigation
- **WHEN** an authenticated workspace user views the Admin UI for a workspace with only `["core-compliance", "seo-audit"]` enabled
- **THEN** sidebar navigation displays **Dashboard**, **SEO & GEO Audit**, and workspace settings while omitting **Weekly Report**, **Draft Review**, **Campaigns**, and **Sources**
