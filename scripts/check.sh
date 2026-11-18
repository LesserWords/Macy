#!/usr/bin/env sh
# Single entry point for all quality gates (ADR-006). Used by pre-commit and CI.
set -eu

uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run complexipy engine --max-complexity-allowed 15 --output-format json --output artifacts/complexipy.json
uv run vulture
uv run lint-imports
npx --yes jscpd@4.0.5 .
npx --yes squawk-cli --no-error-on-unmatched-pattern db/migrations/*.sql
uv run pytest
BASE_BRANCH="${DIFF_COVER_BASE:-origin/dev}"
if ! git rev-parse --verify "$BASE_BRANCH" >/dev/null 2>&1; then
  BASE_BRANCH="origin/main"
fi
uv run diff-cover coverage.xml --compare-branch="$BASE_BRANCH" --fail-under=100
uv run python scripts/hotspots.py artifacts/complexipy.json artifacts/coverage.json artifacts/hotspots.json
uv run python scripts/check_oss_hygiene.py
npx --yes @fission-ai/openspec@1.16.0 validate --all --strict --no-interactive
