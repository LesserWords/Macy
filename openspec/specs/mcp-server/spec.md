# mcp-server Specification

## Purpose
Exposes an authenticated, tenant-isolated Model Context Protocol (`MCPServer`) interface over `stdio` and `streamable-http` (`/mcp`, `/healthz`) delegating strictly through `engine.services` (`ADR-027`, `F21`).

## Requirements

### Requirement: Dual-transport MCP initialization and stdio framing protection
The system SHALL expose an MCP server over local `stdio` and containerized `streamable-http` (`/mcp`, with unauthenticated `/healthz`) adhering to Model Context Protocol JSON-RPC 2.0, while preventing logs or stdout prints from corrupting `stdio` protocol frames (`ADR-027`).

#### Scenario: Clean stdio handshake advertises protocol capabilities
- **WHEN** an MCP client connects and issues `initialize` over `stdio`
- **THEN** the server SHALL respond on stdout with valid JSON-RPC 2.0 protocol metadata advertising `tools`, `resources`, and `prompts` capabilities, while routing Python logging and stray prints to `stderr`

#### Scenario: Streamable HTTP health endpoint responds cleanly
- **WHEN** HTTP `GET /healthz` is requested on the HTTP MCP server
- **THEN** it SHALL return HTTP `200` JSON status `ok` without requiring Bearer credentials

### Requirement: Non-production mock mode with strict production and outage refusal
The system SHALL provide an explicit `--mock` (or `MACY_MCP_STANDALONE=1`) offline demo workspace (`acme-demo`) for local dev/test use ONLY, and MUST refuse `--mock` in staging/production or silently degrading on Postgres failure (`ADR-027`, `F01 AC6`).

#### Scenario: Explicit mock mode boots demo general store in non-production
- **WHEN** `content-engine mcp --mock` executes while `APP_ENV` is neither `staging`, `production`, nor `prod`
- **THEN** the server SHALL initialize an in-memory general store (`acme-demo`, `owner@example.com`) backed by test doubles and log a diagnostic notice to `stderr`

#### Scenario: Mock mode is refused in staging and production
- **WHEN** `--mock` or `MACY_MCP_STANDALONE=1` is supplied while `APP_ENV` or `ENV` is `staging`, `production`, or `prod`
- **THEN** the CLI and server SHALL fail fast with a non-zero exit code before accepting MCP traffic

#### Scenario: Database outage fails cleanly instead of falling back to mock
- **WHEN** the server starts without `--mock` and PostgreSQL is unreachable or misconfigured
- **THEN** the server SHALL exit non-zero cleanly and SHALL NOT silently substitute demo data

### Requirement: Fail-closed caller authentication and identity pinning
The system SHALL enforce fail-closed Bearer token authentication on `streamable-http` and tiered caller identity resolution on `stdio`, blocking fake identities in production and preventing caller impersonation (`ADR-013`, `ADR-027`, `F01 AC6`).

#### Scenario: Streamable HTTP rejects missing, empty, or wrong Bearer tokens
- **WHEN** an HTTP client accesses `/mcp` without a valid `Authorization: Bearer <token>` matching configured `MCP_AUTH_TOKEN` (verified in constant time)
- **THEN** the server SHALL fail closed with HTTP `401` and invoke zero MCP tool/resource handlers

#### Scenario: HTTP transport startup fails closed when auth config is incomplete
- **WHEN** the MCP server starts in `streamable-http` mode while `MCP_AUTH_TOKEN` or `MCP_ACTOR_EMAIL` is unset or placeholder (`CHANGE_ME`) in `staging`/`production`/`prod`
- **THEN** server startup SHALL abort non-zero cleanly before binding traffic

#### Scenario: Pinned transport identity rejects spoofed tool arguments
- **WHEN** an MCP call arrives over authenticated `streamable-http` pinned to `MCP_ACTOR_EMAIL` and supplies a conflicting `actor_email` or `actor_user_id` argument
- **THEN** the server SHALL reject the spoofed override with `PERMISSION_DENIED` rather than switching caller identity

#### Scenario: Stdio resolves identity cleanly without silent owner privilege escalation
- **WHEN** an MCP tool is invoked over `stdio`
- **THEN** identity resolves from `actor_email` -> `MACY_USER_EMAIL` -> `MACY_ACTOR_USER_ID` -> `MACY_TEST_USER_EMAIL` (rejected with `FakeIdentityForbiddenError` in `staging`/`production`/`prod`), falling back to the workspace owner ONLY when `--allow-owner-fallback` is explicitly set in non-production environments

### Requirement: Workspace member boundary, CLI entrypoint, and scrubbed telemetry
Every workspace-scoped MCP operation SHALL enforce `require_member`, operate strictly through `engine.services` and `engine.contracts`, emit Sentry telemetry tagged with `mcp_tool` and scrubbed of secrets, and expose `content-engine mcp` in CLI (`ADR-014`, `ADR-023`, `ADR-027`).

#### Scenario: Non-member caller is refused with structured permission error
- **WHEN** a resolved authenticated user invokes a workspace-scoped MCP handler for a workspace where they hold no membership
- **THEN** the server SHALL return structured MCP error `PERMISSION_DENIED` without mutating state

#### Scenario: MCP failures report scrubbed Sentry telemetry
- **WHEN** an unhandled error occurs inside an MCP tool execution while `SENTRY_DSN` is set
- **THEN** Sentry receives the exception tagged with `mcp_tool`, `workspace_id`, and optional `run_id` with `MCP_AUTH_TOKEN` and `Bearer` strings redacted

#### Scenario: CLI check flag validates startup readiness without hanging
- **WHEN** `content-engine mcp --check` executes (with `--mock` in dev, or live DB config)
- **THEN** it validates dependencies, configuration, and identity/DB connectivity and exits `0` cleanly

### Requirement: Workspace discovery and operational status MCP tools
The system SHALL expose `macy_list_workspaces` and `macy_get_workspace_status` MCP tools delegating strictly to `engine.services`, respecting tenant boundaries and returning campaign locale and budget health (`F01`, `F17`, `F21 AC3`, `ADR-027`).

#### Scenario: Listing accessible workspaces for resolved actor
- **WHEN** `macy_list_workspaces` is invoked by an authenticated actor
- **THEN** the server SHALL return only workspaces where the actor holds active membership, including workspace ID, slug, name, campaign locales (`pt-BR` or `en-US`), and configured monthly/run spend caps

#### Scenario: Querying workspace status and approval queues
- **WHEN** `macy_get_workspace_status` is called with `workspace_id` by an authorized member
- **THEN** the server SHALL return monthly spend vs `monthly_cap_usd`, monitored source counts, configured integration status, Gate 1 (`'scored'`) opportunity count, and Gate 2 (`'pass'`) draft count

### Requirement: Research collection, topic scoring, and Gate 1 decision MCP tools
The system SHALL expose `macy_research_topic`, `macy_score_topics`, `macy_list_topics_awaiting_approval`, and `macy_decide_topic` MCP tools enforcing `'scored'` queue semantics, campaign creator Gate 1 authority, weekly quotas, and event auditing (`F07`, `F09`, `F10`, `F21 AC4`, `ADR-013`, `ADR-027`).

#### Scenario: Collecting grounded research documents for a workspace
- **WHEN** `macy_research_topic` is called with valid `workspace_id` and topic/query parameters
- **THEN** the server SHALL invoke research services and return extracted documents containing canonical URLs, titles, explicit `published_at` / `date_status`, and extracted text passages

#### Scenario: Scoring opportunities with rubric breakdown
- **WHEN** `macy_score_topics` is called with valid `workspace_id`
- **THEN** the server SHALL return ranked topics containing weighted rubric criterion sub-scores and code-computed totals

#### Scenario: Listing Gate 1 opportunities awaiting approval in scored status
- **WHEN** `macy_list_topics_awaiting_approval` is called with valid `workspace_id`
- **THEN** the server SHALL return only opportunities currently in status `'scored'` for that workspace (never returning cross-tenant opportunities or `'pending'` placeholders)

#### Scenario: Campaign creator approves or rejects a scored topic at Gate 1
- **WHEN** `macy_decide_topic` is called with `workspace_id`, `opportunity_id`, and `decision` (`"approved"` or `"rejected"`) by an owner who matches `campaign.created_by`
- **THEN** the service transitions `'scored'` atomically, enforces the `articles_per_week` cap on approvals, and emits a `topic.decided` event stamped with `actor_user_id`

#### Scenario: Non-creator owner or cross-tenant caller refused at Gate 1
- **WHEN** `macy_decide_topic` is called by a non-member, a `member` role user, or an owner who is not `campaign.created_by`
- **THEN** the server SHALL return structured MCP error `PERMISSION_DENIED` and leave the opportunity status unchanged

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

### Requirement: Tenant-scoped MCP resources and editorial review prompt
The system SHALL expose `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, and `macy://drafts/{id}/claims` resources and the `macy_editorial_review` prompt enforcing `require_member` (`F21 AC8`, `ADR-009`, `ADR-027`).

#### Scenario: Authorized member reads MCP resources and prompt
- **WHEN** an authorized member reads `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, or `macy://drafts/{id}/claims`, or renders `macy_editorial_review`
- **THEN** the server SHALL return current tenant-scoped brand rules, Gate 1 candidates, or claim provenance/evaluation details

#### Scenario: Cross-tenant resource read is refused
- **WHEN** an authenticated caller reads a `macy://` URI belonging to another workspace
- **THEN** the server SHALL raise `PERMISSION_DENIED` without exposing tenant data

### Requirement: MCP client documentation and containerized E2E verification
The system SHALL document MCP client configurations in `docs/MCP.md` and verify containerized `streamable-http` MCP execution end-to-end both offline in Compose (`test_journey_mcp_e2e.py`) and against live staging (`test_live_e2e_mcp.py`, `@pytest.mark.live_e2e`) (`F21 AC9, AC10`, `ADR-018`, `ADR-027`).

#### Scenario: Client configuration guide documents stdio and HTTPS Bearer endpoints
- **WHEN** a developer opens `docs/MCP.md`
- **THEN** it provides accurate setup snippets for Antigravity, Claude Desktop, Claude Code, and Cursor covering both local `stdio` (`--mock` and Postgres) and HTTPS `streamable-http` (`/mcp` with `Authorization: Bearer`)

#### Scenario: Offline E2E proves containerized MCP lifecycle against Postgres and webhook receiver
- **WHEN** `tests/test_journey_mcp_e2e.py` executes against the Compose E2E stack (`compose.yaml` + `compose.e2e.yaml` with `mcp` profile active)
- **THEN** authenticated MCP HTTP calls traverse discovery -> Gate 1 (`'scored'` -> `'approved'`) -> drafting -> claim verification -> Gate 2 (`'pass'` -> `'approved'`) -> publish (`http_webhook` -> `webhook-receiver`) without touching `evals/`

#### Scenario: Nightly live E2E verifies staging MCP fail-closed auth and read-only session
- **WHEN** `tests/test_live_e2e_mcp.py` runs under `e2e-live.yml` against `STAGING_APP_URL`
- **THEN** unauthenticated `POST /mcp` returns HTTP `401` and authenticated `initialize` + `macy_list_workspaces` succeeds within budget cap

### Requirement: Agent-driven research ingestion, external draft import, and Strands Agents MCP runner (Agent-Driven Mode)
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

#### Scenario: Headless Strands Agents client executes Agent-Driven Mode compliance loop over MCP
- **WHEN** `scripts/compliance_agent.py` connects to `content-engine mcp` via `strands.tools.mcp.MCPClient`
- **THEN** the agent discovers the `macy_*` toolset, ingests research sources, imports and iteratively refines the draft against `macy_evaluate_draft` findings (`revision 0..2`), and halts at Gate 2 (`status = 'pass'`) without importing any agent framework inside `engine/`

### Requirement: Capability-scoped MCP tool exposure and stateless mode
The MCP server (`engine/mcp/`) SHALL filter advertised tools according to the workspace's `enabled_capabilities` (returning `CAPABILITY_DISABLED` when a disabled tool is invoked directly) and SHALL support `--stateless` over `stdio` for `core-compliance` and `seo-audit` tools without PostgreSQL (`ADR-029`, `F22 AC2, AC3`).

#### Scenario: Disabled capability tool returns CAPABILITY_DISABLED
- **WHEN** a client invokes `macy_generate_draft` on a workspace where `editorial-pipeline` is disabled
- **THEN** the MCP server refuses execution with structured error `CAPABILITY_DISABLED`

#### Scenario: Stateless stdio MCP runs audit and compliance tools with zero database
- **WHEN** `content-engine mcp --stateless` handles `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, or `macy_import_draft`
- **THEN** each tool executes deterministically via in-memory contracts and returns structured JSON without opening a database connection
