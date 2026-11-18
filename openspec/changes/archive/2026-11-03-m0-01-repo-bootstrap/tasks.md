## 1. Python project

- [x] 1.1 Write failing smoke test `tests/test_package.py` (engine version) → verify: pytest fails on missing package
- [x] 1.2 Add `pyproject.toml`, `engine/__init__.py` → verify: `uv run pytest` passes; `ruff check`, `ruff format --check`, `mypy` clean

## 2. Gates and hooks

- [x] 2.1 Add `scripts/check.sh` and `.pre-commit-config.yaml` (ruff, mypy, gitleaks) → verify: `pre-commit run --all-files` green
- [x] 2.2 Plant a fake GitHub token → verify: gitleaks reports `github-pat`; probe removed

## 3. OpenSpec and CI

- [x] 3.1 `openspec init` and project rules in `openspec/config.yaml` → verify: `openspec validate --all --strict` green
- [x] 3.2 Add `.github/workflows/ci.yml`, PR template, Dependabot → verify: workflow YAML parses (`check-yaml`); jobs run on first push
- [ ] 3.3 Branch protection on `main` (required checks, linear history, squash) → verify: direct push to `main` rejected (needs GitHub remote)
