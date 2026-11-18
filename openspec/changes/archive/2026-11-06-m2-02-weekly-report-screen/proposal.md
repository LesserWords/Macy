# Change Proposal: Weekly Report Screen and Recommendations (M2-02)

## Motivation
In Milestone 2 Item 2 (M2-02), editorial decision-makers require a clear, ranked weekly report to review top story opportunities generated during research and scoring runs (F09 AC3, AC4, AC5). The report must recommend the top $N$ opportunities matching the campaign's publication volume ($N = \max(3, \text{articles\_per\_week})$), present complete evidence and provenance for each item, ensure date integrity by never labelling unverified dates as verified, and signal readiness by emitting `topics.awaiting_approval`.

## Proposed Changes
1. **Service Layer**:
   - Add `services.report(conn=None, *, workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]` in `engine/services.py`.
   - Query up to 10 top opportunities joined with documents and research records.
   - Determine recommendation limit $N = \max(3, \text{articles\_per\_week})$ from active campaign configuration.
   - Mark the top $N$ ranked opportunities with `is_recommended = True`.
   - Include `already_covered = False` (prepared for sitemap inventory in M2-05).
   - Ensure date display strictly respects date status (unverified dates never labelled verified, F09 AC4).
   - Idempotently emit `topics.awaiting_approval` event into `events` table (F09 AC5).

2. **Admin UI (Streamlit)**:
   - Add Weekly Report navigation option in `app.py` (`"report": _("Weekly Report")`).
   - Implement `_render_weekly_report(user, workspace)` displaying the top 10 opportunities with recommended badges, criteria score breakdowns, dates, angles, and original links.
   - Add a direct link/trigger from Runs view to inspect the latest generated report.
   - Fully bilingual interface supporting both `pt-BR` and `en-US`.

3. **E2E & Acceptance Testing**:
   - Add unit and service tests verifying F09 AC3, AC4, and AC5.
   - Extend offline E2E suite with Journey J3 (`tests/test_journey_j3_weekly_report.py`).

## Impact
- Fulfills Milestone 2 Item 2 exit criteria.
- Zero breaking changes to existing data model or pipeline contracts.
