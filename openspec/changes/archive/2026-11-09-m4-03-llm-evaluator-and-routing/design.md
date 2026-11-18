# Design: M4-03 — LLM evaluator and revision routing

## Architecture & Module Boundaries
- **Isolation (`engine/quality/evaluate.py` vs `engine/production/writer.py`, `F14 AC6`, `lint-imports`):**
  - `engine/quality/evaluate.py` imports `config`, `contracts`, `db`, `events`, `llm`, `integrations.ports`, and `quality.checks` only. It never imports `engine.production`, `engine.strategy`, or `engine.services` and never reads `prompts/write_blog.md`.
  - `_prepare_evaluate_user_input` extracts an explicit allowlist of draft fields (`title`, `meta_description`, `slug`, `body_md`, `faq`, `cta`, `claims`), brief fields, sanitized evidence summaries/facts, rubric criteria, and non-critical deterministic findings — stripping any writer prompt or reasoning metadata.
- **Rubric Evaluation & Invariant Enforcement (`evaluate.py`):**
  - Loads `packs/formats/<slug>/rubric.yaml` via `config.load_format_pack`.
  - Calls `llm.structured_call(stage="evaluate", model_cls=QualityReport, reasoning_effort="high", ...)`.
  - Post-validates returned `QualityReport`: merges non-critical deterministic findings, enforces that any `"critical"` finding or failure on a `critical: true` rubric criterion cannot leave verdict as `"PASS"`, clamps score `[0.0, 100.0]`, and resolves deterministic dominant route via `determine_revision_route(report)` (highest severity tier first, tie-broken upstream-first: `research > brief > write`).
- **State Machine & Max-2-Revision Guard (`DATA-MODEL.md §4`, `F14 AC3–AC4`, `F12 AC4`, `F13 AC3`):**
  - Guarded draft transitions: `'draft' -> 'evaluating' -> ('pass' | 'revision' | 'reject')`.
  - To prevent infinite loops when a `brief` revision creates `brief v+1` while advancing toward the global 2-revision cap, `writer._execute_write` computes `next_revision = max(brief_next, opp_max + 1)` across all briefs for the same `opportunity_id`.
  - When a draft at `revision == 2` still returns a non-`research` failure (`REVISION`), `db.transition_draft_status` rejects `'revision'` at `revision == 2` (`DATA-MODEL.md §4`), transitioning `drafts.status` to `'pass'` so the draft enters the Gate 2 queue as-is, while `quality_reports.verdict` records `'revision'` alongside the full `findings[]` and `draft.awaiting_approval` is emitted idempotently (`surfaced_to_owner = True`).
  - When verdict is `REJECT` or dominant route is `'research'`, `quality_reports.verdict = 'reject'`, `drafts.status = 'reject'`, and `db.return_opportunity_to_pool` resets the approved opportunity to `'scored'` (`decided_by = NULL`, `decided_at = NULL`, satisfying the Postgres `opportunities` check constraint and freeing the campaign's `articles_per_week` slot).
- **Orchestration Loop (`engine/services.py`):**
  - `services.evaluate_draft` evaluates a persisted draft once (returning report, route, surfaced status).
  - `services.evaluate_and_route_draft` runs the full bounded loop (`evaluate -> brief/write revision -> re-evaluate`) until `'pass'`, `'reject'` (`research` return to pool), or `revision == 2` surfacing.
