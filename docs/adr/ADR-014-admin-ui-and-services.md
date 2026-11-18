# ADR-014: Admin UI — Streamlit over a services layer, bilingual (pt-BR / en-US)

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F01–F06, F09, F10, F15, F17, F19, ADR-009 · PRD §6

## Context
There are 1–2 internal users now, external customers later. The interface must be in pt-BR and en-US, chosen per user. Business logic must not end up inside the screens.

## Options considered
| Option | Fit |
|---|---|
| **A. Streamlit + `engine/services.py` as the only entry point + gettext/Babel** | Fast to build; logic stays testable; i18n with standard tools |
| B. FastAPI + React now | Weeks more; not needed for internal users |
| C. NiceGUI / Reflex | Similar speed; less familiar, smaller ecosystem |

## Decision
A.
- **Import rule:** `app.py` and `cli.py` import only `engine.services` and `engine.contracts`, enforced by import-linter.
- **Services:** every service checks membership and writes an `events` row.
- **i18n:**
  - all strings go through `gettext`;
  - catalogues live in `locales/{pt_BR,en_US}`;
  - CI fails on untranslated or fuzzy strings;
  - `users.ui_locale` defaults to the browser's `Accept-Language`, then pt-BR.
  - The UI language is independent of the campaign's content locale.

## Consequences
- Easier: replacing Streamlit later only means rewriting screens.
- Harder: Streamlit has limits on custom interaction (rerun model) and needs a small i18n helper.

## Revisit when
The first external customer logs in, or design matters for sales. Then build FastAPI + a front end over `services`.

## Action items
- [x] M0-05: `services.py` skeleton + import-linter
- [ ] M1-02: UI shell + i18n + translation gate
