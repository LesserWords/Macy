# Design: Keyword provider port, DataForSEO plugin, keyword normalization, and 30-day cache

## Architecture Overview
The keyword research subsystem provides verified SEO metrics (search volume, CPC, keyword difficulty, intent) to editorial briefs while preventing unnecessary external API expenses through a global multi-tenant cache.

```
+-----------------------------------------------------------+
|              Editorial Briefing / Strategy                |
+-----------------------------------------------------------+
                             |
                             v
                 +-----------------------+
                 |    seo.keywords_for   |
                 +-----------------------+
                   /                   \
        (1. Check Cache)           (2. If Expired / Missing)
                 v                               v
    +-------------------------+      +-------------------------+
    | keyword_cache (Global)  |      |   KeywordProviderPort   |
    |  - provider             |      |   (DataForSEO / Fake)   |
    |  - locale, market       |      +-------------------------+
    |  - keyword_norm         |                   |
    |  - data (KeywordData)   |                   v
    |  - fetched_at (30 days) |      +-------------------------+
    +-------------------------+      |   Budget Guard & Usage  |
                                     |   - check_run_budget    |
                                     |   - usage_ledger insert |
                                     +-------------------------+
```

## Data Model & Caching
1. **Global Cache Table (`keyword_cache`)**:
   - `provider`: text (e.g. `dataforseo`, `fake`)
   - `locale`: text (e.g. `pt-BR`, `en-US`)
   - `market`: text (e.g. `br`, `us`)
   - `keyword_norm`: text (normalized keyword)
   - `data`: jsonb (`KeywordData` contract payload)
   - `fetched_at`: timestamptz (used to evaluate 30-day freshness window)
   - `PRIMARY KEY (provider, locale, market, keyword_norm)`
2. **Freshness Strategy**:
   - Cache records with `fetched_at >= NOW() - INTERVAL '30 days'` are considered fresh and returned immediately.
   - Stale or missing records trigger vendor lookup.
   - Results are written back to `keyword_cache` with `ON CONFLICT DO UPDATE`.

## Keyword Normalization
- Pure function `normalize_keyword(text: str) -> str`:
  1. Strip whitespace.
  2. Lowercase string.
  3. Decompose unicode characters via NFKD and strip combining characters (`Mn` category).
  4. Collapse redundant whitespace.
- Covered by Hypothesis property testing to verify idempotency:
  `normalize_keyword(normalize_keyword(k)) == normalize_keyword(k)`.

## DataForSEO Integration
- Endpoint: `https://api.dataforseo.com/v3/dataforseo_labs/google/keyword_ideas/live` or `search_volume/live`.
- Authentication: Basic auth (`login` + `password`).
- Supported Locales:
  - `pt-BR` / `br`: location_code 2076, language_code "pt".
  - `en-US` / `us`: location_code 2840, language_code "en".
  - Other locales raise `UnsupportedLocale` (F11 AC5).
- Cost Tracking:
  - Units = number of terms looked up.
  - Cost calculated per unit and written to `usage_ledger` with `kind='api'`, `vendor='dataforseo'`, `stage='keywords'`.
