# claim-benchmark Specification

## ADDED Requirements

### Requirement: Benchmark rules are frozen before measurement
The benchmark SHALL commit win thresholds, the development and holdout split, and dataset hashes before any run that counts toward a key result, and SHALL record the git SHA in every report.

#### Scenario: Run recorded after freeze
- **WHEN** a benchmark report counts toward a key result
- **THEN** the commit that froze the rules is an ancestor of the SHA in that report

#### Scenario: Holdout is single-use
- **WHEN** holdout results have been computed and a method or threshold is then changed
- **THEN** that holdout SHALL NOT count toward key results for the changed method

### Requirement: Decisions API client
The benchmark SHALL call Jev through the OpenRouter Decisions API with a pinned model, a single configurable endpoint, and `provider.data_collection` set to `deny`, and SHALL record the resolved model and reported cost.

#### Scenario: Batched claim decision request
- **WHEN** claims from one source are judged
- **THEN** one request is sent with the source in `state`, one `choice` question per claim, and model `typesafe/jev-1.13`

#### Scenario: Refused question
- **WHEN** a response contains a `refusal` answer for a claim
- **THEN** the other answers are kept and that claim is routed to the LLM judge

#### Scenario: Oversized source
- **WHEN** source text would exceed 30,000 UTF-8 bytes
- **THEN** it is split at paragraph boundaries so that no request exceeds the limit

### Requirement: Comparison methods
The benchmark SHALL provide `plain_tuned`, `per_claim_v2`, `jev_judge` and `jev_cascade`, tuned only on development data.

#### Scenario: Advice is not checkable
- **WHEN** an article sentence is advice or the writer's opinion
- **THEN** `plain_tuned` and `per_claim_v2` do not report it as a factual problem

#### Scenario: Attributed prediction is checkable
- **WHEN** an article attributes a forecast or plan to a named organization or person
- **THEN** the claim is judged against the source

### Requirement: Cascade acceptance
`jev_cascade` SHALL accept a claim as supported only when Jev chooses `supported` at or above the threshold fitted on development data and every number in the claim appears in the source, and SHALL send every other claim to the LLM judge with the verbatim-quote guard.

#### Scenario: Confident supported claim with matching numbers
- **WHEN** Jev chooses `supported` at or above the threshold and the numbers match
- **THEN** the claim is accepted without an LLM judge call and receives a code-selected evidence passage

#### Scenario: Number missing from source
- **WHEN** Jev chooses `supported` but a number in the claim does not appear in the source
- **THEN** the claim is sent to the LLM judge

#### Scenario: Low confidence
- **WHEN** Jev's confidence is below the threshold or absent
- **THEN** the claim is sent to the LLM judge

### Requirement: Private holdout dataset
The benchmark SHALL evaluate key results on a private holdout of at least 30 real public articles with person-reviewed labels, whose content is excluded from git and whose hash is recorded.

#### Scenario: Holdout composition is checked
- **WHEN** the holdout is loaded for a key-result run
- **THEN** the run stops unless it has at least 15 articles per language, 120 planted errors, 30 clean articles and 15% multi-error articles, and its hash matches the frozen value

### Requirement: Run reports support audit
Every benchmark report SHALL include git SHA, dataset SHA-256, resolved model IDs, measured cost per article, escalation rate, per-language metrics and a Jev reliability table.

#### Scenario: Incomplete report is rejected
- **WHEN** a report lacks a required field
- **THEN** key-result evaluation fails and names the missing field

### Requirement: Outcome is computed from key results
The benchmark SHALL compute each key result and a GO, GO WITHOUT JEV or STOP outcome from three holdout reports using the thresholds frozen in the change proposal.

#### Scenario: All worth results pass
- **WHEN** W1 through W9 pass
- **THEN** the outcome is GO

#### Scenario: Jev adds no value
- **WHEN** `per_claim_v2` passes W1, W2, W3, W7, W8 and W9 and the cascade fails W4, W5 or W6
- **THEN** the outcome is GO WITHOUT JEV

#### Scenario: No method beats the tuned prompt
- **WHEN** no method passes W1, W2 and W3
- **THEN** the outcome is STOP
