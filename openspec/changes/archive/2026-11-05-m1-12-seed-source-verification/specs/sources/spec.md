# Spec Delta

## ADDED Requirements

### Requirement: Seed source verification run and exit gate
The system SHALL verify all seed sources against live or mock feeds and search queries over a verification window (default 30 days) via `cli verify-sources`, generating a verification report (records found, dated %, errors), reclassifying or disabling failing sources with explanatory notes, and verifying the 14-day M1 exit gate.

#### Scenario: CLI verify-sources generates per-source verification report
- **WHEN** `cli verify-sources --days 30` runs for an industry pack
- **THEN** every shared pack source is queried across its fetch mode and a summary report is returned reporting records found, percentage with verified publication dates, and fetch status

#### Scenario: Failing source is reclassified or disabled with explanatory notes
- **WHEN** a seed source yields zero dated records over the 30-day verification window
- **THEN** the source is either reclassified or marked `enabled: false` with explanatory notes recorded in the pack configuration so no seed source is left unexplained

#### Scenario: M1 exit gate verification for pt-BR and en-US over 14 days
- **WHEN** a 14-day research run executes for both pt-BR and en-US campaigns and 20 sample records are inspected
- **THEN** the run stores deduplicated research records with verified publication dates, validating the M1 exit gate
