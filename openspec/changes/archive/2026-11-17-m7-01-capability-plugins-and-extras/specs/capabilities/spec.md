# capabilities Delta Specification

## ADDED Requirements

### Requirement: Capability plugin discovery and workspace switchboard
The system SHALL discover capability plugins registered under `content_engine.capabilities` (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), organize optional dependencies under composable extras in `pyproject.toml`, enforce `core-compliance` as always enabled, and persist per-workspace capability selections in `workspaces.config.enabled_capabilities`.

#### Scenario: Discovering built-in capability plugins and composable extras
- **WHEN** `discover_capabilities()` runs against `pyproject.toml`
- **THEN** `core-compliance`, `seo-audit`, `editorial-pipeline`, and `route-monitors` are returned with declared ports and surface bindings, and `core-compliance` has `always_enabled = true`

#### Scenario: Updating workspace capability switchboard persists configuration
- **WHEN** a workspace owner updates enabled capabilities to `["seo-audit"]` via `services.set_enabled_capabilities`
- **THEN** `core-compliance` is automatically included alongside `seo-audit`, `workspaces.config.enabled_capabilities` is saved, and `workspace.capabilities_updated` is recorded in `events`

### Requirement: Stateless CLI and MCP execution for deterministic capabilities (Path B)
The system SHALL provide `--stateless` (and `MACY_STATELESS=true`) execution across CLI (`content-engine audit`, `content-engine import-draft`) and `stdio` MCP (`content-engine mcp --stateless`) so `core-compliance` and `seo-audit` execute with zero PostgreSQL connection or Docker container.

#### Scenario: Stateless CLI and MCP run SEO audit, crawl, and draft compliance with no database
- **WHEN** `content-engine audit <url> [--crawl] --stateless`, `content-engine import-draft <file> --stateless`, or `content-engine mcp --stateless` invokes `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, or `macy_import_draft` with `DATABASE_URL` unset
- **THEN** the checks, JSON-LD generation, single-URL audit, and bounded BFS crawl complete with exit status `0` and zero `psycopg` connections
