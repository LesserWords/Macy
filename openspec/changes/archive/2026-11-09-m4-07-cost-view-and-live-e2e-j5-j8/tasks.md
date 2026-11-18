# Tasks

## 1. Cost Summary Service & UI Cost View (M4-07 Step 1, F17 AC5–AC6)
- [x] 1.1 Write failing tests in `tests/test_cost_view.py` verifying `services.cost_summary()` against literal `usage_ledger` fixtures (monthly spend vs `monthly_cap_usd`, per-run cost vs `run_cap_usd`, cost per published article, `< $5.00` pilot target flag, and non-member `PermissionError`) and testing the Streamlit Cost View in `pt-BR` and `en-US`.
- [x] 1.2 Implement `db.list_runs_with_usage_summary()` and `db.list_published_articles_with_costs()` in `engine/db.py`, `services.cost_summary()` in `engine/services.py`, and render the Cost View on Dashboard and Runs screens in `app.py` with compiled `pt_BR` and `en_US` translations.

## 2. Live E2E Suite Extension J5–J8 (M4-07 Step 2, ADR-018)
- [x] 2.1 Write failing tests in `tests/test_live_e2e_harness.py` asserting that `tests/test_live_e2e_j0_j4.py` defines live journeys `J5`, `J6`, `J7`, and `J8` and that `.github/workflows/e2e-live.yml` executes `J0–J8` within the budget guard.
- [x] 2.2 Extend `tests/test_live_e2e_j0_j4.py` with `J5` (draft + evaluation + `lingua` check), `J6` (publish in draft mode + cost view check), `J7` (topic and draft reject paths), and `J8` (integrations + signed webhooks to receiver + private IP SSRF refusal), and update `.github/workflows/e2e-live.yml`.

## 3. Three End-to-End Articles & Three Consecutive Live E2E Runs in Docker Compose (M4-07 Steps 3 & 4)
- [x] 3.1 Execute 3 end-to-end articles through the pipeline + editor review + draft-mode publishing and run 3 consecutive full `J0–J8` E2E runs against the containerized Docker Compose stack, recording the evidence and `< $5.00` cost per article in `docs/plans/M4.md`.
- [x] 3.2 Update `docs/features/F17-spend-control.md`, `docs/adr/ADR-016-spend-control.md`, `docs/adr/ADR-018-testing-strategy.md`, `docs/adr/ADR-023-observability.md`, and `docs/plans/M4.md`, sync OpenSpec specs, and pass all quality gates.
