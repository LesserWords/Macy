# operations Delta Specification

## MODIFIED Requirements

### Requirement: Declarative Compose environments
The repository SHALL provide `compose.yaml`, `compose.override.yaml` (bind-mounting `./engine`, `./app.py`, `./.streamlit`, `./packs`, `./prompts`, `./locales`, and `./fixtures` read-only for live local development), `compose.e2e.yaml`, and `compose.prod.yaml`.

#### Scenario: Local development stack starts healthy
- **WHEN** `docker compose up -d` executes
- **THEN** the `db`, `migrate`, `app`, and `worker` services initialize without errors, with healthchecks passing and no host-installed Python or Postgres required

#### Scenario: Compose configurations validate cleanly in CI
- **WHEN** `docker compose config` runs across all Compose file combinations (`compose.yaml`, with override, with e2e, and with prod)
- **THEN** all configurations parse without errors

#### Scenario: Local development override mounts mutable repo assets read-only
- **WHEN** `docker compose -f compose.yaml -f compose.override.yaml config` is rendered
- **THEN** `app` mounts `./packs`, `./prompts`, `./locales`, and `./fixtures` `:ro` alongside `./engine`, `./app.py`, and `./.streamlit`, and `worker` mounts `./packs`, `./prompts`, and `./fixtures` `:ro` alongside `./engine`
