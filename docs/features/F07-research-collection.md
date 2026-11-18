# F07 — Research collection (feeds, site search, extraction, dates)

**Status:** Done (M1-07, M1-08, M1-11 delivered; RSSHub & GDELT trials decided/dropped) · **Milestone:** M1 · **Plan items:** M1-07, M1-08, M1-11 · **ADRs:** 003, 005, 022 · **OpenSpec capability:** `research`

## User story
As an owner, I want every weekly run to gather recent, dated articles from my trusted sources and relevant search results, so topics rest on real evidence.

## Scope
- In:
  - `rss`, `brave` and `trafilatura` plugins;
  - the `site_search` fetch mode;
  - URL canonicalisation;
  - date extraction and verification;
  - `documents` + `research_records`;
  - an LLM extraction step (facts, entities, summary) into `ResearchRecord`;
  - RSSHub and GDELT trials.
- Out: PDF extraction (Docling trial in M3), JS-rendered sites.

## Acceptance criteria
- **AC1** WHEN a run's research stage executes, THEN each enabled `feed` source is polled, and each `site_search` source is queried through `NewsSearch` scoped to its domain, for the run window.
- **AC2** WHEN a page is fetched, THEN it is stored once per canonical URL in `documents`, with `fetch_status`, `content_hash` and the detected `lang`.
- **AC3** WHEN no publication date can be extracted, THEN `date_status` is `unverified` and the record's confidence is capped at "medium".
- **AC4** WHEN extraction fails or the page is paywalled, THEN `fetch_status` says so and the run continues.
- **AC5** WHEN the LLM extraction step runs, THEN a valid `ResearchRecord` payload is stored with `schema_version`.
- **AC6** WHEN robots.txt disallows a URL, THEN it is not fetched.
- **AC7** WHEN the trials run, THEN their results are recorded against the keep criteria:
  - RSSHub: at least 5 sources get dated feeds;
  - GDELT: at least 20% unique relevant pt-BR stories.

## Data
`documents`, `research_records`, `runs`

## Interfaces
- `FeedSource`, `NewsSearch` and `Extractor` ports
- `research.collect(run)`
- `ResearchRecord` contract

## Tests
| Seam | Test | E2E |
|---|---|---|
| plugins | Contract suite (recorded HTTP) for `rss`, `brave`, `trafilatura`, `gdelt` | — |
| core | Canonicalisation (Hypothesis); date status rules | — |
| LLM step | Fake LLM → valid `ResearchRecord` | — |
| UI / pipeline | Run now → at least 1 dated record per enabled feed | **J3** |

## Dependencies
F04, F05, M1-10 (runs)
