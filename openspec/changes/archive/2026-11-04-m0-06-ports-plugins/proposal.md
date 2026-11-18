# Proposal

## Why

Plan item **M0-06** (docs/plans/M0.md). Establish integration ports (`typing.Protocol`), plugin discovery via entry points, test double fakes, contract suites, and encrypted per-workspace configuration. Features: F05. ADRs: 005, 008.

## What Changes

- Write `engine/integrations/ports.py` with 7 `typing.Protocol` port definitions (`LLM`, `FeedSource`, `NewsSearch`, `Extractor`, `KeywordProvider`, `Publisher`, `AnalyticsSource`).
- Write `engine/integrations/registry.py` for runtime plugin discovery via standard Python entry points, including `IntegrationMissing(port)` exception.
- Write `engine/integrations/fakes.py` with in-memory implementations of all 7 ports with Pydantic `Settings`.
- Register fakes in `pyproject.toml` entry points and implement `cli plugins list`.
- Write contract suite harness parametrised across registered plugins and fakes.
- Write `plugin_config(conn, *, workspace_id, port)` with Fernet encryption / MultiFernet key rotation, ensuring secrets are masked and omitted from representations.

Out of scope: OpenRouter production plugin (M0-07), live third-party vendor plugins (M1–M4).

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `integrations`: Add specifications for protocol ports, entry point discovery, fake adapters, contract suite verification, and MultiFernet key rotation.

## Impact

- New: `engine/integrations/ports.py`, `engine/integrations/registry.py`, `engine/integrations/fakes.py`, `engine/integrations/config.py`, `tests/test_integrations.py`, `tests/test_contract_suites.py`.
- Modified: `engine/cli.py`, `pyproject.toml`, `openspec/specs/integrations/spec.md`.
