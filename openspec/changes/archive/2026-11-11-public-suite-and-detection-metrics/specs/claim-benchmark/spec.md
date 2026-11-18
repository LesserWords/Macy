# claim-benchmark Delta Specification

## ADDED Requirements

### Requirement: Holdout audit uses a non-candidate model with verified quotes
Before the holdout hash is frozen, every planted error and clean sentence SHALL be checked against the full source by a model that is not a method under test, and only verbatim passages confirmed in code SHALL be used as evidence.

#### Scenario: Planted error supported deeper in the source
- **WHEN** the auditor quotes a verbatim passage from the full source that supports a planted error
- **THEN** the error is listed for correction and is not kept as an error

#### Scenario: Quote not found in the source
- **WHEN** an auditor quote does not appear verbatim in the source
- **THEN** the item is marked unverified and left for human review

### Requirement: Public regression suite
The benchmark SHALL load fixed-seed, label-stratified samples from public labeled datasets as one-sentence articles, verify them against a committed manifest of sample ids and SHA-256, and never commit sample text.

#### Scenario: Same seed gives the same sample
- **WHEN** the same rows are sampled twice with the same seed
- **THEN** the selected ids are identical and balanced across labels

#### Scenario: Gated dataset without a token
- **WHEN** a gated tier is requested and `HF_TOKEN` is not set
- **THEN** loading stops with a message naming the variable

### Requirement: Detection metrics in every report
Every method report SHALL include article-level TPR for single-error and multi-error articles, TNR for clean articles and clean sentences, and the Rogan-Gladen estimate.

#### Scenario: Rogan-Gladen undefined
- **WHEN** TPR + TNR − 1 is zero or negative
- **THEN** the estimate is reported as null rather than a number
