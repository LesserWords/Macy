# mcp-server Delta Specification

## ADDED Requirements

### Requirement: Capability-scoped MCP tool exposure and stateless mode
The MCP server (`engine/mcp/`) SHALL filter advertised tools according to the workspace's `enabled_capabilities` (returning `CAPABILITY_DISABLED` when a disabled tool is invoked directly) and SHALL support `--stateless` over `stdio` for `core-compliance` and `seo-audit` tools without PostgreSQL (`ADR-029`, `F22 AC2, AC3`).

#### Scenario: Disabled capability tool returns CAPABILITY_DISABLED
- **WHEN** a client invokes `macy_generate_draft` on a workspace where `editorial-pipeline` is disabled
- **THEN** the MCP server refuses execution with structured error `CAPABILITY_DISABLED`

#### Scenario: Stateless stdio MCP runs audit and compliance tools with zero database
- **WHEN** `content-engine mcp --stateless` handles `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, or `macy_import_draft`
- **THEN** each tool executes deterministically via in-memory contracts and returns structured JSON without opening a database connection
