## ADDED Requirements

### Requirement: Safe outbound HTTP requests (safe_http)
The system SHALL validate all outbound user-supplied URLs and refuse requests to private, loopback, link-local, and metadata ranges, enforcing HTTPS in production, a 10s timeout, a 1 MB response cap, and blocking cross-host redirects.

#### Scenario: Refuse private, loopback, and cloud metadata addresses
- **WHEN** an outbound HTTP request targets a private IP, loopback, or metadata service (`169.254.169.254`, `127.0.0.1`, `10.0.0.0/8`, `::1`)
- **THEN** the request is refused with an unsafe URL error before any connection is made

#### Scenario: Enforce HTTPS in production
- **WHEN** an outbound HTTP request is made using plain HTTP while in production environment
- **THEN** the request is refused requiring HTTPS

#### Scenario: Enforce response size cap and block cross-host redirects
- **WHEN** a remote server redirects to a different host or returns more than 1 MB of response data
- **THEN** the request is aborted

### Requirement: Sentry exception tracking with secret scrubbing
The system SHALL capture unhandled exceptions in Sentry, tag events with workspace and run IDs, and scrub all credentials and sensitive tokens before sending.

#### Scenario: Scrubber strips credentials and authorization headers
- **WHEN** an exception event contains API keys, passwords, or Authorization headers
- **THEN** the `before_send` hook scrubs all sensitive values

#### Scenario: Exceptions tagged with workspace and run identifiers
- **WHEN** an exception occurs within an active pipeline execution
- **THEN** the event is tagged with `workspace_id` and `run_id`

### Requirement: Heartbeat run monitoring
The system SHALL support pinging a healthcheck heartbeat URL upon run completion to monitor scheduled run execution.

#### Scenario: Ping heartbeat on run completion and no-op when unset
- **WHEN** a pipeline run completes
- **THEN** a heartbeat ping is dispatched to the configured URL, or safely skipped if no URL is set
