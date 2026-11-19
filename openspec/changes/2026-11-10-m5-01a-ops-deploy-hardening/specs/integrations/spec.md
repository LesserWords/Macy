# integrations Delta Specification

## ADDED Requirements

### Requirement: Atomic cross-table MultiFernet key rotation
The system SHALL rotate Fernet-encrypted secrets atomically within a single database transaction across both `workspace_integrations.secrets` (incrementing `key_version`) and `webhook_subscriptions.secret` via `services.rotate_encryption_keys` when `macy rotate-key` runs, keeping `engine.cli` decoupled from `engine.db`.

#### Scenario: Rotate-key re-encrypts integrations and webhooks atomically and permits old-key removal
- **WHEN** `macy rotate-key --current-keys "<new>,<old>" --new-key "<new>"` runs against existing encrypted integration secrets and webhook signing secrets
- **THEN** all integration secrets increment `key_version`, both integration and webhook ciphertexts decrypt under `<new>` alone after dropping `<old>`, and JSON summaries report counts for both tables

#### Scenario: Failed rotation rolls back both tables cleanly
- **WHEN** an unhandled database error occurs partway through `services.rotate_encryption_keys`
- **THEN** the transaction rolls back so neither `workspace_integrations` nor `webhook_subscriptions` retains partially rotated ciphertexts

#### Scenario: Key rotation respects service-layer import boundaries
- **WHEN** `lint-imports` runs after wiring `rotate-key` to `services.rotate_encryption_keys`
- **THEN** `engine.cli` does not import `engine.db` directly and all import-linter contracts pass
