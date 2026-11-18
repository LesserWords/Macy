# scoring Specification

## MODIFIED Requirements

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
