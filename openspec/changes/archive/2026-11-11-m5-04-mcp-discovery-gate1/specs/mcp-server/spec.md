# mcp-server Delta Specification

## ADDED Requirements

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
