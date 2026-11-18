# Proposal: M6-02 — Non-WordPress URL & Bounded Site Audit Crawler

**Plan Item:** M6-02 (Proposed) · **Features:** F08, F14, F21 · **ADRs:** ADR-005, ADR-006, ADR-009, ADR-025

## Why

Macy’s goal extends beyond generating articles to **auditing blogs, landing pages, and websites** cleanly without WordPress lock-in. Today Macy has `trafilatura` extraction, `sitemap.py`, and SSRF-safe `safe_http.py`, but no way to audit a live URL or crawl a site for broken links, technical SEO issues, or AI-search (GEO) readiness.

## Source Logic Absorbed (License-Safe)

- **OpenSEO (`MIT`, `src/server/audit/` & `src/server/crawl.ts`):**
  - **7-category weighted scoring (`scoring.ts`, `grades.ts`):** `onpage` (30%), `performance` (20% — optional PSI/TTFB), `usability` (12%), `security` (12%), `links` (10%), `social` (8%), `ai` (8%); `pass=1.0`, `warn=0.5`, `fail=0.0`; letter grades `A+` (`>=95`) to `F` (`<40`).
  - **GEO / AI Search checks (`checks/ai.ts`):** `robots.txt` policy split between AI search bots (`OAI-SearchBot`, `ChatGPT-User`, `Claude-SearchBot`, `PerplexityBot`) vs. training bots (`GPTBot`, `ClaudeBot`, `Google-Extended`, `CCBot`), `/llms.txt` check, SSR content-without-JS check (`wordCount < 50` + SPA root markers), semantic landmarks (`<main>`, `<article>`, `<header>`, `<nav>`, `<footer>`), entity schema (`Organization`/`Person`/`LocalBusiness` + `sameAs`).
  - **Bounded BFS crawler (`crawl.ts`):** `MAX_CRAWL_PAGES = 500`, `www.` + `#fragment` URL normalization, binary/asset extension skip list, per-page extraction (`title`, `description`, `h1`, `wordCount`, `canonical`, `noindex`, `lang`, `imagesMissingAlt`, internal/external link counts).
- **SEOnaut (`MIT`, `internal/crawler/crawler.go`, `internal/issues/`):**
  - **Technical & Multipage issue detectors:** `3xx/4xx/5xx`, redirect chains & loops, broken internal & external links (concurrent `HEAD` with `GET` fallback & timeout), duplicate titles/descriptions/content, **orphan pages** (in sitemap or discovered with 0 internal inlinks), **dead-end pages** (0 outlinks), canonical vs. `noindex` vs. sitemap conflicts, slow TTFB, missing `viewport`, security headers (`HSTS`, `CSP`, `X-Content-Type-Options`).
- **seo-audit-skill (`MIT`, `seo-audit-full/scripts/check-site.py` & `check-page.py`):**
  - `robots.txt` per-user-agent group parser, sitemap directory inventory classifier, staging subdomain exposure check (`staging.`, `dev.`, `beta.`), and `difflib.SequenceMatcher` soft-404 / near-duplicate content similarity.
- **llms-txt (`Apache-2.0`, `llms_txt/core.py`) & zens-ink (`MIT`):**
  - `/llms.txt` parser/validator and generator from crawled pages / `sitemap.py`.
- **axe-core (`MPL-2.0`):**
  - Optional Playwright `axe.min.js` runner for JS-rendered landing pages (WCAG + SEO overlap: `image-alt`, `document-title`, `html-has-lang`, `heading-order`, `link-name`, `color-contrast`, `meta-viewport`).

## What Changes

- **Single-URL Auditor (`engine/audit/`):**
  - `scoring.py`: 7-category weighted scoring + `A+..F` grades + prioritized recommendations.
  - `page_checks.py`, `ai_checks.py`, `links.py`: Deterministic checks on fetched HTML/headers via `engine/safe_http.py`.
- **Bounded Async Site Crawler (`engine/crawl/`):**
  - `crawler.py`: `httpx` + `asyncio` BFS crawler respecting `robots.txt`, sitemap seeding (`engine/sitemap.py`), configurable `max_pages` (default 100, hard cap 500), and `safe_http` SSRF protection.
  - `multipage.py`: Cross-page detectors (duplicate title/meta/content, orphan pages, dead-ends, redirect chains, external broken links).
- **Persistence, CLI, UI & MCP:**
  - Postgres migration for `site_audits` and `audit_pages` (workspace-scoped, composite FK to `workspaces`).
  - CLI: `content-engine audit <workspace> <url> [--crawl] [--max-pages 100] [--axe]`.
  - Streamlit Audit tab + MCP tools (`macy_audit_url`, `macy_crawl_site`).

## Capabilities

### New Capabilities
- `url-audit`: Single-URL SEO + GEO + technical audit with 0–100 weighted scoring.
- `site-crawl`: Bounded site crawl with broken-link, redirect-chain, duplicate, and orphan-page detection.

### Modified Capabilities
- `mcp-server`: Expose `macy_audit_url` and `macy_crawl_site` tools.

## Out of Scope

- Unbounded internet crawling or heavy Chromium/Redis infrastructure (keeps `TECH-RADAR.md` Hold on Crawl4AI/Firecrawl; `axe-core` via Playwright is strictly opt-in).
- Continuous scheduled monitoring, GSC, and SERP rank tracking (covered in `M6-03`).

## Key Results: Delivered

All `D1`–`D4` must pass before `M6-02` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `engine/audit/` computes weighted `0–100` (`A+..F`) scores across 7 categories (`onpage`, `performance`, `usability`, `security`, `links`, `social`, `ai`/GEO) via `safe_http` and normalizes weights dynamically when optional toggles are disabled | `uv run pytest tests/test_url_audit.py --no-cov` exits `0` |
| **D2** | `engine/crawl/` enforces plan-based `max_pages` (`20` free default, `50` paid, `200` hard cap), persists workspace-scoped `site_audits` + `audit_pages`, and detects broken internal/external links, redirect chains, duplicate meta, and orphan pages | `uv run pytest tests/test_site_crawl.py tests/test_tenant_scoping.py --no-cov` exits `0` |
| **D3** | `BrowserAuditPort` (`[audit-browser]` `axe-core` extra) and `PageSpeedPort` (Google PSI) toggles degrade gracefully to local HTML/TTFB checks when disabled, missing, or timed out | `uv run pytest tests/test_audit_toggles.py --no-cov` exits `0` |
| **D4** | `content-engine audit`, Streamlit Audit UI, and MCP tools (`macy_audit_url`, `macy_crawl_site`) pass end-to-end tests and all repo quality gates | `uv run pytest tests/test_cli.py tests/test_mcp_audit.py --no-cov && sh scripts/check.sh` exits `0` |

## Decisions Locked (2026-11-12)

1. **Plan-Flexible Crawl Caps:** Default `max_pages = 20` (free tier), configurable up to `50` for paid plans, with a `200` hard cap enforced in code.
2. **Modular `axe-core` Toggle:** `Playwright + axe-core` lives in optional extra `[project.optional-dependencies] audit-browser` behind a `BrowserAuditPort` toggle (off by default on the 4 GB VPS).
3. **Modular Google PageSpeed Insights (PSI) Toggle:** `PageSpeedPort` is an optional toggle layered on top of local TTFB + HTML/asset weight checks, with dynamic score normalization when toggled off.
