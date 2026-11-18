# F10 — Topic approval (gate 1)

**Status:** Done (M2-03 delivered in PR #24) · **Milestone:** M2 · **Plan items:** M2-03 · **ADRs:** 013 · **OpenSpec capability:** `approvals`

## User story
As the campaign creator, I approve or reject recommended topics, so only topics I chose become briefs.

## Scope
- In:
  - approve or reject with an optional note;
  - limit of `articles_per_week` approvals per run;
  - expiry of unapproved topics at the next run;
  - rejected topics excluded from later reports;
  - `topic.decided` event.
- Out: delegation, multiple approvers.

## Acceptance criteria
- **AC1** WHEN the campaign creator (an owner) approves a scored opportunity, THEN its status becomes `approved` with `decided_by` and `decided_at` set, and a `topic.decided` event is emitted. Brief generation is then queued.
- **AC2** WHEN any other user tries to approve, THEN it is refused (`Forbidden`).
- **AC3** WHEN approvals would exceed `articles_per_week` for that run, THEN the extra approval is refused with a message.
- **AC4** WHEN two approvals race on the same opportunity, THEN exactly one succeeds (guarded `UPDATE … WHERE status = 'scored'`).
- **AC5** WHEN a topic is rejected, THEN the same story (same document or a duplicate cluster) is not recommended in later runs.
- **AC6** WHEN the next run starts, THEN still-`scored` opportunities from the previous run become `expired`.

## Data
`opportunities.status`, `decided_by`, `decided_at`, `decision_note`

## Interfaces
- `services.decide_topic(opportunity_id, approve|reject, note)`

## Tests
| Seam | Test | E2E |
|---|---|---|
| services | Approver rule, limit, race (two connections), expiry | — |
| UI | Approve the top topic → brief queued | **J4** |
| UI | Reject → not re-recommended | **J7** |

## Dependencies
F09, F01
