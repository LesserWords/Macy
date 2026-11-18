# Proposal: M4-03 — LLM evaluator and revision routing

## Why
Once deterministic checks pass (`M4-02`, `F14 AC1`, `AC5`), the engine must evaluate article quality using an independent LLM auditor governed by `prompts/evaluate.md` and the format's weighted rubric (`packs/formats/blog/rubric.yaml`), persist 1:1 `quality_reports`, record usage in `usage_ledger`, route `REVISION`/`REJECT` verdicts back to the responsible stage (`brief`, `write`, or `research` → topic pool), and cap revision loops at 2 so drafts at revision 2 surface as-is to the owner with findings (`F14 AC2–AC4`, `AC6`, `F12 AC4`, `F13 AC3`, `ADR-002`).

## What Changes
- **Evaluator Prompt & Rubric Wiring (`prompts/evaluate.md`, `engine/quality/evaluate.py`, `engine/integrations/fakes.py`):**
  - Replace the placeholder `prompts/evaluate.md` with an independent, weighted-rubric editorial quality auditor prompt aligned to `packs/formats/blog/rubric.yaml` (`factual_accuracy` 30% critical, `audience_relevance` 25%, `search_optimization` 20%, `brand_voice_alignment` 15%, `clarity_and_structure` 10%).
  - Extend `FakeLLM` (`_build_fake_quality_report`) for offline evaluation of good and known-bad drafts.
  - Ensure strict writer/evaluator isolation (`F14 AC6`): evaluator prompts and serialized inputs never load `prompts/write_blog.md`, import `engine.production`, or include writer reasoning/chain-of-thought.
- **Persistent Quality Reports, Opportunity Return-to-Pool & Usage Logging (`engine/db.py`, `engine/quality/evaluate.py`):**
  - Add tenant-scoped `engine/db.py` helpers for draft evaluation context, guarded `drafts.status` transitions (enforcing that `revision == 2` never transitions to `'revision'`), 1:1 `quality_reports` upserts/lookups, cross-brief opportunity max draft revision lookup, and `return_opportunity_to_pool` (resetting `'approved'` → `'scored'` with `decided_by=NULL, decided_at=NULL` to free campaign weekly quota).
  - Execute `llm.structured_call(stage="evaluate", reasoning_effort="high", ...)` so every LLM evaluation logs `usage_ledger` and `llm_payloads` with `prompt_version`.
- **Revision Routing Matrix & Max-2-Loop Cap (`engine/services.py`, `engine/production/writer.py`):**
  - Track draft revisions monotonically (`0..2`) across an opportunity even when a `brief` revision creates `brief v+1`, preventing reset back to `0` and enforcing max 2 revision loops.
  - Route verdicts cleanly: `PASS` → `drafts.status = 'pass'` + `draft.awaiting_approval`; `brief` → `services.revise_brief` + next draft; `write` → `services.revise_draft` (`revision+1`); `research` / `REJECT` → `drafts.status = 'reject'` + return opportunity to `'scored'` pool; exhausted `revision == 2` → stop revising, transition `drafts.status = 'pass'` (keeping `quality_reports.verdict = 'revision'` with findings), and emit `draft.awaiting_approval` (`F14 AC4`, `F15 AC1`).
- **Regression Suite & Verification (`evals/test_regression.py`, `tests/test_evaluator.py`, `tests/test_tenant_scoping.py`, docs):**
  - Verify stub evaluator failure vs known-bad drafts, regression cases 1–7 green, prompt/reasoning isolation test, routing-matrix + max-2-loop tests, and tenant isolation tests.
