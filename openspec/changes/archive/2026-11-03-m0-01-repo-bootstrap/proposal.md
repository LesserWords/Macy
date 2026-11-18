## Why

Plan item **M0-01** (docs/plans/M0.md). Every later PR needs one place to run quality gates, a CI pipeline that enforces them, secret scanning, and the OpenSpec workflow. ADRs: 006, 019, 020.

## What Changes

- Python project managed by uv (`pyproject.toml`, Python 3.12+), `engine` package skeleton.
- ruff (lint + format + complexity limits) and mypy `--strict`.
- `scripts/check.sh` as the single gate entry point; pre-commit with ruff, mypy, gitleaks.
- OpenSpec initialised; `openspec validate --all --strict` in CI.
- GitHub Actions `ci.yml` (jobs: `check`, `test`, `openspec-validate`), PR template, Dependabot.

Out of scope: complexipy, vulture, jscpd, import-linter, coverage gates (M0-02); Docker (M0-03); branch protection is applied in GitHub settings when the remote exists.

## Impact

- New: `pyproject.toml`, `uv.lock`, `engine/`, `tests/`, `scripts/check.sh`, `.pre-commit-config.yaml`, `.github/`, `openspec/`.
