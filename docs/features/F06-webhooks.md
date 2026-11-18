# F06 — Outbound webhooks (n8n and other tools)

**Status:** Done (M2-04 & M4-07 delivered in PR #25, #38) · **Milestone:** M2, M4 · **Plan items:** M2-04, M4-07 · **ADRs:** 008, 017 · **OpenSpec capability:** `webhooks`

## User story
As an owner, I subscribe a URL (e.g. an n8n Webhook node) to engine events, so my tools react when topics or drafts await approval or an article is published.

## Scope
- In:
  - subscriptions (URL, event types, secret shown once, enabled flag);
  - transactional outbox;
  - delivery worker;
  - Standard Webhooks signatures;
  - retries and dead-lettering;
  - delivery log and manual re-send;
  - a documented n8n receiver example.
- Out: inbound API, per-event payload templates.

## Acceptance criteria
- **AC1** WHEN a state change emits an event, THEN the `events` row and one `webhook_deliveries` row per matching enabled subscription are written in the same transaction. If the state change rolls back, neither exists.
- **AC2** WHEN the worker delivers, THEN the request carries the `webhook-id`, `webhook-timestamp` and `webhook-signature` headers, and the `standardwebhooks` library verifies it with the subscription secret.
- **AC3** WHEN the receiver returns non-2xx or times out, THEN the delivery is retried with exponential backoff, and after 24 h its status is `dead`.
- **AC4** WHEN the owner clicks re-send on a `dead` delivery, THEN a new attempt is made.
- **AC5** WHEN a subscription URL is not HTTPS (in prod) or resolves to a private address, THEN saving is refused.
- **AC6** WHEN delivery fails, THEN the pipeline continues unaffected.
- **AC7** WHEN a subscription is created, THEN its secret is shown exactly once and stored encrypted.

## Data
`webhook_subscriptions`, `events`, `webhook_deliveries`

## Interfaces
- `events.emit()` (in the caller's transaction)
- worker `deliver_due()`
- `services.save_subscription()`
- `services.resend()`

## Tests
| Seam | Test | E2E |
|---|---|---|
| events | Transactional emit (rollback ⇒ nothing); signature verifies; backoff schedule (Hypothesis) | — |
| integration | Real Postgres + local test receiver: delivered, retried, dead | — |
| UI | Subscribe the test receiver; events arrive signed | **J8** |

## Dependencies
M0-08 (`safe_http`), M2-01/M2-03 (first events: `topics.awaiting_approval`)
