# Design: Topic Approval Gate 1 (M2-03)

## Architectural Context
- **ADR-013**: Authentication via Google OIDC + workspace membership. Approver for Gate 1 and Gate 2 is strictly `campaigns.created_by`, who must possess the `owner` role.
- **ADR-014**: Admin UI interacts exclusively via the `services` layer with full bilingual i18n support.
- **F10 Requirements**:
  - Human approval/rejection with optional decision notes.
  - Strict volume cap: at most `articles_per_week` approvals per run.
  - Guarded state transitions: only `scored` opportunities can be decided.
  - Race condition prevention with Postgres row locking (`FOR UPDATE`).
  - Rejection persistence: rejected stories (same document or duplicate cluster) are never re-recommended in subsequent runs.
  - Run transition expiry: starting a new run transitions previous undecided `scored` opportunities to `expired`.
  - Audit trail: `topic.decided` event emitted to `events` table with actor and payload.

## Component Details

### 1. Database Operations (`engine/db.py`)
- `decide_opportunity(conn, *, workspace_id, opportunity_id, decision, user_id, decided_at, note=None)`:
  Executes atomic guarded update:
  ```sql
  UPDATE opportunities
  SET status = %(status)s,
      decided_by = %(user_id)s,
      decided_at = %(decided_at)s,
      decision_note = %(note)s
  WHERE workspace_id = %(workspace_id)s
    AND id = %(opportunity_id)s
    AND status = 'scored'
  RETURNING *;
  ```
- `count_approved_opportunities(conn, *, workspace_id, run_id) -> int`:
  Counts opportunities with `status = 'approved'` for the specified run.
- `expire_previous_opportunities(conn, *, workspace_id, campaign_id, current_run_id) -> int`:
  Updates all still-`scored` opportunities belonging to previous runs of the campaign to `status = 'expired'`.
- `is_story_rejected(conn, *, workspace_id, document_id, duplicate_of=None) -> bool`:
  Checks whether the given document or any member of its duplicate cluster was previously rejected in any opportunity.

### 2. Service Layer (`engine/services.py`)
- `decide_topic(conn=None, *, workspace_id, opportunity_id, decision, user_id, note=None) -> dict[str, Any]`:
  1. Validates `workspace_id` membership and owner role for `user_id`.
  2. Queries opportunity, run, and campaign details.
  3. Verifies `user_id == campaign["created_by"]` (ADR-013, F10 AC2).
  4. Acquires row lock on the run (`SELECT id FROM runs WHERE id = ... FOR UPDATE`) to serialize per-run volume cap enforcement.
  5. If `decision == "approved"`:
     - Verifies `approved_count < campaign["articles_per_week"]`.
     - Raises `ValueError` if cap is exceeded.
  6. Calls `db.decide_opportunity(...)`. If 0 rows updated, checks if opportunity does not exist or was already decided/expired, raising `ValueError`.
  7. Emits `topic.decided` event to `events` table with decision, actor, note, rank, and `brief_queued`.
  8. Returns updated opportunity record.
- `services.report(...)`:
  - Updated to consider rejection status: items belonging to previously rejected stories are excluded from `is_recommended`.
- `pipeline.py` & `scheduler.py`:
  - Automatically call `expire_previous_opportunities` when initiating a new run execution.

### 3. Streamlit Admin UI (`app.py`)
- `_render_opportunity_card`:
  - Renders decision status badges (`Approved`, `Rejected`, `Expired`).
  - For `scored` items, renders an optional text field for `note` and action buttons (`Approve Topic`, `Reject Topic`).
  - Handles clicks, invokes `services.decide_topic`, surfaces feedback banners, and reruns cleanly.
- Translations:
  - All new labels and messages extracted and translated into `pt-BR` and `en-US`.
