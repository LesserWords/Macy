# Proposal

## Why

Plan item **M2-01** (`docs/plans/M2.md#m2-01`). Kick off Milestone 2 by implementing the core opportunity scoring pipeline step. Each non-duplicate, verified research record from the collection stage is evaluated by an LLM against the 6 rubric criteria (novelty, audience fit, brand fit, competitor gap, trend, urgency), returning criterion sub-scores (0–10) with factual evidence and `is_estimate` flags. The overall weighted total score (0–100) is deterministically computed in Python code using campaign weights (`CampaignConfig`), persisted in a new `opportunities` table with squawk-clean migration, and verified with Hypothesis property tests and regression cases 1–5. Features: F09 (AC1, AC2, AC6). ADRs: 002, 004, 025.

Out of scope:
- M2-02 weekly report screen and `topics.awaiting_approval` event emission.
- M2-03 Streamlit approval/rejection decision buttons.
- Live staging VPS deployment (M2-05).

## What Changes

- **Database migration**: Create `db/migrations/20261105000004_create_opportunities.sql` with table `opportunities` adhering to DATA-MODEL §8: foreign keys, multi-tenant composite foreign keys, checks on `funnel_stage`, `confidence`, `status`, and `total BETWEEN 0 AND 100`, plus index `opp_report ON opportunities (run_id, total DESC)`.
- **Score prompt and versioning**: Author `prompts/score.md` defining criteria rubrics, sub-score scale (0–10), evidence requirements, and funnel stage / angle classification; hash the prompt with SHA-256 to track `prompt_version`.
- **Scoring module**: Implement `engine/strategy/score.py` (and export in `engine/services.py`) executing LLM structured calls using `engine.contracts.Opportunity`, calculating the weighted total with `CampaignConfig.scoring_weights`, and flagging `is_estimate` for SEO potential when keyword data is absent.
- **Pipeline integration**: Wire scoring stage into `engine/pipeline.py` executing after research collection and dedup.
- **Hypothesis property tests**: Verify total score bounds (0–100), monotonicity in each criterion score, and weight distribution invariance.
- **Regression test suite**: Add fixtures in `evals/cases/case_01.yaml` through `case_05.yaml` and regression test runner in `evals/test_regression.py` marked `@pytest.mark.eval` verifying that evaluated opportunities land in their expected priority bands.

## Capabilities

### Modified Capabilities
- `scoring`: Add requirements and scenarios for code-computed weighted totals, criteria sub-scores with evidence, `is_estimate` flag on missing keyword data, and regression priority band assertions.

## Impact

- Database: `db/migrations/20261105000004_create_opportunities.sql`, `db/schema.sql`, `engine/db.py`.
- Engine: `engine/strategy/score.py`, `engine/pipeline.py`, `engine/services.py`, `engine/contracts.py`.
- Prompts & Evals: `prompts/score.md`, `evals/cases/*.yaml`, `evals/test_regression.py`.
- Tests: `tests/test_scoring.py`, `tests/test_services.py`.
