# approvals Specification

## MODIFIED Requirements

### Requirement: Authorized decision gates for topics and drafts
The system SHALL require authorized approvers to approve or reject topics and drafts, enforcing weekly volume caps and guarded transitions.

#### Scenario: Topic approval triggers brief generation
- **WHEN** the campaign creator (an owner) approves a scored opportunity
- **THEN** opportunity status transitions to approved with decided_by and decided_at recorded, a topic.decided event is emitted, and brief generation is queued

#### Scenario: Unauthorized decision attempt is forbidden
- **WHEN** any user other than the campaign creator attempts to approve or reject an opportunity
- **THEN** request is rejected with a forbidden permission error

#### Scenario: Approvals capped by weekly article volume
- **WHEN** approvals for a run would exceed the campaign articles_per_week limit
- **THEN** additional approval is refused with a volume limit error

#### Scenario: Concurrent approval race resolves atomically
- **WHEN** two approval requests race on the same opportunity concurrently
- **THEN** exactly one succeeds via guarded transition and the other is refused

#### Scenario: Rejected topic excluded from future recommendations
- **WHEN** a topic is rejected
- **THEN** opportunities matching the same story or duplicate cluster are not recommended in future runs

#### Scenario: Undecided opportunities expire at next run
- **WHEN** a new run starts for a campaign
- **THEN** undecided scored opportunities from previous runs transition to expired status
