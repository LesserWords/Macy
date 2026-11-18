# publishing Specification

## Purpose
Publishes approved articles to configured destinations (webhooks or file exports) and tracks published URLs.

## Requirements

### Requirement: Idempotent publication to external destinations
The system SHALL dispatch publish requests upon draft approval when a `publisher` integration is configured, enforce `UNIQUE (workspace_id, draft_id, destination)` idempotency in `publications`, force `mode = 'draft'` in staging environments, record failed deliveries with error details for retry, transition published drafts to `published`, and emit `article.published` with the published URL (`F16 AC1-AC5`).

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

### Requirement: Content inventory sitemap import
The system SHALL import existing blog articles from XML sitemaps or sitemap index files into `publications` with `mode = 'live'`, `status = 'published'`, and unique `(workspace_id, url)` for idempotent re-runs.

#### Scenario: Sitemap import persists published articles idempotently
- **WHEN** `import-sitemap` is executed with a valid blog sitemap URL
- **THEN** each article URL, title, and publication date is stored in `publications`, and subsequent re-runs do not create duplicate rows

### Requirement: Publisher port contract suite and n8n guide
The system SHALL verify every `PublisherPort` plugin against a reusable contract suite asserting `PublisherPort` protocol conformance, `Settings`, idempotency per `(draft_id, destination)`, non-live behavior in `draft` mode, and recorded `url`, while documenting a validated `PublishedContent` n8n integration flow (`ADR-021`, `M4-05 Steps 1 & 5`).

#### Scenario: Contract suite passes for compliant plugins and rejects non-idempotent fake
- **WHEN** `test_publisher_contract_suite` runs against registered publisher plugins and a deliberately non-idempotent fake publisher
- **THEN** `fake`, `markdown_export`, and `http_webhook` pass all contract assertions and the deliberately non-idempotent fake fails the contract check

#### Scenario: Sample n8n publishing guide documents verified signature check and valid response
- **WHEN** the sample response documented in `docs/guides/n8n-publish.md` is parsed
- **THEN** it validates strictly against the `PublishedContent` contract model

### Requirement: Markdown export publisher plugin
The system SHALL provide a `markdown_export` `PublisherPort` entry point writing a deterministic Markdown file with YAML front-matter (`title`, `meta_description`, `slug`, `locale`, `destination`, `mode`, `draft`, `draft_id`) and the article body inside a sandboxed export directory (`F16 AC6`).

#### Scenario: Markdown export writes expected front-matter and body idempotently
- **WHEN** `MarkdownExportPublisher.publish()` executes for an approved `ContentDraft`
- **THEN** a `.md` file matching the expected YAML front-matter (with `draft: true` in `draft` mode) and body is written without path traversal, and repeat calls overwrite idempotently with a consistent URL

### Requirement: Signed HTTP webhook publisher plugin
The system SHALL provide an `http_webhook` `PublisherPort` entry point sending Standard Webhooks-signed `PublishRequest` JSON via `safe_http`, validating the response as `PublishedContent`, and recording `failed` plus the error message on HTTP error, invalid JSON/schema, or network/SSRF failure (`F16 AC4`).

#### Scenario: Valid webhook response records publication and verifies signature
- **WHEN** `http_webhook` posts to a receiver that verifies `webhook-id`, `webhook-timestamp`, and `webhook-signature` and returns valid `PublishedContent` JSON
- **THEN** publication succeeds, `PublishedContent.url` is stored in `publications`, and `article.published` is emitted

#### Scenario: Malformed or HTTP 500 webhook response stores failed status and allows retry
- **WHEN** the webhook receiver returns HTTP 500, malformed JSON, or non-`PublishedContent` data
- **THEN** `publications` stores `status = 'failed'` with a non-empty `error`, leaves `url` nullable, and subsequent retry recovers to `published` when the endpoint succeeds
