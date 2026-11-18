# Design: M4-07 — Cost View and Live E2E J5–J8

## 1. Cost Summary & Per-Article Cost Accounting (`F17 AC5, AC6`, `ADR-016`, `ADR-023`)
- **Single Source of Truth (`usage_ledger`):**
  - `monthly_spend_usd` is summed from `usage_ledger` in the active calendar month (`db.get_monthly_spend`).
  - `total_spend_usd` is summed across all `usage_ledger` rows for the workspace (or campaign runs).
  - **Cost per run:** For each run in `runs`, sum `usage_ledger.cost_usd`, `input_tokens`, `output_tokens`, `cached_tokens`, and call count grouped by `run_id` (falling back to `runs.cost_usd` if no ledger rows exist), alongside `run_cap_usd` and the count of distinct published drafts produced in that run.
  - **Cost per published article:** Each published draft (`drafts.status = 'published'` / `publications.status = 'published'`) links via `drafts.brief_id -> briefs.opportunity_id -> opportunities.run_id` to its originating `run_id`.
    - When a run produces $N_{\text{pub}} \ge 1$ published articles, the run's ledger cost is attributed across its published articles (`run_ledger_cost / N_pub`, quantized to 6 decimal places), and each article carries `under_pilot_target = (cost_usd < Decimal("5.00"))`.
    - `average_cost_per_published_article_usd` is the mean `cost_usd` across all published articles (or `Decimal("0.000000")` when none exist).

## 2. Live E2E J5–J8 & Local Docker Compose Parity (`ADR-018`)
- Because all verification runs locally on Docker and Docker Compose with 100% parity to a VPS, `tests/test_live_e2e_j0_j4.py` supports driving a running Macy stack (`STAGING_APP_URL`) and verifying:
  - **J5:** Draft review UI & contract checks (`lingua-py` language match, `draft_claims` source provenance, `QualityReport` verdict, `revision <= 2`).
  - **J6:** Gate 2 draft approval -> draft-mode publication (`mode="draft"`), `publications` record with `destination` + `external_id`, and cost view showing `< $5.00` per published article.
  - **J7:** Rejecting a topic at Gate 1 (`rejected`) and declining a draft at Gate 2 (`declined`), verifying state transitions and non-re-recommendation on subsequent runs.
  - **J8:** Integrations secret masking + connection test, Standard Webhooks HMAC delivery (`topics.awaiting_approval`, `draft.awaiting_approval`, `article.published`) to the `webhook-receiver` container, and `safe_http` refusal of private/loopback IPs.
