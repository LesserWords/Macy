## 1. OpenRouter spike and plugin adapter

- [x] 1.1 Write failing test asserting OpenRouter plugin discovery, Settings validation, and healthcheck → verify: tests fail
- [x] 1.2 Implement `scripts/spike_openrouter.py` testing Opus 5.5 and Haiku 5.5 models, caching, and usage cost, and document spike findings in `docs/adr/ADR-007-llm-gateway-openrouter.md` → verify: spike runs and ADR-007 is updated
- [x] 1.3 Implement `engine/integrations/openrouter.py`, register entry point in `pyproject.toml`, and wire recorded HTTP cassette in `tests/test_contract_suites.py` → verify: `test_contract_suites.py` passes for all registered plugins

## 2. Instructor structured call helper

- [x] 2.1 Write failing tests in `tests/test_llm.py` asserting `structured_call` schema enforcement, validation retry with feedback, JSON fallback, cached prefix, and session_id → verify: tests fail
- [x] 2.2 Implement `engine/llm.py` using Instructor over OpenRouter with retry loop, JSON fallback, and sticky session routing → verify: tests pass

## 3. Usage ledger and payload recording

- [x] 3.1 Write failing tests in `tests/test_llm.py` asserting exact numeric cost in `usage_ledger` and payload persistence in `llm_payloads` → verify: tests fail
- [x] 3.2 Implement `engine/db.py` usage and payload recording functions while preserving tenant scoping invariants → verify: all tests pass, check.sh is green with 100% diff coverage
