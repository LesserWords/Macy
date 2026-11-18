## ADDED Requirements

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
