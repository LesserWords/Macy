# Change Proposal: Outbound Webhooks, Outbox & Delivery (M2-04)

## Motivation
In Milestone 2 Item 4 (M2-04), n8n and other external tools must be able to react to engine state changes (e.g., `topics.awaiting_approval`, `topic.decided`, `run.completed`, `run.failed`) reliably without losing events or inventing state (F06 AC1–AC7, ADR-008, ADR-017).

To satisfy this requirement:
1. **Transactional Outbox**: Whenever an event is emitted, the `events` row and corresponding `webhook_deliveries` rows for all matching, active subscriptions must be created in the caller's transaction. If the caller rolls back, neither exists (F06 AC1).
2. **Standard Webhooks Signatures**: The delivery worker signs outgoing HTTP requests using HMAC-SHA256 according to the Standard Webhooks specification (`webhook-id`, `webhook-timestamp`, `webhook-signature`), verified with the `standardwebhooks` library (F06 AC2).
3. **Resilience & Backoff**: Deliveries failing due to non-2xx status, network timeouts, or connection failures are retried with exponential backoff for up to 24 hours before being marked `dead` (F06 AC3).
4. **Manual Re-send**: Workspace owners can trigger manual re-sends of dead/failed deliveries from the Webhooks screen (F06 AC4).
5. **SSRF Guard**: Subscription URLs must be HTTPS in production and cannot resolve to private, loopback, or cloud metadata IP addresses (F06 AC5).
6. **Pipeline Isolation**: Delivery failures or slow receivers must never block or fail engine pipeline runs (F06 AC6).
7. **Secret Security**: Secrets are generated with high entropy, displayed exactly once upon subscription creation, and stored encrypted at rest using Fernet under `APP_ENCRYPTION_KEY` (F06 AC7).

## Proposed Changes

### 1. Event Outbox Emission (`engine/events.py`)
- Implement `events.emit(conn, *, workspace_id, event_type, entity_type, entity_id, payload, actor_user_id=None) -> uuid.UUID`.
- Atomically insert domain event into `events` table and insert matching delivery rows into `webhook_deliveries` for active subscriptions.
- Replace direct `db.insert_event` calls in `services.py`, `pipeline.py`, and `budget.py` with `events.emit` to guarantee outbox dispatch across all domain events.

### 2. Webhook Delivery Worker (`engine/webhooks/delivery.py`)
- Implement `deliver_due(conn=None, *, batch_size=10, timeout=10.0) -> list[dict[str, Any]]`:
  - Query due pending/retrying deliveries using `SELECT ... FOR UPDATE OF d SKIP LOCKED`.
  - Decrypt subscription secret with Fernet key.
  - Sign request with Standard Webhooks (`v1,<base64>`).
  - Dispatch POST request using `safe_http.safe_request`.
  - Handle 2xx success (`delivered`) and non-2xx / timeout failures (`retrying` with exponential backoff or `dead` after 24 hours).
- Implement pure exponential backoff calculation function with Hypothesis property tests.

### 3. Service Layer (`engine/services.py`)
- Implement `services.save_subscription`:
  - Validate subscription URL with `safe_http.validate_safe_url`.
  - Generate Standard Webhooks secret (`whsec_...`) and encrypt it.
  - Insert or update subscription record, returning the secret only on initial creation.
- Implement `services.list_subscriptions` (returning masked secrets).
- Implement `services.delete_subscription` and toggle enabled.
- Implement `services.list_deliveries` and `services.resend` (resetting delivery status to pending for immediate re-attempt).

### 4. Admin UI (`app.py`)
- Add **Webhooks** navigation section with full bilingual (`pt-BR` and `en-US`) support.
- Subscription form with URL validation, event type picker, and one-time secret display.
- Delivery history table with HTTP status code, attempts, last error, and one-click "Re-send" button.

### 5. Documentation & Verification (`docs/guides/n8n-webhooks.md`)
- Author comprehensive guide for receiving and validating Macy webhooks in n8n.
- Include Python verification snippet utilizing `standardwebhooks` library.

## Impact
- Fulfills Milestone 2 Item 4 (M2-04) and passes Feature F06 AC1–AC7.
- Zero breaking changes to existing data models.
