# webhooks Specification

## MODIFIED Requirements

### Requirement: Transactional outbox and signed webhook delivery
The system SHALL record webhook deliveries transactionally alongside events and deliver signed payloads to active subscriptions.

#### Scenario: Transactional event and delivery emission
- **WHEN** an event is emitted within a transaction
- **THEN** matching webhook deliveries are created in the same database transaction, and if the transaction rolls back, neither exists

#### Scenario: Standard Webhooks signature verification
- **WHEN** the webhook delivery worker POSTs an event to a subscriber
- **THEN** headers contain valid webhook-id, webhook-timestamp, and HMAC-SHA256 signature verified by the standardwebhooks library

#### Scenario: Delivery retry with exponential backoff and dead status
- **WHEN** a webhook delivery fails or times out
- **THEN** the worker retries delivery with exponential backoff and marks it dead after 24 hours

#### Scenario: Manual re-send of failed deliveries
- **WHEN** an authorized user triggers re-send on a dead or failed delivery
- **THEN** a new delivery attempt is scheduled immediately

#### Scenario: Subscription URL SSRF validation
- **WHEN** a user creates or updates a webhook subscription
- **THEN** non-HTTPS schemes in production or private/loopback/cloud-metadata addresses are rejected

#### Scenario: Pipeline execution isolation
- **WHEN** webhook delivery fails or the receiver is offline
- **THEN** the engine pipeline run continues unaffected

#### Scenario: One-time secret display and encrypted storage
- **WHEN** a webhook subscription is created
- **THEN** the signing secret is displayed exactly once to the user and stored encrypted at rest
