# F01 — Access, workspaces and Google sign-in

**Status:** Done (`M0-04`, `M0-05`, `M1-01`, `M4-07` `.env`-driven OIDC, `M7-02` local PBKDF2 auth & `M7-01` switchboard delivered) · **Milestone:** M0, M1, M7 · **Plan items:** M0-04, M0-05, M1-01, M7-01, M7-02 · **ADRs:** 009, 013, 014, 029, 030 · **OpenSpec capability:** `access`

## User story
As a workspace owner, I sign in with Google and only see and act on my workspace's data.

## Scope
- In:
  - `workspaces`, `users` and `memberships` tables;
  - Google OIDC login;
  - membership check in every service;
  - `cli.py add-owner`.
- Out: roles other than `owner`, invitations by email, SSO other than Google.

## Acceptance criteria
- **AC1** WHEN a Google user whose email has a membership signs in, THEN they see their workspace and their email is shown.
- **AC2** WHEN a Google user without a membership signs in, THEN access is refused and no workspace data is rendered.
- **AC3** WHEN any service is called by a user who isn't a member of the target workspace, THEN it raises `Forbidden` and writes nothing.
- **AC4** WHEN two workspaces exist, THEN no query run on behalf of workspace A returns rows from workspace B.
- **AC5** WHEN `cli.py add-owner <workspace> <email>` runs, THEN the user and the membership exist, and a `membership.created` event is logged.
- **AC6** WHEN `ENV=prod`, THEN the fake-identity test login is refused.

## Data
`workspaces`, `users` (incl. `ui_locale`), `memberships`, `events`

## Interfaces
- `services.current_user()`
- `services.require_member(workspace_id)`
- `cli add-owner`

## Tests
| Seam | Test | E2E |
|---|---|---|
| services | Forbidden for non-members on every service (parametrised) | — |
| db | Tenant-scoping test (every query filters by `workspace_id`; a second workspace sees 0 rows) | — |
| UI | Sign in as member / non-member | **J0** |

## Dependencies
M0-03 (Compose + DB), M0-04 (schema)
