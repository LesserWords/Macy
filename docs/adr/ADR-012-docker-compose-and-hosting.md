# ADR-012: Docker + Compose in every environment; hosting on the existing VPS

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F20, ADR-011, ADR-018, ADR-019 · PRD §6

## Context
You want every deploy to run on Docker and Compose, and you already have a standard VPS. The stack has a web UI, a scheduled worker that runs for 10–30 minutes, Postgres, and HTTPS.

## Options considered
| Option | Est./month | Fit |
|---|---|---|
| **A. Existing VPS + Compose + Caddy; GitHub Actions deploys over SSH** | $0–5 extra | You already have it; the whole stack fits |
| B. PaaS with managed Postgres | $20–50 | Less ops; different topology from dev |
| C. Kubernetes / serverless | — | Not needed; long-running steps don't fit serverless |

## Decision
A.
- **One image** for app and worker.
- **Compose files:**
  - `compose.yaml` (base: db, migrate, app, worker, plus an optional `rsshub` profile);
  - `compose.override.yaml` (dev);
  - `compose.e2e.yaml` (CI: fakes, webhook receiver, fake OIDC);
  - `compose.prod.yaml` (caddy, backup, pinned tags, `unless-stopped`).
- **Two projects on the VPS:** `prod` and `staging`, each with its own `.env`, database and subdomain.
- **Rules:** every service has a healthcheck, images are pinned, and the database port is never exposed outside dev.
- **Minimum box:** 2 vCPU, 4 GB RAM, 40 GB disk.
- **Backups:** nightly `pg_dump` to offsite storage, 14 days kept, one restore test.
- **Ops:** unattended OS security updates.

## Consequences
- Easier: the same artefact and topology everywhere, and no host-installed runtimes.
- Harder: we own OS patching and backups. The VPS is a single point of failure, which the weekly cadence tolerates.

## Revisit when
The VPS can't hold both stacks, more than one worker is needed, or an SLA is promised. Then move staging off the VPS or use a managed database.

## Action items
- [x] M0-03: Dockerfile + 4 Compose files, `docker compose config` in CI
- [x] M2-06: staging on the VPS + Caddy + deploy workflow (scope change S1)
- [x] M4-06: prod project + backups + restore test
