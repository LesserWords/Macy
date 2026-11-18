# Change Proposal: Topic Approval Gate 1 (M2-03)

## Motivation
In Milestone 2 Item 3 (M2-03), editorial decision-makers must approve or reject recommended topics (Gate 1) before briefs are generated (F10 AC1–AC6, ADR-013). This human-in-the-loop gate ensures that only topics explicitly selected by the campaign creator become editorial briefs. The system must enforce:
1. Authorization: Only the campaign creator (who holds the owner role) can approve or reject opportunities (AC1, AC2).
2. Weekly volume cap: The number of approved opportunities per run cannot exceed `articles_per_week` (AC3).
3. Concurrency safety: Race conditions must be prevented using row-level locking and guarded state transitions (`status = 'scored'`) so that exactly one concurrent attempt succeeds and volume caps are never exceeded (AC4).
4. Rejection memory: When a topic is rejected, the same story (same document or duplicate cluster) is not recommended in future runs (AC5).
5. Lifecycle expiry: When a new run starts, undecided opportunities remaining in `scored` status from previous runs automatically expire (AC6).
6. Auditability: Every decision emits a `topic.decided` event with decision metadata and queues brief generation upon approval.

## Proposed Changes
1. **Database Layer (`engine/db.py`)**:
   - Add `decide_opportunity(conn, *, workspace_id, opportunity_id, decision, user_id, note=None)` with atomic guarded transition `WHERE status = 'scored'`.
   - Add `count_approved_opportunities(conn, *, workspace_id, run_id)` to count current approvals for a run.
   - Add `expire_previous_opportunities(conn, *, workspace_id, campaign_id, current_run_id)` transitioning earlier undecided `scored` opportunities to `expired`.
   - Add rejection memory tracking in opportunity queries to identify documents or duplicate clusters previously rejected.

2. **Service Layer (`engine/services.py`)**:
   - Implement `decide_topic(conn=None, *, workspace_id, opportunity_id, decision, user_id, note=None)`:
     - Enforce membership and verify `user_id` matches `campaign.created_by` (ADR-013, AC1, AC2).
     - Lock the run row to serialize volume cap checks under concurrency.
     - Validate run approval count against `articles_per_week` (AC3).
     - Atomically update opportunity status (`approved` or `rejected`) with `decided_by`, `decided_at`, and `decision_note`.
     - Emit `topic.decided` event into `events` table (AC1, AC5).
     - Queue brief generation if approved (`brief_queued = True`).
   - Integrate expiry in run lifecycle (`expire_previous_opportunities`) when a new run starts (AC6).
   - In `services.report`, exclude rejected topics and stories matching previously rejected documents/clusters from recommendations (AC5).

3. **Admin UI (`app.py`)**:
   - Enhance opportunity card in Weekly Report view to display current decision status (`Approved`, `Rejected`, `Expired`).
   - For `scored` opportunities, provide an optional decision note input and "Approve Topic" / "Reject Topic" action buttons.
   - Show status badges and feedback toasts upon decision.
   - Full bilingual support (`pt-BR` and `en-US`).

4. **Testing & Verification**:
   - Unit and integration tests for F10 AC1–AC6 in `tests/test_topic_approval.py`.
   - Concurrency race test with two parallel database connections verifying guarded transition and volume cap serialization.
   - Offline E2E Journey J4 (approve path → brief queued) and J7 part 1 (reject path → not re-recommended) using Streamlit `AppTest`.
   - Strict quality gates: 100% diff-cover on `engine/`, full translation checks, zero linting or type errors.

## Impact
- Completes Milestone 2 Item 3 (M2-03) and satisfies Feature F10 exit criteria.
- Zero breaking changes to existing data models or pipeline contracts.
