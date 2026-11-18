# Proposal: OpenRouter Structured Calls via JSON-Schema Mode

**Plan Item:** post-M5-01 fix · **Features:** F02 · **ADRs:** ADR-002, ADR-007

## Why

Live calls to `anthropic/claude-haiku-5.5` and `anthropic/claude-opus-5.5` with `provider.require_parameters: true` fail in Instructor's `TOOLS` mode with a 404 (`No endpoints found that support the provided 'tool_choice' value`, routing step `Filter by Tool Compatibility`). `engine.llm._execute_openrouter` catches the failure and silently switches to `json_object`, which loses Instructor's validation retries, gives the model no response schema, and leaves no signal that it happened. `OpenRouterLLM.complete` has no fallback, so it fails outright.

## What Changes

- Build the OpenRouter Instructor client in `Mode.JSON_SCHEMA`, verified live on both default models.
- Log a warning when `structured_call` uses the JSON fallback.
- Include the response JSON Schema in the fallback request.

## Capabilities

### Modified Capabilities

- `integrations`: OpenRouter structured completion mode and fallback behaviour.

## Impact

`engine/integrations/openrouter.py`, `engine/llm.py`, `tests/test_llm.py`. No schema, settings, or API changes.
