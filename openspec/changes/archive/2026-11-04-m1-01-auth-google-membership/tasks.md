## 1. Authentication helpers and membership authorization

- [x] 1.1 Implement user lookup and authentication service methods in `engine/services.py` and `engine/db.py` → verify: tests pass
- [x] 1.2 Implement `engine/auth.py` providing Streamlit auth helpers (`get_current_identity`, `require_authenticated_member`, fake identity provider) → verify: unit tests pass
- [x] 1.3 Ensure fake-identity provider strictly refuses execution when `APP_ENV=production` or `ENV=prod` (AC6) → verify: unit tests assert refusal

## 2. CLI `add-owner` command

- [x] 2.1 Implement `add-owner` CLI command in `engine/cli.py` calling `services.add_workspace_owner` and logging `membership.created` event (AC5) → verify: unit tests pass
- [x] 2.2 Verify CLI respects architectural boundary (no direct `engine.db` import) → verify: AST boundary test passes

## 3. UI access gate & Offline E2E Journey J0

- [x] 3.1 Wire authentication gate into `app.py` ensuring non-members see no workspace data and sign-out is available (AC1, AC2) → verify: tests pass
- [x] 3.2 Implement offline E2E Journey J0 in `tests/test_journey_j0_auth.py` (member sign-in, non-member rejection, multi-workspace isolation) → verify: journey tests pass
