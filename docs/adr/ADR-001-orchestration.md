# ADR-001: Orchestration — DB state machine + cron (DBOS as the upgrade path)

**Status:** A confirmed (post-M0-09 spike) · **Date:** 2026-11-04 · **Deciders:** Product owner
**Related:** F18, ADR-010, ADR-017 · PRD §5.1

## Context
The blueprint (§12) suggests n8n, Temporal or a custom runner. The workload is small:
- one batch per week per campaign;
- about 200 records and 1–3 articles per batch;
- two human approval gates, which can wait for days.

## Options considered
| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. Postgres status columns + cron + CLI re-run** | Low | $0 | Batch work and long human waits map naturally to rows in states |
| B. DBOS Transact: durable workflows as a Python library on our Postgres (queues, schedules, durable waits) | Low–Med | $0, no extra service | Could replace the hand-written runner, cron and retry code |
| C. Hatchet | Med | Extra engine service | Same idea as B, but needs a service to run |
| D. Temporal | High | Cluster or cloud | Built for mission-critical, polyglot scale; overkill here |
| E. n8n as orchestrator | Low–Med | Self-host | Logic lives in visual graphs, which are hard to version, test and type-check |

## Decision
A. The worker claims rows with `FOR UPDATE SKIP LOCKED`. Each stage moves rows from state X to state Y with guarded updates (ADR-010). Supercronic reads `campaigns.schedule_cron`.

**M0 spike outcome:** Option A is confirmed. The spike implemented the 2-stage toy pipeline (`research` -> `score`) under both approaches and proved that Option A achieves all required failure isolation, resume, and concurrency constraints with significantly lower operational overhead and without database privilege violations.

## M0-09 Spike Results & Evaluation

A 2-stage toy pipeline (`research` -> `score` with fakes) was implemented in `spikes/orchestration/` and verified via `tests/test_spike_orchestration.py` across both Option A (Postgres state machine) and Option B (DBOS Transact 3.4.0) under identical test criteria.

| Evaluation Criterion | Option A: Postgres State Machine | Option B: DBOS Transact | Assessment |
|---|---|---|---|
| **Python Lines of Code (LOC)** | ~140 lines | ~120 lines (workflow) + ~50 lines (config/lifecycle scaffolding) | Roughly equivalent; DBOS does not eliminate tracking logic for multi-item batch loops. |
| **Database Footprint & Migrations** | 0 extra schemas; standard domain tables (`runs`, `items`) | 1 extra schema (`dbos`), **70+ internal migrations**, **13 system tables** (`workflow_status`, `operation_outputs`, `streams`, etc.) | **Option A wins**. DBOS introduces heavy schema baggage and migration overhead. |
| **Database Permissions (Least Privilege)** | Standard `app` user with DML rights | **Requires DDL / superuser rights** at startup (`CREATE SCHEMA "dbos"`). Crashed with `permission denied for database macy` under `app` user. | **Option A wins**. DBOS violates ADR-011 and ADR-012 least-privilege security model. |
| **UI & SQL Transparency (Streamlit)** | Native SQL queries on domain status columns (`status = 'researched'`) | Opaque pickled/serialized state in `dbos.operation_outputs`; requires DBOS SDK clients or SaaS console. | **Option A wins**. Direct queryability in Streamlit with zero translation layer. |
| **Failure Isolation & Resume** | Handled natively via `FOR UPDATE SKIP LOCKED` and per-row `status = 'failed'` updates | Requires manual `try/except` in workflow loops; step replay works but depends on internal framework state. | **Option A wins** on explicitness and statelessness. |
| **Runtime Dependencies** | 0 new dependencies (pure `psycopg`) | `dbos`, `sqlalchemy`, `greenlet`, `croniter` + background queue threads | **Option A wins**. Keeps the runtime lightweight and predictable. |
| **Cloud/SaaS Leakage** | Fully self-hosted, air-gapped | Logs prompts to connect to DBOS SaaS Conductor (`console.dbos.dev`) | **Option A wins**. |

### Conclusion: Option A Confirmed
Option A is confirmed for production implementation in M1-10. It is simpler, introduces zero schema baggage, adheres strictly to least-privilege database roles, and seamlessly integrates with Streamlit and Supercronic cron. DBOS remains an upgrade path should Macy ever scale to multi-worker distributed clusters with complex per-step timeout DAGs.

## Consequences
- Easier: no extra infrastructure, all state can be queried with SQL, and a failed row is re-run on its own.
- Harder: retry policies are written by hand. Streamlit shows stage status, since there is no built-in dashboard.

## Revisit when
Any of:
- more than 1 run per day per campaign, or about 50 workspaces running in series (more than 1 day of total run time);
- more than 1,000 items fanned out in one run;
- per-step timeouts or retry policies beyond "re-run stage";
- more than one worker process.

When that happens, move to B.

## Action items
- [x] M0-09: DBOS spike and decision recorded in this ADR
- [x] M1-10: `runs` table, stage runner and scheduler (confirmed: state machine with SKIP LOCKED)
