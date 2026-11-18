# Design: M5-04 — MCP Workspace Discovery, Research, Scoring & Gate 1 Tools

**ADR:** [`docs/adr/ADR-027-mcp-server.md`](../../../docs/adr/ADR-027-mcp-server.md) · **Plan Item:** `M5-04` (`<= ~400` non-test LOC)

## Real Service Mapping (Correcting Prior Draft)

| MCP Tool | Real Service Call (`engine/services.py`) | Status & Auth Invariant |
|---|---|---|
| `macy_list_workspaces` | `services.list_accessible_workspaces(conn, user_id=actor_id)` | Scoped to actor's `memberships` rows only |
| `macy_get_workspace_status` | `services.get_workspace_status(conn, workspace_id=ws_id, user_id=actor_id)` | `require_member`; aggregates monthly spend, sources, integrations, `'scored'` & `'pass'` counts |
| `macy_research_topic` | `services.run_mcp_research(conn, workspace_id=ws_id, user_id=actor_id, topic=...)` -> delegates to `run_now` / `run_stage(..., stage="research")` | `require_member`; enforces real/mock adapter boundary per env |
| `macy_score_topics` | `services.score_run(...)` + `services.report(...)` | `require_member`; returns ranked `'scored'` stories & sub-scores (`ADR-004`) |
| `macy_list_topics_awaiting_approval` | `services.list_topics_for_approval(conn, workspace_id=ws_id, user_id=actor_id, run_id=...)` | `require_member`; queries `opportunities.status = 'scored'` |
| `macy_decide_topic` | `services.decide_topic(conn, workspace_id=ws_id, opportunity_id=opp_id, decision=..., user_id=actor_id, note=..., auto_brief=False)` | `_validate_topic_approver` (`owner` + `campaign.created_by`), `FOR UPDATE` run lock, `articles_per_week` cap |

## Service Layer Helper Additions (`engine/services.py` & `engine/db.py`)

- Add `list_topics_for_approval(conn=None, *, workspace_id, user_id, run_id=None)`: validates `require_member`, defaults to latest scored/active run for `workspace_id` when `run_id` is omitted, and returns `opportunities` where `status = 'scored'` (filtering exclusively by `workspace_id`).
- Add `run_mcp_research(conn=None, *, workspace_id, user_id, query=None, run_id=None, offline=False)`: validates `require_member`, ensures or reuses an active research run, runs research collection, and returns extracted documents with `date_status` (`verified`/`unverified`) cleanly.
- Tool implementations live in `engine/mcp/tools_research.py` importing **only** `engine.services`, `engine.contracts`, and `engine.mcp.{auth,errors,mock_store}`.

## Mock Mode Parity

- `MockStore` (`M5-03`) supports reading `acme-demo` workspaces, running `FakeFeedSource`/`FakeNewsSearch` extraction on `macy_research_topic`, deterministic rubric scores (`status='scored'`) on `macy_score_topics`, and stateful Gate 1 decisions (`'approved'`/`'rejected'` enforcing weekly quota `articles_per_week` and non-creator rejection tests using a second seeded non-creator user).
