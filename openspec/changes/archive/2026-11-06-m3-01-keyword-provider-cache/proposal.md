# Change: Keyword provider port, DataForSEO plugin, keyword normalization, 30-day cache, and usage tracking (M3-01)

## Why
Per PRD §5.6, ADR-004, ADR-005, ADR-015, and F11, approved editorial topics require real search-volume, CPC, and search-intent data for the campaign's locale and market, ensuring that content production targets topics with verified search demand. To prevent recurring API expenses, market keyword data must be cached globally with 30-day freshness, and all paid vendor calls must be gated by budget checks and recorded in the usage ledger.

## What Changes
1. **Database Migration**: Create `keyword_cache` global table with `(provider, locale, market, keyword_norm)` primary key and 30-day freshness.
2. **Keyword Normalization**: Implement pure, idempotent normalization (`normalize_keyword`) stripping leading/trailing whitespace, converting to lowercase, and removing diacritics/accents, validated with Hypothesis property testing.
3. **DataForSEO Plugin**: Implement `DataForSEOKeywordProvider` adhering to `KeywordProviderPort`, mapping `pt-BR`/`en-US` to vendor location and language codes, checking budget guards before paid calls, recording costs in `usage_ledger`, and raising `UnsupportedLocale` for unmapped locales.
4. **Keyword Service & Cache Resolution (`engine/seo.py`)**: Implement `seo.keywords_for(...)` resolving terms against the 30-day cache before invoking the provider, marking terms with missing data as `is_estimate=True`, and storing snapshot results.
5. **Contract Suite & Integration Tests**: Extend contract tests to cover DataForSEO with VCR recording, unit tests for normalization and caching, and budget limit checks.
