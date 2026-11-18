## Why

Plan item **M0-02** (docs/plans/M0.md). Completes the fallow-equivalent quality gate suite in Python so that cognitive complexity, dead code, duplication, architectural boundaries, test coverage, property-based testing, and CRAP-style hotspot reporting are enforced on every commit and PR from an empty skeleton onward. ADRs: 006, 018, 025. Features: — (infrastructure).

## What Changes

- Add `complexipy` (threshold ≤ 15), `vulture` (`min_confidence = 60` so Vulture's 60 % default for unused functions/classes/methods is enforced, with `vulture_whitelist.py`), `jscpd` (threshold 0 % / 0 new clones, min 50 tokens), and `import-linter` (no circular imports + initial package boundary contracts) to `pyproject.toml` and `scripts/check.sh`.
- Add `pytest-cov` (`--cov=engine --cov-branch --cov-fail-under=90`), `diff-cover` (`--fail-under=100` against base branch), and `hypothesis` with a deterministic `ci` profile (`derandomize=True`) loaded via `tests/conftest.py`.
- Add `scripts/hotspots.py` to join `complexipy` JSON and `coverage.json` into a CRAP-style top-10 hotspot report, and upload complexity, duplication, coverage, and hotspot JSON artefacts in `.github/workflows/ci.yml`.
- Update `docs/adr/ADR-006-python-and-quality-gates.md`, `docs/adr/ADR-018-testing-strategy.md`, and `docs/adr/ADR-025-supporting-libraries.md` action items for M0-02.

Out of scope: squawk SQL migration checks (M0-03), tenant-scoping SQL AST test (M0-04), full `app`/`cli`/`services`/`db` import-linter boundary contracts once those modules exist (M0-05), and Babel translation completeness checks (M1).

## Impact

- Modified: `pyproject.toml`, `uv.lock`, `scripts/check.sh`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.gitignore`, `docs/adr/ADR-006-python-and-quality-gates.md`, `docs/adr/ADR-018-testing-strategy.md`, `docs/adr/ADR-025-supporting-libraries.md`.
- New: `vulture_whitelist.py`, `.jscpd.json`, `tests/conftest.py`, `scripts/hotspots.py`, `tests/test_hotspots.py`, `tests/test_quality_gates.py`.
