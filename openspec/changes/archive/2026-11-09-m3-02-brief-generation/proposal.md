# Change: Editorial Brief Generation & Versioning (M3-02)

## Why
Per PRD §5.7, F12 (AC1–AC4), F03 (AC2), ADR-002, and ADR-022, approved editorial opportunities must be synthesized into structured, fact-grounded editorial briefs before drafting begins. Brief generation connects current brand positioning, industry evidence sources (`data` and `management` roles), and cached or provider-backed keyword demand snapshots while recording exact brand and prompt provenance.

## What Changes
1. **Database Schema (`briefs`)**: Add `db/migrations/20261109000000_create_briefs.sql` with composite foreign keys enforcing workspace isolation for `(workspace_id, opportunity_id)` and `(workspace_id, brand_profile_id)`, plus monotonic `(opportunity_id, version)` uniqueness.
2. **Brief Prompt (`prompts/brief.md`)**: Author editorial brief generation prompt versioned via deterministic SHA-256 hash and paired with industry pack, format pack, and brand profile system context.
3. **Regression Cases 6–7 (`evals/cases/case_06.yaml`, `evals/cases/case_07.yaml`, `evals/test_regression.py`)**: Add bilingual brief regression evaluation cases (`pt-BR` and `en-US`) verifying structured `EditorialBrief` completeness and workspace document grounding.
4. **Brief Generation Engine (`engine/strategy/brief.py`)**:
   - Lookup evidence from enabled `data` and `management` sources, extracting and persisting documents in `documents`.
   - Resolve target keyword metrics via `seo.keywords_for` with fallback `is_estimate=True` markers when keyword providers are unconfigured.
   - Pin the current `brand_profile_id` and `version` (`F03 AC2`).
   - Execute structured LLM synthesis returning `EditorialBrief` and strictly reject invalid, missing, or cross-tenant `required_facts[].source_id` references (`F12 AC2`).
5. **Brief Revision Versioning & UI/E2E (`engine/services.py`, `app.py`, E2E suites)**:
   - Expose `generate_brief` and `revise_brief` (`version + 1` preserving historical brief rows for `REVISION -> brief` routing).
   - Display validated briefs, keyword search volumes or `"Estimated"` badges, and evidence citations in the Weekly Report approval flow and extend J4 offline and live E2E tests.
