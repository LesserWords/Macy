# Tasks

## 1. Holdout audit (before freeze)

- [x] 1.1 Audit every planted error and clean sentence against the full source with Sonnet 5.5; verify quotes in code — verify: audit file covers all 188 errors and 217 clean sentences
- [x] 1.2 Correct flagged errors and clean sentences; replace closing advice with sourced facts — verify: rebuild passes composition and number checks
- [x] 1.3 Regenerate the review sheet with exact passages and a change table — verify: every modified line listed

## 2. Detection metrics

- [x] 2.1 Write failing tests for single/multi TPR, TNR and Rogan-Gladen on hand-worked examples — verify: tests fail
- [x] 2.2 Implement in `method_report` — verify: tests pass

## 3. Public suite

- [x] 3.1 Write failing tests for stratified fixed-seed sampling, schema mapping, missing token and manifest check — verify: tests fail
- [x] 3.2 Implement loader and harness datasets — verify: tests pass; `uv run mypy`, `uv run ruff check .`
- [x] 3.3 Fetch Tier B and commit its manifest — verify: 150 samples, 75 per label
- [x] 3.4 Add the GitHub workflow and `evals/README.md` — verify: workflow YAML parses
