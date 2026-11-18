# Design: Weekly Report Screen and Recommendations (M2-02)

## Architectural Context
- **ADR-004**: Code computes deterministic weighted totals and recommendation caps, while LLM provides criteria sub-scores and evidence.
- **ADR-013 & ADR-014**: Streamlit UI consumes services layer with full bilingual localization (`pt-BR` / `en-US`).
- **F09 (AC3, AC4, AC5)**:
  - Top 10 opportunities rendered with full provenance (original URL, date, status, summary, angle, funnel stage, total, confidence).
  - Recommended count $N = \max(3, \text{articles\_per\_week})$.
  - Unverified dates must never be labelled verified.
  - Emission of `topics.awaiting_approval` upon report generation.

## Component Details
1. `engine/db.py`:
   - `get_opportunity_report_items(conn, *, workspace_id, run_id, limit=10)` joins `opportunities`, `research_records`, and `documents` to extract complete reporting metadata.
2. `engine/services.py`:
   - `report(conn=None, *, workspace_id, run_id) -> dict[str, Any]` orchestrates retrieval, computes recommendation flags, emits `topics.awaiting_approval` if not previously emitted, and returns structured report dictionary.
3. `app.py`:
   - `_render_weekly_report(user, workspace)` provides rich UI view with metric headers, recommendation badges, collapsible criteria breakdown, and direct source links.
