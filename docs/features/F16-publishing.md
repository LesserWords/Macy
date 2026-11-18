# F16 — Publishing

**Status:** Done · **Milestone:** M4 · **Plan items:** M4-05 · **ADRs:** 008, 021 · **OpenSpec capability:** `publishing`

## User story
As an owner, approved articles are published through the destination I configured (a Markdown export, or my n8n flow into any CMS), and the published URL is recorded.

## Scope
- In:
  - `Publisher` port and contract suite;
  - `markdown_export` and `http_webhook` plugins;
  - `publications` table;
  - `article.published` event;
  - a sample n8n flow doc.
- Out: native CMS plugins (Phase 3).

## Acceptance criteria
- **AC1** WHEN a draft is approved, THEN a `PublishRequest` is sent to the configured publisher, and `PublishedContent` is stored in `publications`.
- **AC2** WHEN publishing is retried for the same (draft, destination), THEN no duplicate is created (unique key).
- **AC3** WHEN `mode=draft`, THEN the destination never makes the content public. This is checked by the contract suite, and staging forces `draft`.
- **AC4** WHEN `http_webhook` publishes, THEN the request is signed (Standard Webhooks). The response must validate as `PublishedContent`; otherwise the status is `failed`, with the error stored and retry possible.
- **AC5** WHEN publishing succeeds, THEN `article.published` is emitted with the URL.
- **AC6** WHEN `markdown_export` runs, THEN a file with front-matter (title, meta, slug, locale) and the body is written.

## Data
`publications`

## Interfaces
- `Publisher.publish(PublishRequest) -> PublishedContent`

## Tests
| Seam | Test | E2E |
|---|---|---|
| plugins | Contract suite (idempotent, draft never live, url recorded) for both plugins + fake | — |
| integration | `http_webhook` against the test receiver (signature, bad response → failed) | — |
| UI | Approve → `publications` row; file or webhook received | **J6** |

## Dependencies
F15, F05, F06
