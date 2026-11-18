## Why

Plan item **M0-04** (docs/plans/M0.md). Establish Schema v1 in PostgreSQL enforcing tenant isolation by construction, configuration storage, append-only immutable events, and exact monetary tracking in usage ledger. Features: F01, F17. ADRs: 009, 010, 011.

## What Changes

- Write dbmate migration `db/migrations/20261104000000_schema_v1.sql` implementing tables per DATA-MODEL §3: `workspaces`, `users`, `memberships`, `brand_profiles`, `campaigns`, `sources`, `workspace_integrations`, `webhook_subscriptions`, `events`, `webhook_deliveries`, `usage_ledger`, `llm_payloads`, `runs`.
- Configure database roles `owner`, `app`, `retention`, `readonly`, and enforce `REVOKE UPDATE, DELETE ON events FROM app`.
- Dump and commit `db/schema.sql` via dbmate.
- Implement `engine/db.py` connection management and queries.
- Add `tests/test_tenant_scoping.py` parsing SQL queries in `engine/db.py` ensuring every query filters by `workspace_id`, and verifying multi-workspace row isolation.
- Add `tests/test_schema_constraints.py` validating database-level constraints: composite FK cross-tenant rejections, `UNIQUE NULLS NOT DISTINCT` on `sources`, and `runs_one_active`.

Out of scope: domain contracts and packs loader (M0-05), port fakes and plugin registry (M0-06), LLM gateway integration (M0-07).

## Impact

- New: `db/migrations/20261104000000_schema_v1.sql`, `db/schema.sql`, `engine/db.py`, `tests/test_tenant_scoping.py`, `tests/test_schema_constraints.py`.
- Modified: `pyproject.toml`, `uv.lock`, `docs/adr/ADR-009-multi-workspace-foundations.md`, `docs/adr/ADR-010-data-model-principles.md`.
