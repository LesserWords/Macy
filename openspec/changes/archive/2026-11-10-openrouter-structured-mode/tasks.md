# Tasks

## 1. Structured mode

- [x] 1.1 Write failing test at the HTTP boundary asserting `OpenRouterLLM.complete` sends `response_format.type = json_schema` and no `tool_choice` — verify: `uv run pytest tests/test_llm.py -k json_schema_mode --no-cov` fails before the change
- [x] 1.2 Build the Instructor client with `Mode.JSON_SCHEMA` — verify: the test passes

## 2. Fallback

- [x] 2.1 Write failing tests asserting the fallback logs a warning and sends the response schema — verify: `uv run pytest tests/test_llm.py -k fallback_warns --no-cov` fails before the change
- [x] 2.2 Add the warning and schema message in `_execute_openrouter` — verify: the tests pass

## 3. Verification

- [x] 3.1 Live `structured_call` succeeds on Haiku 5.5 and Opus 5.5 without using the fallback — verify: no fallback warning logged
- [x] 3.2 Quality gates — verify: `sh scripts/check.sh`
