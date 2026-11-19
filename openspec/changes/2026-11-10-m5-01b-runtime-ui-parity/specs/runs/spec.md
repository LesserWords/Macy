# runs Delta Specification

## MODIFIED Requirements

### Requirement: Pipeline run orchestration and stage transitions
The system SHALL manage pipeline stage transitions via a PostgreSQL state machine (`FOR UPDATE SKIP LOCKED`), enforce single active run per campaign, reject unsupported `run-stage` values non-zero, prevent `run-now --offline` worker races, support resuming blocked runs, and emit lifecycle events with heartbeat pings.

#### Scenario: Single active run enforced per campaign
- **WHEN** a manual or scheduled run is triggered while a run is already queued or running for that campaign
- **THEN** new run creation is rejected by database constraint

#### Scenario: Resuming blocked runs continues from current stage
- **WHEN** a blocked run is resumed after fixing the blocking cause
- **THEN** execution resumes from the blocked stage without re-running earlier completed stages

#### Scenario: Two-worker stage claim prevents double processing
- **WHEN** two concurrent workers attempt to claim the next queued run or items using `FOR UPDATE SKIP LOCKED`
- **THEN** only one worker claims and processes each item and no double-processing occurs

#### Scenario: Run now and scheduler trigger run creation
- **WHEN** a campaign trigger fires via schedule or manual Run now
- **THEN** a new run row is created with a run window, snapshot of run_cap_usd, code_version, and pack_version

#### Scenario: Terminal run events and heartbeat pings
- **WHEN** a run completes or fails
- **THEN** the corresponding `run.completed` or `run.failed` event is emitted and an observability heartbeat ping (or `/fail` ping on failure) is sent

#### Scenario: Unsupported stage name in run-stage fails non-zero
- **WHEN** `re_run_failed_stage_items` or `macy run-stage <id> --stage unknown_stage` is invoked
- **THEN** a `ValueError` naming supported stages is raised and the CLI exits with non-zero status instead of claiming zero items re-processed

#### Scenario: Offline run-now cannot be hijacked by background worker polling
- **WHEN** `macy run-now <workspace> --offline` executes while a live worker loop could poll `queued` rows
- **THEN** the run is claimed before returning control so `run_worker_tick` never executes an offline CLI run with live adapters

### Requirement: Production integration resolution, report compilation, and worker tick
The system SHALL resolve workspace integrations via `plugin_config` for non-offline runs (`blocked(integration_missing)` with `run.blocked` event when `llm` or `news_search` is unconfigured), align `macy run-week --workspace`, compile `report()` on completion, and drive queued runs and webhooks in `run_worker_tick`.

#### Scenario: Non-offline production run resolves configured plugins or blocks on integration_missing
- **WHEN** `execute_run` executes with `offline=False` or in `staging`/`production` without explicit fake adapters
- **THEN** configured `llm` and `news_search` integrations load via `plugin_config` (with `RSSFeedSource` / `TrafilaturaExtractor` defaults for feeds/extraction), or if missing the run transitions cleanly to `blocked` with `blocked_reason='integration_missing'`, emits `run.blocked`, and resumes once configured

#### Scenario: Worker tick and run-week complete queued runs and compile reports
- **WHEN** `run_worker_tick()` runs against a queued run or `services.run_week()` finishes a run
- **THEN** `execute_run` runs to completion, `services.run_week` compiles `services.report()`, and queued webhook deliveries are processed
