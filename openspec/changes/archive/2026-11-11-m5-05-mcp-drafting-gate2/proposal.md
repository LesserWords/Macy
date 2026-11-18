# Proposal: M5-05 — MCP Briefing, Drafting, Runtime Claim Check, Gate 2 Review, Spend Guards & Resources

**Plan Item:** M5-05 ([docs/plans/M5.md](../../../docs/plans/M5.md)) · **Features:** F12, F13, F14, F15, F16, F17, F21 · **ADRs:** ADR-002, ADR-013, ADR-016, ADR-017, ADR-021, ADR-025, ADR-026, ADR-027

## Why

To complete Macy’s editorial loop over MCP, AI clients need tools for SEO briefing, FK-grounded drafting, factual claim verification, deterministic + rubric quality evaluation, Gate 2 draft decisions, and publishing—plus read-only MCP resources and editorial review prompts.

However, the runtime Docker container built by `Dockerfile` omits `evals/`, and `evals/claim_benchmark/jev.py` uses `httpx2`, which is not a runtime dependency. Calling `evals.harness` from MCP would raise `ModuleNotFoundError` in production. Additionally, `services._normalize_draft_decision` expects `"approved"`/`"declined"` (not `"accepted"`), `services.publish_draft` enforces `_validate_topic_approver` (campaign creator owner, not any member), staging forces `mode="draft"`, and every generative tool requires preflight budget enforcement (`ADR-016`) returning `BUDGET_CAP_EXCEEDED`.

## What Changes

- **Runtime Claim Verification (`engine/quality/claims.py` + `services.verify_claims`):** Implement a runtime-safe claim verification helper using deterministic numeric/quote checks plus an optional `LLMPort` / Jev judge over `engine.safe_http.safe_request` (`ADR-026`), zero dependency on `evals/`. Record verification cost in `usage_ledger`.
- **MCP Spend Preflight (`services.preflight_mcp_spend`):** Wrap `engine/budget.py` (`check_monthly_budget`, `check_run_budget`) before any LLM or paid keyword/search MCP call, aborting cleanly with structured error code `BUDGET_CAP_EXCEEDED`.
- **Editorial, Gate 2 & Publish Tools (`engine/mcp/tools_editorial.py`):**
  - `macy_get_editorial_brief`: Generate or retrieve brief via `services.get_latest_brief` / `services.generate_brief`.
  - `macy_generate_draft`: Draft markdown article + FK-backed `draft_claims` via `services.write_draft`.
  - `macy_verify_claims`: Verify claim statements against candidate passages or stored draft document sources via `services.verify_claims`.
  - `macy_evaluate_draft`: Run deterministic checks + isolated rubric evaluator via `services.evaluate_and_route_draft`.
  - `macy_list_drafts_awaiting_approval`: Surface `'pass'` drafts (including `revision = 2` owner-escalated drafts) via `services.list_draft_reviews`.
  - `macy_decide_draft`: Decide `"approved"` or `"declined"` (matching `_normalize_draft_decision`) via `services.decide_draft` with `_validate_topic_approver`.
  - `macy_publish_draft`: Publish approved/published drafts via `services.publish_draft` enforcing `_validate_topic_approver` and `APP_ENV=staging` `mode="draft"`.
- **MCP Resources & Prompts (`engine/mcp/resources.py`, `engine/mcp/prompts.py`):**
  - Resources (`require_member` enforced): `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, `macy://drafts/{id}/claims`.
  - Prompt template: `macy_editorial_review`.

## Capabilities

### New Capabilities
- None (extends existing capabilities).

### Modified Capabilities
- `mcp-server`: Add briefing, drafting, runtime claim verification, evaluation, Gate 2 (`"approved"`/`"declined"`), publishing, resources (`brand`, `weekly-report`, `claims`), and `macy_editorial_review` prompt.
- `quality`: Provide runtime claim verification helper (`engine/quality/claims.py`) decoupled from `evals/`.
- `spend`: Enforce MCP generative preflight cap checks (`BUDGET_CAP_EXCEEDED`) and ledger accounting.

## Out of Scope

- Auth, transport, discovery, research, and Gate 1 (`M5-03`, `M5-04` prerequisites).
- Production Compose/Caddy/deploy script changes and live E2E suites — deferred to `M5-06`.

## Impact

- **Engine:** `engine/quality/claims.py`, `engine/services.py`, `engine/mcp/{tools_editorial,resources,prompts,server}.py`
- **Tests:** `tests/test_mcp_editorial_gate2.py`, `tests/test_mcp_spend.py`, `tests/test_mcp_properties.py` (Hypothesis `ADR-025`)
