# Tasks: M7-03 — Zero-Key & Local Plug-and-Play Integration Adapters

## 1. Universal OpenAI-Compatible & Local Ollama LLM Adapter (Delivery KR: D1)
- [x] 1.1 Write failing contract and unit tests in `tests/test_zero_key_llm_adapters.py` covering `LLMPort` compliance (`healthcheck()`, structured `complete()`), Ollama local default URL + `$0` ledger recording, SSRF refusal on invalid `base_url`s, and per-stage model routing.
- [x] 1.2 Implement `UniversalOpenAILLM` and `OllamaLLM` in `engine/integrations/openai_compat.py` and register `openai_compat` + `ollama` under `content_engine.llm` in `pyproject.toml`. Verify D1: `uv run pytest tests/test_zero_key_llm_adapters.py --no-cov` exits 0.

## 2. Zero-Key Google News RSS Search Adapter (Delivery KR: D2)
- [x] 2.1 Write failing contract and recorded-HTTP unit tests in `tests/test_gnews_rss.py` covering `NewsSearchPort`, `site:<domain>` queries, `pt-BR`/`BR` + `en-US`/`US` locale mapping, `UnsupportedLocale`, and RSS parsing.
- [x] 2.2 Implement `GoogleNewsRSSSearch` in `engine/integrations/gnews_rss.py` and register `gnews_rss` under `content_engine.news_search` in `pyproject.toml`. Verify D2: `uv run pytest tests/test_gnews_rss.py --no-cov` exits 0.

## 3. Zero-Key Autocomplete Keyword Estimator (Delivery KR: D3)
- [x] 3.1 Write failing contract and unit tests in `tests/test_autocomplete_keywords.py` covering `KeywordProviderPort`, `UnsupportedLocale`, `is_estimate=True`, and error handling.
- [x] 3.2 Implement `AutocompleteKeywordProvider` in `engine/integrations/autocomplete_keywords.py` and register `autocomplete` under `content_engine.keyword_provider` in `pyproject.toml`. Verify D3: `uv run pytest tests/test_autocomplete_keywords.py --no-cov` exits 0.

## 4. One-Click Integration Stack Presets (Delivery KR: D4)
- [x] 4.1 Write failing tests in `tests/test_integration_presets.py` and `tests/test_tenant_scoping.py` for `services.apply_integration_preset` across `pocket_free`, `local_ollama`, and `pro_cloud` presets, verifying tenant isolation, secret encryption, and `integration.preset_applied` event emission.
- [x] 4.2 Implement `engine/integrations/presets.py` and `services.apply_integration_preset`. Verify D4: `uv run pytest tests/test_integration_presets.py tests/test_tenant_scoping.py --no-cov && sh scripts/check.sh` exits 0.
