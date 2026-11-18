# mcp-server Delta Specification

## ADDED Requirements

### Requirement: Editorial briefing, grounded drafting, and runtime claim verification MCP tools
The system SHALL expose `macy_get_editorial_brief`, `macy_generate_draft`, and `macy_verify_claims` MCP tools delegating to `engine/services.py`, persisting FK-backed `draft_claims`, and running claim verification inside `engine/quality/claims.py` without importing `evals/` (`F12`, `F13`, `F14`, `F21 AC5`, `ADR-026`, `ADR-027`).

#### Scenario: Generating or retrieving an SEO-grounded editorial brief
- **WHEN** `macy_get_editorial_brief` is invoked with `workspace_id` and an approved `opportunity_id`
- **THEN** the server SHALL return or create the structured editorial brief including target audience, primary/secondary keywords, angle, outline, and cited source documents

#### Scenario: Generating a source-grounded article draft
- **WHEN** `macy_generate_draft` is invoked with `workspace_id` and `brief_id`
- **THEN** the server SHALL persist and return the draft Markdown alongside `draft_claims` rows anchored by foreign keys to verified workspace `documents`

#### Scenario: Verifying factual claims against source passages in runtime containers
- **WHEN** `macy_verify_claims` is invoked with claim statements and candidate source passages (or a `draft_id`)
- **THEN** the runtime claim checker (`engine/quality/claims.py`, importing zero modules from `evals/`) SHALL return each claim's verdict (`supported`, `refuted`, or `unverifiable`), confidence score, and verbatim evidence passage

### Requirement: Quality evaluation, Gate 2 draft review, and publishing MCP tools
The system SHALL expose `macy_evaluate_draft`, `macy_list_drafts_awaiting_approval`, `macy_decide_draft`, and `macy_publish_draft` MCP tools enforcing Gate 2 status transitions, campaign creator authorization across Gate 2 and publish, and staging draft-only mode (`F14`, `F15`, `F16`, `F21 AC6`, `ADR-013`, `ADR-021`, `ADR-027`).

#### Scenario: Evaluating an article draft deterministically and by rubric
- **WHEN** `macy_evaluate_draft` is called with `workspace_id` and `draft_id`
- **THEN** the server SHALL execute deterministic checks prior to isolated LLM rubric scoring and return verdict, quality score, findings, and revision routing state

#### Scenario: Listing passing drafts awaiting Gate 2 review
- **WHEN** `macy_list_drafts_awaiting_approval` is called with `workspace_id`
- **THEN** the server SHALL return drafts currently in status `'pass'` awaiting Gate 2 decision

#### Scenario: Approving or declining a draft at Gate 2 with normalized values
- **WHEN** `macy_decide_draft` is called with `workspace_id`, `draft_id`, and `decision` (`"approved"` or `"declined"`) by the campaign creator owner
- **THEN** `services.decide_draft` transitions `'pass'` atomically and records `draft.decided` with `actor_user_id`, whereas non-creator owners or invalid decision strings fail cleanly

#### Scenario: Publishing an approved draft enforces creator authority and staging draft mode
- **WHEN** `macy_publish_draft` is called with an `'approved'` `draft_id`
- **THEN** the server SHALL enforce campaign-creator ownership (`_validate_topic_approver`), dispatch the configured `PublisherPort`, and force `mode="draft"` whenever `APP_ENV=staging`

### Requirement: Tenant-scoped MCP resources and editorial review prompt
The system SHALL expose `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, and `macy://drafts/{id}/claims` resources and the `macy_editorial_review` prompt enforcing `require_member` (`F21 AC8`, `ADR-009`, `ADR-027`).

#### Scenario: Authorized member reads MCP resources and prompt
- **WHEN** an authorized member reads `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, or `macy://drafts/{id}/claims`, or renders `macy_editorial_review`
- **THEN** the server SHALL return current tenant-scoped brand rules, Gate 1 candidates, or claim provenance/evaluation details

#### Scenario: Cross-tenant resource read is refused
- **WHEN** an authenticated caller reads a `macy://` URI belonging to another workspace
- **THEN** the server SHALL raise `PERMISSION_DENIED` without exposing tenant data
