## 1. Static analysis gates (complexipy, vulture, jscpd, import-linter)

- [x] 1.1 Write failing test `tests/test_quality_gates.py` asserting `pyproject.toml`, `.jscpd.json`, `vulture_whitelist.py`, and `scripts/check.sh` configure and invoke `complexipy`, `vulture`, `jscpd`, and `lint-imports` → verify: `uv run pytest` fails on missing config and tools (`tests/test_quality_gates.py::test_static_analysis_gate_configs_exist`, `tests/test_quality_gates.py::test_check_script_invokes_all_static_gates`)
- [x] 1.2 Install `complexipy`, `vulture`, `import-linter`; add `.jscpd.json`, `vulture_whitelist.py`, `[tool.complexipy]`, `[tool.vulture]` and `[tool.importlinter]` in `pyproject.toml`, and wire them into `scripts/check.sh` and `.pre-commit-config.yaml` → verify: `uv run pytest` passes and each tool exits 0 from `scripts/check.sh`

## 2. Coverage gates and Hypothesis CI profile

- [x] 2.1 Write failing test in `tests/test_quality_gates.py` asserting `pytest-cov` branch coverage (≥ 90 %), `diff-cover` (100 %), and Hypothesis `ci` profile (`derandomize=True`) are configured and active → verify: `uv run pytest` fails on missing Hypothesis profile / coverage config (`tests/test_quality_gates.py::test_coverage_and_hypothesis_ci_profile_configured`)
- [x] 2.2 Install `pytest-cov`, `diff-cover`, `hypothesis`; configure coverage in `pyproject.toml` and Hypothesis `ci` profile in `tests/conftest.py`; wire coverage and `diff-cover` into `scripts/check.sh` and `.github/workflows/ci.yml` → verify: `uv run pytest` reports ≥ 90 % branch coverage and `uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=100` exits 0

## 3. CRAP-style hotspot script and CI trend artefacts

- [x] 3.1 Write failing unit + Hypothesis property tests in `tests/test_hotspots.py` for `scripts/hotspots.py` CRAP score computation and top-10 ranking → verify: `uv run pytest tests/test_hotspots.py` fails on missing module (`tests/test_hotspots.py::test_crap_score_extremes`, `tests/test_hotspots.py::test_crap_score_monotonic_in_coverage`, `tests/test_hotspots.py::test_rank_hotspots_joins_and_limits_to_top_10`, `tests/test_hotspots.py::test_hotspots_cli_writes_json_report`)
- [x] 3.2 Implement CRAP hotspot calculation and CLI output (`artifacts/hotspots.json`), and update `.github/workflows/ci.yml` to upload complexity, duplication, coverage, and hotspot JSON artefacts → verify: `uv run pytest` passes with 100 % diff coverage and `scripts/hotspots.py` generates `artifacts/hotspots.json`

## 4. Bad-sample verification

- [x] 4.1 Verify every gate blocks on planted violations (cyclomatic complexity > 10 / cognitive > 15, duplicated block, unused function, forbidden/cyclic import, untested line) and clean up probe → verify: each gate exits non-zero naming its violation, and clean tree passes `sh scripts/check.sh`
