# integrations Delta Specification

## ADDED Requirements

### Requirement: OpenRouter structured calls use JSON-schema mode with a visible fallback
The OpenRouter LLM plugin SHALL request structured output through JSON-schema `response_format` rather than a forced tool call, and `structured_call` SHALL log a warning and include the response schema whenever it falls back to JSON mode.

#### Scenario: Structured completion requests JSON-schema output
- **WHEN** the OpenRouter adapter executes a structured completion
- **THEN** the request carries `response_format` of type `json_schema` and no `tool_choice`

#### Scenario: Fallback is visible and carries the schema
- **WHEN** the Instructor call fails and `structured_call` falls back to JSON mode
- **THEN** a warning is logged, the fallback request includes the response JSON Schema, and a validated result is returned
