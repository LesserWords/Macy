# Proposal: M5-04 — MCP Workspace Discovery, Research, Scoring & Gate 1 Tools

**Plan Item:** M5-04 ([docs/plans/M5.md](../../../docs/plans/M5.md)) · **Features:** F01, F04, F07, F09, F10, F21 · **ADRs:** ADR-003, ADR-004, ADR-009, ADR-013, ADR-014, ADR-018, ADR-019, ADR-027

## Why

With the MCP server core, auth, and `services.py` boundary in place (`M5-03`), editors and autonomous assistants need to inspect workspace health, trigger/collect grounded research documents, review rubric-scored weekly topics, and record Gate 1 approvals without duplicating SQL or relaxing campaign-creator permissions.

Crucially, Macy stores Gate 1 awaiting opportunities in status `'scored'` (verified in `services._execute_decide_topic`), not `'pending'`, and enforces both workspace membership (`require_member`) and campaign creator ownership (`_validate_topic_approver` + `articles_per_week` weekly limit).

## What Changes

- **Service Layer Read/Trigger Helpers (`engine/services.py`):** Add clean service wrappers (`list_topics_for_approval`, workspace-scoped research execution/document query helpers) keeping SQL inside `engine/db.py` and logic in `engine/services.py`.
- **Research & Gate 1 Tool Module (`engine/mcp/tools_research.py`):** Register and implement:
  - `macy_list_workspaces`: List workspaces accessible to the resolved actor with slugs, names, campaign locales (`pt-BR`/`en-US`), and spend caps.
  - `macy_get_workspace_status`: Return monthly spend balance vs cap, source readiness counts, active/latest run summary, and counts of Gate 1 (`'scored'`) and Gate 2 (`'pass'`) items awaiting decision.
  - `macy_research_topic`: Trigger or collect workspace research via feeds/search with canonical URLs, verified/unverified `published_at` status, and extracted passages.
  - `macy_score_topics`: Execute/return ranked weekly topics with criterion sub-scores and code-computed totals (`ADR-004`).
  - `macy_list_topics_awaiting_approval`: Return opportunities currently in status **`'scored'`** awaiting Gate 1 review.
  - `macy_decide_topic`: Approve (`"approved"`) or reject (`"rejected"`) a `'scored'` opportunity via `services.decide_topic`, enforcing `_validate_topic_approver` (`owner` + `campaign.created_by`), weekly `articles_per_week` caps, concurrent update locking, and `topic.decided` audit events with `actor_user_id`.
- **Dual Mode Parity:** Wire identical tool schemas and contracts against both real Postgres services and `--mock` general demo store (`M5-03`).

## Capabilities

### New Capabilities
- None (extends `mcp-server` introduced in `M5-03`).

### Modified Capabilities
- `mcp-server`: Add discovery, status, research, scoring, and Gate 1 (`'scored'`) approval tools with multi-tenant and campaign-creator enforcement.

## Out of Scope

- MCP foundation, transport, and Bearer auth implementation (`M5-03` prerequisite).
- Briefing, article drafting, runtime claim verification, Gate 2 draft review, publishing, spend cap preflight, resources, and prompts — deferred to `M5-05`.
- Compose/Caddy deployment, smoke checks, client docs, and E2E suites — deferred to `M5-06`.

## Impact

- **Engine:** `engine/services.py`, `engine/mcp/tools_research.py`, `engine/mcp/server.py`
- **Tests:** `tests/test_mcp_research_gate1.py`, `tests/test_tenant_scoping.py`
