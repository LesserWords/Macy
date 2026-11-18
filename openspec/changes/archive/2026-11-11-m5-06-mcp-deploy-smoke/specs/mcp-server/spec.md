# mcp-server Delta Specification

## ADDED Requirements

### Requirement: MCP client documentation and containerized E2E verification
The system SHALL document MCP client configurations in `docs/MCP.md` and verify containerized `streamable-http` MCP execution end-to-end both offline in Compose (`test_journey_mcp_e2e.py`) and against live staging (`test_live_e2e_mcp.py`, `@pytest.mark.live_e2e`) (`F21 AC9, AC10`, `ADR-018`, `ADR-027`).

#### Scenario: Client configuration guide documents stdio and HTTPS Bearer endpoints
- **WHEN** a developer opens `docs/MCP.md`
- **THEN** it provides accurate setup snippets for Antigravity, Claude Desktop, Claude Code, and Cursor covering both local `stdio` (`--mock` and Postgres) and HTTPS `streamable-http` (`/mcp` with `Authorization: Bearer`)

#### Scenario: Offline E2E proves containerized MCP lifecycle against Postgres and webhook receiver
- **WHEN** `tests/test_journey_mcp_e2e.py` executes against the Compose E2E stack (`compose.yaml` + `compose.e2e.yaml` with `mcp` profile active)
- **THEN** authenticated MCP HTTP calls traverse discovery -> Gate 1 (`'scored'` -> `'approved'`) -> drafting -> claim verification -> Gate 2 (`'pass'` -> `'approved'`) -> publish (`http_webhook` -> `webhook-receiver`) without touching `evals/`

#### Scenario: Nightly live E2E verifies staging MCP fail-closed auth and read-only session
- **WHEN** `tests/test_live_e2e_mcp.py` runs under `e2e-live.yml` against `STAGING_APP_URL`
- **THEN** unauthenticated `POST /mcp` returns HTTP `401` and authenticated `initialize` + `macy_list_workspaces` succeeds within budget cap
