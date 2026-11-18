# Change: Live E2E test suite (J0–J4), budget guard fixture, trace capture, and nightly workflow (M2-07)

## Motivation
Per ADR-018, PRD §9.6, and M2-07, the Macy content engine requires end-to-end user journeys (J0 through J4) running against a live environment (staging or local containerized stack) to guarantee real-world operation:
- J0: Authentication and workspace tenant boundaries (using Playwright storage_state or test credentials).
- J1: Campaign settings (locale pt-BR/en-US, volume, spend caps, language switch).
- J2: Trusted sources management (add feed source, disable source).
- J3: Pipeline execution ("Run now" -> research, dedup, scoring, recommendations report).
- J4: Gate 1 topic approval (approve top topic -> brief queued).
- Budget guard: Suite aborts if cumulative spend exceeds the staging campaign cap.
- Resilience and diagnostics: 1 retry per test, trace and screenshot capture retained on failure.
- Automated orchestration: GitHub Actions workflow `e2e-live.yml` scheduled nightly (cron `0 3 * * *`) and on `workflow_dispatch`, gated cleanly when staging credentials/URL are not yet configured.

## What Changes
1. Register `live_e2e` marker in `pyproject.toml` so normal CI/local test runs exclude live tests while allowing targeted live runs.
2. Add dev dependencies `pytest-playwright` and `pytest-rerunfailures`.
3. Implement `tests/test_live_e2e_j0_j4.py` covering live journeys J0 through J4, Playwright browser fixtures with `storage_state` loading, and a strict budget guard fixture.
4. Implement `tests/test_live_e2e_harness.py` validating budget guard abort logic, failure trace retention, storage state resolution, and workflow syntax.
5. Create `.github/workflows/e2e-live.yml` with nightly cron, manual dispatch, Playwright browser installation, artifact upload for traces and screenshots, and gating via `LIVE_E2E_ENABLED`.
6. Update `docs/plans/M2.md` and `docs/runbook.md` with instructions for running and maintaining live E2E tests.
