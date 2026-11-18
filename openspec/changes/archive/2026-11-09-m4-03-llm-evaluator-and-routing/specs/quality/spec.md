## ADDED Requirements

### Requirement: Isolated LLM rubric evaluation and persisted quality reports
After deterministic checks pass, the system SHALL evaluate drafts with `prompts/evaluate.md` and the format weighted rubric without passing the writer's prompt or reasoning, log `usage_ledger` for stage `"evaluate"` (`reasoning_effort = "high"`), and persist a 1:1 `quality_reports` record per draft revision (`F14 AC2`, `AC6`, `ADR-002`).

#### Scenario: Isolated evaluator produces rubric-grounded QualityReport and usage record
- **WHEN** `quality.evaluate` or `services.evaluate_draft` evaluates a draft that passes critical deterministic checks
- **THEN** the evaluator loads `prompts/evaluate.md` and the format rubric, never passes `write_blog.md` instructions or writer reasoning in the LLM prompt/input, records a `usage_ledger` row with `stage = 'evaluate'` and `prompt_version`, and returns a `QualityReport` whose findings cite rubric criteria and valid `responsible_module` values (`research`, `brief`, or `write`)

#### Scenario: Known-bad drafts fail against calibrated rubric evaluation whereas stub fails red
- **WHEN** regression cases 6–7 grounded drafts and known-bad drafts (thin stub, hallucinated statistics, prohibited claims, or off-brief copy) are evaluated
- **THEN** a rubber-stamp stub evaluator erroneously passes known-bad drafts (red check), whereas `prompts/evaluate.md` + `quality.evaluate` passes valid grounded drafts and rejects known-bad drafts with non-PASS verdicts and targeted findings

### Requirement: Revision routing and maximum two-revision surfacing cap
The system SHALL route verdicts by `responsible_module` (`brief` → `revise_brief`, `write` → `revise_draft`, `research` or `REJECT` → reject draft and return opportunity to `'scored'` pool) and stop revising at `revision = 2`, surfacing failing drafts as-is with findings (`F14 AC3`, `AC4`, `F12 AC4`, `F13 AC3`).

#### Scenario: Routing matrix dispatches brief, write, and research findings accurately
- **WHEN** `services.evaluate_and_route_draft` processes evaluator outcomes for `PASS`, `REVISION(brief)`, `REVISION(write)`, and `REVISION(research)` / `REJECT`
- **THEN** `PASS` marks `drafts.status = 'pass'` and emits `draft.awaiting_approval`; `brief` creates brief `v+1` (preserving `v`) and drafts the next revision; `write` creates `revision+1` incorporating findings; and `research` / `REJECT` marks `drafts.status = 'reject'`, `quality_reports.verdict = 'reject'`, and returns the opportunity to `'scored'` with `decided_by = NULL` freeing the weekly approval slot

#### Scenario: Failing draft at revision 2 surfaces to owner without attempting revision 3
- **WHEN** a draft fails evaluation across two revision loops and reaches `revision = 2` with non-`PASS`, non-`research` findings
- **THEN** no `revision = 3` draft or extra revision brief is created, `quality_reports.verdict` records `'revision'` with full findings, `drafts.status` transitions to `'pass'` for Gate 2 review (`revision = 2` cannot move to `'revision'`), and `draft.awaiting_approval` is emitted with owner surfacing metadata
