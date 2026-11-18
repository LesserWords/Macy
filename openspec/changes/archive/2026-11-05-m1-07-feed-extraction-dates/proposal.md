# Proposal

## Why

Plan item **M1-07** (`docs/plans/M1.md`). Implement the `documents` and `research_records` database tables, the `rss` (`FeedSourcePort` via `feedparser`) and `trafilatura` (`ExtractorPort` via `trafilatura` + `urllib.robotparser`) plugins, URL canonicalisation, date verification (`date_status` and confidence capping), language detection (`lingua-py`), and the LLM extraction pipeline (`engine/research.py` + `prompts/extract_research.md`) so runs can collect feeds, extract clean text/metadata, verify publication dates, and store structured `ResearchRecord` rows with failure isolation. Features: F07 (AC2–AC6). ADRs: 003, 005, 011, 025.

## What Changes

- Add migration `db/migrations/20261105000000_documents_and_research_records.sql` creating `documents` (with `documents_title_trgm` and `documents_published` indexes) and `research_records` (with composite FKs, `(status = 'duplicate') = (duplicate_of IS NOT NULL)` constraint, and `rr_queue` index), and grant appropriate permissions to `app`, `retention`, and `readonly` roles.
- Add database CRUD helpers in `engine/db.py` (`upsert_document`, `get_document_by_canonical_url`, `get_document`, `create_research_record`, `update_research_record`, `list_research_records`) strictly scoped by `workspace_id`.
- Add `rss` plugin (`engine/integrations/rss.py`, registered under `content_engine.feed_source`) using `feedparser` and `safe_http`.
- Add `trafilatura` plugin (`engine/integrations/trafilatura_extractor.py`, registered under `content_engine.extractor`) extracting main text, date, author, and outbound links via `trafilatura`, honouring `robots.txt` (`urllib.robotparser`) and detecting paywalled/failed pages (`F07 AC4, AC6`).
- Add `engine/research.py` with:
  - `canonicalize_url(url)` (strips tracking parameters `utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `ref`, `_hsenc`, `_hsmi`, normalizes scheme/host/path/query/fragment, idempotent).
  - `resolve_date_status(published_at, is_verified_metadata, inferred_date)` and `cap_confidence_for_date_status(confidence, date_status)` (`F07 AC3`).
  - `detect_language(text)` using `lingua` restricted to `pt` and `en` (`F07 AC2`).
  - `collect_and_extract_feed_item(...)` / `extract_research_record_with_llm(...)` storing `documents` once per canonical URL (`F07 AC2`), handling `robots.txt` blocks (`blocked`), paywalls (`paywalled`), HTTP/extraction failures (`http_error`, `extraction_failed`), and LLM extraction into `research_records` with failure isolation (`status='failed'` on invalid LLM output while the run continues, `F07 AC4, AC5`).
- Add prompt `prompts/extract_research.md` for structured `ResearchRecord` LLM extraction.

## Capabilities

### Modified Capabilities
- `research`: Add scenarios for canonical URL storage with `fetch_status`, `content_hash`, and `lang` (AC2), `unverified` date status and confidence cap at `"medium"` (AC3), extraction failure or paywall resilience (AC4), structured LLM `ResearchRecord` persistence with `schema_version` and failure isolation (AC5), and `robots.txt` enforcement (AC6).

## Impact

- New: `db/migrations/20261105000000_documents_and_research_records.sql`, `engine/integrations/rss.py`, `engine/integrations/trafilatura_extractor.py`, `engine/research.py`, `prompts/extract_research.md`, `tests/test_research.py`.
- Modified: `pyproject.toml`, `uv.lock`, `engine/db.py`, `tests/test_schema_constraints.py`, `tests/test_contract_suites.py`, `docs/features/F07-research-collection.md`, `docs/adr/ADR-003-research-sources.md`.
