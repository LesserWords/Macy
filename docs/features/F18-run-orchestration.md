# F18 — Run orchestration and scheduling

**Status:** Done (M0-09 & M1-10 delivered; AC6 pilot timing verified in M5-02) · **Milestone:** M1 · **Plan items:** M0-09, M1-10 · **ADRs:** 001, 010 · **OpenSpec capability:** `runs`

## User story
As an owner, the weekly run happens on schedule without me, I can trigger it with "Run now", and I can see where each run is and why it stopped.

## Scope
- In:
  - `runs` table;
  - stage runner (research → score → report → [gate 1] → keywords → brief → write → evaluate → [gate 2] → publish);
  - scheduler (supercronic reading `schedule_cron` in the workspace timezone);
  - Run now;
  - one active run per campaign;
  - resume after blocked or failed;
  - run history screen;
  - `run.completed` / `run.failed` events;
  - heartbeat;
  - DBOS spike (ADR-001).
- Out: parallel workers (the schema already supports them).

## Acceptance criteria
- **AC1** WHEN the schedule fires, THEN a `scheduled` run is created with a window, a snapshot of the cap, `code_version` and `pack_version`.
- **AC2** WHEN Run now is clicked while a run is queued or running for that campaign, THEN it is refused (partial unique index).
- **AC3** WHEN a stage fails for one row, THEN the other rows continue, and the failed row can be re-run with `cli run-stage <id>`.
- **AC4** WHEN a run is blocked (missing integration or cap reached) and the cause is fixed, THEN resuming continues from the blocked stage.
- **AC5** WHEN a run completes or fails, THEN the corresponding event is emitted and a heartbeat ping is sent.
- **AC6** Pilot: the weekly run completes unattended from cron to report in under 30 minutes, 4 weeks out of 4.

## Data
`runs`, plus state columns on the pipeline tables

## Interfaces
- `pipeline.run(run_id)`
- `services.run_now(campaign_id)`
- `cli run-week`
- `cli run-stage`

## Tests
| Seam | Test | E2E |
|---|---|---|
| pipeline | Full offline run with fakes; failure isolation; resume | — |
| db | One-active-run constraint; `SKIP LOCKED` claim with two workers | — |
| UI | Run now → report ready | **J3** |

## Dependencies
M0-04, F02
