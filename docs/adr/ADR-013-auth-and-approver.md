# ADR-013: Authentication — Google OIDC + workspace membership; approver = campaign creator

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F01, F10, F15, ADR-009, ADR-014

## Context
This is an internal tool for now, but it must become multi-customer later. You chose Google login, and the campaign creator approves topics and drafts.

## Options considered
| Option | Fit |
|---|---|
| **A. Streamlit native OIDC (`st.login("google")`) + `users`/`memberships`** | Built in; no extra service; ready for multiple customers |
| B. Shared password | No identity for audit; rejected |
| C. Auth proxy (oauth2-proxy) | An extra service; only worth it if more apps share the login |

## Decision
A.
- **Access:** a signed-in user's email must match a `users` row with a membership in the workspace. The first owner is seeded with `cli.py add-owner`.
- **Audit:** `users.id` is the actor recorded in `events`.
- **Approval:** gates 1 and 2 are approved by `campaigns.created_by`, who must have the `owner` role (the only role in the MVP).
- **Tests:**
  - Offline E2E uses a fake identity, which is refused when `ENV=prod`.
  - Live E2E uses a dedicated Google test account with saved Playwright `storage_state`.

## Consequences
- Easier: every action is attributable to a person, with no extra service.
- Harder: if the single approver is away, approvals wait. Items stay pending safely in the database.

## Revisit when
A workspace has more than one person (add the `editor`, `approver` and `viewer` roles), or other apps need the same login (add a proxy).

## Action items
- [ ] M1-01: OIDC, membership check, `add-owner`, J0
- [ ] Setup (outside code): create the Google OAuth client with prod + staging redirect URIs, plus a test account
