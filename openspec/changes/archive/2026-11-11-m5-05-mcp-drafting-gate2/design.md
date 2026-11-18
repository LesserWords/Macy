# Design: M5-05 — MCP Briefing, Drafting, Runtime Claim Check, Gate 2, Spend & Resources

**ADR:** [`docs/adr/ADR-027-mcp-server.md`](../../../docs/adr/ADR-027-mcp-server.md) · **Plan Item:** `M5-05` (`<= ~400` non-test LOC)

## Real Service & Runtime Boundary Fixes

| Requirement | Prior Draft Bug | `M5-05` Implementation |
|---|---|---|
| Claim verification | Called `evals.harness` (excluded from Docker image, uses unbundled `httpx2`) | New `engine/quality/claims.py` + `services.verify_claims`: deterministic number/date/quote matching + injected `LLMPort` / `safe_http.safe_request` Jev client (`ADR-026`); meters USD via `db.record_usage` |
| Gate 2 decision | Passed `"accepted"` / `"declined"` | Uses real [`_normalize_draft_decision`](../../../engine/services.py#L2349-L2357): **`"approved"`** / **`"declined"`** via `services.decide_draft` |
| Publish auth & staging | Assumed `Member` role | Uses `services.publish_draft` enforcing [`_validate_topic_approver`](../../../engine/services.py#L3540-L3545) (`owner` + `campaign.created_by`) and staging `mode="draft"` ([`_resolve_publish_mode`](../../../engine/services.py#L3346-L3350)) |
| Spend cap preflight | Non-existent `check_budget_guardrails` / `record_spend` | New `services.preflight_mcp_spend` wrapping `budget.check_monthly_budget` & `budget.check_run_budget` + mapping `MonthlyCapReached`/`BudgetCapReached` -> `BUDGET_CAP_EXCEEDED` |

## Module Breakdown (`engine/mcp/`, `engine/quality/`, `engine/services.py`)

1. **`engine/quality/claims.py`**
   - Pure + runtime-safe functions keeping cognitive complexity `<= 15` (`complexipy`):
     - `verify_claim_deterministic(claim_text, source_text)` -> extracts numbers/dates/entities, matches verbatim evidence spans, returns preliminary score/quote.
     - `verify_claims_against_sources(claims, passages, *, llm=None, jev_api_key=None)` -> uses injected `LLMPort` or `safe_http.safe_request` against `DECISIONS_URL` (`ADR-026`) + code guard, outputting `ClaimVerificationResult(claim, verdict, confidence, evidence_quote, cost_usd)`.
   - Zero imports from `evals/`.
2. **`engine/services.py`**
   - `preflight_mcp_spend(conn=None, *, workspace_id, run_id=None, estimated_cost=Decimal("0"))` -> checks campaign monthly cap (`check_monthly_budget`) and active run cap (`check_run_budget`).
   - `verify_claims(conn=None, *, workspace_id, user_id, draft_id=None, claims=None, passages=None, offline=False)` -> `require_member`, preflights budget, delegates to `engine/quality/claims.py`, records `usage_ledger` row.
3. **`engine/mcp/tools_editorial.py`**
   - Registers `macy_get_editorial_brief`, `macy_generate_draft`, `macy_verify_claims`, `macy_evaluate_draft`, `macy_list_drafts_awaiting_approval`, `macy_decide_draft`, `macy_publish_draft`.
   - Every generative tool runs preflight spend guard before delegating; errors route through `engine/mcp/errors.py` and `observability.capture_exception(..., mcp_tool=..., workspace_id=..., run_id=...)`.
4. **`engine/mcp/resources.py` & `engine/mcp/prompts.py`**
   - `macy://workspaces/{workspace_id}/brand` -> `services.get_brand_profile` (`require_member`)
   - `macy://workspaces/{workspace_id}/weekly-report` -> `services.report` / `list_topics_for_approval` (`require_member`)
   - `macy://drafts/{draft_id}/claims` -> `services.get_draft_review_bundle` (`require_member`)
   - Prompt `macy_editorial_review` -> formats inspection checklist referencing those URIs and deterministic findings.
5. **Property-Based Tests (`ADR-025`, `tests/test_mcp_properties.py`)**
   - Hypothesis generators verify:
     - `preflight_mcp_spend` & decimal budget accounting monotonicity;
     - Gate 1/2 decision normalization rejects invalid enum inputs cleanly;
     - Claim verifier deterministic quote/number extractor invariants (no crash on Unicode/pt-BR/en-US text);
     - `MCPError` formatting never leaks embedded `Bearer` or DSN strings.
