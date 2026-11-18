# approvals Specification Delta (M4-04)

## ADDED Requirements

### Requirement: Gate 2 draft review queue and decision transitions
The system SHALL surface passed or max-revised drafts with `draft.awaiting_approval` events, enforce campaign-creator ownership on `decide_draft`, emit `draft.decided` atomically, and prevent declined stories from future recommendation.

#### Scenario: Draft surfaced to review queue on PASS or after max 2 revisions
- **WHEN** a draft passes evaluation or reaches `revision = 2` with non-reject findings
- **THEN** a `draft.awaiting_approval` event is emitted idempotently and the draft appears in `list_draft_reviews`

#### Scenario: Campaign creator approves draft and queues publishing
- **WHEN** the campaign creator (an owner) approves a `pass` draft via `services.decide_draft`
- **THEN** `drafts.status` transitions atomically to `approved` with `decided_by` and `decided_at` stamped, and a `draft.decided` event with `publish_queued = true` is emitted in the same transaction

#### Scenario: Owner declines draft with note and excludes story from future runs
- **WHEN** the campaign creator declines a `pass` draft with an optional note
- **THEN** `drafts.status` transitions to `declined` with `decision_note` recorded, `draft.decided` (`publish_queued = false`) is emitted, and later runs mark matching stories/clusters as non-recommended

#### Scenario: Non-approver refused and concurrent draft race resolves atomically
- **WHEN** a non-creator or non-owner calls `decide_draft`, or two connections race on the same `pass` draft
- **THEN** the non-approver is refused with `PermissionError` and exactly one concurrent decision succeeds via the guarded `WHERE status = 'pass'` update

### Requirement: Bilingual draft review interface with provenance and telemetry
The Admin UI SHALL render reviewable drafts with Markdown content, claim-to-source provenance metadata, evaluator findings, revision history, cost breakdown, and Approve/Decline actions in `pt-BR` and `en-US` (`F15 AC2`, `F19`).

#### Scenario: Review view renders Markdown, claim sources, findings, revision history, and cost
- **WHEN** an owner opens the Draft Review screen in `pt-BR` or `en-US`
- **THEN** each reviewable draft displays rendered Markdown (`title`, `meta_description`, `slug`, `body_md`, `faq`, `cta`), claim rows with source title, URL, publication date and `date_status`, deterministic checks and evaluator findings, revision history across revisions `0..2`, and stage cost breakdown

#### Scenario: Offline E2E journeys J5 through J7 verify draft evaluation, approve, and decline flows
- **WHEN** the offline E2E journeys run against `app.py` with `FakeLLM` and test Postgres
- **THEN** J5 surfaces an evaluated draft in the review queue, J6 approves it with UI confirmation and DB persistence, and J7 declines it with a note and verifies story exclusion
