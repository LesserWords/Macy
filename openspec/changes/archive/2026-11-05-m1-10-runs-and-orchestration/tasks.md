# Tasks: M1-10 — Runs, stage runner, scheduler, budget guard, Run now

- [ ] 1. Build the stage runner (`engine/pipeline.py`) per the ADR-001 confirmed decision (Postgres state machine with `SKIP LOCKED` claim, failure isolation, and resume; `run-stage` and `run-week` CLI). Verify F18 AC3 and AC4 with a two-worker test showing no double-processing.
- [ ] 2. Add the scheduler helper and "Run now" (`engine/scheduler.py`, `services.run_now`, UI action): reading `schedule_cron` in the workspace timezone; one active run per campaign constraint. Verify F18 AC1 and AC2.
- [ ] 3. Add the budget guard in `engine/budget.py` (before each paid call and at stage boundaries), `blocked(cap_reached)`, the `budget.cap_reached` event, and monthly refusal. Verify F17 AC2–AC4 with a fixed-cost fake.
- [ ] 4. Emit `run.completed` / `run.failed` events and heartbeat pings (`F18 AC5`). Add the run history screen in Admin UI (`app.py`), and verify offline E2E J3 (Run now -> records stored).
