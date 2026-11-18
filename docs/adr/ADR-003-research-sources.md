# ADR-003: Research inputs — curated sources first, one search API second

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F07, F08, ADR-022 · PRD §5.8 · TECH-RADAR (RSSHub, GDELT, Docling trials)

## Context
Topics must be based on real, dated, verifiable industry news. Of the 18 seed sources, most have no RSS feed. Some evidence sources (IBGE, The Vision Council) publish their statistics as PDFs.

## Options considered
| Option | Fit |
|---|---|
| **A. Source feeds + one news-search plugin (Brave) for site-scoped and open search** | Deterministic core and broad discovery; one paid vendor |
| B. Search API only | Loses source authority and dates; costs more |
| C. Claude server-side web search | Results land inside a model turn, which makes them hard to store, dedupe and reproduce |
| Trial: RSSHub | Generates feeds for sites without RSS; would reduce paid search calls |
| Trial: GDELT DOC 2.0 | Free; filters by country and language |
| Trial: Docling | Extracts tables from PDFs for evidence sources |

## Decision
A.
- **Feeds:** sources with fetch mode `feed` are polled through RSS or sitemaps.
- **Site search:** sources with fetch mode `site_search` are queried through the `NewsSearch` port, scoped to their domain.
- **Dates:** every document needs an extracted publication date. Otherwise its `date_status` is `unverified`, and it can't score above "medium" confidence.
- **Original source:** found by following outbound links to tier-1 domains.
- **One search plugin in M1:** Brave only (scope change S4).
- **Trials:** RSSHub and GDELT run in M1, Docling in M3. Each has keep/drop criteria in its plan.

## Consequences
- Easier: authority and dates come from known sources, and costs stay bounded.
- Harder: the source list has to be maintained. Sources with no feed depend on search quality until RSSHub proves itself.

## Revisit when
More than 30% of the top-10 stories come from open search rather than sources, meaning the source list is incomplete. Also revisit when a trial meets its keep criteria (for Docling: `>= 3` un-gated open statistical PDF data sources used in `>= 3` briefs/month on a host with `>= 8 GB` RAM or via a lightweight hosted API).

## Trial Outcomes (M1-11 & M3-03)
- **RSSHub Trial:** Evaluated against the 13 `site_search` seed sources from `packs/industries/general/sources.yaml`. 0 sources produced dated feeds via built-in radar routes in the standard non-Chromium image ($0 < 5$). **Decision: Drop.** Profile removed from `compose.yaml`.
- **GDELT DOC 2.0 Trial:** Implemented `NewsSearchPort` plugin with 1 req / 5 s rate limiting and compared against Brave Search News over a 14-day window for `pt-BR` optical retail queries. Result: Brave found 12 relevant trade/retail stories; GDELT found 0 unique relevant stories ($0\% < 20\%$) alongside frequent 429 rate limit errors and high press-wire noise. **Decision: Drop.** Brave remains the sole search plugin for M1.
- **Docling Trial (M3-03):** Implemented `DoclingExtractor` (`ExtractorPort`, `application/pdf` only) against `quay.io/docling-project/docling-serve-cpu:latest` and evaluated 3 PDF fixtures (real IBGE August 2026 PMC `Tabela 1`/`Tabela 6` excerpt, Vision Council *Market inSights* summary, Euromonitor *Eyewear* summary). Table accuracy passed on all 3 fixtures ($3/3 = 100\%$, vs $0/3$ for `trafilatura`), taking $34.07\text{ s}$ CPU on the 3-page IBGE excerpt. However, live HTTP audit showed only 1/3 named seed `data` sources publishes open un-gated statistical PDFs (IBGE `pt-BR` only; Vision Council gates full PDFs behind login with public HTML highlights; Euromonitor returns HTTP 403 / paid Passport), yielding $< 3$ briefs/month needing PDF-only extraction, while `docling-serve-cpu` consumes $7.05\text{ GiB}$ unpacked disk ($2.07\text{ GiB}$ compressed), $820.8\text{ MiB}$ idle RAM, and $1.60\text{ GiB}$ active RSS RAM against the ADR-012 $4\text{ GB}$ VPS ceiling. **Decision: Drop.** Runtime plugin entry point and Compose profile omitted/removed; trial code, fixtures, and comparator preserved in `spikes/trials/`.

## Action items
- [x] M1-07: `rss` and `trafilatura` plugins, plus date verification
- [x] M1-08: `brave` plugin and `site_search` mode
- [x] M1-11: RSSHub and GDELT trials
- [x] M3-03: Docling trial
