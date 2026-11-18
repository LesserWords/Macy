# Tasks

## 1. Evaluator Prompt, Rubric Wiring & Known-Bad Regression Cases (M4-03 Step 1)
- [x] 1.1 Add failing Step 1 tests in `evals/test_regression.py` verifying a stub evaluator fails on known-bad drafts (thin stub, hallucinated stat, brand violation, off-brief) while calibrated evaluation passes grounded case 6–7 drafts and rejects known-bad drafts.
  → verify: new tests in `evals/test_regression.py` fail red against the 2-line stub `prompts/evaluate.md`.
- [x] 1.2 Author `prompts/evaluate.md` wired to `packs/formats/blog/rubric.yaml` weights/critical rules and extend `FakeLLM._build_fake_quality_report` in `engine/integrations/fakes.py`.
  → verify: Step 1 regression tests pass green offline against `FakeLLM`.

## 2. Isolated LLM Evaluator, Persistence & Usage Ledger (M4-03 Step 2)
- [x] 2.1 Write failing unit/DB tests in `tests/test_evaluator.py` and `tests/test_tenant_scoping.py` for isolated prompt assembly (`F14 AC6` — no `write_blog.md` or writer reasoning in LLM input/prompt), rubric critical-criterion enforcement (`F14 AC2`), 1:1 `quality_reports` persistence, `usage_ledger` logging for `stage = 'evaluate'`, and cross-tenant isolation (`workspace_id`).
  → verify: `uv run pytest tests/test_evaluator.py tests/test_tenant_scoping.py` fails red before implementation.
- [x] 2.2 Implement `engine/db.py` evaluation/opportunity-pool helpers and isolated LLM rubric evaluation + persistence in `engine/quality/evaluate.py` (updating `test_evaluate_invokes_evaluator_when_deterministic_checks_pass` in `tests/test_quality_checks.py`).
  → verify: evaluator isolation, rubric enforcement, persistence, usage logging, and tenant-scoping tests pass green.

## 3. Revision Routing Matrix & Max-2-Loop Cap (M4-03 Step 3)
- [x] 3.1 Write failing routing-matrix tests in `tests/test_evaluator.py` with a fake evaluator covering `PASS`, `REVISION -> brief` (`v+1` preserving `v`, `F12 AC4`), `REVISION -> write` (`revision+1`, `F13 AC3`), `REVISION -> research` / `REJECT` (returning opportunity to `'scored'` pool), and `revision == 2` surfacing without attempting revision 3 (`F14 AC3`, `AC4`).
  → verify: routing-matrix tests fail red before routing service implementation.
- [x] 3.2 Implement cross-brief opportunity revision tracking in `engine/production/writer.py`, `determine_revision_route` in `engine/quality/evaluate.py`, and bounded revision routing in `engine/services.py` (`evaluate_draft`, `evaluate_and_route_draft`).
  → verify: all routing-matrix tests pass green with fake evaluator and Postgres constraints upheld.

## 4. Full Regression Suite, Docs & Quality Gates (M4-03 Step 4)
- [x] 4.1 Verify regression cases 1–7 (score, brief, write, evaluate) green offline (and run `@pytest.mark.eval` live verification if `OPENROUTER_API_KEY` is configured), recording usage/cost metrics in `usage_ledger`.
- [x] 4.2 Update `docs/features/F14-quality-evaluation.md`, `docs/adr/ADR-002-llm-call-pattern.md`, and run all quality gates (`ruff`, `mypy`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk`, `check_translations.py`, `pytest`, `diff-cover == 100%`, `openspec validate --all --strict`).
