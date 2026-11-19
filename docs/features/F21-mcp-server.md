# F21 — Model Context Protocol (MCP) Server

**Status:** Done (`M5-03`–`M5-08`, `M6-01`–`M6-03`, `M7-01` `--stateless` & capability gating delivered) · **Milestone:** M5–M7 · **Plan items:** M5-03..M5-08, M6-01..M6-03, M7-01 · **ADRs:** 002, 009, 012, 013, 014, 016, 018, 019, 020, 023, 025, 026, 027, 028, 029 · **OpenSpec capability:** `mcp-server`

## User story
As an editor or operator working in an AI coding/assistant client (Antigravity, Claude Desktop, Claude Code, Cursor), I want to inspect workspaces, trigger or ingest external research, execute Gate 1/2 approvals cleanly within my authorization and spend limits, import existing `.md`/PDF or agent-written drafts with configurable format sizes (`blog-short`, `blog`, `blog-long`), verify grounded claims and compliance rules, read workspace context resources, and deploy the MCP endpoint safely alongside the production stack.

## Scope
- In:
  - Dual-transport `FastMCP` server (`stdio` for local IDEs and `streamable-http` behind Caddy at `/mcp*`);
  - Fail-closed Bearer authentication for HTTP transport and 3-tier `stdio` caller identity resolution blocking fake identities and `--mock` in `staging`/`production`/`prod`;
  - Strict `engine.mcp` -> `engine.services` / `engine.contracts` architecture boundary enforced by `importlinter`;
  - Full lifecycle & Agent-Driven Mode tools (`macy_list_workspaces`, `macy_get_workspace_status`, `macy_research_topic`, `macy_ingest_research_documents`, `macy_score_topics`, `macy_list_topics_awaiting_approval`, `macy_decide_topic`, `macy_get_editorial_brief`, `macy_generate_draft`, `macy_import_draft`, `macy_verify_claims`, `macy_evaluate_draft`, `macy_list_drafts_awaiting_approval`, `macy_decide_draft`, `macy_publish_draft`);
  - Format size selection (`format_slug`: `blog-short`, `blog`, `blog-long`) and deterministic word-count enforcement (`check_word_count`);
  - Runtime claim verification helper in `engine/quality/claims.py` (no runtime dependency on `evals/`);
  - Spend cap preflight (`BUDGET_CAP_EXCEEDED`), `usage_ledger` recording, and Sentry `mcp_tool` tagging/scrubbing;
  - MCP resources (`macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, `macy://drafts/{id}/claims`) and prompt (`macy_editorial_review`);
  - Explicit non-production `--mock` general demo mode (`acme-demo`);
  - `macy mcp` and `macy import-draft` CLI, Docker Compose (`compose*.yaml`), `Caddyfile`, `scripts/deploy.sh`, `scripts/post_deploy_smoke.py`, workflows (`deploy.yml`, `e2e-live.yml`), `docs/MCP.md`, and `docs/runbook.md`.
- Out:
  - Per-user OAuth 2.1 Dynamic Client Registration over public internet (deferred per ADR-027 revisit trigger);
  - Schema provisioning or destructive DB mutations (workspace/integration creation stays in UI/CLI);
  - Bypassing Gate 1/2 owner/campaign-creator checks or staging `mode="draft"` safeguards.

## Acceptance criteria
- **AC1 (Stdio Framing & Mock Guard):** WHEN the MCP server runs on `stdio`, THEN JSON-RPC 2.0 `initialize` advertises `tools`, `resources`, and `prompts` without non-protocol output on stdout; `--mock` initializes `acme-demo` in dev/test and fails fast in `staging`/`production`/`prod` or when omitted with an unreachable DB.
- **AC2 (Fail-Closed Auth & Boundaries):** WHEN an HTTP `/mcp` request lacks a valid `MCP_AUTH_TOKEN` Bearer token, THEN HTTP `401` is returned; non-members or cross-tenant calls return `PERMISSION_DENIED`; `MACY_TEST_USER_EMAIL` is rejected in prod; and `engine.mcp` cannot import `engine.db`.
- **AC3 (Discovery & Health Tools):** WHEN `macy_list_workspaces` or `macy_get_workspace_status` executes, THEN accessible workspaces, locales (`pt-BR`/`en-US`), spend balance vs cap, source health, and Gate 1 (`scored`) / Gate 2 (`pass`) queue counts are returned.
- **AC4 (Research, Scoring & Gate 1):** WHEN `macy_research_topic`, `macy_score_topics`, `macy_list_topics_awaiting_approval`, or `macy_decide_topic` (`"approved"`/`"rejected"`) executes, THEN only `'scored'` topics are listed/decided, `_validate_topic_approver` and `articles_per_week` caps are enforced, and `topic.decided` records `actor_user_id`.
- **AC5 (Briefing, Drafting & Runtime Claim Check):** WHEN `macy_get_editorial_brief`, `macy_generate_draft`, or `macy_verify_claims` executes, THEN grounded briefs/drafts persist FK-backed `draft_claims` and runtime claim verification (`engine/quality/claims.py`) returns `supported`/`refuted`/`unverifiable` verdicts, confidence, and evidence quotes without importing `evals/`.
- **AC6 (Evaluation, Gate 2 & Publishing):** WHEN `macy_evaluate_draft`, `macy_list_drafts_awaiting_approval`, `macy_decide_draft` (`"approved"`/`"declined"`), or `macy_publish_draft` executes, THEN Gate 2 surfaces `'pass'` drafts, `_validate_topic_approver` governs both `decide_draft` and `publish_draft`, and `APP_ENV=staging` forces `mode="draft"`.
- **AC7 (Spend Control & Telemetry):** WHEN generative tools exceed monthly or run spend caps, THEN preflight halts before upstream LLM/API calls with `BUDGET_CAP_EXCEEDED`, while passing calls record USD in `usage_ledger` and exceptions tag Sentry with `mcp_tool`, `workspace_id`, and `run_id` with tokens scrubbed.
- **AC8 (Resources & Prompts):** WHEN clients read `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, `macy://drafts/{id}/claims`, or render `macy_editorial_review`, THEN member authorization is verified cleanly.
- **AC9 (Deployment & Smoke):** WHEN deployed via Compose + `Caddyfile` + `scripts/deploy.sh`, THEN `mcp` publishes no raw port in `compose.prod.yaml`, isolates secrets from `x-app-env`, passes Docker healthcheck `/healthz`, validates `MCP_AUTH_TOKEN` in preflight, rolls back cleanly, and passes `post_deploy_smoke.py --check-mcp` (`401` unauthenticated, `200` authenticated).
- **AC10 (Delivery Quality Gates):** WHEN all PRs (`M5-03`–`M5-07`) run `scripts/check.sh`, CI, and E2E suites, THEN branch coverage `>= 90%`, diff coverage `= 100%`, `complexipy <= 15`, `jscpd = 0%`, `lint-imports` = 0, `openspec validate --all --strict` = 0, and offline/live MCP E2E journeys pass.
- **AC11 (Agent-Driven Research Ingestion, External Draft Import & Format Sizing — Agent-Driven Mode):** WHEN an authorized actor calls `macy_ingest_research_documents` or `macy_import_draft` (or CLI `macy import-draft <file.md|.pdf>`), THEN external source documents are persisted in `documents`/`research_records`, ready-made or agent-written drafts (with or without an existing `brief_id`) are persisted with FK-anchored `draft_claims` at revision `0..2`, `format_slug` (`blog-short`, `blog`, `blog-long`) enforces deterministic `check_word_count` bounds during `macy_evaluate_draft`, and `deterministic_only=True` executes compliance checks with zero LLM cost.

## Data
Uses existing Schema v1 tables (`users`, `workspaces`, `memberships`, `campaigns`, `runs`, `documents`, `research_records`, `opportunities`, `briefs`, `drafts`, `draft_claims`, `quality_reports`, `publications`, `usage_ledger`, `events`) with no new SQL migrations required.

## Interfaces
- `engine/mcp/{__init__,auth,errors,mock_store,prompts,resources,server,tools_research,tools_editorial}.py`
- `engine/quality/claims.py`, `engine/services.py`, `engine/cli.py` (`macy mcp`), `engine/observability.py`
- `pyproject.toml`, `compose*.yaml`, `Caddyfile`, `.env.*.example`, `scripts/deploy.sh`, `scripts/post_deploy_smoke.py`, `.github/workflows/{deploy,e2e-live}.yml`, `docs/MCP.md`, `docs/runbook.md`

## Tests
| Seam | Test | E2E journey |
|---|---|---|
| Protocol, Framing, Errors & Telemetry | `tests/test_mcp_server.py` | — |
| Auth & Fail-Closed Production Guards | `tests/test_mcp_auth.py`, `tests/test_cli.py` | — |
| Property Invariants (ADR-025) | `tests/test_mcp_properties.py` | — |
| Multi-Tenant & Core Lifecycle (Real Postgres) | `tests/test_tenant_scoping.py`, `tests/test_mcp_research_gate1.py`, `tests/test_mcp_editorial_gate2.py`, `tests/test_mcp_spend.py` | — |
| Infra, Compose, Deploy & Smoke | `tests/test_compose_and_docker.py`, `tests/test_deploy_script.py`, `tests/test_post_deploy_smoke.py` | `scripts/post_deploy_smoke.py --check-mcp` |
| Offline & Live E2E | `tests/test_journey_mcp_e2e.py` | `tests/test_live_e2e_mcp.py` (`@pytest.mark.live_e2e`) |

## Dependencies
`F01`, `F02`, `F04`, `F07`, `F09`–`F17`, `F20`, and `ADR-026` (`M5-01` & `BM-6` baseline).
