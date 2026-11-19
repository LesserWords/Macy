# runs Specification

## Purpose
Orchestrates pipeline execution stages, scheduled cron triggers, and run failure isolation.

## Requirements

### Requirement: Pipeline run orchestration and stage transitions
The system SHALL manage pipeline stage transitions using a PostgreSQL state machine (`FOR UPDATE SKIP LOCKED`), enforce single active run per campaign, support resuming blocked runs, dispatch scheduled runs via `schedule_cron` in workspace timezone, and emit terminal events with success or failure heartbeat pings.

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

### Requirement: Production integration resolution, report compilation, and worker tick
The system SHALL resolve workspace integrations via `plugin_config` for non-offline or production runs (`blocked(integration_missing)` when `llm` or `news_search` is unconfigured), align `macy run-week --workspace` with `crontab` output, compile `report()` on `run-week` completion, and process queued runs and webhooks in `engine.__main__.run_worker_tick`.

#### Scenario: Non-offline production run resolves configured plugins or blocks on integration_missing
- **WHEN** `execute_run` executes with `offline=False` or in `staging`/`production` without explicit fake adapters
- **THEN** configured `llm` and `news_search` integrations load via `plugin_config` (with `RSSFeedSource` / `TrafilaturaExtractor` defaults for feeds/extraction), or if missing the run transitions cleanly to `blocked` with `blocked_reason='integration_missing'` and resumes once configured

#### Scenario: Worker tick and run-week complete queued runs and compile reports
- **WHEN** `run_worker_tick()` runs against a queued run or `services.run_week()` finishes a run
- **THEN** `execute_run` runs to completion, `services.run_week` compiles `services.report()`, and queued webhook deliveries are processed

### Requirement: Orchestration architecture validation (ADR-001 spike)
The system SHALL evaluate and confirm the pipeline orchestration architecture using a comparative spike testing failure isolation, resume capability, and active run constraints against the same PostgreSQL database.

#### Scenario: Comparative spike across state machine and durable workflow
- **WHEN** the 2-stage toy pipeline is executed under failure, resume, and concurrency conditions
- **THEN** both implementations demonstrate failure isolation and idempotent resume, and the evaluation matrix confirms the production orchestrator
