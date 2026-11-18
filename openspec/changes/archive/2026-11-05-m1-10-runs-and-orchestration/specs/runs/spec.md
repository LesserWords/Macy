# Delta for runs

## MODIFIED Requirements

### Requirement: Pipeline run orchestration and stage transitions
The system SHALL manage pipeline stage transitions using a PostgreSQL state machine (`FOR UPDATE SKIP LOCKED`), enforce single active run per campaign, support resuming blocked runs, dispatch scheduled runs via `schedule_cron` in workspace timezone, and emit terminal events with heartbeat pings.

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
- **THEN** the corresponding `run.completed` or `run.failed` event is emitted and an observability heartbeat ping is sent
