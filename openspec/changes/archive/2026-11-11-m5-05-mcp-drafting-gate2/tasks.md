# Tasks: M5-05 — MCP Briefing, Drafting, Runtime Claim Check, Gate 2 Review, Spend Guards & Resources

**PR:** `M5-05` (branch `m5-05-mcp-drafting-gate2`, `<= ~400` non-test LOC per `ADR-019`) · **Plan:** [docs/plans/M5.md](../../../docs/plans/M5.md)

## 1. Runtime Claim Verification & Spend Preflight Service

- [x] 1.1 Write failing tests in `tests/test_mcp_editorial_gate2.py` and `tests/test_mcp_spend.py` verifying runtime claim verification runs with `evals` blocked/absent (`sys.modules["evals"] = None`), preflight `BUDGET_CAP_EXCEEDED` aborts before paid calls, and `usage_ledger` logs exact `Decimal` USD — verify: `uv run pytest tests/test_mcp_spend.py tests/test_mcp_editorial_gate2.py -k "claims or budget" --no-cov` fails before implementation
- [x] 1.2 Implement `engine/quality/claims.py` (deterministic numeric/quote check + injected `LLMPort` / `safe_http.safe_request` Jev client per `ADR-026`, zero `evals/` imports) and add `services.verify_claims` + `services.preflight_mcp_spend` in `engine/services.py` — verify: `uv run pytest tests/test_mcp_spend.py --no-cov && uv run mypy && uv run lint-imports` exit 0

## 2. Editorial, Gate 2, Publish Tools, Resources & Prompts

- [x] 2.1 Write failing tests in `tests/test_mcp_editorial_gate2.py` and `tests/test_mcp_properties.py` (Hypothesis `ADR-025`) covering `macy_get_editorial_brief`, `macy_generate_draft` (FK `draft_claims`), `macy_evaluate_draft`, `macy_list_drafts_awaiting_approval`, `macy_decide_draft` (`"approved"`/`"declined"`), `macy_publish_draft` (`_validate_topic_approver` + `APP_ENV=staging` forces `mode="draft"`), `macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, `macy://drafts/{id}/claims`, `macy_editorial_review`, and property invariants — verify: `uv run pytest tests/test_mcp_editorial_gate2.py tests/test_mcp_properties.py --no-cov` fails before implementation
- [x] 2.2 Implement `macy_get_editorial_brief`, `macy_generate_draft`, `macy_verify_claims`, `macy_evaluate_draft`, `macy_list_drafts_awaiting_approval`, `macy_decide_draft`, and `macy_publish_draft` in `engine/mcp/tools_editorial.py` (wrapping preflight spend checks and `MockStore` parity) — verify: `uv run pytest tests/test_mcp_editorial_gate2.py --no-cov` passes
- [x] 2.3 Implement `engine/mcp/resources.py` (`macy://workspaces/{id}/brand`, `macy://workspaces/{id}/weekly-report`, `macy://drafts/{id}/claims` with `require_member`) and `engine/mcp/prompts.py` (`macy_editorial_review`) and wire into `engine/mcp/server.py` — verify: `uv run pytest tests/test_mcp_editorial_gate2.py tests/test_mcp_spend.py tests/test_mcp_properties.py --no-cov` passes

## 3. Full Quality, Complexity & Diff-Cover Verification

- [x] 3.1 Run full `M5-05` delivery checks (`HYPOTHESIS_PROFILE=ci sh scripts/check.sh`) ensuring `complexipy <= 15`, `jscpd = 0%`, `lint-imports` clean, branch coverage `>= 90%`, `diff-cover = 100%`, `openspec validate --all --strict`, and `<= ~400` non-test LOC diff — verify: `HYPOTHESIS_PROFILE=ci sh scripts/check.sh` exits 0

## Scenario ↔ Test Mapping

| Spec Scenario | Test Assertion |
|---|---|
| Generating brief, FK-grounded draft & verifying claims without `evals/` | `tests/test_mcp_editorial_gate2.py::test_brief_draft_and_runtime_claim_verification_without_evals` |
| Evaluating draft & listing `'pass'` queue | `tests/test_mcp_editorial_gate2.py::test_evaluate_and_list_drafts_awaiting_approval` |
| Approving/declining Gate 2 draft (`"approved"`/`"declined"`) | `tests/test_mcp_editorial_gate2.py::test_decide_draft_gate2_normalized_values_and_creator_check` |
| Publishing draft enforces creator ownership & staging `mode="draft"` | `tests/test_mcp_editorial_gate2.py::test_publish_draft_creator_gate_and_staging_draft_mode` |
| Authorized resource reads & cross-tenant refusal | `tests/test_mcp_editorial_gate2.py::test_mcp_resources_and_prompt_tenant_scoping` |
| Spend cap breach aborts cleanly (`BUDGET_CAP_EXCEEDED`); usage recorded | `tests/test_mcp_spend.py::test_preflight_spend_cap_aborts_before_llm_and_records_usage` |
| Property invariants across spend, claim verdicts, error redaction | `tests/test_mcp_properties.py` (Hypothesis `ci` profile) |

## Workflow follow-up

- Squash-merge `m5-05-mcp-drafting-gate2` PR into `dev` once CI checks pass.
- Archive `2026-11-11-m5-05-mcp-drafting-gate2` with `openspec archive`.
