## ADDED Requirements

### Requirement: Database-enforced tenant isolation
The schema SHALL enforce tenant isolation by requiring `workspace_id` on all tenant-scoped tables and using composite foreign keys `(workspace_id, id)` for child relationships.

#### Scenario: Cross-workspace foreign key reference is rejected
- **WHEN** an insert or update attempts to reference a record belonging to another workspace
- **THEN** PostgreSQL rejects the statement with a foreign key violation

#### Scenario: Queries are scoped by workspace
- **WHEN** data access queries in `engine/db.py` execute
- **THEN** every query includes `workspace_id` filtering, and a query executed for workspace A returns 0 records belonging to workspace B

### Requirement: Immutable event stream and database role permissions
The schema SHALL define database roles (`owner`, `app`, `retention`, `readonly`) and prevent application code from modifying or deleting existing events.

#### Scenario: App role cannot update or delete events
- **WHEN** an UPDATE or DELETE statement executes on the `events` table under the `app` role
- **THEN** PostgreSQL raises a permission denied error

### Requirement: Data invariants and uniqueness constraints
The schema SHALL enforce single active run per campaign (`runs_one_active`) and domain uniqueness per workspace (`UNIQUE NULLS NOT DISTINCT`).

#### Scenario: Second active run is rejected
- **WHEN** a second run with status `queued` or `running` is inserted for the same campaign
- **THEN** PostgreSQL rejects the insertion with a unique constraint violation
