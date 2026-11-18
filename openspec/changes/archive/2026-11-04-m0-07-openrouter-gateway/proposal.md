# Proposal

## Why

Plan item **M0-07** (docs/plans/M0.md). Implement the LLM port with OpenRouter plugin, Instructor structured outputs helper with validation retries and prompt caching, and cost logging to `usage_ledger` and `llm_payloads`. Features: F05, F17. ADRs: 002, 007, 016, 023.

## What Changes

- Conduct the ADR-007 spike via `scripts/spike_openrouter.py` verifying exact model ids (`anthropic/claude-opus-5.5`, `anthropic/claude-haiku-5.5`), strict schemas via Instructor, prompt caching prefix, and `usage.cost`; update `docs/adr/ADR-007-llm-gateway-openrouter.md`.
- Implement `engine/integrations/openrouter.py` defining `OpenRouterLLM` and its `Settings` (API key, model per stage, data collection policy, base URL).
- Register `openrouter` plugin under `[project.entry-points."content_engine.llm"]` in `pyproject.toml`.
- Implement `engine/llm.py` with `structured_call(stage, model_cls, prompt, input, ...)` providing:
  - up to 2 validation retries with error feedback;
  - JSON-mode fallback on strict schema failure;
  - stable cached prefix with `cache_control: {"type": "ephemeral"}`;
  - `session_id = run_id` for sticky OpenRouter routing;
  - `provider: {"data_collection": "deny", "require_parameters": true}`.
- Update `engine/db.py` to record `usage_ledger` rows with exact numeric cost and store debug request/response in `llm_payloads`.
- Add recorded HTTP fixtures (pytest-recording/vcrpy) so `test_contract_suites.py` passes for `OpenRouterLLM` alongside `FakeLLM`.
- Add test suite in `tests/test_llm.py` asserting structured calls, retries, fallbacks, and exact ledger/payload recording.

Out of scope:
- Full pipeline stage runner (M1-10).
- Fallback `anthropic_direct` plugin (only needed if OpenRouter fails spike).
- UI spend caps configuration screen (M1-10, M4-07).

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `integrations`: Add specifications for OpenRouter LLM plugin, schema-enforced completions, and recorded contract suite.
- `spend`: Add specifications for LLM call cost recording in usage ledger and debug payload archiving.

## Impact

- New: `engine/integrations/openrouter.py`, `engine/llm.py`, `scripts/spike_openrouter.py`, `tests/test_llm.py`, `tests/cassettes/`.
- Modified: `engine/db.py`, `pyproject.toml`, `docs/adr/ADR-007-llm-gateway-openrouter.md`, `tests/test_contract_suites.py`.
