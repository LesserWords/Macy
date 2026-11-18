# Spec Delta

## ADDED Requirements

### Requirement: Opportunity scoring with criteria evidence and code-computed totals
The system SHALL evaluate non-duplicate research records against campaign objectives and audience needs, generating 6 criteria sub-scores (0–10) with evidence, and deterministically computing a weighted total score (0–100) in code using campaign weights.

#### Scenario: Code computes weighted total from sub-scores
- **WHEN** the scoring stage evaluates a verified research record
- **THEN** each criterion receives a score and evidence, and the total score is computed in code as $\sum(\text{score} \times \text{weight}) \times 10 / 100$ bounded between 0 and 100

#### Scenario: SEO potential flagged as estimate when keyword data is absent
- **WHEN** opportunity scoring evaluates a topic without keyword provider data
- **THEN** the SEO potential criterion is flagged `is_estimate = true` and never formatted as an exact search volume count

#### Scenario: Regression cases 1 through 5 land in expected priority bands
- **WHEN** regression fixtures 1 through 5 are evaluated against the score rubric
- **THEN** each opportunity lands within its predefined priority band (high, medium, low)
