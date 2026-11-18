# F11 — Keyword research

**Status:** Done (M3-01 AC1–AC5 delivered in PR #29) · **Milestone:** M3 · **Plan items:** M3-01 · **ADRs:** 004, 005, 015 · **OpenSpec capability:** `keywords`

## User story
As an owner, each approved topic comes with real search-volume and search-intent data for my market, so articles target terms people actually search for.

## Scope
- In:
  - `KeywordProvider` port;
  - `dataforseo` plugin with locale mapping;
  - global `keyword_cache` (fresh for 30 days);
  - cost recorded in `usage_ledger`;
  - `is_estimate` flags.
- Out: rank tracking, competitor SERP analysis.

## Acceptance criteria
- **AC1** WHEN a brief is prepared, THEN keyword data for the topic's seed terms is fetched for the campaign locale and market and stored as a `KeywordData` snapshot on the brief.
- **AC2** WHEN the same (provider, locale, market, normalised keyword) was fetched in the last 30 days, THEN the cache is used and no paid call is made.
- **AC3** WHEN the provider has no data for a term, THEN the brief marks it `is_estimate=true`, and no number is invented.
- **AC4** WHEN a paid call is made, THEN a `usage_ledger` row records its units and cost, and the budget guard is checked before the call.
- **AC5** WHEN the locale isn't supported by the provider, THEN `UnsupportedLocale` is raised (and is already surfaced when the setting is saved, F02).

## Data
`keyword_cache`, `briefs.keywords`, `usage_ledger`

## Interfaces
- `KeywordProvider.lookup(terms, locale, market)`
- `seo.keywords_for(opportunity)`

## Tests
| Seam | Test | E2E |
|---|---|---|
| plugins | Contract suite, `dataforseo` against recorded HTTP | — |
| core | Cache hit/miss; keyword normalisation (Hypothesis) | — |
| UI / pipeline | Brief shows volumes or an "estimate" label | **J4** |

## Dependencies
F10, F05, F17
