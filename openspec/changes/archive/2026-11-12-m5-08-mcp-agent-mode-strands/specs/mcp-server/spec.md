# mcp-server Delta Specification

## MODIFIED Requirements

### Requirement: Editorial briefing, grounded drafting, and runtime claim verification MCP tools
The system SHALL expose `macy_get_editorial_brief`, `macy_generate_draft`, and `macy_verify_claims` MCP tools delegating to `engine/services.py`, supporting explicit `format_slug` (`blog-short`, `blog`, `blog-long`) and `funnel_stage` (`tofu`, `mofu`, `bofu`) selection, persisting FK-backed `draft_claims`, and running claim verification inside `engine/quality/claims.py` without importing `evals/` (`F12`, `F13`, `F14`, `F21 AC5`, `ADR-026`, `ADR-027`).

#### Scenario: Generating or retrieving an SEO-grounded editorial brief
- **WHEN** `macy_get_editorial_brief` is invoked with `workspace_id`, an approved `opportunity_id`, and optional `format_slug` (`blog-short`, `blog`, or `blog-long`)
- **THEN** the server SHALL return or create the structured editorial brief pinned to the requested format pack, including target audience, primary/secondary keywords, angle, outline, and cited source documents

#### Scenario: Generating a source-grounded article draft
- **WHEN** `macy_generate_draft` is invoked with `workspace_id` and `brief_id`
- **THEN** the server SHALL persist and return the draft Markdown alongside `draft_claims` rows anchored by foreign keys to verified workspace `documents`

#### Scenario: Verifying factual claims against source passages in runtime containers
- **WHEN** `macy_verify_claims` is invoked with claim statements and candidate source passages (or a `draft_id`)
- **THEN** the runtime claim checker (`engine/quality/claims.py`, importing zero modules from `evals/`) SHALL return each claim's verdict (`supported`, `refuted`, or `unverifiable`), confidence score, and verbatim evidence passage

### Requirement: Quality evaluation, Gate 2 draft review, and publishing MCP tools
The system SHALL expose `macy_evaluate_draft` (supporting `deterministic_only: bool = False`), `macy_list_drafts_awaiting_approval`, `macy_decide_draft`, and `macy_publish_draft` MCP tools enforcing Gate 2 status transitions, campaign creator authorization across Gate 2 and publish, and staging draft-only mode (`F14`, `F15`, `F16`, `F21 AC6`, `ADR-013`, `ADR-021`, `ADR-027`).

#### Scenario: Evaluating an article draft deterministically and by rubric
- **WHEN** `macy_evaluate_draft` is called with `workspace_id` and `draft_id`
- **THEN** the server SHALL execute deterministic checks (including `check_word_count` against the brief's `FormatPack`) prior to isolated LLM rubric scoring and return verdict, quality score, findings, and revision routing state

#### Scenario: Evaluating a draft in deterministic-only compliance mode with zero LLM spend
- **WHEN** `macy_evaluate_draft` is called with `workspace_id`, `draft_id`, and `deterministic_only=True`
- **THEN** the server SHALL execute all deterministic compliance checks (`prohibited_phrases`, `fact_sources`, `cta_intensity`, `language_match`, `slug_format`, `meta_length`, `primary_keyword_placement`, `word_count`) and persist the `QualityReport` without invoking any LLM call

#### Scenario: Listing passing drafts awaiting Gate 2 review
- **WHEN** `macy_list_drafts_awaiting_approval` is called with `workspace_id`
- **THEN** the server SHALL return drafts currently in status `'pass'` awaiting Gate 2 decision

#### Scenario: Approving or declining a draft at Gate 2 with normalized values
- **WHEN** `macy_decide_draft` is called with `workspace_id`, `draft_id`, and `decision` (`"approved"` or `"declined"`) by the campaign creator owner
- **THEN** `services.decide_draft` transitions `'pass'` atomically and records `draft.decided` with `actor_user_id`, whereas non-creator owners or invalid decision strings fail cleanly

#### Scenario: Publishing an approved draft enforces creator authority and staging draft mode
- **WHEN** `macy_publish_draft` is called with an `'approved'` `draft_id`
- **THEN** the server SHALL enforce campaign-creator ownership (`_validate_topic_approver`), dispatch the configured `PublisherPort`, and force `mode="draft"` whenever `APP_ENV=staging`

## ADDED Requirements

### Requirement: Agent-driven research ingestion, external draft import, and Strands Agents MCP runner (Modo A)
The system SHALL expose `macy_ingest_research_documents` and `macy_import_draft` MCP tools and an external `strands-agents` MCP client runner (`scripts/compliance_agent.py`) so external AI agents can register research sources and import/revise `.md` or `.pdf` drafts (`revision 0..2`) while keeping `engine/` agent-free (`F21 AC11`, `ADR-002`, `ADR-027`).

#### Scenario: Ingesting external agent-gathered research documents for FK claim grounding
- **WHEN** `macy_ingest_research_documents` is called with `workspace_id` and a list of external source items (`url`, `title`, `extracted_text`, optional `published_at`, `summary`, `facts`)
- **THEN** the server validates each URL via `safe_http.validate_safe_url`, upserts canonical `documents` and `research_records` rows for the workspace, and returns the persisted `document_id` UUIDs

#### Scenario: Importing a standalone Markdown draft without an existing brief
- **WHEN** `macy_import_draft` is called with `workspace_id`, `body_md` (or local `file_path` on `stdio`), `format_slug` (`blog-short`, `blog`, or `blog-long`), and `brief_id=None`
- **THEN** the server bootstraps an ad-hoc `run`, approved `opportunity`, and `EditorialBrief` for the requested format and funnel stage, persists the draft at `revision = 0` with FK-linked `draft_claims`, and returns `draft_id` and `brief_id` ready for `macy_evaluate_draft`

#### Scenario: Importing an agent-revised draft against an existing brief
- **WHEN** `macy_import_draft` is called with `workspace_id`, `brief_id`, and revised `body_md` / `claims` after an evaluation returned `REVISION`
- **THEN** the server persists the draft as `revision + 1` (up to `revision = 2`) under `brief_id` and updates `draft_claims` atomically

#### Scenario: Headless Strands Agents client executes Modo A compliance loop over MCP
- **WHEN** `scripts/compliance_agent.py` connects to `content-engine mcp` via `strands.tools.mcp.MCPClient`
- **THEN** the agent discovers the `macy_*` toolset, ingests research sources, imports and iteratively refines the draft against `macy_evaluate_draft` findings (`revision 0..2`), and halts at Gate 2 (`status = 'pass'`) without importing any agent framework inside `engine/`
