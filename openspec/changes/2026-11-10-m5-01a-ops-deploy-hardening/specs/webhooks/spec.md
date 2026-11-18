# webhooks Delta Specification

## ADDED Requirements

### Requirement: Webhook signing secrets participate in key rotation
The system SHALL re-encrypt `webhook_subscriptions.secret` ciphertext rows atomically during `content-engine rotate-key` (`services.rotate_encryption_keys`) so outbound Standard Webhooks deliveries continue signing valid HMAC-SHA256 headers after retiring old Fernet keys.

#### Scenario: Rotated webhook secret delivers valid Standard Webhooks signatures under the new key alone
- **WHEN** `content-engine rotate-key` rotates a subscription's secret from `<old>` to `<new>` and `APP_ENCRYPTION_KEY` is subsequently reduced to `<new>`
- **THEN** `webhooks.deliver_due` decrypts the rotated secret using `<new>` and delivers payloads whose `webhook-signature` header validates against the original `whsec_...` plaintext secret
