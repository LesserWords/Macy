# ADR-009: Multi-workspace foundations from day one

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F01, F03, F05, ADR-010, ADR-013, ADR-014 · [MVP-Product-Analysis](../architecture/MVP-Product-Analysis.md) · PRD §5.9

## Context
The MVP serves one customer (Acme Demo), but the aim is a product later. Retrofitting multi-tenancy means backfilling data, auditing every query, and risking data leaks between customers.

## Options considered
| Option | Cost now | Cost later |
|---|---|---|
| **A. Six foundations now (below)** | ~5–6 dev-days | Onboarding a customer, not a rewrite |
| B. Single-tenant MVP, refactor later | 0 | Weeks of migration plus a security audit |
| C. Full SaaS now (billing, roles, REST API, RLS) | Weeks | — (over-built) |

## Decision
A:
1. **Tenant key everywhere:** `workspace_id NOT NULL` on every customer table, enforced with composite FKs (ADR-010).
2. **Customer configuration in the database:**
   - Brand profile, campaign and sources live in Postgres, per workspace.
   - Industry packs, formats and prompts stay in git, because they are product assets.
3. **One source of truth for campaigns:** the `campaigns` table.
4. **Plugin configuration per workspace** (ADR-008).
5. **Access through memberships:** `users` + `memberships(role)`, with only the `owner` role in the MVP (ADR-013).
6. **Single entry point:** the UI, CLI and any future API call `engine/services.py` only (ADR-014).

## Consequences
- Easier: per-customer cost from day one; a second workspace is a matter of setup; shared research becomes possible later.
- Harder: every query and test carries `workspace_id`, and a CI test enforces it.

## Revisit when
Add Postgres row-level security when a second developer joins or at the first security review. Add more roles when a workspace has more than one person.

## Action items
- [x] M0-04: schema v1 + tenant-scoping test
- [x] M0-05: `services.py` + import-linter contracts
