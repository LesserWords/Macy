# Publishing Articles to Any CMS via n8n (`http_webhook`)

This guide explains how to connect Macy's `http_webhook` Publisher plugin (`F16`, `ADR-021`) to an [n8n](https://n8n.io) workflow so approved articles can be created as drafts or published in any CMS (WordPress, Ghost, Webflow, Strapi, Shopify, or a custom backend).

---

## 1. Flow Overview

```
Macy Gate 2 Approved ──(Signed POST PublishRequest)──► n8n Webhook Node
                                                            │
                                                            ▼
                                               Verify Standard Webhooks +
                                               Check Idempotency-Key
                                                            │
                                                            ▼
                                               CMS Node (mode: draft/live/scheduled)
                                                            │
                                                            ▼
Macy stores URL & row ◄──(PublishedContent JSON)── Respond to Webhook Node
```

Unlike outbound lifecycle notifications ([`docs/guides/n8n-webhooks.md`](n8n-webhooks.md)), the `http_webhook` publisher operates synchronously on a single request/response cycle and expects a JSON body conforming to **`PublishedContent`**.

---

## 2. Configuring `http_webhook` in Macy

1. Open **Integrations** in the Macy Admin UI and select port **`publisher`** → plugin **`http_webhook`**.
2. Set **`endpoint_url`** to your n8n Webhook Trigger URL (configured with **Respond: Using 'Respond to Webhook' Node**), for example:
   - `https://n8n.example.com/webhook/macy-publish`
3. Set **`signing_secret`** (`whsec_...` or any strong shared secret; stored encrypted at rest with Fernet per `ADR-008`).
4. Set **`default_mode`** (`draft`, `scheduled`, or `live`). When `APP_ENV=staging`, Macy always forces `mode = "draft"`.
5. Click **Save Integration** and **Test Connection**.

---

## 3. Incoming Request Headers & `PublishRequest` Payload

Every publish attempt sends a `POST` request (`Content-Type: application/json`) signed with **Standard Webhooks** headers plus an idempotency key:

| Header | Purpose | Example |
|---|---|---|
| `webhook-id` | Deterministic UUIDv5 for `(draft_id, destination)` | `0192f440-1111-7000-8000-000000000001` |
| `webhook-timestamp` | Unix epoch seconds | `1793971200` |
| `webhook-signature` | Versioned HMAC-SHA256 (`v1,<base64>`) | `v1,k5Z...` |
| `Idempotency-Key` | Stable deduplication key `<draft_id>:<destination>` | `0192f440-...:http_webhook` |

### Sample `PublishRequest` Body

```json
{
  "schema_version": 1,
  "draft_id": "0192f440-1111-7000-8000-000000000001",
  "locale": "pt-BR",
  "destination": "http_webhook",
  "mode": "draft",
  "scheduled_at": null,
  "draft": {
    "schema_version": 1,
    "title": "Gestão de Estoque em Óticas",
    "meta_description": "Como equilibrar giro de armações e lentes no varejo óptico.",
    "slug": "gestao-estoque-oticas",
    "body_md": "# Gestão de Estoque em Óticas\n\nConteúdo editorial validado...",
    "faq": [
      {
        "question": "Qual o giro ideal de estoque?",
        "answer": "Entre 45 e 60 dias para armações de giro regular."
      }
    ],
    "cta": {
      "headline": "Agende um diagnóstico",
      "body": "Fale com nossa equipe comercial.",
      "button_text": "Solicitar Diagnóstico"
    },
    "claims": [
      {
        "kind": "fact",
        "text": "O setor óptico cresceu 8% no terceiro trimestre.",
        "source_id": "0192f440-2222-7000-8000-000000000002"
      }
    ]
  }
}
```

---

## 4. Verification, Idempotency & `mode=draft` Rules in n8n

### Step A: Verify the Standard Webhooks Signature

Place an n8n **Code (JavaScript)** node right after the **Webhook** node (using the same `whsec_...` verification helper documented in [`docs/guides/n8n-webhooks.md`](n8n-webhooks.md)):

```javascript
const crypto = require('crypto');

const secret = $env.MACY_PUBLISH_SECRET;
const headers = $input.first().json.headers;
const rawBody = JSON.stringify($input.first().json.body);

const msgId = headers['webhook-id'];
const timestamp = headers['webhook-timestamp'];
const sigHeader = headers['webhook-signature'];

if (!msgId || !timestamp || !sigHeader) {
  throw new Error('Missing Standard Webhooks headers');
}

const cleanSecret = secret.startsWith('whsec_') ? secret.slice(6) : secret;
const hmac = crypto.createHmac('sha256', Buffer.from(cleanSecret, 'base64'));
hmac.update(`${msgId}.${timestamp}.${rawBody}`);
const expectedSig = hmac.digest('base64');

const matched = sigHeader
  .split(' ')
  .some((part) => part === `v1,${expectedSig}`);

if (!matched) {
  throw new Error('Invalid Standard Webhooks signature');
}

return $input.first().json.body;
```

### Step B: Enforce `mode` and Idempotency in Your CMS Node

1. **Deduplicate using `Idempotency-Key`** (`<draft_id>:<destination>`): Look up or upsert the post in your CMS by `external_id` / Macy `draft_id` metadata rather than creating a second post on retry.
2. **Never publish live when `mode === "draft"`:**
   - Map `mode === "draft"` → WordPress `status: "draft"`, Ghost `status: "draft"`, Webflow `isDraft: true`.
   - Map `mode === "scheduled"` → scheduled status with `scheduled_at` ISO timestamp.
   - Map `mode === "live"` → published status.
   - If Macy sees `raw.is_live === true`, `raw.live === true`, or `raw.public === true` in the response while `mode === "draft"`, Macy treats the response as a contract violation (`PublishError`).

---

## 5. Required `PublishedContent` Response JSON

End the n8n workflow with a **Respond to Webhook** node returning HTTP `200` and `Content-Type: application/json` matching the `PublishedContent` contract:

```json
{
  "schema_version": 1,
  "destination": "http_webhook",
  "external_id": "cms-post-9482",
  "url": "https://blog.otica.example.com/posts/gestao-estoque-oticas",
  "status": "published",
  "published_at": "2026-11-09T14:00:00Z",
  "raw": {
    "cms": "wordpress",
    "mode": "draft",
    "is_live": false,
    "idempotency_key": "0192f440-1111-7000-8000-000000000001:http_webhook"
  }
}
```

### Valid `status` Values and Error Handling

- **Success statuses:** `"published"`, `"scheduled"`, `"draft"`. Macy records `external_id`, `url`, `published_at`, and `raw` in `publications`, marks the draft as published, and emits the `article.published` domain event.
- **Failure handling:** If your n8n workflow returns HTTP `>= 300`, invalid JSON, schema errors, or `PublishedContent.status == "failed"`, Macy stores `publications.status = 'failed'` with the error message and enables **Retry Publish** in the Draft Review UI.
