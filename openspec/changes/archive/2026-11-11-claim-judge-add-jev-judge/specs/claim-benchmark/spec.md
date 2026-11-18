# claim-benchmark Delta Specification

## ADDED Requirements

### Requirement: Jev-only judge is an outcome candidate
The outcome SHALL treat both `jev_judge` and `jev_cascade` as candidates, each evaluated against W1 through W9 with `per_claim_v2` as the W4 and W5 reference, and SHALL build on the cheaper passing candidate.

#### Scenario: Only the Jev-only judge passes
- **WHEN** `jev_judge` passes W1 through W9 and `jev_cascade` does not
- **THEN** the outcome is GO with `jev_judge`

#### Scenario: Both candidates pass
- **WHEN** both candidates pass W1 through W9
- **THEN** the outcome is GO with the candidate of lower measured cost per article, and `jev_judge` on a tie

#### Scenario: Neither candidate passes
- **WHEN** neither candidate passes W1 through W9 and `per_claim_v2` passes W1, W2, W3, W7, W8 and W9
- **THEN** the outcome is GO WITHOUT JEV
