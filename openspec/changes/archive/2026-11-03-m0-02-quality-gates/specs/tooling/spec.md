## ADDED Requirements

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
