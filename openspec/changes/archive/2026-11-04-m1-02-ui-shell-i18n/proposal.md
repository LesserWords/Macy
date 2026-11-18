# Proposal

## Why

Plan item **M1-02** (docs/plans/M1.md). Build the bilingual administrative UI shell in Streamlit with pt-BR and en-US internationalization via gettext/Babel, locale persistence in `users.ui_locale`, CI translation gate (0 untranslated or fuzzy strings), and locale-aware date and currency formatting. Features: F19. ADRs: 014.

## What Changes

- Add `engine/i18n.py` providing gettext translation helper `_()`, catalog loading, `format_date()`, `format_currency_usd()`, and `resolve_initial_locale()`.
- Add Babel configuration (`babel.cfg`) and message catalogues for `pt_BR` and `en_US` under `locales/`.
- Add CI translation completeness script `scripts/check_translations.py` ensuring zero untranslated or fuzzy entries.
- Add `services.set_user_ui_locale()` in `engine/services.py` and `engine/db.py` to persist user language selection in `users.ui_locale` and emit `user.locale_updated` event in `events`.
- Update `app.py` with the app shell: workspace header, user header, language switcher, navigation, sign-out button, and translated labels.
- Add unit tests for i18n, services, and offline journey testing switching languages and verifying persistence and locale independence from campaign content.

## Capabilities

### Modified Capabilities
- `admin-ui`: Extend requirements and scenarios for bilingual UI shell, translation CI completeness gate, and locale persistence across sessions.

## Impact

- New: `engine/i18n.py`, `babel.cfg`, `locales/`, `scripts/check_translations.py`, `tests/test_i18n.py`, `tests/test_ui_shell_i18n.py`.
- Modified: `engine/db.py`, `engine/services.py`, `app.py`, `pyproject.toml`, `openspec/specs/admin-ui/spec.md`.
