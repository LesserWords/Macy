# Design

## Context

See `proposal.md` for motivation and background. Macy uses curated feeds and Brave NewsSearch for `site_search` sources. RSSHub and GDELT DOC 2.0 were scheduled as time-boxed spike trials in M1 to determine if self-hosted RSS generation or free global news search could reduce paid search calls while maintaining data quality and verified dates.

## Goals / Non-Goals

**Goals:**
- Provide empirical evaluation scripts and test coverage for both trials under `spikes/trials/` and `tests/test_spike_trials.py`.
- Formally evaluate RSSHub routes against the 13 `site_search` seed sources in `packs/industries/general/sources.yaml`.
- Implement `GDELTNewsSearch` implementing `NewsSearchPort` with strict 1 request / 5 seconds rate limiting.
- Compare a 14-day sample of `pt-BR` optical queries between Brave NewsSearch and GDELT DOC 2.0, measuring unique relevant stories.
- Remove the `rsshub` service profile from `compose.yaml` upon failing the $\ge 5$ keep criterion.
- Document trial methodology, empirical numbers, and decisions in `docs/architecture/TECH-RADAR.md` and `docs/adr/ADR-003-research-sources.md`.

**Non-Goals:**
- Adding custom Chromium-based scrapers or maintaining bespoke CSS selectors for corporate sites in RSSHub.
- Registering GDELT as a permanent production entry point when it yields < 20% unique relevant stories.
- Testing full production ingestion pipeline with live external networks during unit CI.

## Decisions

### Decision 1: Drop RSSHub and remove profile from Compose
- **Context:** RSSHub (standard non-Chromium Docker image) lacks pre-built radar routes for all 13 niche optical association, trade fair, and corporate domains (`abioptica.com.br`, `fenoptica.com.br`, `fiquedeolho.com.br`, `thevisioncouncil.org`, `mido.com`, `silmoparis.com`, `essilorluxottica.com`, `zeiss.com`, `hoyavision.com`, `keringeyewear.com`, `bausch.com`, `coopervision.com`, `euromonitor.com`).
- **Outcome:** 0 sources yield dated feeds ($0 < 5$). Running custom Puppeteer/Chromium scrapers contradicts the single-VPS resource budget (TECH-RADAR Hold).
- **Action:** Remove `rsshub` service profile from `compose.yaml`.

### Decision 2: Drop GDELT DOC 2.0 and preserve trial implementation in spikes/trials
- **Context:** GDELT DOC 2.0 is a free API with strict global IP rate limits (1 req / 5s, frequent 429 errors). On optical retail terminology in Brazilian Portuguese (`pt-BR`), GDELT returns broad press wire aggregations and noisy crime/police reports mentioning eyeglasses, while returning 0 site-scoped results for niche trade domains.
- **Outcome:** In 14-day comparison against Brave Search News, Brave delivered 12 relevant optical stories with verified dates and authoritative domains, while GDELT delivered 0 unique relevant stories ($0\% < 20\%$).
- **Action:** Drop GDELT from production entry points (`content_engine.news_search`). Maintain the trial plugin and comparison logic in `spikes/trials/` and `tests/test_spike_trials.py` as durable architectural evidence.

## Risks / Trade-offs

- [Risk: Future changes to Brave pricing or API limits] → Mitigation: Brave remains the sole paid search plugin for M1 (ADR-003). GDELT architecture is documented in spikes should specialized international monitoring be reconsidered in later milestones.
- [Risk: Clean compose.yaml without dangling trial services] → Mitigation: Verified via `docker compose config` and `test_compose_and_docker.py`.
