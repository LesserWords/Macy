# Tasks

## 1. Migration for `documents` and `research_records`
- [x] 1.1 Write failing schema constraint tests in `tests/test_schema_constraints.py` verifying `documents` and `research_records` tables exist, composite foreign keys reject cross-tenant references, unique `(workspace_id, canonical_url)` is enforced on `documents`, and `CHECK ((status = 'duplicate') = (duplicate_of IS NOT NULL))` holds on `research_records`.
- [x] 1.2 Add `db/migrations/20261105000000_documents_and_research_records.sql`, verify `squawk` is clean, apply migration to database, and add `db.py` functions (`upsert_document`, `get_document_by_canonical_url`, `get_document`, `create_research_record`, `update_research_record`, `list_research_records`).

## 2. `rss` and `trafilatura` Plugins with `robots.txt` Enforcement
- [x] 2.1 Write failing plugin contract and recorded-HTTP tests in `tests/test_research.py` and `tests/test_contract_suites.py` for `rss` (`FeedSourcePort`) and `trafilatura` (`ExtractorPort` extracting text, date, author, outbound links, paywall detection, and honouring `robots.txt`) against 3 real seed sources.
- [x] 2.2 Implement `engine/integrations/rss.py` and `engine/integrations/trafilatura_extractor.py`, register entry points in `pyproject.toml`, and verify contract suites pass.

## 3. URL Canonicalisation, `date_status` Rules, and Language Detection
- [x] 3.1 Write failing Hypothesis property tests and literal unit tests in `tests/test_research.py` for `canonicalize_url()` (idempotence, tracking param stripping, fragment stripping, host/scheme normalization), `resolve_date_status()`, `cap_confidence_for_date_status()`, and `detect_language()`.
- [x] 3.2 Implement `canonicalize_url()`, `resolve_date_status()`, `cap_confidence_for_date_status()`, and `detect_language()` in `engine/research.py`.

## 4. LLM Extraction Step into `ResearchRecord` with Failure Isolation
- [x] 4.1 Write failing tests in `tests/test_research.py` for `prompts/extract_research.md` and the LLM extraction pipeline (`process_feed_item` / `collect_feed_source`), verifying valid `ResearchRecord` storage (`schema_version`, `status='extracted'`) and failure isolation when extraction or LLM output fails (`status='failed'`, run continues).
- [x] 4.2 Implement `prompts/extract_research.md` and the collection/extraction pipeline in `engine/research.py`, and verify all quality gates and 100% diff coverage.
