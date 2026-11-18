# Design: M4-04 — Draft review screen (gate 2)

## Architecture & Boundaries
- **Approver Authorization & Concurrency (`engine/services.py`, `engine/db.py`, `ADR-013`):**
  - Gate 2 reuses `_validate_topic_approver` (`owner` membership + `campaigns.created_by == user_id`) so approver rules are enforced identically across Gates 1 and 2 without code duplication.
  - `services.decide_draft` executes inside `with conn.transaction():`, calling `db.decide_draft` (`UPDATE drafts ... WHERE workspace_id = ... AND id = ... AND status = 'pass'`) and emitting `draft.decided` (`entity_type="draft"`, `payload={"draft_id", ..., "decision", "note", "publish_queued": decision == "approved"}`) atomically in the same transaction. Two concurrent connections racing on the same draft serialize on Postgres's row lock so exactly one succeeds.
- **Declined Story Memory (`engine/db.py`, `F15 AC4`):**
  - `db.get_opportunity_report_items` extends the `is_rejected_story` check to inspect `EXISTS (... JOIN briefs JOIN drafts WHERE prev_d.status = 'declined')`, ensuring future runs for the same document or `duplicate_of` cluster never re-recommend a declined story.
- **Review Bundle & Bilingual UI (`app.py`, `ADR-014`):**
  - `app.py` communicates solely via `engine.services` and `engine.i18n` (no `engine.db` or `engine.quality` imports).
  - `services.get_draft_review_bundle` aggregates draft + brief/opportunity context, `draft_claims` joined with `documents` provenance, 1:1 `quality_reports`, cross-brief `revision_history`, and `usage_ledger` breakdown grouped by stage for the draft's `run_id`.
  - After Approve/Decline rerun, `app.py` displays the decided draft under "Recent Decisions" with status banner (`Draft Approved` / `Draft Declined`) and decision note so confirmation feedback remains visible across reruns.
