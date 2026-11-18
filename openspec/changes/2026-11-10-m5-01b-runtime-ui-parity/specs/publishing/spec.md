# publishing Delta Specification

## MODIFIED Requirements

### Requirement: Idempotent publication to external destinations
The system SHALL dispatch publish requests immediately upon Gate 2 draft approval when a `publisher` integration is configured, enforce `UNIQUE (workspace_id, draft_id, destination)`, force `mode = 'draft'` in staging, track failures for retry, emit `article.published`, and document auto-publish and container volume behavior in `docs/guides/user-guide.md`.

#### Scenario: Publishing creates publication record and emits event
- **WHEN** an approved draft is published successfully
- **THEN** publication details and URL are recorded in `publications`, draft status updates to `published`, and an `article.published` event containing `url` is emitted

#### Scenario: Draft mode prevents live publication
- **WHEN** publish mode is set to draft or the runtime environment is `staging` (even if `live` is requested)
- **THEN** staging forces `mode = 'draft'` on the outbound `PublishRequest` and the destination is instructed not to make content public

#### Scenario: Approving a draft publishes through configured publisher and emits article.published
- **WHEN** an authorized campaign creator approves a `pass` draft while a `publisher` integration is configured
- **THEN** a `PublishRequest` is sent to the configured publisher, `PublishedContent` is persisted in `publications` with `draft_id`, `destination`, `external_id`, `url`, and `mode`, and an `article.published` event containing `url` is emitted

#### Scenario: Retrying publish for the same draft and destination is idempotent
- **WHEN** publishing or retry executes more than once for the same `(workspace_id, draft_id, destination)`
- **THEN** no duplicate `publications` row is created (`UNIQUE (workspace_id, draft_id, destination)`) and existing publication metadata is updated cleanly

#### Scenario: User guide reflects Gate 2 auto-publish and container markdown export volumes
- **WHEN** an operator reads `docs/guides/user-guide.md` Draft Review instructions
- **THEN** it accurately states that clicking **Approve** with a configured publisher publishes immediately (reserving manual buttons for unconfigured/failed/idempotent retries) and explains persistent host volume mounting for `markdown_export` inside containers

### Requirement: Publisher port contract suite and n8n guide
The system SHALL verify every `PublisherPort` plugin against a reusable contract suite and document an accurate n8n `PublishedContent` integration flow in `docs/guides/n8n-publish.md`, including raw-body HMAC verification, `whsec_` vs non-`whsec_` base64 secret semantics, and `Idempotency-Key` deduplication (`ADR-021`).

#### Scenario: Contract suite passes for compliant plugins and rejects non-idempotent fake
- **WHEN** `test_publisher_contract_suite` runs against registered publisher plugins and a deliberately non-idempotent fake publisher
- **THEN** `fake`, `markdown_export`, and `http_webhook` pass all contract assertions and the deliberately non-idempotent fake fails the contract check

#### Scenario: Sample n8n publishing guide documents verified signature check and valid response
- **WHEN** the sample response and verification instructions in `docs/guides/n8n-publish.md` are checked
- **THEN** the response validates against `PublishedContent`, HMAC verification warns against `JSON.stringify` re-serialization and uses n8n raw request body bytes, secret handling accurately reflects `_normalize_webhook_secret`, and `Idempotency-Key` deduplication is concrete
