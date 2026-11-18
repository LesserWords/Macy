# Connecting Macy Webhooks to n8n

This guide explains how to receive and verify outbound Macy events in [n8n](https://n8n.io) workflows using the **Standard Webhooks** specification (ADR-017, F06).

---

## 1. Overview

Macy uses a transactional outbox to emit domain events whenever editorial milestones or state transitions occur:
- `topics.awaiting_approval`: Weekly report scored opportunities and is waiting for Gate 1 decision.
- `topic.decided`: An opportunity was approved or rejected by the campaign creator.
- `run.completed`: An orchestration run finished successfully.
- `run.failed`: A run encountered an unrecoverable error.
- `budget.cap_reached`: A run or workspace reached its spend ceiling.
- `article.published`: A draft was published through a configured CMS/publishing integration.

Deliveries are dispatched asynchronously with exponential backoff for up to 24 hours.

---

## 2. Standard Webhooks Headers

Every outbound HTTP request from Macy is sent as `POST` with `Content-Type: application/json` and includes three signature headers:

| Header | Description | Example |
|---|---|---|
| `webhook-id` | Unique ID of the event | `0192f440-1111-7000-8000-000000000001` |
| `webhook-timestamp` | Unix epoch timestamp (seconds) | `1793971200` |
| `webhook-signature` | Versioned HMAC-SHA256 signature | `v1,k5Z...` |

The signature is computed over:
```
{webhook-id}.{webhook-timestamp}.{raw_body}
```
using the subscription secret (`whsec_...`).

---

## 3. Configuring the Subscription in Macy

1. In the Macy Admin UI, navigate to the **Webhooks** section in the sidebar.
2. Under **Add Webhook Subscription**, enter your n8n Webhook node URL:
   - *Example:* `https://n8n.example.com/webhook/macy-events`
3. Select the event types your n8n workflow should listen to.
4. Click **Create Webhook Subscription**.
5. **Important:** Copy the displayed secret token (`whsec_...`). It is shown only once and stored encrypted at rest with Fernet.

---

## 4. Verification in n8n

### Option A: Python Verification (Recommended)

Install the `standardwebhooks` package or use the verification snippet below in a Python Code node or standalone service:

```python
import sys
from standardwebhooks import Webhook, WebhookVerificationError

def verify_macy_webhook(secret: str, raw_payload: str, headers: dict[str, str]) -> dict:
    """Verify incoming Macy webhook signature and return parsed data.

    Raises WebhookVerificationError if signature or timestamp is invalid.
    """
    wh = Webhook(secret)
    return wh.verify(raw_payload, headers)


if __name__ == "__main__":
    secret = "whsec_YOUR_WEBHOOK_SECRET"

    # In n8n, retrieve raw body and request headers:
    raw_body = '{"id":"0192f440-1111-7000-8000-000000000001","type":"topic.decided","workspace_id":"...","occurred_at":"...","data":{"decision":"approved"}}'
    headers = {
        "webhook-id": "0192f440-1111-7000-8000-000000000001",
        "webhook-timestamp": "1793971200",
        "webhook-signature": "v1,...",
    }

    try:
        payload = verify_macy_webhook(secret, raw_body, headers)
        print("Signature verified successfully:", payload["type"])
    except WebhookVerificationError as err:
        print(f"Verification failed: {err}", file=sys.stderr)
        sys.exit(1)
```

### Option B: Node.js / JavaScript Verification

In an n8n Code node (JavaScript):

```javascript
const crypto = require('crypto');

function verifyWebhook(secret, rawBody, headers) {
  const msgId = headers['webhook-id'];
  const timestamp = headers['webhook-timestamp'];
  const signatureHeader = headers['webhook-signature'];

  if (!msgId || !timestamp || !signatureHeader) {
    throw new Error('Missing required webhook headers');
  }

  // Strip 'whsec_' prefix if present and base64 decode key
  const cleanSecret = secret.startsWith('whsec_') ? secret.slice(6) : secret;
  const key = Buffer.from(cleanSecret, 'base64');

  // Payload to sign
  const toSign = `${msgId}.${timestamp}.${rawBody}`;
  const hmac = crypto.createHmac('sha256', key);
  hmac.update(toSign);
  const expectedSig = hmac.digest('base64');

  const signatures = signatureHeader.split(' ');
  for (const part of signatures) {
    const [version, signature] = part.split(',');
    if (version === 'v1' && signature === expectedSig) {
      return JSON.parse(rawBody);
    }
  }

  throw new Error('No matching signature found');
}
```

---

## 5. Event Payload Format

Every webhook payload follows this JSON envelope:

```json
{
  "id": "0192f440-1111-7000-8000-000000000001",
  "type": "topics.awaiting_approval",
  "workspace_id": "0192f440-2222-7000-8000-000000000002",
  "occurred_at": "2026-11-06T12:00:00Z",
  "data": {
    "run_id": "0192f440-3333-7000-8000-000000000003",
    "campaign_id": "0192f440-4444-7000-8000-000000000004",
    "recommended_count": 3,
    "total_opportunities": 8,
    "top_opportunity_ids": [
      "0192f440-5555-7000-8000-000000000005"
    ]
  }
}
```

---

## 6. Resilience, Retries & Idempotency

- **Deduplication:** Always deduplicate events in your n8n workflow using the `webhook-id` header or the `id` field.
- **Retries:** If n8n returns an HTTP status $\ge 300$ or times out, Macy automatically retries with exponential backoff (starting at 1 minute, doubling up to 1 hour intervals) for 24 hours before marking the delivery as `dead`.
- **Manual Re-send:** Any failed or dead delivery can be inspected and manually re-sent from the **Delivery Log** tab in the Macy Admin UI.
