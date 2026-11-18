# operations Specification Delta

## ADDED Requirements

### Requirement: Staging deploys automatically with one-shot migrations
The repository SHALL provide a deploy workflow and deploy script that, on merge to `main`, build and publish the image, run database migrations exactly once before replacing running services, health-check the result, and restore the previous app version when the post-deploy check fails.

#### Scenario: Failing migration leaves the previous version running
- **WHEN** a deploy runs with a migration that fails
- **THEN** the deploy exits non-zero and the previously running `app` and `worker` containers are not recreated

#### Scenario: Unhealthy release is rolled back
- **WHEN** the post-deploy smoke test fails
- **THEN** the previous app image tag is redeployed and the deploy exits non-zero

#### Scenario: Placeholder secrets are rejected
- **WHEN** the deploy preflight finds a missing, world-readable or placeholder-valued environment file
- **THEN** it exits non-zero before any container is touched

### Requirement: Staging stack exposes only HTTPS and no database port
The staging Compose combination SHALL publish only the reverse proxy ports, SHALL NOT publish the database port, SHALL give every long-running service a healthcheck, SHALL pin third-party images, and SHALL NOT pass the fake test identity variable to application containers.

#### Scenario: Staging configuration is safe by construction
- **WHEN** `docker compose -f compose.yaml -f compose.prod.yaml config` is rendered
- **THEN** only `caddy` publishes ports, no service sets `MACY_TEST_USER_EMAIL`, and every long-running service defines a healthcheck

### Requirement: Application container serves the admin UI
The runtime image SHALL contain the Streamlit entry point, locale catalogues, industry packs and prompts, and the `app` service SHALL run Streamlit with a healthcheck.

#### Scenario: App service reports healthy
- **WHEN** the `app` service starts
- **THEN** `/_stcore/health` responds `ok` and the container reports healthy

### Requirement: Operator runbook
The repository SHALL document deploy, rollback, logs, restart and first-time VPS preparation in `docs/runbook.md`.

#### Scenario: Runbook covers operational procedures
- **WHEN** an operator opens `docs/runbook.md`
- **THEN** it contains the deploy, rollback, logs, restart and VPS preparation procedures
