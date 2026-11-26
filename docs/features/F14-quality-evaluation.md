# F14 — Quality evaluation and revision loop

**Status:** Done (M4-02 AC1, AC5 in PR #33; M4-03 AC2–AC4, AC6 delivered) · **Milestone:** M4 · **Plan items:** M4-02, M4-03 · **ADRs:** 002, 025 · **OpenSpec capability:** `quality`

## User story
As an owner, every draft is checked by code and by an independent evaluator before I see it. Problems go back to the step that caused them, at most twice.

## Scope
- In:
  - Deterministic checks:
    - every fact claim has a source;
    - source dates fall inside the freshness window;
    - cited URLs were fetched successfully;
    - the primary keyword appears in the title, H1 and meta;
    - meta length and slug format are valid;
    - no prohibited brand phrases;
    - CTA intensity is within the campaign limit;
    - the language matches the campaign (lingua-py);
    - cross-domain adaptation uniqueness (`check_adaptation_uniqueness`, `ADR-034`, `M11-02`).
  - LLM evaluator with the format rubric.
  - Verdicts PASS / REVISION / REJECT.
  - Routing to brief, write or research.
  - Regression cases 6–7.
- Out: human-in-the-loop edits inside the evaluator.

## Acceptance criteria
- **AC1** WHEN a draft is evaluated, THEN the deterministic checks run first. Any critical failure gives REVISION without an LLM call.
- **AC2** WHEN the deterministic checks pass, THEN the LLM evaluator returns a `QualityReport` with per-criterion findings, each naming a `responsible_module`.
- **AC3** WHEN the verdict is REVISION and findings name `brief`, THEN a new brief version is created. If they name `write`, a new draft revision is created. If they name `research`, the topic goes back to the pool (REJECT).
- **AC4** WHEN a draft at revision 2 still fails, THEN it is surfaced to the owner as-is, with its findings.
- **AC5** WHEN the draft language detected by lingua-py ≠ the campaign language, THEN the check fails.
- **AC6** WHEN the evaluator runs, THEN it never receives the writer's prompt or reasoning.

## Data
`quality_reports` (1:1 per draft revision), `drafts.status`

## Interfaces
- `quality.checks.run(draft)`
- `quality.evaluate(draft, brief, evidence, rubric)`
- `QualityReport` contract

## Tests
| Seam | Test | E2E |
|---|---|---|
| core | Each deterministic check (Hypothesis for slug/meta/keyword rules) | — |
| LLM step | Routing matrix with a fake evaluator | — |
| eval | Regression cases 6–7, including known-bad drafts | — |
| UI / pipeline | `QualityReport` verdict present; revision loop ≤ 2 | **J5** |

## Dependencies
F13
