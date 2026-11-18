# Design: Live E2E test suite (J0–J4), budget guard fixture, trace capture, and nightly workflow

## Architecture Overview
The live E2E subsystem bridges our web application and test infrastructure across two runtime modes:
1. **Target Environment**:
   - Staging VPS (`https://staging.<domain>`), or
   - Containerized staging/local stack (`http://localhost:8501`).
2. **Execution Context**:
   - Nightly CI cron (`.github/workflows/e2e-live.yml`) triggered at 03:00 UTC,
   - Manual workflow dispatch on GitHub Actions (`workflow_dispatch`),
   - Local command line via `uv run pytest -m live_e2e`.

## Components
1. **Pytest Marker and Filtering**:
   - `live_e2e`: Registered in `[tool.pytest.ini_options]` with `addopts = "-q -m 'not eval and not live_e2e' ..."`.
   - Normal test runs (`pytest`) execute in sub-minute time without launching Chromium.
   - Targeted runs (`pytest -m live_e2e`) activate the live browser harness.

2. **Authentication and Storage State**:
   - In production/staging, Google OIDC cannot be automated with static passwords without risk of MFA blocks.
   - Per ADR-018 and M2-07, Playwright's `storage_state` (JSON containing cookies and local storage tokens) is sourced from `PLAYWRIGHT_STORAGE_STATE` (env var or GitHub Actions secret) and written to a secure file.
   - When running against local test/staging stacks with `APP_ENV=test`, simulated identities (`MACY_TEST_USER_EMAIL`) or fake OIDC endpoints are used as fallback.

3. **Budget Guard Fixture (`live_budget_guard`)**:
   - Before executing pipeline-triggering steps (J3), the fixture queries or captures the active spend cap from campaign settings (configured in J1).
   - After execution (or incrementally), it computes delta spend. If cumulative spend exceeds the cap, it immediately raises `BudgetOverrunError`, failing the suite and preventing costly cost runaway.

4. **Failure Diagnostics**:
   - `pytest-playwright` flags `--tracing retain-on-failure --screenshot only-on-failure`.
   - When a test fails, Playwright writes trace zip files and PNG screenshots to `artifacts/playwright-results/`.
   - The GitHub Actions workflow uploads this directory via `actions/upload-artifact@v4` with `if: always()`.

5. **Retry Mechanism**:
   - Per ADR-018 ("One retry; a journey that flakes twice in 7 days gets a fix change"), tests run with `--reruns 1`.
