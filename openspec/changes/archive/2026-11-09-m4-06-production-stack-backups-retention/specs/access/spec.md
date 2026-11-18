# access Specification Delta (M4-06)

## MODIFIED Requirements

### Requirement: Immutable event stream and database role permissions
The schema SHALL define database roles (`owner`, `app`, `retention`, `readonly`), prevent application code from directly modifying or deleting existing events, grant `retention` the privileges required to purge expired data across `events`, `llm_payloads`, `webhook_deliveries`, and `documents` (`extracted_text`, `text_purged_at`), and provide a controlled `SECURITY DEFINER` function for scrubbing personal data in `events.payload` during LGPD user anonymisation (`F20 AC7, AC8`, `ADR-024`).

#### Scenario: App role cannot update or delete events
- **WHEN** an UPDATE or DELETE statement executes on the `events` table under the `app` role
- **THEN** PostgreSQL raises a permission denied error

#### Scenario: Retention role can purge expired records but cannot delete usage_ledger or documents rows
- **WHEN** the `retention` role executes retention cleanup queries
- **THEN** it can delete expired `events`, `llm_payloads` (joining `usage_ledger`), and `webhook_deliveries`, and update `documents.extracted_text` / `documents.text_purged_at`, while `DELETE` on `documents` or `usage_ledger` is denied
