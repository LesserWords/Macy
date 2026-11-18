# Proposal: M7-04 — Path A: First-Boot 3-Step Setup Wizard & Website URL Auto-Seed Onboarding

**Plan Item:** M7-04 (Planned) · **Features:** F01, F03, F04, F08, F19, F22 · **ADRs:** ADR-009, ADR-014, ADR-029, ADR-030, ADR-031

## Why

After `M7-02` enables zero-`.env` startup and `M7-03` delivers zero-key adapters, a brand-new user still lands on an empty database that requires running 4 manual `docker compose exec` CLI commands (`create-workspace`, `add-owner`, `seed-brand`, `seed-sources`) and optionally `import-sitemap` before the Admin UI is usable. To make Macy genuinely plug-and-play in under two minutes, first-time boot must guide the user through a 3-step UI Wizard (and one-command `content-engine init` CLI) that provisions their owner account, capabilities, preset, and auto-seeds brand & sitemap data from their website URL.

## What Changes

- **Atomic Onboarding Bootstrap Service (`engine/services.py`, `engine/onboarding.py`):**
  - Implement `services.bootstrap_onboarding(...)` creating the owner user (with PBKDF2 password hash for local auth), workspace, membership, `enabled_capabilities`, and initial integration stack preset inside one database transaction—refusing unauthenticated execution once workspaces exist.
- **Website URL & Sitemap Auto-Seeder (`engine/onboarding.py`):**
  - Implement `autoseed_from_url()` fetching the homepage and `sitemap.xml` strictly via `safe_http` + `trafilatura` + `lingua-py` to seed `brand_profiles` (`v1`, `is_current=true`) and `content_inventory` with zero LLM calls, falling back cleanly to pack fixtures if the target URL is unreachable or skipped.
- **Bilingual 3-Step Setup Wizard & `content-engine init` CLI (`app.py`, `engine/cli.py`, `locales/`):**
  - When `list_workspaces()` returns empty, render the bilingual (`pt-BR`/`en-US`) 3-step Setup Wizard in `app.py` (Step 1: Owner + Capabilities; Step 2: Integration Preset + optional LLM key; Step 3: Target Website URL / Industry Pack) and expose `content-engine init`.
- **End-to-End Verification (`J-POCKET` + Path B Smoke):**
  - Add Playwright offline E2E journey `J-POCKET` (`tests/e2e/test_j_pocket_onboarding.py`) and Path B stateless CLI check (`DATABASE_URL="" content-engine audit ... --stateless`), plus updated `docs/runbook.md`, `docs/guides/user-guide.md`, and `docs/MCP.md`.

## Capabilities

### New Capabilities
- None (extends `capabilities`, `brand`, and `admin-ui`).

### Modified Capabilities
- `capabilities`: Atomic onboarding bootstrap service and `content-engine init` CLI.
- `brand`: Deterministic website URL and sitemap auto-seeder creating `brand_profiles` (`v1`) and `content_inventory`.
- `admin-ui`: Bilingual (`pt-BR`/`en-US`) 3-step First-Boot Setup Wizard and offline E2E journey `J-POCKET`.

## Out of Scope

- LLM-driven competitor scraping or multi-site onboarding crawls beyond homepage + sitemap seeding.
- Modifying existing `J0`–`J8` E2E test assertions on pre-seeded workspaces.

## Key Results: Delivered

All `D1`–`D4` must pass before `M7-04` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `services.bootstrap_onboarding` atomically creates owner, workspace, `enabled_capabilities`, and preset in one transaction, rolling back on error and blocking unauthenticated re-bootstrap once a workspace exists | `uv run pytest tests/test_onboarding.py -k bootstrap --no-cov` exits `0` |
| **D2** | `autoseed_from_url` extracts homepage metadata and sitemap URLs via `safe_http` (refusing private/loopback/metadata IPs) with zero LLM cost to seed `brand_profiles` (`v1`) and `content_inventory`, with graceful pack fallback | `uv run pytest tests/test_onboarding.py -k autoseed --no-cov` exits `0` |
| **D3** | Bilingual 3-step Setup Wizard (`pt-BR`/`en-US`) passes translation catalog checks with 0 fuzzy/untranslated strings, and `content-engine init` bootstraps workspaces in one CLI command | `uv run python scripts/check_translations.py && uv run pytest tests/test_cli_init.py --no-cov` exits `0` |
| **D4** | Offline E2E `J-POCKET` completes the 3-step wizard (`core-compliance` + `seo-audit` only), verifies hidden editorial tabs, runs a UI audit with 0 errors, Path B stateless audit succeeds, and all repo gates pass | `uv run pytest tests/e2e/test_j_pocket_onboarding.py --no-cov && sh scripts/check.sh` exits `0` |

## Impact

- **Code:** `engine/onboarding.py`, `engine/services.py`, `engine/cli.py`, `app.py`, `locales/{pt_BR,en_US}/LC_MESSAGES/macy.po`
- **Docs & Tests:** `tests/test_onboarding.py`, `tests/test_cli_init.py`, `tests/e2e/test_j_pocket_onboarding.py`, `docs/runbook.md`, `docs/guides/user-guide.md`, `docs/MCP.md`
