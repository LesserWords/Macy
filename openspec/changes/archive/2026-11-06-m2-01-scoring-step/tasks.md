# Tasks

## 1. Opportunities Database Migration

- [ ] 1.1 Create migration `db/migrations/20261105000004_create_opportunities.sql` creating table `opportunities` with composite foreign keys, checks on `funnel_stage`, `confidence`, `status`, and `total BETWEEN 0 AND 100`, plus index `opp_report`.
- [ ] 1.2 Verify squawk is clean on the new migration and apply migration with dbmate to update `db/schema.sql`.
- [ ] 1.3 Add CRUD and query functions in `engine/db.py` (`create_opportunity`, `get_opportunity`, `list_opportunities_for_run`, `find_opportunity_by_record`).

## 2. Score Prompt & Scoring Engine

- [ ] 2.1 Author `prompts/score.md` defining criteria sub-scores (0-10), evidence requirements, and classification guidelines.
- [ ] 2.2 Implement `engine/strategy/score.py` computing weighted totals deterministically with campaign weights, flagging `is_estimate` for SEO potential without keyword data.
- [ ] 2.3 Wire scoring stage into `engine/pipeline.py` and export in `engine/services.py`.

## 3. Hypothesis Property Tests & Regression Suite

- [ ] 3.1 Write Hypothesis property-based tests in `tests/test_scoring.py` verifying total bounds [0, 100] and monotonicity in each criterion score.
- [ ] 3.2 Add regression fixtures `evals/cases/case_01.yaml` through `case_05.yaml` and regression test runner in `evals/test_regression.py` marked `@pytest.mark.eval`.
- [ ] 3.3 Verify fake LLM scoring tests and regression suite pass with quality gates.

## Workflow follow-up

- Archive the change after quality gates and GitHub Actions CI pass.
- Squash-merge PR to dev branch.
