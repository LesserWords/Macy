# Design: M4-05 — Publisher port and plugins

## Architecture & Boundaries
- **CMS-Neutral Publisher Port (`ADR-005`, `ADR-021`):**
  - Both `markdown_export` and `http_webhook` implement `PublisherPort` (`healthcheck()`, `publish(PublishRequest) -> PublishedContent`), declare Pydantic `Settings` with `extra="forbid"`, and register under `[project.entry-points."content_engine.publisher"]`.
  - Plugins remain stateless/DB-free; `engine/services.py` orchestrates authorization, staging `mode=draft` overrides, `publications` persistence, `drafts` status transition, and `article.published` event emission.
- **Database Idempotency & Sitemap Compatibility (`engine/db.py`, `20261109000002`):**
  - Existing sitemap inventory rows (`destination='sitemap'`, `draft_id=NULL`, `mode='live'`) continue using `UNIQUE (workspace_id, url)` with `ON CONFLICT (workspace_id, url)`.
  - Draft publishing uses `UNIQUE (workspace_id, draft_id, destination)` via `upsert_draft_publication()` (`ON CONFLICT (workspace_id, draft_id, destination) DO UPDATE ... WHERE publications.workspace_id = %(workspace_id)s`), allowing `url = NULL` + `error` on failed webhook attempts without losing retry state, and clearing `error` cleanly upon retry success.
- **Transaction Hygiene on Approval (`engine/services.py`):**
  - `decide_draft()` first commits the guarded `'pass' -> 'approved'` transition and `draft.decided` event in one atomic transaction, then dispatches publishing outside the approval transaction when a `publisher` integration is configured so external HTTP or filesystem failures record a retryable `'failed'` row in `publications` without rolling back the editorial approval.
- **Standard Webhooks Signing & SSRF Defense (`ADR-008`, `ADR-017`):**
  - `HttpWebhookPublisher` reuses `engine/webhooks.py` (`build_webhook_headers`) and `engine/safe_http.py` (`validate_safe_url`, `safe_request`), checking non-2xx HTTP status, JSON validity, `PublishedContent` schema validation, and non-live invariants when `mode == "draft"`.
  - Any `http_webhook` `Settings.signing_secret` (`SecretStr`) is encrypted with Fernet in `workspace_integrations` and never returned unmasked in UI, logs, services, or events.
