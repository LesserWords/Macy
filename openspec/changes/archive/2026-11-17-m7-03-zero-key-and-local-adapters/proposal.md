# Proposal: M7-03 — Zero-Key & Local Plug-and-Play Integration Adapters

**Plan Item:** M7-03 (Planned) · **Features:** F05, F07, F11, F22 · **ADRs:** ADR-004, ADR-005, ADR-007, ADR-008, ADR-015, ADR-016, ADR-031

## Why

Today, executing a live editorial cycle requires procuring paid/card-gated accounts for three separate third-party vendors: **OpenRouter** (`LLMPort`), **Brave Search** (`NewsSearchPort`), and **DataForSEO** (`KeywordProviderPort`). Users who already run local **Ollama** models, hold a direct OpenAI/Gemini/Anthropic key, or simply want a `$0` zero-key plug-and-play setup for topic discovery and keyword estimates cannot run live pipelines out of the box.

## What Changes

- **Universal OpenAI-Compatible & Local Ollama LLM Adapter (`engine/integrations/openai_compat.py`):**
  - Implement `UniversalOpenAILLM` (`openai_compat`) and `OllamaLLM` (`ollama`) adhering to `LLMPort` + `instructor` structured outputs.
  - Default `ollama` to `http://host.docker.internal:11434/v1` with explicit local-loopback opt-in and `$0.000000` ledger cost; support direct custom provider endpoints for `openai_compat` protected by `safe_http`.
- **Zero-Key News Search Adapter (`engine/integrations/gnews_rss.py`):**
  - Implement `GoogleNewsRSSSearch` (`gnews_rss`) on `NewsSearchPort` querying Google News RSS feeds (`site:<domain>` and topic searches) across `pt-BR`/`BR` and `en-US`/`US` via `safe_http` and `defusedxml`/`feedparser` with `$0.000000` spend.
- **Zero-Key Autocomplete Keyword Estimator (`engine/integrations/autocomplete_keywords.py`):**
  - Implement `AutocompleteKeywordProvider` (`autocomplete`) on `KeywordProviderPort` querying public autocomplete suggestions via `safe_http`, reusing the 30-day `keyword_cache`, enforcing `UnsupportedLocale`, and strictly marking all returned `KeywordData` rows as `is_estimate=True` (`search_volume=0` unless enriched by cached GSC metrics).
- **One-Click Integration Stack Presets (`engine/integrations/presets.py`, `engine/services.py`):**
  - Provide `services.apply_integration_preset(conn, workspace_id, preset=...)` supporting `pocket_free`, `local_ollama`, and `pro_cloud`, writing encrypted/non-secret bindings across ports atomically and recording `integration.preset_applied` in `events`.

## Capabilities

### New Capabilities
- None (extends existing `integrations`, `research`, and `keywords` specs).

### Modified Capabilities
- `integrations`: Universal OpenAI-compatible + local Ollama LLM plugins and one-click integration stack presets.
- `research`: Zero-key Google News RSS `NewsSearchPort` plugin (`gnews_rss`).
- `keywords`: Zero-key Autocomplete `KeywordProviderPort` estimator (`autocomplete`) honoring `is_estimate=True` and 30-day cache.

## Out of Scope

- Scraping Google SERPs via headless Chromium (rejected per `TECH-RADAR.md` and `ADR-028`).
- First-boot UI Setup Wizard and website URL auto-seeding (covered in `M7-04`).

## Key Results: Delivered

All `D1`–`D4` must pass before `M7-03` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `openai_compat` and `ollama` plugins pass the `LLMPort` contract suite (`healthcheck()`, structured `complete()`), enforce `safe_http` on arbitrary URLs while supporting explicit local Ollama loopback, and record `$0.000000` (Ollama) or token USD cost in `usage_ledger` | `uv run pytest tests/test_openai_compat_llm.py --no-cov` exits `0` |
| **D2** | `gnews_rss` passes the `NewsSearchPort` contract suite using recorded HTTP cassettes, supports `site:<domain>` queries, maps `pt-BR`/`BR` and `en-US`/`US`, raises `UnsupportedLocale` otherwise, and records `$0` API spend | `uv run pytest tests/test_gnews_rss.py --no-cov` exits `0` |
| **D3** | `autocomplete` passes the `KeywordProviderPort` contract suite, enforces `UnsupportedLocale` and 30-day `keyword_cache`, and always marks unbacked keyword suggestions as `is_estimate=True` (`search_volume=0`) | `uv run pytest tests/test_autocomplete_keywords.py --no-cov` exits `0` |
| **D4** | `services.apply_integration_preset` atomically configures `pocket_free`, `local_ollama`, and `pro_cloud` presets scoped to the target workspace, emits `integration.preset_applied`, and passes all repo gates | `uv run pytest tests/test_integration_presets.py tests/test_tenant_scoping.py --no-cov && sh scripts/check.sh` exits `0` |

## Impact

- **Code:** `engine/integrations/openai_compat.py`, `engine/integrations/gnews_rss.py`, `engine/integrations/autocomplete_keywords.py`, `engine/integrations/presets.py`, `engine/services.py`, `pyproject.toml`
- **Tests:** `tests/test_openai_compat_llm.py`, `tests/test_gnews_rss.py`, `tests/test_autocomplete_keywords.py`, `tests/test_integration_presets.py`, `tests/test_tenant_scoping.py`
