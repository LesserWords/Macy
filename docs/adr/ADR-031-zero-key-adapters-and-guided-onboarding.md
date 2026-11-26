# ADR-031: Zero-Key Plug-and-Play Adapters & Guided URL Auto-Seed Onboarding

**Status:** Accepted · **Date:** 2026-11-17 · **Deciders:** Project Maintainer
**Related:** features F01, F03, F04, F05, F07, F11, F22 · ADR-003, ADR-005, ADR-007, ADR-008, ADR-029, ADR-030

## Context
Even after simplifying container startup (`ADR-030`), running a first editorial cycle currently requires:
1. Running 4 CLI commands (`create-workspace`, `add-owner`, `seed-brand`, `seed-sources`).
2. Procuring paid/card-gated API keys for **OpenRouter** (`LLMPort`), **Brave Search** (`NewsSearchPort`), and **DataForSEO** (`KeywordProviderPort`).
Users who want to run local models (`Ollama`), use a single direct LLM key (`OpenAI`, `Gemini`, `Anthropic`), or test the pipeline with zero paid search/keyword subscriptions cannot run live research or brief generation out of the box.

## Options considered
| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. Zero-key default adapters (`ollama`/`openai_compat`, `gnews_rss`, `autocomplete`) + Stack Presets + 3-Step First-Boot Setup Wizard with URL Auto-Seeding** (chosen) | Medium | `$0` | High — immediate end-to-end usability with zero paid SEO/search APIs |
| B. Require `Fake*` adapters until all 3 paid vendor keys are entered | Low | `$0` | Poor — `Fake*` returns static fixture strings, not real site/topic data |
| C. Bundle headless browser scraping for Google SERPs | High | High RAM | Rejected per `TECH-RADAR.md` and `ADR-028` |

## Decision
1. **Universal OpenAI-Compatible & Local Ollama LLM Adapter (`engine/integrations/openai_compat.py`):**
   - Register `openai_compat` and `ollama` under `macy.llm`.
   - Support custom `base_url` (defaulting to `http://host.docker.internal:11434/v1` for `ollama` with `$0.000000` ledger cost, or provider endpoints for direct OpenAI/Gemini/Anthropic keys) using `instructor` structured outputs.
2. **Zero-Key News Search Adapter (`engine/integrations/gnews_rss.py`):**
   - Register `gnews_rss` under `macy.news_search`.
   - Query Google News RSS (`https://news.google.com/rss/search?q=...&hl=<locale>&gl=<market>`) including domain-scoped `site:<domain>` queries via `safe_http` + `defusedxml`/`feedparser` with zero API keys required.
3. **Zero-Key Autocomplete Keyword Provider (`engine/integrations/autocomplete_keywords.py`):**
   - Register `autocomplete` under `macy.keyword_provider`.
   - Fetch public search autocomplete expansions via `safe_http`, combine with any cached GSC query metrics when available, and return `KeywordData` explicitly marked as `is_estimate=True` (honoring `F11` / `ADR-004` estimate labeling and the 30-day `keyword_cache`).
4. **Integration Stack Presets (`services.apply_integration_preset`):**
   - Provide one-click presets: `pocket_free` (`gnews_rss` + `autocomplete` + `rss` + `trafilatura` + `markdown_export`), `local_ollama` (`pocket_free` + `ollama`), and `pro_cloud` (`openrouter` + `brave` + `dataforseo` + `http_webhook`).
5. **First-Boot Setup Wizard & URL Auto-Seeder (`engine/onboarding.py` & `app.py`):**
   - When `list_workspaces()` is empty, `app.py` renders a bilingual (`pt-BR`/`en-US`) 3-step Setup Wizard (also exposed via `macy init`):
     - **Step 1:** Create owner account + choose active Capability Plugins (`ADR-029`).
     - **Step 2:** Select Integration Stack Preset and enter optional LLM key/URL.
     - **Step 3:** Enter target website URL (or choose an industry pack). `autoseed_from_url()` fetches the homepage and sitemap via `safe_http` + `trafilatura` to seed `brand_profiles` (`v1`) and `content_inventory` automatically.

## Consequences
- Easier: A new user goes from `docker compose -f compose.pocket.yaml up -d` to a seeded workspace with live search, keyword estimates, and local/cloud LLM in under 60 seconds without running any CLI commands.
- Harder: Public RSS/autocomplete endpoints can rate-limit aggressive polling; mitigated via strict per-run query bounds, `safe_http` timeouts, and the 30-day `keyword_cache`.

## Revisit when
Google News RSS or Autocomplete endpoints change response formats (covered by contract tests and graceful fallback).

## Action items
- [ ] `M7-03` — Implement `openai_compat`/`ollama`, `gnews_rss`, `autocomplete` keyword provider, and `services.apply_integration_preset`.
- [ ] `M7-04` — Implement `engine/onboarding.py` (`bootstrap_onboarding`, `autoseed_from_url`), `macy init`, the 3-step Streamlit Setup Wizard, and offline E2E journey `J-POCKET`.
