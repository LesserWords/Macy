# Tasks: Outbound Webhooks, Outbox & Delivery (M2-04)

## 1. Event Outbox Emission
- [ ] 1.1 Implement `engine/events.py` with `events.emit` writing event and webhook delivery rows within the caller transaction (F06 AC1).
- [ ] 1.2 Wire `events.emit` across `services.py`, `pipeline.py`, and `budget.py`.
- [ ] 1.3 Add transactional rollback and emission unit tests in `tests/test_webhooks.py`.

## 2. Webhook Delivery Worker & Backoff
- [ ] 2.1 Implement `engine/webhooks/delivery.py` with `calculate_next_backoff`, Standard Webhooks signing, and `deliver_due` with `safe_http` (F06 AC2, AC3).
- [ ] 2.2 Add Hypothesis property test for backoff schedule and 24-hour dead transition.
- [ ] 2.3 Add integration test with local HTTP test receiver verifying `standardwebhooks` validation and retries.

## 3. Service Layer & Admin UI
- [ ] 3.1 Implement subscription management and re-send in `engine/services.py` (`save_subscription`, `list_subscriptions`, `delete_subscription`, `list_deliveries`, `resend`) with SSRF validation and Fernet encryption (F06 AC4, AC5, AC7).
- [ ] 3.2 Build Webhooks admin UI in `app.py` with one-time secret display, subscription manager, delivery logs, and re-send button.
- [ ] 3.3 Extract and compile gettext translations for `pt-BR` and `en-US` (`messages.po` / `messages.mo`) and check with `scripts/check_translations.py`.

## 4. Documentation, E2E & Quality Gates
- [ ] 4.1 Author `docs/guides/n8n-webhooks.md` with verification code snippet.
- [ ] 4.2 Add offline E2E Journey test verifying webhooks subscription and delivery flow.
- [ ] 4.3 Verify all quality gates pass (100% diff-cover on `engine/`, full test suite, ruff, mypy, complexipy <= 15, vulture, lint-imports, jscpd, squawk, openspec validate).
