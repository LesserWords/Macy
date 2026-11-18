# ADR-027: Model Context Protocol (MCP) Server — Dual Transport, Services Boundary, and Fail-Closed Auth

**Status:** Proposed · **Date:** 2026-11-11 · **Deciders:** Product owner
**Related:** features F01, F02, F04, F07, F09–F17, F20, F21; ADR-002, ADR-006, ADR-009, ADR-010, ADR-012, ADR-013, ADR-014, ADR-016, ADR-018, ADR-019, ADR-020, ADR-023, ADR-026

## Context
AI clients and coding agents (Antigravity, Claude Desktop, Claude Code, Cursor) interact with external systems through the Model Context Protocol (MCP). Macy currently exposes its two-gate editorial workflow only through the Streamlit Admin UI (`app.py`) and CLI subcommands (`engine/cli.py`).

Exposing Macy as an MCP server without distorting its invariants imposes hard architectural constraints:
1. **Multi-tenant boundary (ADR-009, ADR-010):** Every operation must stay scoped to `workspace_id` and validate membership via `require_member`.
2. **Services boundary (ADR-014):** Frontends and adapters must import only `engine.services` and `engine.contracts`, never `engine.db` directly, enforced by `import-linter`.
3. **Human approval authority (ADR-013, F10, F15, F16):** Gate 1 (`decide_topic`), Gate 2 (`decide_draft`), and publishing (`publish_draft`) require an `owner` matching `campaign.created_by` and must emit `events` with `actor_user_id`.
4. **Fail-closed auth & non-production mock separation (F01 AC6, ADR-013):** `MACY_TEST_USER_EMAIL` is forbidden in production (`FakeIdentityForbiddenError`), and silent fallbacks to mock data or default owners on DB outage would mask failures or enable privilege confusion.
5. **Spend control (ADR-016, F17):** Every LLM or paid search call invoked over MCP must pass budget guardrails before execution and record exact cost in `usage_ledger`.
6. **Container & deployment parity (ADR-012, F20):** `Dockerfile` intentionally excludes `evals/`. Any claim-verification capability exposed over MCP in deployed containers must live in runtime `engine/`, deploy via the single image and Caddy HTTPS reverse proxy without exposing internal ports, and be verified by Compose config checks, `deploy.sh` preflight/rollback, and post-deploy smoke tests.

## Options considered
| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. FastMCP (`mcp>=1.2.0`) over `engine.services`, dual transport (`stdio` local + `streamable-http` behind Caddy) with fail-closed Bearer auth** (chosen) | Medium | Low (reuses existing Docker runtime image, Postgres pool, and Caddy) | Serves local IDEs (`stdio`) and VPS/CI E2E (`streamable-http`) cleanly under existing quality & deploy gates |
| B. Stdio-only CLI command (`content-engine mcp`) | Low | Very low | Cannot be deployed, healthchecked, or smoke-tested as a containerized service on staging/prod VPS |
| C. Separate FastAPI microservice + custom JSON-RPC server | High | Medium | Duplicates auth/observability plumbing, adds deployment drift, and bypasses FastMCP SDK protocol guarantees |

## Decision
Adopt **Option A**:

1. **Dual Transport & Stdio Framing:**
   - Implement `engine/mcp/` using the official Python SDK (`mcp>=1.2.0`, `FastMCP`).
   - Support `--transport stdio` (default for local AI clients) and `--transport streamable-http` (`--host 0.0.0.0 --port 8765`, internal Compose network only, proxied at `/mcp*` by Caddy). Expose an unauthenticated HTTP `/healthz` endpoint for Docker healthchecks.
   - In `stdio` mode, reroute Python `sys.stdout` and `logging` handlers to `sys.stderr` while keeping a dedicated handle to the true JSON-RPC stdout pipe so pipeline logs or third-party prints never corrupt protocol frames.

2. **Strict Services Layer Boundary (`ADR-014`):**
   - Add an `import-linter` forbidden contract in `pyproject.toml`: `engine.mcp` MUST NOT import `engine.db`.
   - All workspace discovery, status aggregation, Gate 1/2 queries, claim verification, spend preflight, and domain mutations go through `engine.services` and `engine.contracts`.

3. **Caller Authentication & Fail-Closed Production Guards:**
   - **Remote HTTP (`streamable-http`):** Fail closed. Require `MCP_AUTH_TOKEN` (minimum 32 chars in `staging`/`production`/`prod`) validated with `hmac.compare_digest` on `Authorization: Bearer <token>`, returning HTTP `401` on missing/invalid tokens. Bind HTTP callers to `MCP_ACTOR_EMAIL` (fail-closed startup error if unset in HTTP mode when auth is enabled). Tool arguments (`actor_email` / `actor_user_id`) CANNOT impersonate another user when transport identity is pinned by token config.
   - **Local `stdio`:** Resolve identity in order: explicit `actor_email` / `actor_user_id` -> `MACY_USER_EMAIL` -> `MACY_ACTOR_USER_ID` -> `MACY_TEST_USER_EMAIL` (explicitly blocked with `FakeIdentityForbiddenError` in `staging`/`production`/`prod`). Fallback to workspace owner is **disabled by default** and permitted only when `--allow-owner-fallback` is explicitly passed in non-production (`APP_ENV` not in `staging`/`production`/`prod`).
   - **Authorization:** Every workspace-scoped tool and `macy://` resource enforces `services.require_member`. Gate 1 (`macy_decide_topic`), Gate 2 (`macy_decide_draft`), and Publish (`macy_publish_draft`) enforce `_validate_topic_approver` (`owner` + `campaign.created_by`), and staging forces `mode="draft"` per `_resolve_publish_mode`.

4. **Explicit Non-Production Mock Mode (`--mock`):**
   - `--mock` (or `MACY_MCP_STANDALONE=1`) boots an isolated seeded general store (`acme-demo`, `owner@example.com`, using existing test doubles `FakeLLM`, `FakeFeedSource`, `FakeKeywordProvider`, `FakePublisher`) strictly for local client testing and offline unit tests.
   - Running `--mock` or `MACY_MCP_STANDALONE=1` in `staging`/`production`/`prod` MUST fail fast on startup.
   - Normal DB mode MUST NEVER silently fall back to `--mock` if Postgres is unreachable.

5. **Runtime Claim Verification Outside `evals/`:**
   - Implement reusable runtime claim verification in `engine/quality/claims.py` (deterministic number/quote matching + injected `LLMPort` or `safe_http.safe_request` Jev judge per ADR-026) wrapped by `services.verify_claims`, keeping `evals/` excluded from `Dockerfile`.

6. **Spend Preflight, Observability & Scrubbing (`ADR-016`, `ADR-023`):**
   - Generative MCP tools call `services.preflight_mcp_spend` before running LLM/API calls, log exact USD into `usage_ledger`, and return structured tool error code `BUDGET_CAP_EXCEEDED` on cap breach.
   - Extend `engine/observability.py` to tag Sentry scopes with `mcp_tool` alongside `workspace_id` and `run_id`, and ensure `MCP_AUTH_TOKEN` and `Bearer` headers are scrubbed prior to telemetry emission.

7. **Container, Compose & Release Integration (`ADR-012`, `F20`):**
   - Add an opt-in `mcp` Compose service (`profiles: ["mcp"]`) in `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml` reusing the single runtime image. Keep `MCP_AUTH_TOKEN` out of shared `x-app-env` and expose zero host ports (`8765` internal only) in `compose.prod.yaml`.
   - Route `/mcp*` through Caddy in `Caddyfile`; validate `MCP_AUTH_TOKEN`/`MCP_ACTOR_EMAIL` in `scripts/deploy.sh` preflight when `MCP_ENABLED=true`, include `mcp` in rollout & `--rollback`, and verify fail-closed `401` + authenticated read-only MCP smoke via `scripts/post_deploy_smoke.py --check-mcp`.

## Consequences
- **Easier:** External AI agents participate safely across the entire Macy lifecycle using identical DB constraints, approvals, spend caps, and CI gates; single image handles app, worker, CLI, and MCP.
- **Harder:** Remote multi-tenant SaaS clients share token-to-actor binding per deployment until per-user OAuth 2.1 DCR is warranted; CI checks expand to cover HTTP MCP protocol, Compose profiles, and deploy smoke/rollback verification.

## Revisit when
External self-serve tenants require distinct user logins over public internet to `/mcp` without per-user Bearer tokens; then upgrade `engine/mcp/auth.py` and Caddy to OAuth 2.1 Dynamic Client Registration + OIDC JWT verification against `users`.

## Action items
- [ ] `M5-03` — `ADR-027`, `F21`, OpenSpec change `2026-11-11-m5-03-mcp-foundation-auth`, `mcp>=1.2.0` + `importlinter`, `engine/mcp/{auth,errors,mock_store,server}.py`, `observability.py` MCP tags, `content-engine mcp` CLI.
- [ ] `M5-04` — OpenSpec change `2026-11-11-m5-04-mcp-discovery-gate1`, service helpers & `engine/mcp/tools_research.py` (`macy_list_workspaces`, `macy_get_workspace_status`, `macy_research_topic`, `macy_score_topics`, `macy_list_topics_awaiting_approval`, `macy_decide_topic`).
- [ ] `M5-05` — OpenSpec change `2026-11-11-m5-05-mcp-drafting-gate2`, `engine/quality/claims.py` + `services.verify_claims`, `services.preflight_mcp_spend`, `engine/mcp/{tools_editorial,resources,prompts}.py`, Hypothesis property suite.
- [ ] `M5-06` — OpenSpec change `2026-11-11-m5-06-mcp-deploy-smoke`, Compose/Caddy/`deploy.sh`/`post_deploy_smoke.py`/CI/live-E2E integration + `docs/MCP.md` + `docs/runbook.md` + `macy-prod` Compose rehearsal.
