# Proposal: M1-08 — Brave plugin and `site_search` mode

## Why
Most of the 18 seed sources do not publish an RSS feed and rely on `fetch_mode = 'site_search'` (ADR-003, F07 AC1). We need the `brave` `NewsSearch` plugin to execute both domain-scoped (`site:<domain>`) and open news search queries, map campaign `locale` and `market` to Brave API parameters (raising `UnsupportedLocale` for unmapped locales), and record each search call's unit cost in `usage_ledger` (ADR-016). We also need `research.collect` to poll both enabled `feed` sources and enabled `site_search` sources for the run window.

## What Changes
- Implement `BraveNewsSearch` (`engine/integrations/brave.py`) implementing `NewsSearchPort` and register entry point `brave` under `content_engine.news_search` in `pyproject.toml`.
- Support domain-scoped (`site:<domain>`) and open queries, locale/market mapping (`pt-BR`/`br` and `en-US`/`us`, raising `UnsupportedLocale` otherwise), date extraction/filtering against `since`, and `usage_ledger` cost recording (`kind='api'`, `vendor='brave'`, `units=1`, `cost_usd=settings.unit_cost_usd`).
- Extend `engine/research.py` (`collect_site_search_sources` and `collect`) to query enabled `site_search` sources through `NewsSearchPort` scoped to their domain alongside `feed` sources for the run window (`F07 AC1`).
- Add recorded HTTP VCR cassettes and contract/unit tests in `tests/test_contract_suites.py` and `tests/test_research.py`.

## Impact
- Capabilities: `research`
- Code: `engine/integrations/brave.py`, `engine/integrations/fakes.py`, `engine/research.py`, `pyproject.toml`, `docs/adr/ADR-003-research-sources.md`
- Tests: `tests/test_contract_suites.py`, `tests/test_research.py`
