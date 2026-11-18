# Tasks: Keyword provider port, DataForSEO plugin, keyword normalization, 30-day cache, and usage tracking

- [x] Add `keyword_cache` migration with squawk validation <!-- id: 0 -->
- [x] Implement `normalize_keyword` in `engine/seo.py` with Hypothesis idempotency and pt-BR tests <!-- id: 1 -->
- [x] Implement `keyword_cache` database queries in `engine/db.py` (get fresh, upsert) <!-- id: 2 -->
- [x] Implement `DataForSEOKeywordProvider` plugin in `engine/integrations/dataforseo.py` with locale mapping and budget enforcement <!-- id: 3 -->
- [x] Register `dataforseo` plugin entry-point in `pyproject.toml` <!-- id: 4 -->
- [x] Implement `seo.keywords_for` resolution logic with 30-day cache-hit bypass and usage recording <!-- id: 5 -->
- [x] Add tests: contract suite for DataForSEO, unit tests for cache hits/misses, budget guard aborts, and estimate fallbacks <!-- id: 6 -->
- [x] Verify quality gates (lint, format, types, complexity, dead code, boundaries, duplication, tests) <!-- id: 7 -->
