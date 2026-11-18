## 1. Internationalization infrastructure & catalogues

- [x] 1.1 Create `babel.cfg`, `engine/i18n.py` with gettext `_()` helper, catalog loader, date/currency formatters, and `resolve_initial_locale` → verify: tests pass
- [x] 1.2 Initialize and compile message catalogues for `pt_BR` and `en_US` under `locales/` → verify: `pybabel compile` succeeds
- [x] 1.3 Add CI translation gate `scripts/check_translations.py` enforcing 0 untranslated and 0 fuzzy messages → verify: gate test detects incomplete catalog

## 2. Service & database persistence

- [x] 2.1 Add `update_user_locale` in `engine/db.py` and `services.set_user_ui_locale` in `engine/services.py` with event audit logging → verify: service tests pass
- [x] 2.2 Validate locale format (`pt-BR`, `en-US`) in service layer → verify: invalid locale rejected

## 3. App shell & bilingual UI

- [x] 3.1 Update `app.py` to render the bilingual app shell with workspace header, language switcher, navigation, and sign-out → verify: UI tests pass
- [x] 3.2 Implement offline UI test for language switching, session reload persistence, and campaign content locale independence → verify: tests pass
