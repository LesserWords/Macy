# ADR-017: Outbound webhooks — Standard Webhooks format + transactional outbox

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F06, ADR-008, ADR-010

## Context
n8n and other tools must be able to react to the engine (a run finished, a gate is pending, an article was published) without code changes. Events must never be lost or invented, and receivers must be able to verify they came from us.

## Options considered
| Option | Fit |
|---|---|
| **A. `events` + `webhook_deliveries` written in the same transaction; the worker delivers; Standard Webhooks signatures (`standardwebhooks` lib)** | Exactly-once intent, at-least-once delivery; receivers verify with existing libraries |
| B. Our own HMAC header format | Every receiver writes custom verification code |
| C. Self-hosted Svix | An extra service; overkill at this volume |

## Decision
A.
- **Events:**
  - `run.completed` / `run.failed`
  - `topics.awaiting_approval`
  - `draft.awaiting_approval`
  - `topic.decided` / `draft.decided`
  - `article.published`
  - `budget.cap_reached`
- **Payload:** `{type, id, workspace_id, occurred_at, data}`, where `data` is the relevant contract.
- **Headers:** `webhook-id` (equal to the event id), `webhook-timestamp`, `webhook-signature`.
- **Delivery:** retried with exponential backoff for 24 h, then marked `dead`. Every attempt is logged, and failed deliveries can be re-sent manually from the UI.
- **Safety:** all URLs go through `safe_http` (ADR-008). Delivery never blocks the pipeline.

## Consequences
- Easier: n8n users verify with copy-paste code, and moving to Svix later stays compatible.
- Harder: receivers must deduplicate by `webhook-id`, which is documented on the Webhooks screen.

## Revisit when
Volume exceeds about 10k deliveries per day, or customers want a delivery portal. Then consider Svix.

## Action items
- [ ] M2-04: outbox, delivery worker, Webhooks screen, signature tests
