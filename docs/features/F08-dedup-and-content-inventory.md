# F08 — Deduplication and content inventory

**Status:** Done (M1-09 & M2-05 delivered in PR #26) · **Milestone:** M1 (dedup), M2 (inventory import) · **Plan items:** M1-09, M2-05 · **ADRs:** 003, 010 · **OpenSpec capability:** `research`

## User story
As an owner, I never see the same story twice in a report, and the engine never proposes a topic my blog has already covered.

## Scope
- In:
  - exact dedup by canonical URL;
  - near-duplicate dedup by `pg_trgm` title similarity within the window;
  - "already covered" check against `publications`;
  - `cli.py import-sitemap <url>` to import existing blog articles (scope change S2).
- Out: semantic (embedding) dedup.

## Acceptance criteria
- **AC1** WHEN two records share a canonical URL, THEN only one document exists.
- **AC2** WHEN two records' titles have trigram similarity ≥ the threshold (confirmed at `0.60`, validated against a labelled sample of 30 title pairs across pt-BR and en-US in `tests/test_research.py`) within 14 days, THEN the later one is `duplicate` with `duplicate_of` set.
- **AC3** WHEN `import-sitemap` runs on the Acme blog sitemap, THEN each article becomes a `publications` row (`mode='live'`, url, title, date). Re-running it creates no duplicates.
- **AC4** WHEN an opportunity is similar to an existing publication above the threshold, THEN it is flagged "already covered" in the report.
- **AC5** WHEN 20 records are manually sampled after a 14-day run, THEN duplicates are merged and no distinct stories are merged wrongly.

## Data
`documents.title` (GIN trigram index), `research_records.duplicate_of`, `publications`

## Interfaces
- `research.dedup(run)`
- `cli import-sitemap`

## Tests
| Seam | Test | E2E |
|---|---|---|
| core | Labelled fixture pairs (duplicate / not duplicate) | — |
| CLI | Sitemap fixture import is idempotent | — |
| pipeline | Already-covered flag appears in the report | J3 (assert flag) |

## Dependencies
F07
