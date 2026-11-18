## 1. Schema migration and squawk validation

- [x] 1.1 Write failing test `tests/test_schema_constraints.py` asserting tables, composite FKs, and constraints exist in Postgres → verify: `uv run pytest` fails on missing tables
- [x] 1.2 Write migration `db/migrations/20261104000000_schema_v1.sql` and run `dbmate up` → verify: squawk is clean, and `db/schema.sql` is generated and committed

## 2. Database roles and immutable events permissions

- [x] 2.1 Write failing test asserting `app` role cannot UPDATE or DELETE `events` → verify: test fails before permissions revocation
- [x] 2.2 Grant roles and revoke UPDATE/DELETE on `events` from `app` → verify: test passes with permission denied error on UPDATE/DELETE

## 3. Tenant scoping and query verification

- [x] 3.1 Write failing AST tenant-scoping test `tests/test_tenant_scoping.py` asserting every query in `engine/db.py` includes `workspace_id` → verify: test fails on planted unscoped query
- [x] 3.2 Implement `engine/db.py` scoped queries and verify two seeded workspaces see zero rows from each other → verify: tenant scoping test passes

## 4. Constraint tests (composite FKs, sources, runs)

- [x] 4.1 Write constraint tests asserting composite FK cross-tenant rejection, `UNIQUE NULLS NOT DISTINCT` on `sources`, and `runs_one_active` → verify: each test asserts literal SQLSTATE and passes
