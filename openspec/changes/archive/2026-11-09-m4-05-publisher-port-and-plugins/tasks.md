# Tasks

## 1. Publisher Contract Suite & Schema Migration (M4-05 Step 1)
- [x] 1.1 Add failing contract assertions in `tests/test_contract_suites.py` (`test_publisher_contract_suite` checking idempotency per `(draft_id, destination)`, non-live behavior under `mode="draft"`, recorded `url`, and `test_non_idempotent_fake_publisher_fails_contract`), plus failing unit/DB/service tests in `tests/test_publishing.py`, `tests/test_schema_constraints.py`, and `tests/test_tenant_scoping.py`.
- [x] 1.2 Add `PublishError` and status validation on `PublishedContent` in `engine/contracts.py`, make `FakePublisher` idempotent with `is_live` metadata in `engine/integrations/fakes.py`, and apply migration `db/migrations/20261109000002_update_publications_for_publishing.sql` (nullable `url`, `error text`, composite FK `(workspace_id, draft_id) -> drafts`, `UNIQUE (workspace_id, draft_id, destination)`) + tenant-scoped `engine/db.py` helpers.

## 2. Markdown Export & HTTP Webhook Plugins (M4-05 Steps 2 & 3)
- [x] 2.1 Implement `MarkdownExportPublisher` in `engine/integrations/markdown_export.py` and verify literal expected `.md` output (YAML front-matter `title`, `meta_description`, `slug`, `locale`, `mode`, `draft`, body/FAQ/CTA) and path safety (`F16 AC6`).
- [x] 2.2 Implement `HttpWebhookPublisher` in `engine/integrations/http_webhook.py` with Standard Webhooks signing via `engine/webhooks.py`, SSRF-safe HTTP calls via `engine/safe_http.py`, strict `PublishedContent` validation, and `PublishError` on non-2xx or invalid responses (`F16 AC4`).
- [x] 2.3 Register `markdown_export` and `http_webhook` entry points in `pyproject.toml`, refresh editable installation metadata, and verify `test_publisher_contract_suite` + receiver integration tests pass green.

## 3. Approval-to-Publish Wiring, Staging Safeguard, UI & Guide (M4-05 Steps 4 & 5)
- [x] 3.1 Implement `services.publish_draft()`, `services.retry_publication()`, and auto-publish on `services.decide_draft(..., decision="approved")` in `engine/services.py` (staging `mode=draft` forcing `F16 AC3`, `article.published` emission `F16 AC5`, retryable `failed` error recording `F16 AC4`, publication status in Draft Review bundle).
- [x] 3.2 Update Draft Review UI (`app.py`, `locales/`) to display publication status/URL/error and Retry/Publish actions, and extend offline E2E J6 (`tests/test_journey_j5_j6_j7_draft_review.py`).
- [x] 3.3 Write `docs/guides/n8n-publish.md` and validate its sample `PublishedContent` JSON in tests; update `docs/features/F16-publishing.md` (`Done`) and `docs/adr/ADR-021-publisher-port.md`.
- [x] 3.4 Pass all quality gates (`ruff`, `mypy`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk`, `check_translations.py`, `pytest`, `diff-cover == 100%`, `openspec validate --all --strict`).
