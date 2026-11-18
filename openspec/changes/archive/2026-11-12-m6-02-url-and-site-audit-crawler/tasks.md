# Tasks: M6-02 — Non-WordPress URL & Bounded Site Audit Crawler

## 1. Single-URL SEO + GEO Checks & 7-Category Weighted Scorer (Delivery KR: D1)
- [x] 1.1 Write failing tests in `tests/test_url_audit.py` covering `scoring.py` (7 categories, `A+..F` grades, dynamic weight normalization when optional toggles are off), `ai_checks.py` (`robots.txt` AI search vs training bots, `/llms.txt`, SSR-without-JS, semantic landmarks, entity schema), and `page_checks.py`.
- [x] 1.2 Implement `engine/audit/{__init__,scoring,page_checks,ai_checks,links}.py` using `engine/safe_http.py`. Verify D1: `uv run pytest tests/test_url_audit.py --no-cov` exits 0.

## 2. Bounded Async Site Crawler & Multipage Detectors (Delivery KR: D2)
- [x] 2.1 Write failing tests in `tests/test_site_crawl.py` covering `max_pages` plan resolution (default `20`, paid `50`, hard cap `200`), `www.`/`#fragment` normalization, sitemap seeding, broken internal/external links, redirect chains, duplicate meta, and orphan pages.
- [x] 2.2 Implement `engine/crawl/{__init__,crawler,multipage}.py` + Postgres migration for `site_audits` and `audit_pages`. Verify D2: `uv run pytest tests/test_site_crawl.py tests/test_tenant_scoping.py --no-cov` exits 0.

## 3. Modular Optional Toggles (`BrowserAuditPort` `axe-core` & `PageSpeedPort`) (Delivery KR: D3)
- [x] 3.1 Write failing tests in `tests/test_audit_toggles.py` verifying graceful fallback when `[audit-browser]` (`axe-core`) or Google PSI API is disabled, missing, or timed out.
- [x] 3.2 Add `BrowserAuditPort` and `PageSpeedPort` in `engine/integrations/ports.py` + `audit-browser` optional extra in `pyproject.toml`. Verify D3: `uv run pytest tests/test_audit_toggles.py --no-cov` exits 0.

## 4. CLI, Streamlit Audit UI & MCP Tools (Delivery KR: D4)
- [x] 4.1 Write failing CLI and MCP tests in `tests/test_cli.py` and `tests/test_mcp_audit.py` for `content-engine audit`, `macy_audit_url`, and `macy_crawl_site`.
- [x] 4.2 Wire CLI, Streamlit Audit tab, and MCP tools via `engine/services.py`. Verify D4: `uv run lint-imports && uv run mypy && uv run pytest tests/test_cli.py tests/test_mcp_audit.py --no-cov && sh scripts/check.sh` exits 0.
