# Tasks: M1-09 — Deduplication

- [x] 1. Implement exact dedup (canonical URL) plus near-duplicate dedup (`pg_trgm` similarity >= 0.6 within 14 days) in `engine/db.py` and `engine/research.py`.
- [x] 2. Verify a labelled fixture of 30 title pairs gives the expected classification (`tests/test_research.py`).
- [x] 3. Wire `research.dedup(run)` into the research stage (`research.collect`) and mark duplicates (`F08 AC1` and `AC2`).
- [x] 4. Record the confirmed threshold (`0.6`) in `docs/features/F08-dedup-and-content-inventory.md` and pass all universal quality gates.
