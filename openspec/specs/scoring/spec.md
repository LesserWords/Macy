# scoring Specification

## Purpose
Scores research records against campaign strategy and audience needs to produce ranked opportunity reports.

## Requirements

### Requirement: Opportunity scoring and report generation
The system SHALL score research records across criteria, compute weighted totals using campaign weights, and rank recommendations.

#### Scenario: Code-computed weighted total score
- **WHEN** scoring evaluates a verified record
- **THEN** sub-scores are generated with evidence and weighted total is computed deterministically in code

#### Scenario: Weekly report recommendations capped by volume
- **WHEN** opportunity report is compiled for a run
- **THEN** top N recommendations matching weekly article volume (minimum 3) are highlighted and awaiting approval event is emitted

#### Scenario: Weekly report presentation and date integrity
- **WHEN** the weekly report is rendered for a completed run
- **THEN** it displays the top 10 opportunities with original URL, publication date, summary, angle, funnel stage, total score, and confidence, and records with unverified date status are never displayed as verified

#### Scenario: Already covered check flags similar past publications
- **WHEN** an opportunity title has trigram similarity >= 0.60 against an existing publication in the same workspace
- **THEN** `already_covered` is flagged `true` in the weekly report item

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
