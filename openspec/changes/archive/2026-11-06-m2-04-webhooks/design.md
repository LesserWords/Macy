# Design Document: Outbound Webhooks, Outbox & Delivery (M2-04)

## Architecture & Data Flow

```
+-----------------------------------------------------------------------------------------+
| Caller Database Transaction (e.g., pipeline, services.decide_topic, finalize_run)        |
|                                                                                         |
| 1. Business operation writes changes (run, opportunity, etc.)                           |
| 2. events.emit() writes event row into `events` table                                   |
| 3. events.emit() queries active matching subscriptions from `webhook_subscriptions`     |
| 4. events.emit() inserts initial rows into `webhook_deliveries` (status='pending')       |
| 5. Transaction commits: BOTH state changes and deliveries exist; on rollback, NEITHER    |
+-----------------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------------+
| Delivery Worker (engine/webhooks/delivery.py - deliver_due())                           |
|                                                                                         |
| 1. SELECT ... FROM webhook_deliveries d FOR UPDATE OF d SKIP LOCKED                     |
|    WHERE d.status IN ('pending', 'retrying') AND d.next_attempt_at <= now()              |
| 2. Decrypt subscription secret via Fernet (APP_ENCRYPTION_KEY)                          |
| 3. Construct Standard Webhooks headers:                                                 |
|    - webhook-id: str(event_id)                                                          |
|    - webhook-timestamp: unix seconds                                                    |
|    - webhook-signature: v1,<base64 HMAC-SHA256 signature>                              |
| 4. Dispatch via safe_http.safe_request("POST", url, body, headers)                      |
| 5. If 2xx: status='delivered', delivered_at=now()                                       |
|    If non-2xx or error:                                                                 |
|      - if elapsed >= 24h: status='dead'                                                 |
|      - else: status='retrying', next_attempt_at = now() + backoff_delay                 |
+-----------------------------------------------------------------------------------------+
```

## Standard Webhooks Specification
- Format: `v1,<base64_hmac>`
- Signature input string: `{webhook-id}.{webhook-timestamp}.{payload_json}`
- Secrets: Prefixed with `whsec_` followed by base64-encoded random entropy.

## Backoff Formula
- Initial delay: 60 seconds (1 minute).
- Backoff factor: 2.0.
- Max interval: 3600 seconds (1 hour).
- Max retry window: 86400 seconds (24 hours).
- If `now - event.occurred_at >= 86400`: status transitions to `dead`.

## Security & SSRF
- All outbound requests route through `safe_http.safe_request`.
- In production, plain HTTP is forbidden.
- Loopback (127.0.0.0/8), private RFC1918, link-local, and cloud metadata addresses are rejected.
- DNS resolution is verified prior to connection.
