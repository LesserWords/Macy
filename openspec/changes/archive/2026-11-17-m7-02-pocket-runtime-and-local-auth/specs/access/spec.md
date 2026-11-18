# access Delta Specification

## ADDED Requirements

### Requirement: Built-in local admin authentication with automatic OIDC switching
The system SHALL support `AUTH_MODE=local | oidc | auto`, storing nullable `users.password_hash` via non-blocking migration, verifying local passwords using PBKDF2-HMAC-SHA256 (`600,000` iterations, constant-time `hmac.compare_digest`) with brute-force rate limiting (`5` failed attempts per `15m`), and selecting local auth in `auto` mode whenever `GOOGLE_CLIENT_ID` is unset (`ADR-030`, `F22 AC5`).

#### Scenario: Local admin authenticates without Google OAuth credentials
- **WHEN** `AUTH_MODE=auto` with `GOOGLE_CLIENT_ID` unset and an existing workspace member submits valid email and password
- **THEN** PBKDF2-HMAC-SHA256 verification succeeds and grants authenticated workspace access without requiring Google Cloud OIDC configuration

#### Scenario: Repeated failed local login attempts trigger rate-limit lockout
- **WHEN** 5 consecutive invalid passwords are submitted for an email within 15 minutes
- **THEN** subsequent login attempts within the lockout window are rejected with a structured rate-limit error
