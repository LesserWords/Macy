# ADR-021: CMS-neutral `Publisher` port; `markdown_export` + `http_webhook` in the MVP

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F16, ADR-005, ADR-008, ADR-017

## Context
The CMS isn't chosen yet. Publishing must work now, and adding a CMS later must not require changes to the core.

## Options considered
| Option | Fit |
|---|---|
| **A. Fixed `PublishRequest` / `PublishedContent` contracts + a contract suite; MVP plugins `markdown_export` and `http_webhook` (POST to a user URL, e.g. an n8n flow into any CMS)** | Publishing works today; any CMS can be added later as a plugin |
| B. Pick a CMS now (e.g. WordPress) | A premature decision |
| C. Export only | Manual copy-paste; no `published_url` captured |

## Decision
A.
- **Request:** `PublishRequest` = approved `ContentDraft` + `locale` + `destination` + `mode` (`draft` \| `scheduled` \| `live`) + `scheduled_at?`.
- **Response:** `PublishedContent` = destination + `external_id` + `url?` + status + `published_at?` + `raw?`.
- **Contract suite:**
  - publishing the same `draft_id` twice is idempotent (unique `(workspace_id, draft_id, destination)`);
  - `draft` mode never goes live;
  - the returned `url` is recorded in `publications`.
- **`http_webhook`:** signs its requests (Standard Webhooks) and expects `PublishedContent` JSON back.

## Consequences
- Easier: any CMS can be connected through n8n now, and a native plugin is a drop-in later.
- Harder: n8n flows are the user's responsibility. A sample n8n flow is documented.

## Revisit when
A CMS is chosen: build its native plugin, which must pass the same suite.

## Action items
- [x] M4-05: port, contract suite, both plugins, `publications`, sample n8n flow
