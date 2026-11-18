# F15 — Draft review and approval (gate 2)

**Status:** Done (M4-04 AC1–AC5 delivered) · **Milestone:** M4 · **Plan items:** M4-04 · **ADRs:** 013, 014 · **OpenSpec capability:** `approvals`

## User story
As the campaign creator, I review each draft next to its sources and the evaluator's findings, then approve or decline it.

## Scope
- In:
  - review screen (rendered Markdown, claim → source table with links and dates, findings, revision history, cost);
  - approve or decline with a note;
  - `draft.awaiting_approval` and `draft.decided` events.
- Out: an in-app rich-text editor. Light edits happen after export or in the CMS.

## Acceptance criteria
- **AC1** WHEN a draft reaches PASS, or is surfaced after 2 revisions, THEN a `draft.awaiting_approval` event is emitted and the draft appears in the review queue.
- **AC2** WHEN the owner opens the draft, THEN each claim shows its source title, URL, publication date and date status.
- **AC3** WHEN the campaign creator approves, THEN the status becomes `approved`, a `draft.decided` event is emitted, and publishing is queued.
- **AC4** WHEN the owner declines, THEN the status becomes `declined` with a note, and the topic is not re-recommended.
- **AC5** WHEN a non-approver tries to decide, THEN it is refused.

## Data
`drafts.status`, `decided_by`, `decided_at`, `decision_note`, `events` (`draft.awaiting_approval`, `draft.decided`)

## Interfaces
- `services.produce_and_evaluate_draft(workspace_id, brief_id, user_id, offline=...)`
- `services.list_draft_reviews(workspace_id, user_id, include_decided=...)`
- `services.get_draft_review_bundle(workspace_id, draft_id, user_id)`
- `services.decide_draft(workspace_id, draft_id, decision, user_id, note=...)`

## Tests
| Seam | Test | E2E |
|---|---|---|
| services / DB | Approver rule (`F15 AC5`), guarded race (`tests/test_draft_review.py`), declined-story exclusion (`F15 AC4`), tenant scoping (`tests/test_tenant_scoping.py`) | — |
| UI (`pt-BR` / `en-US`) | Review bundle, Markdown preview, claim provenance, findings, history, cost, approve (`F15 AC1–AC3`) | **J5**, **J6** (`tests/test_journey_j5_j6_j7_draft_review.py`) |
| UI (`pt-BR` / `en-US`) | Decline with note & future topic exclusion (`F15 AC4`) | **J7** (`tests/test_journey_j5_j6_j7_draft_review.py`) |

## Dependencies
F14
