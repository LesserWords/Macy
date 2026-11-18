# Proposal

## Why

Plan item **M1-01** (docs/plans/M1.md). Implement Google sign-in via Streamlit OIDC and workspace membership authorization, `cli add-owner` for seeding workspace owners, fake-identity testing support (refused in production), and offline E2E journey J0. Features: F01. ADRs: 009, 013, 014.

## What Changes

- Add Google OIDC authentication integration with Streamlit:
  - Configure OIDC from environment (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, etc.).
  - Check user identity and look up membership by email (`services.get_user_by_email`, `services.authenticate_user`, `services.require_member`).
  - Refuse access and render no workspace data if user lacks membership (AC1, AC2, AC3).
- Implement `cli.py add-owner <workspace_slug> <email>`:
  - Finds workspace by slug, creates user if absent, creates membership with role `owner`, and logs `membership.created` event in `events` table (AC5).
- Implement fake-identity authentication for offline E2E and dev/testing:
  - Refused when `ENV=prod` or `APP_ENV=production` (AC6).
- Implement offline E2E Journey J0 (`tests/test_journey_j0_auth.py`):
  - Member signs in and accesses workspace.
  - Non-member access is refused.
  - Workspace B cannot see Workspace A data (tenant isolation).
- Update capability `access` specification.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `access`: Add requirements and scenarios for Google sign-in authentication, membership verification, `add-owner` CLI command, and fake identity restrictions in production.

## Impact

- New: `engine/auth.py`, `tests/test_auth.py`, `tests/test_journey_j0_auth.py`.
- Modified: `engine/cli.py`, `engine/db.py`, `engine/services.py`, `app.py`, `pyproject.toml`, `openspec/specs/access/spec.md`.
