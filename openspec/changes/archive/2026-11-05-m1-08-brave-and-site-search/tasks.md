# Tasks: M1-08 — Brave plugin and `site_search` mode

- [x] 1. Write the `brave` `NewsSearch` plugin (`engine/integrations/brave.py`) supporting domain-scoped and open queries, locale mapping (`UnsupportedLocale` for unmapped locales), and unit cost recording in `usage_ledger`, and register its entry point in `pyproject.toml`.
- [x] 2. Verify `brave` passes the `NewsSearch` contract suite against recorded HTTP (`tests/test_contract_suites.py`) and unit tests for locale mapping and `usage_ledger` writes (`tests/test_research.py`).
- [x] 3. Update `engine/research.py` (`collect_site_search_sources` and `collect`) to call `site_search` sources through `NewsSearchPort` scoped to each source's domain alongside `feed` sources for the run window (`F07 AC1`).
- [x] 4. Verify `F07 AC1` with fakes (`feed` and `site_search` are both called with the right arguments) and pass all universal quality gates.
