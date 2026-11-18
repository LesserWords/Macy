# Tasks: M5-04 — MCP Workspace Discovery, Research, Scoring & Gate 1 Tools

**PR:** `M5-04` (branch `m5-04-mcp-discovery-gate1`, `<= ~400` non-test LOC per `ADR-019`) · **Plan:** [docs/plans/M5.md](../../../docs/plans/M5.md)

## 1. Service Read/Trigger Wrappers & Failing Tests

- [x] 1.1 Write failing unit & Postgres integration tests in `tests/test_mcp_research_gate1.py` and `tests/test_tenant_scoping.py` for `macy_list_workspaces`, `macy_get_workspace_status`, `macy_research_topic`, `macy_score_topics`, `macy_list_topics_awaiting_approval` (status `'scored'`), and `macy_decide_topic` (`"approved"`/`"rejected"`, `_validate_topic_approver`, `articles_per_week` limit, cross-workspace refusal) — verify: `uv run pytest tests/test_mcp_research_gate1.py tests/test_tenant_scoping.py -k mcp --no-cov` fails before implementation
- [x] 1.2 Add `list_topics_for_approval` and `run_mcp_research` helpers in `engine/services.py` (backed by existing `engine/db.py` & pipeline functions, enforcing `require_member` and `'scored'` filter) — verify: `uv run mypy && uv run lint-imports` exit 0

## 2. Discovery, Research, Scoring & Gate 1 MCP Tools

- [x] 2.1 Implement `macy_list_workspaces`, `macy_get_workspace_status`, `macy_research_topic`, and `macy_score_topics` in `engine/mcp/tools_research.py` supporting both Postgres services and `--mock` store (`M5-03`) — verify: `uv run pytest tests/test_mcp_research_gate1.py -k "workspaces or status or research or score" --no-cov` passes
- [x] 2.2 Implement `macy_list_topics_awaiting_approval` and `macy_decide_topic` in `engine/mcp/tools_research.py` and register all 6 tools in `engine/mcp/server.py`, verifying `actor_user_id` audit event persistence and concurrent/limit guards — verify: `uv run pytest tests/test_mcp_research_gate1.py tests/test_tenant_scoping.py --no-cov` passes

## 3. Full Quality & Delivery Checks

- [x] 3.1 Run complete `M5-04` delivery checks (`sh scripts/check.sh`) verifying `ruff`, `mypy --strict`, `complexipy <= 15`, `vulture`, `lint-imports`, `jscpd = 0%`, `pytest >= 90%` branch coverage, `diff-cover = 100%`, and `openspec validate --all --strict`, plus `<= ~400` non-test LOC diff size — verify: `sh scripts/check.sh` exits 0

## Scenario ↔ Test Mapping

| Spec Scenario | Test Assertion |
|---|---|
| Listing accessible workspaces for resolved actor | `tests/test_mcp_research_gate1.py::test_macy_list_workspaces_scoped_to_actor` |
| Querying workspace status and approval queues | `tests/test_mcp_research_gate1.py::test_macy_get_workspace_status_metrics` |
| Collecting grounded research documents | `tests/test_mcp_research_gate1.py::test_macy_research_topic_extracts_dates_and_urls` |
| Scoring opportunities with rubric breakdown | `tests/test_mcp_research_gate1.py::test_macy_score_topics_returns_subscores` |
| Listing Gate 1 opportunities in `'scored'` status | `tests/test_mcp_research_gate1.py::test_list_topics_awaiting_approval_queries_scored` |
| Campaign creator approves/rejects topic; non-creator refused | `tests/test_mcp_research_gate1.py::test_decide_topic_gate1_creator_and_weekly_cap`, `tests/test_tenant_scoping.py::test_mcp_gate1_cross_tenant_refused` |

## Workflow follow-up

- Squash-merge `m5-04-mcp-discovery-gate1` PR into `main` once CI checks pass.
- Archive `2026-11-11-m5-04-mcp-discovery-gate1` with `openspec archive`.
