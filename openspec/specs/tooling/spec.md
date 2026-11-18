# tooling Specification

## Purpose
Enforces automated quality, security, complexity, architecture, coverage, and specification gates across local pre-commit and CI workflows.

## Requirements

### Requirement: Single quality-gate entry point
The repository SHALL provide `scripts/check.sh` that runs every enabled quality gate and exits non-zero if any gate fails. Pre-commit and CI SHALL use the same gate definitions.

#### Scenario: Gates pass on clean code
- **WHEN** `scripts/check.sh` runs on code that satisfies lint, format, type and spec rules
- **THEN** it exits with status 0

#### Scenario: A gate fails
- **WHEN** any gate reports a violation
- **THEN** `scripts/check.sh` exits non-zero and CI marks the `check` job as failed

### Requirement: Secrets are blocked before commit
The repository SHALL run gitleaks on every commit and in CI.

#### Scenario: Planted token
- **WHEN** a staged file contains a credential pattern such as a GitHub personal access token
- **THEN** the gitleaks hook reports the leak and the commit is blocked

### Requirement: Spec changes are validated
CI SHALL run `openspec validate --all --strict` on every pull request.

#### Scenario: Malformed change
- **WHEN** a change's spec delta lacks a scenario for a requirement
- **THEN** validation fails and the `openspec-validate` job fails

### Requirement: Cognitive complexity, dead code, duplication, and boundary gates
`scripts/check.sh` and CI SHALL enforce cognitive complexity (`complexipy` ≤ 15), dead code detection (`vulture` with `min_confidence = 60` and `vulture_whitelist.py`), copy-paste detection (`jscpd` 0 % threshold / 0 new clones with minimum 50 tokens), and module import contracts (`import-linter`).

#### Scenario: Static analysis gates pass on clean codebase
- **WHEN** `scripts/check.sh` runs on code with cognitive complexity ≤ 15, no unused symbols at ≥ 60 % confidence, 0 token clones (≥ 50 tokens), and valid import boundaries
- **THEN** `complexipy`, `vulture`, `jscpd`, and `lint-imports` each exit with status 0

#### Scenario: Static analysis gate blocks a violation
- **WHEN** code introduces a function exceeding complexity limits, an unwhitelisted unused function, a duplicated block above threshold, or a forbidden/cyclic import
- **THEN** the corresponding gate exits non-zero and names the offending symbol or file

### Requirement: Branch coverage, diff coverage, and deterministic property testing
The test suite SHALL enforce ≥ 90 % overall branch coverage via `pytest-cov`, 100 % diff coverage on changed lines via `diff-cover`, and a deterministic Hypothesis `ci` profile (`derandomize=True`).

#### Scenario: Coverage gates pass on tested changes
- **WHEN** `pytest` and `diff-cover` run on changes where ≥ 90 % of branches and 100 % of modified lines in `engine/` are covered
- **THEN** both coverage commands exit with status 0 and emit `coverage.xml` and `artifacts/coverage.json`

#### Scenario: Untested changed line fails diff-cover
- **WHEN** a changed line in `engine/` is not executed by any test
- **THEN** `diff-cover` exits non-zero reporting < 100 % diff coverage

#### Scenario: Hypothesis CI profile is deterministic
- **WHEN** tests run under the `ci` Hypothesis profile
- **THEN** Hypothesis executes with `derandomize=True` so property tests are reproducible across runs

### Requirement: CRAP-style hotspot report and trend artefacts
The repository SHALL provide a hotspot script that combines per-function complexity and line/branch coverage into a CRAP-style score, ranks the top 10 hotspots, and writes JSON trend artefacts for CI upload.

#### Scenario: Hotspot score calculation and ranking
- **WHEN** the hotspot script reads complexity and coverage JSON reports
- **THEN** it computes `CRAP(m) = comp(m)^2 * (1 - cov(m))^3 + comp(m)` for each function, writes the ranked top-10 JSON report, and exits with status 0

### Requirement: Live E2E test suite and nightly workflow
The repository SHALL provide a live E2E test suite executing journeys J0 through J4 against staging, with a budget guard fixture that aborts the suite if spend exceeds the campaign cap, 1 retry, failure trace and screenshot capture, and a nightly GitHub Actions workflow (`e2e-live.yml`).

#### Scenario: Live E2E suite verifies user journeys J0 through J4
- **WHEN** the live E2E suite runs against an active application instance
- **THEN** it executes authentication (J0), campaign configuration and language toggle (J1), source management (J2), pipeline execution with report generation (J3), and topic approval (J4)

#### Scenario: Budget overrun aborts the test suite
- **WHEN** the cumulative spend during a live test run exceeds the campaign spend cap
- **THEN** the budget guard fixture raises an error and fails the suite immediately

#### Scenario: Forced failure retains trace and screenshot artefacts
- **WHEN** a test in the live E2E suite encounters a failure
- **THEN** Playwright retains trace archives and screenshots in the results directory for diagnostic upload

#### Scenario: Workflow provides nightly cron and manual dispatch
- **WHEN** `.github/workflows/e2e-live.yml` is parsed
- **THEN** it defines a scheduled cron at 03:00 UTC and a `workflow_dispatch` trigger with artifact upload on failure

### Requirement: MCP package enforces services-only import contract
`import-linter` SHALL forbid `engine.mcp` from importing `engine.db` (directly or transitively through non-service helpers), matching `engine.cli` and `app.py` (`ADR-014`, `ADR-027`).

#### Scenario: Direct or transitive DB import inside engine.mcp is blocked
- **WHEN** `uv run lint-imports` runs via `scripts/check.sh` or CI
- **THEN** `engine.mcp` satisfies `"MCP imports services and contracts only"` (`forbidden_modules = ["engine.db"]`), and introducing an `import engine.db` in `engine/mcp/` causes `lint-imports` to exit non-zero
