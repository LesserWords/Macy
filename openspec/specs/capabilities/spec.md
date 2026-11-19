# capabilities Specification

## Purpose
Manages modular capability plugins (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), per-workspace feature switchboards, zero-Docker stateless CLI/MCP execution (`Path B`), and guided URL auto-seed onboarding (`Path A`) (`ADR-029`, `ADR-030`, `ADR-031`, `F22`).

## Requirements

### Requirement: Dual-path modular capability baseline (F22)
The system SHALL organize product features into discoverable capability plugins (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), allow per-workspace capability toggling (`Path A`), and provide zero-database stateless execution for `core-compliance` and `seo-audit` (`Path B`).

#### Scenario: Capability plugins and dual-path execution are supported
- **WHEN** `discover_capabilities()` inspects registered plugins or a user executes `audit` / `import-draft` via `--stateless` (`Path B`) or configures a workspace switchboard (`Path A`)
- **THEN** all 4 built-in capability plugins are recognized with `core-compliance` always enabled, stateless commands complete without PostgreSQL, and workspace switchboard updates persist in `workspaces.config`

### Requirement: Dynamic surface filtering across Admin UI, CLI, and MCP
The system SHALL filter visible Admin UI sidebar screens, active integration port requirements, and registered MCP tools according to the active workspace's `enabled_capabilities`, and SHALL reject direct invocations of tools belonging to disabled capabilities with `CAPABILITY_DISABLED`.

#### Scenario: Disabled capability hides UI screens and blocks MCP tool execution
- **WHEN** a workspace disables `editorial-pipeline`
- **THEN** Admin UI navigation omits editorial screens (`Weekly Report`, `Draft Review`, `Campaigns`, `Sources`), MCP discovery omits editorial tools for that workspace, and calling `macy_generate_draft` directly returns structured error `CAPABILITY_DISABLED`

### Requirement: Guided first-boot setup wizard and website URL auto-seeding (Path A)
The system SHALL provide an atomic onboarding bootstrap service (`services.bootstrap_onboarding`), website URL + sitemap auto-seeding (`autoseed_from_url`), CLI command `macy init`, and a bilingual (`pt-BR`/`en-US`) 3-step Setup Wizard when no workspaces exist.

#### Scenario: First-boot wizard or CLI init creates owner, capabilities, preset, and auto-seeded brand
- **WHEN** `services.bootstrap_onboarding` or `macy init` runs against an empty database with a target website URL
- **THEN** the owner user, workspace, `enabled_capabilities`, and integration preset are created in one transaction, and `autoseed_from_url` fetches the homepage and sitemap via `safe_http` to seed `brand_profiles` (`v1`) and `content_inventory`

### Requirement: Capability plugin discovery and workspace switchboard
The system SHALL discover capability plugins registered under `macy.capabilities` (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), organize optional dependencies under composable extras in `pyproject.toml`, enforce `core-compliance` as always enabled, and persist per-workspace capability selections in `workspaces.config.enabled_capabilities`.

#### Scenario: Discovering built-in capability plugins and composable extras
- **WHEN** `discover_capabilities()` runs against `pyproject.toml`
- **THEN** `core-compliance`, `seo-audit`, `editorial-pipeline`, and `route-monitors` are returned with declared ports and surface bindings, and `core-compliance` has `always_enabled = true`

#### Scenario: Updating workspace capability switchboard persists configuration
- **WHEN** a workspace owner updates enabled capabilities to `["seo-audit"]` via `services.set_enabled_capabilities`
- **THEN** `core-compliance` is automatically included alongside `seo-audit`, `workspaces.config.enabled_capabilities` is saved, and `workspace.capabilities_updated` is recorded in `events`

### Requirement: Stateless CLI and MCP execution for deterministic capabilities (Path B)
The system SHALL provide `--stateless` (and `MACY_STATELESS=true`) execution across CLI (`macy audit`, `macy import-draft`) and `stdio` MCP (`macy mcp --stateless`) so `core-compliance` and `seo-audit` execute with zero PostgreSQL connection or Docker container.

#### Scenario: Stateless CLI and MCP run SEO audit, crawl, and draft compliance with no database
- **WHEN** `macy audit <url> [--crawl] --stateless`, `macy import-draft <file> --stateless`, or `macy mcp --stateless` invokes `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, or `macy_import_draft` with `DATABASE_URL` unset
- **THEN** the checks, JSON-LD generation, single-URL audit, and bounded BFS crawl complete with exit status `0` and zero `psycopg` connections

### Requirement: Atomic onboarding bootstrap and init CLI (Path A)
The system SHALL provide `services.bootstrap_onboarding` and `macy init` to atomically provision the initial owner account, workspace, `enabled_capabilities`, and integration preset when no workspaces exist, refusing unauthenticated execution once a workspace is present (`ADR-031`, `F22 AC8`).

#### Scenario: Bootstrap onboarding creates owner, workspace, capabilities, and preset atomically
- **WHEN** `services.bootstrap_onboarding` or `macy init` runs against an empty database
- **THEN** the owner user, workspace, membership, selected `enabled_capabilities`, and integration preset commit in a single transaction, and subsequent unauthenticated calls are rejected
