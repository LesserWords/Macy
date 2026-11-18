# Tasks: M7-04 — Path A: First-Boot 3-Step Setup Wizard & Website URL Auto-Seed Onboarding

## 1. Atomic Onboarding Bootstrap Service (Delivery KR: D1)
- [x] 1.1 Write failing tests in `tests/test_onboarding.py` verifying `services.bootstrap_onboarding` atomically creates owner user (with PBKDF2 password hash), workspace, membership, `enabled_capabilities`, and integration preset, rolls back cleanly on failure, and blocks unauthenticated re-bootstrap when workspaces already exist.
- [x] 1.2 Implement `services.bootstrap_onboarding` and `engine/onboarding.py`. Verify D1: `uv run pytest tests/test_onboarding.py -k bootstrap --no-cov` exits 0.

## 2. Deterministic Website URL & Sitemap Auto-Seeder (Delivery KR: D2)
- [x] 2.1 Write failing tests in `tests/test_onboarding.py` verifying `autoseed_from_url()` enforces `safe_http` SSRF protections, extracts brand profile `v1` and sitemap publications with zero LLM calls, and handles missing sitemaps gracefully.
- [x] 2.2 Implement `extract_site_seed()` in `engine/onboarding.py` and `services.autoseed_from_url()`. Verify D2: `uv run pytest tests/test_onboarding.py -k autoseed --no-cov` exits 0.

## 3. Bilingual 3-Step Setup Wizard & `content-engine init` CLI (Delivery KR: D3)
- [x] 3.1 Write failing tests in `tests/test_onboarding.py` and translation catalog checks for `content-engine init` and the 3-step Setup Wizard UI strings across `pt_BR` and `en_US`.
- [x] 3.2 Implement the 3-step Setup Wizard in `app.py`, add `content-engine init` to `engine/cli.py`, and compile `locales/{pt_BR,en_US}`. Verify D3: `uv run python scripts/check_translations.py && uv run pytest tests/test_onboarding.py --no-cov` exits 0.

## 4. Dual-Path E2E Verification (`J-POCKET` + Path B Stateless) & Docs (Delivery KR: D4)
- [x] 4.1 Write failing Playwright offline E2E test `tests/e2e/test_j_pocket_onboarding.py` covering empty-DB boot → local owner + capabilities (`core-compliance`, `seo-audit`) → `pocket_free` preset → filtered sidebar verification → Path B stateless CLI & MCP smoke check.
- [x] 4.2 Update `docs/runbook.md`, `docs/guides/user-guide.md`, and `docs/MCP.md` for Path A and Path B workflows, and verify all quality gates. Verify D4: `uv run pytest tests/e2e/test_j_pocket_onboarding.py --no-cov && sh scripts/check.sh` exits 0.
