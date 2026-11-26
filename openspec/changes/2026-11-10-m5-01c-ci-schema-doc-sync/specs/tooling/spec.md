# tooling Delta Specification

## MODIFIED Requirements

### Requirement: Branch coverage, diff coverage, and deterministic property testing
The test suite SHALL enforce ≥ 90 % overall branch coverage via `pytest-cov`, 100 % diff coverage on changed lines via `diff-cover` across both `pull_request` (`origin/<base_ref>`) and `push` (`github.event.before`, with safe initial-push fallback) events, and a deterministic Hypothesis `ci` profile (`derandomize=True`).

#### Scenario: Coverage gates pass on tested changes
- **WHEN** `pytest` and `diff-cover` run on changes where ≥ 90 % of branches and 100 % of modified lines in `engine/` are covered
- **THEN** both coverage commands exit with status 0 and emit `coverage.xml` and `artifacts/coverage.json`

#### Scenario: Untested changed line fails diff-cover
- **WHEN** a commit is evaluated on a `pull_request` or pushed directly to `dev`/`main` with an uncovered modified line in `engine/`
- **THEN** `.github/workflows/ci.yml` compares against the true prior commit (`origin/<base_ref>` on PR, `github.event.before` or fallback on push) and `diff-cover` exits non-zero rather than comparing the branch against itself

#### Scenario: Hypothesis CI profile is deterministic
- **WHEN** tests run under the `ci` Hypothesis profile
- **THEN** Hypothesis executes with `derandomize=True` so property tests are reproducible across runs

### Requirement: Live E2E test suite and nightly workflow
The repository SHALL provide a Playwright live E2E suite (`tests/test_live_e2e.py`) covering journeys J0 through J8 against staging with `BudgetGuard`, 1 retry, failure trace/screenshot capture, exact bilingual selectors (`"Draft Review"` / `"Revisão de Rascunhos"`), explicit non-silent UI assertions, and nightly cron (`e2e-live.yml`).

#### Scenario: Live E2E suite verifies user journeys J0 through J4
- **WHEN** the live E2E suite runs against an authenticated application instance
- **THEN** it executes authentication (J0), campaign configuration and language toggle (J1), source management (J2), pipeline execution with report generation (J3), and topic approval (J4)

#### Scenario: Live E2E suite verifies full J0 through J8 coverage with bilingual selectors
- **WHEN** `tests/test_live_e2e.py` executes journeys J0 through J8 against an authenticated application instance
- **THEN** J5 and J7 locate `"Draft Review"` / `"Revisão de Rascunhos"` cleanly, all journeys assert rendered pages without silent conditional skips, and `test_live_e2e_harness.py` / `e2e-live.yml` target `tests/test_live_e2e.py`

#### Scenario: Budget overrun aborts the test suite
- **WHEN** the cumulative spend during a live test run exceeds the campaign spend cap
- **THEN** the budget guard fixture raises an error and fails the suite immediately

#### Scenario: Forced failure retains trace and screenshot artefacts
- **WHEN** a test in the live E2E suite encounters a failure
- **THEN** Playwright retains trace archives and screenshots in the results directory for diagnostic upload

#### Scenario: Workflow provides nightly cron and manual dispatch
- **WHEN** `.github/workflows/e2e-live.yml` is parsed
- **THEN** it targets `tests/test_live_e2e.py`, defines a nightly cron at 03:00 UTC and `workflow_dispatch`, and uploads artifacts on failure

## ADDED Requirements

### Requirement: Architecture, ADR status, and milestone documentation consistency
The repository SHALL keep active tooling commands, runtime dependency notes, completed ADR action checklists, and milestone verification records synchronized with delivered code across `docs/adr/`, `docs/architecture/TECH-RADAR.md`, `docs/prd/MVP-PRD.md`, `docs/features/`, `docs/guides/developer-guide.md`, `docs/runbook.md`, and `docs/plans/M5.md` while preserving historical ADR decisions.

#### Scenario: Documentation references match implemented CLI, quality gates, and trial outcomes
- **WHEN** active documentation is inspected after sync
- **THEN** CLI examples use `macy <cmd>`, quality-gate docs reference `scripts/check.sh` and active tools cleanly, completed M0–M4 ADR action checkboxes reflect delivered work without altering original decision sections, and `M5.md` / `runbook.md` distinguish delivered local verification from pending live VPS rollout
