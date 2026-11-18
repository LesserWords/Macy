# Proposal: M4-07 — Cost View and Live E2E J5–J8

## Why
Milestone M4 concludes with `M4-07`:
1. **Cost View (`F17 AC5, AC6`, `ADR-016`, `ADR-023`):** Owners need visibility into spend-to-date against monthly and per-run caps, exact cost per run from `usage_ledger`, and cost per published article (with the `< $5.00` pilot target clearly surfaced).
2. **Live E2E Journeys J5–J8 (`ADR-018`, `PRD §9.6`, `F05`, `F06`):** Extend the live E2E suite from `J0–J4` to `J0–J8` covering draft generation & evaluation (`lingua` check, claim provenance, verdict, revision bound), Gate 2 approval & draft-mode publishing, topic & draft rejection paths, and integrations + signed Standard Webhooks delivery to the receiver container with SSRF refusal of private IPs.
3. **End-to-End Article Verification & Consecutive E2E Runs:** Verify 3 end-to-end articles produced and reviewed with cost per article `< $5.00`, and 3 consecutive green runs of the full `J0–J8` suite against the containerized Docker Compose stack.

## What Changes
- **Database (`engine/db.py`)**:
  - Add `list_runs_with_usage_summary(conn, workspace_id=..., campaign_id=...)` and `list_published_articles_with_costs(conn, workspace_id=..., campaign_id=...)` to query exact `Decimal` cost per run and cost per published article from `usage_ledger`, `runs`, `drafts`, `briefs`, `opportunities`, and `publications`.
- **Service Layer (`engine/services.py`)**:
  - Add `cost_summary(conn=None, *, workspace_id, user_id, campaign_id=None, reference_date=None)` returning spend-to-date vs. `monthly_cap_usd` and `run_cap_usd`, per-run costs and token totals, per-published-article costs, average cost per run, average cost per published article, and `< $5.00` pilot target status (`F17 AC5, AC6`).
- **Admin UI & i18n (`app.py`, `locales/`)**:
  - Render the Cost & Spend View on both the Dashboard (`_render_dashboard`) and Runs (`_render_runs`) screens with bilingual `pt-BR` and `en-US` translations.
- **Live E2E Suite (`tests/test_live_e2e_j0_j4.py`, `tests/test_live_e2e_harness.py`, `.github/workflows/e2e-live.yml`)**:
  - Add `test_journey_j5_live_draft_and_evaluation`, `test_journey_j6_live_publish_draft_mode`, `test_journey_j7_live_reject_paths`, and `test_journey_j8_live_integrations_and_webhooks`.
  - Update `.github/workflows/e2e-live.yml` to run `J0–J8`.
