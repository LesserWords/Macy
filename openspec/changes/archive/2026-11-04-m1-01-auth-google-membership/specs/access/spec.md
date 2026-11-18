## ADDED Requirements

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
- **WHEN** `content-engine add-owner <workspace_slug> <email>` is executed
- **THEN** the user and membership records exist and a `membership.created` event is recorded

### Requirement: Fake identity login restricted to non-production
The system SHALL permit simulated test identity authentication during offline E2E and dev/test environments, but MUST reject it in production environments.

#### Scenario: Fake identity rejected in production
- **WHEN** a fake identity login is attempted while `APP_ENV=production` or `ENV=prod`
- **THEN** the request is rejected with an authentication error
