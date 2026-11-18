# Proposal: M4-05 — Publisher port and plugins

## Why
Approved drafts passing Gate 2 (`M4-04`, `F15`) must now be delivered to the owner's configured publishing destination without binding the core to a single CMS (`ADR-021`, `F16`). The system needs a strict `PublisherPort` contract suite, real `markdown_export` and `http_webhook` entry-point plugins, idempotent persistence in `publications` (`UNIQUE (workspace_id, draft_id, destination)`), automatic approval-to-publish dispatch with `mode=draft` staging enforcement, retryable error storage, and documented n8n webhook publishing integration.

## What Changes
- **Schema Migration & DB Persistence (`db/migrations/`, `engine/db.py`):**
  - Add migration `20261109000002_update_publications_for_publishing.sql` extending `publications` to allow nullable `url` on failed attempts, store `error text`, enforce composite FK `(workspace_id, draft_id) REFERENCES drafts (workspace_id, id)`, and add `UNIQUE (workspace_id, draft_id, destination)` while preserving nullable `draft_id` sitemap inventory rows (`UNIQUE (workspace_id, url)`).
  - Add tenant-scoped `upsert_draft_publication()`, `get_publication_for_draft()`, `list_publications_for_draft()`, and `mark_draft_published()` in `engine/db.py`.
- **Publisher Contract Suite & Fakes (`tests/test_contract_suites.py`, `engine/contracts.py`, `engine/integrations/fakes.py`):**
  - Strengthen `FakePublisher` and `test_publisher_contract_suite` to enforce idempotency per `(draft_id, destination)`, non-live behavior under `mode="draft"`, and recorded `url`, plus add a negative test proving a non-idempotent fake fails the contract suite (`M4-05 Step 1`).
  - Add `PublishError` and validate `PublishedContent.status` (`published | scheduled | draft | failed`) in `engine/contracts.py`.
- **Publisher Plugins (`engine/integrations/markdown_export.py`, `engine/integrations/http_webhook.py`, `pyproject.toml`):**
  - Implement `MarkdownExportPublisher` (`markdown_export` entry point) writing deterministic YAML front-matter (`title`, `meta_description`, `slug`, `locale`, `destination`, `mode`, `draft`, `draft_id`) and Markdown body/FAQ/CTA safely inside `export_dir` (`F16 AC6`).
  - Implement `HttpWebhookPublisher` (`http_webhook` entry point) posting Standard Webhooks-signed (`webhook-id`, `webhook-timestamp`, `webhook-signature`, `Idempotency-Key`) `PublishRequest` payloads over `safe_http`, validating responses against `PublishedContent`, and raising `PublishError` on non-2xx, invalid JSON, or contract failure (`F16 AC4`).
- **Publishing Services, Approval Dispatch, UI & Offline E2E J6 (`engine/services.py`, `app.py`, `locales/`):**
  - Implement `publish_draft()` and `retry_publication()` forcing `mode="draft"` on staging (`F16 AC3`), storing `failed` + `error` on plugin failures (`F16 AC4`), updating `drafts.status='published'`, and emitting `article.published` with `url` on success (`F16 AC5`).
  - Wire `decide_draft(..., decision="approved")` to auto-publish when a `publisher` integration is configured (`F16 AC1`) while remaining backward-compatible when unconfigured.
  - Surface publication status, URL link, stored error, and Retry/Publish buttons on Draft Review (`app.py`, `locales/`), and extend offline E2E J6 (`tests/test_journey_j5_j6_j7_draft_review.py`).
- **n8n Publishing Guide (`docs/guides/n8n-publish.md`):**
  - Document signature verification and sample n8n workflow (`webhook -> CMS -> PublishedContent JSON`), validated against `PublishedContent` in tests (`M4-05 Step 5`).
