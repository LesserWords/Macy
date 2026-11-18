# Proposal: M6-03 — Post-Publish Monitoring, Google Search Console & Rank Tracking (Phase 4)

**Plan Item:** M6-03 (Proposed — Phase 4 per `MVP-PRD.md §2`) · **Features:** F05, F06, F18, F20 · **ADRs:** ADR-005, ADR-008, ADR-016, ADR-017

## Why

Once articles and landing pages are published and audited (`M5` + `M6-02`), operators need a closed feedback loop to know:
1. Whether live pages regress (score drops, broken links, expiring TLS certificates).
2. Which pages/queries are gaining or losing clicks in Google Search Console (decay triage, cannibalization, striking-distance opportunities).
3. Optionally, daily SERP positions for target keywords when GSC data isn't enough.

Per `MVP-PRD.md`, GSC/analytics feedback is intentionally deferred to **Phase 4** (after >= 8 weeks of published content).

## Source Logic Absorbed (License-Safe)

- **CrawlSEO (`MIT`, `crawlseo-prd.md`) & SerpBear (`MIT`, `pages/api/searchconsole.ts`, `services/searchConsole.ts`):**
  - Unified Google integration: **GSC Search Analytics API** (`clicks`, `impressions`, `ctr`, `position` by query + page, 1,200 QPM free) + **Google URL Inspection API** (index coverage status, 2,000/day free).
  - Service-account / OAuth credentials stored encrypted per workspace in `workspace_integrations`.
- **gsc-hub (`MIT`, `.agents/skills/` & `PRODUCT.md`):**
  - **12-hour cache** on URL Inspection API calls to protect the 2,000/day quota.
  - Three high-signal diagnostic workflows:
    1. **Striking-Distance Plan (`gsc-striking-plan`):** Queries at positions `4–20` with above-median impressions and below-benchmark CTR -> prioritize title/meta rewrite vs. internal linking.
    2. **Decay Triage (`gsc-decay-triage`):** Compare recent 7d/28d window vs. prior window; distinguish demand drop (same pos, lower impressions) vs. ranking loss (lower pos + impressions) vs. SERP/CTR shift (same pos/impressions, lower clicks) vs. site-wide technical outage.
    3. **Cannibalization Detection (`gsc-cannibalization`):** Flag queries where 2+ workspace URLs split impressions/clicks.
- **SerpBear (`MIT`, `database/models/keyword.ts`, `scrapers/index.ts`):**
  - `RankTrackerPort` (`Protocol`) with pluggable SERP API adapters (Serper.dev, SerpAPI, ValueSerp, DataForSEO SERP) — avoiding fragile raw scraping.
  - `keyword_rankings` schema (`workspace_id`, `domain`, `keyword`, `device`, `country`, `position`, `url`, `history_json`, `checked_at`) + `usage_ledger` USD cost recording per lookup.
- **OpenSEO (`MIT`, `src/server/monitors/run.ts` & `alerts.ts`):**
  - Scheduled URL monitor with single retry (`30s`), TLS certificate expiry check (`daysLeft`), before/after `AuditReport` diffing, and webhook alert triggers (`score-drop`, `new-failures`, `tls-expiring`).
- **MainWP (`GPL-3.0`, UX concepts only):**
  - Multi-workspace health/uptime overview cards and exportable client/stakeholder summary reports.

## What Changes

- **Implement `AnalyticsSourcePort` for GSC (`engine/integrations/gsc.py`):**
  - Search Analytics queries + 12h-cached URL Inspection + Striking-Distance / Decay / Cannibalization analyzers (`engine/strategy/gsc_insights.py`).
- **Add `RankTrackerPort` (`engine/integrations/ports.py`, `engine/integrations/serp_tracker.py`):**
  - Optional SERP position tracking with Serper / DataForSEO SERP adapters and `usage_ledger` cost caps.
- **Scheduled URL Monitors (`engine/monitors/`):**
  - Periodic re-audit of tracked URLs, TLS expiry inspection, report diffing, and outbound Standard Webhooks (`audit.score_dropped`, `monitor.tls_expiring`).

## Capabilities

### New Capabilities
- `gsc-analytics`: Google Search Console sync, URL inspection, and striking-distance / decay / cannibalization diagnostics.
- `url-monitoring`: Scheduled URL regression & TLS monitoring with webhook alerts.
- `rank-tracking`: Optional `RankTrackerPort` for daily/weekly keyword SERP positions.

### Modified Capabilities
- `integrations`: Register `gsc` under `content_engine.analytics_source` and add `content_engine.rank_tracker` entry point group.

## Out of Scope

- WordPress plugin management, WP uptime/backup/update orchestration (MainWP).
- Self-hosted Puppeteer/browser-farm SERP scraping without an API provider.

## Key Results: Delivered

All `D1`–`D4` must pass before `M6-03` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `GSCAnalyticsSource` (`AnalyticsSourcePort`) authenticates with both encrypted `service_account` JSON and `oauth2` refresh tokens, queries Search Analytics, and enforces a 12-hour Postgres cache on URL Inspection calls (`2,000/day` quota guard) | `uv run pytest tests/test_gsc_integration.py --no-cov` exits `0` |
| **D2** | `engine/strategy/gsc_insights.py` deterministically computes Striking-Distance (`pos 4..20`, above-median impressions, low CTR), Decay Triage (`7d`/`28d` deltas), and Query Cannibalization reports | `uv run pytest tests/test_gsc_insights.py --no-cov` exits `0` |
| **D3** | `engine/monitors/runner.py` retries transient failures once after `30s` and emits HMAC-signed Standard Webhooks (`audit.score_dropped` on `>= 5` pt drop, `audit.critical_issue`, `monitor.tls_expiring` on `<= 14d`) via `events` + `webhook_deliveries` | `uv run pytest tests/test_monitors.py --no-cov` exits `0` |
| **D4** | `RankTrackerPort` (Serper/DataForSEO SERP) is gated behind `rank_tracking_enabled`, enforces `usage_ledger` spend caps (`BUDGET_CAP_EXCEEDED`), persists `keyword_rankings`, and passes all repo gates | `uv run pytest tests/test_rank_tracker.py --no-cov && sh scripts/check.sh` exits `0` |

## Decisions Locked (2026-11-12)

1. **Free vs. Paid Toggle:** Include the free tier (GSC + local URL/TLS monitoring) by default, and gate `RankTrackerPort` (paid SERP APIs) behind a workspace/plan feature toggle (`rank_tracking_enabled`).
2. **Hybrid GSC Auth Model:** Support **Service Account JSON** first (stored encrypted in `workspace_integrations`, ideal for unattended cron) and **OAuth2 refresh token** (multi-property auto-discovery) on the same `AnalyticsSourcePort` interface.
3. **Standard Webhooks Only for Route Monitoring:** Route all monitor alerts exclusively through Macy's existing `events` + `webhook_deliveries` HMAC-signed outbox (`audit.score_dropped`, `audit.critical_issue`, `monitor.tls_expiring`), with no built-in SMTP server.
4. **Default Monitor Triggers & Retry:** Trigger alerts on `score_drop >= 5` points, any new `critical` issue (e.g., `4xx`/`5xx`, `noindex`, broken internal link), or TLS expiry `<= 14` days; retry transient network failures once after `30s`.
