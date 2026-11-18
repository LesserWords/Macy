# ADR-010: Data model principles

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** all features · [DATA-MODEL.md](../architecture/DATA-MODEL.md)

## Context
The data must survive retries, run on several workers, keep evidence for every claim, evolve its contracts, and keep customers isolated. The earlier draft had a missing `runs` table, three overlapping logs, and provenance stored only inside JSON.

## Options considered
| Option | Fit |
|---|---|
| **A. Relational core with typed columns + JSONB contract payloads (`schema_version`) + database-enforced invariants** | Integrity in the database; contracts can evolve |
| B. Document store (everything JSONB) | Flexible, but integrity is left to the application |
| C. ORM-first (SQLAlchemy models) | More code; harder to express composite-FK tenancy |

## Decision
A. The principles (P1–P8 in DATA-MODEL §1):
- **Keys:** `uuidv7()` primary keys, with composite tenant FKs `(workspace_id, id)`.
- **Status:** `text` + `CHECK` with guarded `UPDATE … WHERE status = $expected`.
- **Immutable history:** briefs and drafts are versioned; `events` is append-only and serves as both audit log and webhook outbox.
- **Uniqueness:** idempotency comes from unique keys; one active run per campaign is enforced by a partial unique index.
- **Money:** `numeric` in `usage_ledger`.
- **Provenance:** `draft_claims` with `CHECK (kind <> 'fact' OR document_id IS NOT NULL)`.
- **Evidence vs. runs:** `documents` (evidence) are kept separate from `research_records` (what a run saw).
- **Retention:** purgeable tables and columns (ADR-024).
- **Data access:** psycopg 3 with SQL kept in `engine/db.py` only.

## Consequences
- Easier: a bug can't cross tenants or store an unsourced fact; queues need no broker.
- Harder: hand-written SQL, and contracts need upcasters when `schema_version` changes.

## Revisit when
`documents` grows beyond ~10M rows (then partition), semantic dedup becomes necessary (pgvector), or research is shared across workspaces (then a global `documents` table).

## Action items
- [x] M0-04: schema v1 as dbmate migrations
- [ ] M1-07 … M4-05: each feature adds its tables via migration + squawk
