# webhooks Delta Specification

## ADDED Requirements

### Requirement: Outbound run.blocked lifecycle event support
The system SHALL expose `run.blocked` (emitted by `engine/pipeline.py` when a run blocks) as a first-class selectable event type in the Webhooks subscription UI and document it consistently in `docs/guides/user-guide.md` and `docs/adr/ADR-017-webhooks.md`.

#### Scenario: User subscribes to run.blocked lifecycle event and receives signed webhook
- **WHEN** an owner creates a webhook subscription selecting `run.blocked` in the Webhooks UI and a pipeline run transitions to `blocked`
- **THEN** a signed `run.blocked` delivery is queued in `webhook_deliveries` and delivered to the subscriber endpoint
