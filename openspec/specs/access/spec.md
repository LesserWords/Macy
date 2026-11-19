# access Specification

## Purpose
Defines database schema, role permissions, multi-tenant isolation, and data integrity constraints for the Content Intelligence Engine data model.

## Requirements

### Requirement: Database-enforced tenant isolation
The schema SHALL enforce tenant isolation by requiring `workspace_id` on all tenant-scoped tables and using composite foreign keys `(workspace_id, id)` for child relationships.

#### Scenario: Cross-workspace foreign key reference is rejected
- **WHEN** an insert or update attempts to reference a record belonging to another workspace
- **THEN** PostgreSQL rejects the statement with a foreign key violation

#### Scenario: Queries are scoped by workspace
- **WHEN** data access queries in `engine/db.py` execute
- **THEN** every query includes `workspace_id` filtering, and a query executed for workspace A returns 0 records belonging to workspace B

### Requirement: Immutable event stream and database role permissions
The schema SHALL define database roles (`owner`, `app`, `retention`, `readonly`), prevent application code from directly modifying or deleting existing events, grant `retention` the privileges required to purge expired data across `events`, `llm_payloads`, `webhook_deliveries`, and `documents` (`extracted_text`, `text_purged_at`), and provide a controlled `SECURITY DEFINER` function for scrubbing personal data in `events.payload` during LGPD user anonymisation (`F20 AC7, AC8`, `ADR-024`).

#### Scenario: App role cannot update or delete events
- **WHEN** an UPDATE or DELETE statement executes on the `events` table under the `app` role
- **THEN** PostgreSQL raises a permission denied error

#### Scenario: Retention role can purge expired records but cannot delete usage_ledger or documents rows
- **WHEN** the `retention` role executes retention cleanup queries
- **THEN** it can delete expired `events`, `llm_payloads` (joining `usage_ledger`), and `webhook_deliveries`, and update `documents.extracted_text` / `documents.text_purged_at`, while `DELETE` on `documents` or `usage_ledger` is denied

### Requirement: Data invariants and uniqueness constraints
The schema SHALL enforce single active run per campaign (`runs_one_active`) and domain uniqueness per workspace (`UNIQUE NULLS NOT DISTINCT`).

#### Scenario: Second active run is rejected
- **WHEN** a second run with status `queued` or `running` is inserted for the same campaign
- **THEN** PostgreSQL rejects the insertion with a unique constraint violation

### Requirement: Google OIDC authentication and workspace membership check
The system SHALL authenticate users via Google OIDC and restrict access exclusively to users holding active membership in the target workspace.

#### Scenario: Member signs in and accesses workspace
- **WHEN** a user with active membership in the workspace signs in
- **THEN** their email is displayed and access to workspace data is granted

#### Scenario: Non-member user is refused access
- **WHEN** an authenticated user has no membership in the workspace
- **THEN** access is denied and no workspace data is rendered

### Requirement: CLI owner seeding
The system SHALL provide a CLI command `add-owner <workspace_slug> <email>` to provision workspace owners and log an audit event.

#### Scenario: Add owner CLI provisions membership and logs event
- **WHEN** `macy add-owner <workspace_slug> <email>` is executed
- **THEN** the user and membership records exist and a `membership.created` event is recorded

### Requirement: Fake identity login restricted to non-production
The system SHALL permit simulated test identity authentication during offline E2E and dev/test environments, but MUST reject it in production environments.

#### Scenario: Fake identity rejected in production
- **WHEN** a fake identity login is attempted while `APP_ENV=production` or `ENV=prod`
- **THEN** the request is rejected with an authentication error

### Requirement: CLI workspace provisioning
The system SHALL provide `services.create_workspace` and `macy create-workspace <slug>` so operators can provision a production or staging workspace with its industry pack and timezone without executing raw SQL.

#### Scenario: Create workspace CLI provisions workspace idempotently or rejects duplicates cleanly
- **WHEN** `macy create-workspace acme-demo --name "Acme Demo" --pack general` runs
- **THEN** the workspace row exists with slug `acme-demo` and the configured industry pack and timezone

### Requirement: Built-in local admin authentication with automatic OIDC switching
The system SHALL support `AUTH_MODE=local | oidc | auto`, storing nullable `users.password_hash` via non-blocking migration, verifying local passwords using PBKDF2-HMAC-SHA256 (`600,000` iterations, constant-time `hmac.compare_digest`) with brute-force rate limiting (`5` failed attempts per `15m`), and selecting local auth in `auto` mode whenever `GOOGLE_CLIENT_ID` is unset (`ADR-030`, `F22 AC5`).

#### Scenario: Local admin authenticates without Google OAuth credentials
- **WHEN** `AUTH_MODE=auto` with `GOOGLE_CLIENT_ID` unset and an existing workspace member submits valid email and password
- **THEN** PBKDF2-HMAC-SHA256 verification succeeds and grants authenticated workspace access without requiring Google Cloud OIDC configuration

#### Scenario: Repeated failed local login attempts trigger rate-limit lockout
- **WHEN** 5 consecutive invalid passwords are submitted for an email within 15 minutes
- **THEN** subsequent login attempts within the lockout window are rejected with a structured rate-limit error
