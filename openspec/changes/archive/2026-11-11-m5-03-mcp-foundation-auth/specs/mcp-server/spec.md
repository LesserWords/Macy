# mcp-server Specification

## ADDED Requirements

### Requirement: Dual-transport MCP initialization and stdio framing protection
The system SHALL expose an MCP server over local `stdio` and containerized `streamable-http` (`/mcp`, with unauthenticated `/healthz`) adhering to Model Context Protocol JSON-RPC 2.0, while preventing logs or stdout prints from corrupting `stdio` protocol frames (`ADR-027`).

#### Scenario: Clean stdio handshake advertises protocol capabilities
- **WHEN** an MCP client connects and issues `initialize` over `stdio`
- **THEN** the server SHALL respond on stdout with valid JSON-RPC 2.0 protocol metadata advertising `tools`, `resources`, and `prompts` capabilities, while routing Python logging and stray prints to `stderr`

#### Scenario: Streamable HTTP health endpoint responds cleanly
- **WHEN** HTTP `GET /healthz` is requested on the HTTP MCP server
- **THEN** it SHALL return HTTP `200` JSON status `ok` without requiring Bearer credentials

### Requirement: Non-production mock mode with strict production and outage refusal
The system SHALL provide an explicit `--mock` (or `MACY_MCP_STANDALONE=1`) offline demo workspace (`acme-demo`) for local dev/test use ONLY, and MUST refuse `--mock` in staging/production or silently degrading on Postgres failure (`ADR-027`, `F01 AC6`).

#### Scenario: Explicit mock mode boots demo general store in non-production
- **WHEN** `content-engine mcp --mock` executes while `APP_ENV` is neither `staging`, `production`, nor `prod`
- **THEN** the server SHALL initialize an in-memory general store (`acme-demo`, `owner@example.com`) backed by test doubles and log a diagnostic notice to `stderr`

#### Scenario: Mock mode is refused in staging and production
- **WHEN** `--mock` or `MACY_MCP_STANDALONE=1` is supplied while `APP_ENV` or `ENV` is `staging`, `production`, or `prod`
- **THEN** the CLI and server SHALL fail fast with a non-zero exit code before accepting MCP traffic

#### Scenario: Database outage fails cleanly instead of falling back to mock
- **WHEN** the server starts without `--mock` and PostgreSQL is unreachable or misconfigured
- **THEN** the server SHALL exit non-zero cleanly and SHALL NOT silently substitute demo data

### Requirement: Fail-closed caller authentication and identity pinning
The system SHALL enforce fail-closed Bearer token authentication on `streamable-http` and tiered caller identity resolution on `stdio`, blocking fake identities in production and preventing caller impersonation (`ADR-013`, `ADR-027`, `F01 AC6`).

#### Scenario: Streamable HTTP rejects missing, empty, or wrong Bearer tokens
- **WHEN** an HTTP client accesses `/mcp` without a valid `Authorization: Bearer <token>` matching configured `MCP_AUTH_TOKEN` (verified in constant time)
- **THEN** the server SHALL fail closed with HTTP `401` and invoke zero MCP tool/resource handlers

#### Scenario: HTTP transport startup fails closed when auth config is incomplete
- **WHEN** the MCP server starts in `streamable-http` mode while `MCP_AUTH_TOKEN` or `MCP_ACTOR_EMAIL` is unset or placeholder (`CHANGE_ME`) in `staging`/`production`/`prod`
- **THEN** server startup SHALL abort non-zero cleanly before binding traffic

#### Scenario: Pinned transport identity rejects spoofed tool arguments
- **WHEN** an MCP call arrives over authenticated `streamable-http` pinned to `MCP_ACTOR_EMAIL` and supplies a conflicting `actor_email` or `actor_user_id` argument
- **THEN** the server SHALL reject the spoofed override with `PERMISSION_DENIED` rather than switching caller identity

#### Scenario: Stdio resolves identity cleanly without silent owner privilege escalation
- **WHEN** an MCP tool is invoked over `stdio`
- **THEN** identity resolves from `actor_email` -> `MACY_USER_EMAIL` -> `MACY_ACTOR_USER_ID` -> `MACY_TEST_USER_EMAIL` (rejected with `FakeIdentityForbiddenError` in `staging`/`production`/`prod`), falling back to the workspace owner ONLY when `--allow-owner-fallback` is explicitly set in non-production environments

### Requirement: Workspace member boundary, CLI entrypoint, and scrubbed telemetry
Every workspace-scoped MCP operation SHALL enforce `require_member`, operate strictly through `engine.services` and `engine.contracts`, emit Sentry telemetry tagged with `mcp_tool` and scrubbed of secrets, and expose `content-engine mcp` in CLI (`ADR-014`, `ADR-023`, `ADR-027`).

#### Scenario: Non-member caller is refused with structured permission error
- **WHEN** a resolved authenticated user invokes a workspace-scoped MCP handler for a workspace where they hold no membership
- **THEN** the server SHALL return structured MCP error `PERMISSION_DENIED` without mutating state

#### Scenario: MCP failures report scrubbed Sentry telemetry
- **WHEN** an unhandled error occurs inside an MCP tool execution while `SENTRY_DSN` is set
- **THEN** Sentry receives the exception tagged with `mcp_tool`, `workspace_id`, and optional `run_id` with `MCP_AUTH_TOKEN` and `Bearer` strings redacted

#### Scenario: CLI check flag validates startup readiness without hanging
- **WHEN** `content-engine mcp --check` executes (with `--mock` in dev, or live DB config)
- **THEN** it validates dependencies, configuration, and identity/DB connectivity and exits `0` cleanly
