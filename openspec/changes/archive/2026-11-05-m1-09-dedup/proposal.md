# Proposal: M1-09 — Deduplication

## Why
Syndicated industry news and slightly reworded headlines across sources can produce duplicate stories within the same 14-day window (F08 AC1, AC2; ADR-003). We need exact deduplication by canonical URL (`documents` unique on `(workspace_id, canonical_url)`) plus near-duplicate title deduplication (`pg_trgm` similarity >= `0.6` within 14 days), verified on a labelled fixture of 30 title pairs, and wired into the research stage (`research.dedup(run)` and `research.collect(run)`).

## What Changes
- Add database helpers in `engine/db.py` (`find_earlier_near_duplicate_record` and `mark_research_record_duplicate`) scoped by `workspace_id`, using PostgreSQL `pg_trgm` `similarity(d_curr.title, d_prev.title) >= threshold` within a 14-day window.
- Implement `research.dedup(conn, run, ...)` and `research.is_near_duplicate_title(conn, title_a, title_b, threshold=0.6)` in `engine/research.py`, and wire `dedup` into `research.collect(...)`.
- Add a labelled fixture of 30 title pairs (15 near-duplicates, 15 distinct stories across `pt-BR` and `en-US`) in `tests/test_research.py` verifying the `0.6` threshold, plus `F08 AC1` and `AC2` tests.
- Record the confirmed `0.6` trigram threshold and 30-pair validation in `docs/features/F08-dedup-and-content-inventory.md`.

## Impact
- Capabilities: `research`
- Code: `engine/db.py`, `engine/research.py`, `docs/features/F08-dedup-and-content-inventory.md`
- Tests: `tests/test_db.py`, `tests/test_research.py`
