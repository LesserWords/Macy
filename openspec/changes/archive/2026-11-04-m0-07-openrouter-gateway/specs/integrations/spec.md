## ADDED Requirements

### Requirement: OpenRouter LLM gateway plugin
The system SHALL provide an OpenRouter LLM plugin implementing the LLMPort protocol and supporting per-stage model selection.

#### Scenario: OpenRouter passes LLM contract suite with recorded HTTP
- **WHEN** contract test suite executes against OpenRouter LLM adapter
- **THEN** healthcheck succeeds and structured completion returns valid result dictionary
