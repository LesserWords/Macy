# Proposal: M1-10 — Runs, stage runner, scheduler, budget guard, Run now

## Why
F18 and F17 require an automated stage runner implementing the ADR-001 confirmed decision (Postgres state machine with `SKIP LOCKED` claim, failure isolation, and resume), single active run enforcement per campaign (`runs_one_active`), schedule trigger via `schedule_cron` in the workspace timezone, "Run now" manual trigger in services and UI, pre-call and stage boundary budget guard enforcement with `blocked(cap_reached)` / `budget.cap_reached` events and monthly cap refusal, and `run.completed` / `run.failed` event emissions with heartbeat pings.

## What Changes
- Implement the stage runner in `engine/pipeline.py` executing the research stage (`research.collect` and `research.dedup`) with `SKIP LOCKED` worker claim, guarded state transitions, failure isolation, and resume from blocked/failed states (`ADR-001`, `F18 AC3-AC4`).
- Implement the budget guard in `engine/budget.py` (`check_budget`, `record_cost`, checking per-run cap before calls and at stage boundaries, monthly cap refusal for new runs, `budget.cap_reached` event emission).
- Implement `services.run_now(campaign_id)` and scheduler helper (`engine/scheduler.py` / `engine/services.py`) reading `schedule_cron` in the workspace timezone and refusing creation if a run is already queued/running (`F18 AC1-AC2`, `F17 AC2-AC4`).
- Emit `run.completed` / `run.failed` events and heartbeat pings (`observability.send_heartbeat`) on terminal states (`F18 AC5`).
- Add Run history and Run now actions to Admin UI (`app.py` / `engine/services.py`).
- Add tests covering two-worker `SKIP LOCKED` concurrency, budget guard boundaries, failure isolation, resume, and offline E2E J3.

## Impact
- Capabilities: `runs`, `spend`
- Code: `engine/db.py`, `engine/pipeline.py`, `engine/budget.py`, `engine/services.py`, `engine/cli.py`, `app.py`
- Tests: `tests/test_pipeline.py`, `tests/test_budget.py`, `tests/test_services.py`
