# Contributing to Macy

Thank you for your interest in contributing to **Macy** (`macy`).

## Development Setup

1. Install Python `3.12+` and `uv`.
2. Sync dependencies:
   ```sh
   uv sync --all-extras --dev
   ```
3. Start local PostgreSQL (or the full dev stack):
   ```sh
   docker compose -f infra/compose.yaml -f infra/compose.override.yaml up -d db migrate
   ```

## Workflow & Quality Gates

Macy follows spec-driven, test-first development:

- **OpenSpec:** New capabilities and behavior changes are specified under `openspec/specs/` and `openspec/changes/`.
- **Quality Gates:** Before opening a pull request, run the full local CI gate suite:
  ```sh
  sh scripts/check.sh
  ```
  This enforces `ruff` (lint & format), `mypy --strict`, `complexipy` (`<= 15`), `vulture`, `lint-imports`, `jscpd`, `squawk-cli`, `pytest` (`>= 90%` branch coverage), `100%` `diff-cover`, `scripts/check_oss_hygiene.py`, and `openspec validate --all --strict --no-interactive`.
- **Translations:** If you add or change user-facing strings wrapped in `_("...")`, update `locales/pt_BR/LC_MESSAGES/messages.po` and `locales/en_US/LC_MESSAGES/messages.po`, compile with `uv run pybabel compile -d locales`, and verify `uv run python scripts/check_translations.py`.

## Authoring Custom Packs & Brand Fixtures

- Built-in domain-neutral packs live under `packs/industries/{general,b2b-saas}/` and `packs/formats/`.
- Built-in sample brand profile lives at `fixtures/seed_example.yaml` (see [Brand Fixtures Guide](docs/guides/brand-fixtures-guide.md)).
- Never commit proprietary company context, credentials, or `.env` files. Use `./context/` (git-ignored) or `MACY_CONTEXT_DIR` for private packs and fixtures.
