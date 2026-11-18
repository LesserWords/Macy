# tooling Specification Delta

## ADDED Requirements

### Requirement: MCP package enforces services-only import contract
`import-linter` SHALL forbid `engine.mcp` from importing `engine.db` (directly or transitively through non-service helpers), matching `engine.cli` and `app.py` (`ADR-014`, `ADR-027`).

#### Scenario: Direct or transitive DB import inside engine.mcp is blocked
- **WHEN** `uv run lint-imports` runs via `scripts/check.sh` or CI
- **THEN** `engine.mcp` satisfies `"MCP imports services and contracts only"` (`forbidden_modules = ["engine.db"]`), and introducing an `import engine.db` in `engine/mcp/` causes `lint-imports` to exit non-zero
