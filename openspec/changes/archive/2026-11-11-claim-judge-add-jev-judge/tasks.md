# Tasks

## 1. Amend the decision rule

- [x] 1.1 Write failing test that the frozen rules list `jev_judge` and `jev_cascade` as candidates — verify: `uv run pytest evals/test_claim_benchmark.py -k frozen --no-cov` fails
- [x] 1.2 Add `JEV_CANDIDATES` to `evals/claim_benchmark/rules.py` — verify: test passes
- [x] 1.3 Update ADR-026, `docs/plans/BM.md` and the amended proposal's decision rule pointer; validate — verify: `openspec validate --all --strict` passes; commit SHA recorded in `docs/plans/BM.md` as the new freeze
- [x] 1.4 BM-6 `evaluate-kr` implements this rule — verify: its tests cover every scenario in this change
