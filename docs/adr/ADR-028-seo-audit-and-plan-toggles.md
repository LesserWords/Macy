# ADR-028: Modular SEO/GEO Audit, Bilingual Readability & Plan-Tier Toggles

**Status:** Accepted · **Date:** 2026-11-12 (implemented 2026-11-16) · **Deciders:** Project Maintainer
**Related:** features F05, F08, F13, F14, F17, F21 · ADR-002, ADR-005, ADR-008, ADR-015, ADR-016, ADR-025

## Context

Macy is expanding from a sourced article pipeline (`M0–M5`) into a general-purpose, CMS-agnostic content creation and SEO/GEO audit tool for blogs, landing pages, and websites across WordPress, Webflow, headless CMSs, and custom stacks (`M6-01` through `M6-03`). Key constraints:
1. **4 GB VPS footprint (`TECH-RADAR.md`):** Core runtime must remain lightweight (no mandatory Chromium/Playwright or Redis).
2. **Future Free vs. Paid tiers:** Crawl limits (`max_pages` default `20`, paid `50`, hard cap `200`), optional `axe-core` browser audits, optional Google PageSpeed Insights (PSI), and optional paid `RankTrackerPort` SERP tracking must toggle cleanly per workspace/plan without code forks.
3. **Bilingual accuracy (`ADR-015`):** Readability scoring must handle both `en-US` and `pt-BR` accurately, with a tiered severity threshold (`minor` warning vs. `major` revision-blocking failure).
4. **Modularity (`ADR-002`, `ADR-005`):** Deterministic checks/JSON-LD generation must have zero dependency on LLM rewrite modes (`improve`, `simplify`, `seo_optimize`), and vice versa.

## Options considered

| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. Modular `Protocol` ports + workspace plan/feature toggles** (chosen) | Medium | `$0` base; pay-per-use optional | High — matches `engine/integrations/ports.py` and `TECH-RADAR.md` |
| B. Monolithic audit engine with bundled Chromium + mandatory external APIs | High | High RAM (4 GB VPS risk) + API fees | Poor — violates `TECH-RADAR.md` Hold on browser crawlers |
| C. Separate microservice for crawling & audits | High | Extra container + IPC overhead | Over-engineered for current scale |

## Decision

1. **Tiered Readability & `pt-BR` Flesch (`M6-01`):**
   - `en-US`: standard Flesch Reading Ease (`206.835 - 1.015*(W/S) - 84.6*(Syl/W)`).
   - `pt-BR`: **Martins, Ghiraldelo, Nunes, and Oliveira Jr. (1996)** Portuguese Flesch formula (`248.835 - 1.015*(W/S) - 84.6*(Syl/W)`).
   - **Tiered severity:** Score `>= 60` → `pass`; `40 <= score < 60` → `minor` (advisory, does not block `PASS`); `score < 40` → `major` (triggers `REVISION` loop).
2. **Modular Content + Schema + AI Rewrite (`M6-01`):**
   - `engine/quality/checks.py` and `engine/production/schema.py` (`Article`, `FAQPage`, `Product`, `LocalBusiness`, `HowTo`) are 100% deterministic with zero LLM dependency.
   - Seovela-style LLM rewrite modes (`improve`, `simplify`, `seo_optimize`) live as independent prompt/service actions using `LLMPort`, never imported by `engine/quality/`.
3. **Plan-Parameterized Crawler & Optional Audit Toggles (`M6-02`):**
   - `crawl_site` accepts `max_pages` resolved from workspace plan config: default `20` (free), `50` (paid tier), hard ceiling `200` (enforced in code).
   - **Optional `BrowserAuditPort` (`axe-core` via Playwright):** Behind `[project.optional-dependencies] audit-browser`; when disabled or unavailable, `usability` checks fall back to deterministic HTML checks (`viewport`, `img[alt]`, `html[lang]`, `h1`).
   - **Optional `PageSpeedPort` (Google PSI API):** Layered on top of local TTFB + HTML/asset byte-weight checks; disabled when no API key/toggle is active.
4. **Free-First Monitoring, Hybrid GSC & Webhook Alerts (`M6-03`):**
   - Free tier: **Hybrid GSC** (`AnalyticsSourcePort` supporting both Service Account JSON for unattended cron and OAuth2 refresh tokens for multi-property discovery, with 12h URL Inspection cache) + scheduled local URL/TLS monitoring.
   - **Standard Webhooks only:** Monitor regressions (`score_drop >= 5`, new `critical` issue, or `tls_days_left <= 14`, after 1 retry at `30s`) emit into Macy's existing `events` + `webhook_deliveries` outbox — no built-in SMTP.
   - Paid tier toggle: `RankTrackerPort` (Serper/DataForSEO SERP) gated by workspace feature flag + `usage_ledger` spend cap.

## Consequences

- Easier: Rolling out a free tier vs. paid tier via workspace entitlement config without maintaining two codebases; keeping the 4 GB VPS lean.
- Harder: Scoring must normalize weights dynamically when optional modules (`axe-core`, `PSI`) are toggled off so scores remain comparable.

## Revisit when

- Workspace count or concurrent crawls exceed single-worker `asyncio` capacity on the 4 GB VPS (trigger: crawl queue latency > 5 min).

## Action items

- [x] `M6-01` — Implement tiered readability (`en-US` + Martins et al. `pt-BR`), 5 JSON-LD schemas, and modular LLM rewrite modes.
- [x] `M6-02` — Implement `engine/audit/`, `engine/crawl/` (`20`/`50`/`200` caps), and optional `axe-core` + PSI toggles.
- [x] `M6-03` — Implement hybrid GSC (`AnalyticsSourcePort`), Standard-Webhook URL/TLS monitors, and paid `RankTrackerPort` toggle.
