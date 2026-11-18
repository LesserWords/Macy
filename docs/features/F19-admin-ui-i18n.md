# F19 — Admin UI shell and bilingual interface (pt-BR / en-US)

**Status:** Done (M1-02 delivered) · **Milestone:** M1 · **Plan items:** M1-02 · **ADRs:** 014 · **OpenSpec capability:** `admin-ui`

## User story
As a user, I use the admin interface in Portuguese (Brazil) or English, and my choice is remembered.

## Scope
- In:
  - Streamlit app shell (navigation, workspace header, sign-out);
  - `gettext` helper;
  - Babel extract / update / compile;
  - `locales/pt_BR` and `locales/en_US`;
  - language switch stored in `users.ui_locale`;
  - CI translation-completeness gate;
  - localised dates and numbers.
- Out: translating generated content (that is the campaign locale), right-to-left languages.

## Acceptance criteria
- **AC1** WHEN a user first signs in, THEN the UI language is taken from the browser's `Accept-Language` (pt or en), falling back to pt-BR.
- **AC2** WHEN the user switches language, THEN all labels change immediately, and the choice persists in `users.ui_locale` across sessions.
- **AC3** WHEN a PR adds a UI string without translations in both catalogues, or leaves fuzzy entries, THEN CI fails.
- **AC4** WHEN dates and money are shown, THEN they are formatted for the UI locale (e.g. `03/11/2026` and `R$`-style separators for pt-BR, while amounts stay in USD).
- **AC5** WHEN the UI language differs from the campaign locale, THEN generated content stays in the campaign locale.

## Data
`users.ui_locale`

## Interfaces
- `ui.i18n._()`
- `services.set_ui_locale()`

## Tests
| Seam | Test | E2E |
|---|---|---|
| CI | Babel statistics check (0 untranslated / fuzzy) | — |
| services | Persist locale | — |
| UI | Switch pt-BR ↔ en-US, reload, still switched | **J1** |

## Dependencies
F01
