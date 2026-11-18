## ADDED Requirements

### Requirement: Unified container image
The repository SHALL provide a multi-stage Dockerfile that builds a single minimal container image using uv and a non-root user for both application and worker runtimes.

#### Scenario: Image builds and imports engine
- **WHEN** the Docker image is built
- **THEN** it succeeds and running `python -c "import engine"` within the container exits 0

### Requirement: Declarative Compose environments
The repository SHALL provide `compose.yaml`, `compose.override.yaml`, `compose.e2e.yaml`, and `compose.prod.yaml` covering local development, offline E2E, and production deployment topologies.

#### Scenario: Local development stack starts healthy
- **WHEN** `docker compose up -d` executes
- **THEN** the `db`, `migrate`, `app`, and `worker` services initialize without errors, with healthchecks passing and no host-installed Python or Postgres required

#### Scenario: Compose configurations validate cleanly in CI
- **WHEN** `docker compose config` runs across all Compose file combinations (`compose.yaml`, with override, with e2e, and with prod)
- **THEN** all configurations parse without errors

### Requirement: Safe SQL migrations with squawk
The repository SHALL lint all migration files in `db/migrations/*.sql` with squawk in pre-commit, `scripts/check.sh`, and CI.

#### Scenario: Safe migration passes squawk
- **WHEN** migration files adhere to non-blocking DDL rules
- **THEN** squawk exits with status 0

#### Scenario: Dangerous DDL is blocked
- **WHEN** a migration contains unsafe DDL such as non-concurrent index creation on an existing table
- **THEN** squawk flags the violation and exits non-zero
