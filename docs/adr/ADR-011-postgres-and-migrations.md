# ADR-011: PostgreSQL latest major + dbmate migrations + squawk lint

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** ADR-010, ADR-012 · TECH-RADAR

## Context
You asked for the latest PostgreSQL. PG 19 GA is scheduled for 2026-11-19 (RC1 has been out since 2026-10-15). We need repeatable, reviewable and safe schema changes.

## Options considered
| Option | Fit |
|---|---|
| **A. PG 19 (18 until GA) + dbmate (plain SQL) + squawk** | Native `uuidv7()` and `NULLS NOT DISTINCT`; plain SQL; unsafe DDL blocked |
| B. Alembic | Needs SQLAlchemy models we don't use |
| C. Plain SQL files with no runner | No tracking and no safety checks |
| D. Atlas / pgroll | Powerful; more than this team size needs |

## Decision
A.
- **Image:** official `postgres` Docker image pinned to an exact minor version; Dependabot proposes minor bumps. `pg_trgm` comes from contrib.
- **PG 19 timing:** develop on 18.x if M0 starts before 19 is released, and switch before M1 data exists.
- **Migrations:** dbmate runs as a Compose one-shot (`migrate`), and the committed `db/schema.sql` dump is diffed in review.
  - squawk runs in pre-commit and CI; breaking changes follow expand → migrate → contract.
- **Database roles:** `owner`, `app`, `retention`, `readonly`.

## Consequences
- Easier: a safe migration process from day one.
- Harder: early 19.x minor releases may have bugs. Mitigation: if GA slips into M4, upgrade production only from 19.1.

## Revisit when
Zero-downtime migrations matter (pgroll), or the team grows.

## Action items
- [x] M0-03: Compose `db` + `migrate`, squawk hook
- [ ] M0-04: first migrations
