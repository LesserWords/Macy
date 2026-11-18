# Tasks: Live E2E test suite (J0–J4), budget guard fixture, trace capture, and nightly workflow

- [x] Add `pytest-playwright` and `pytest-rerunfailures` to `pyproject.toml` and register `live_e2e` marker <!-- id: 0 -->
- [x] Implement live E2E test suite covering journeys J0 through J4 (`tests/test_live_e2e_j0_j4.py`) <!-- id: 1 -->
- [x] Implement budget guard fixture with spend cap enforcement (`tests/test_live_e2e_j0_j4.py`) <!-- id: 2 -->
- [x] Implement automated harness tests verifying budget overrun abort, workflow syntax, and diagnostics (`tests/test_live_e2e_harness.py`) <!-- id: 3 -->
- [x] Create GitHub Actions nightly workflow `.github/workflows/e2e-live.yml` with scheduling, dispatch, storage state support, and artifact upload <!-- id: 4 -->
- [x] Update documentation: `docs/runbook.md` and `docs/plans/M2.md` <!-- id: 5 -->
- [x] Verify all quality gates pass (lint, format, types, complexity, dead code, imports, duplication, tests) <!-- id: 6 -->
