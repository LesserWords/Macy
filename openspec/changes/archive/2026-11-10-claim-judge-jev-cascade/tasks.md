# Tasks

Nothing here has started. Confirm the test seams in `design.md` before task 1.

## 1. BM-1: Freeze rules (D1)

- [x] 1.1 Write failing test that thresholds match the proposal and that methods refuse a holdout whose hash differs — verify: `uv run pytest evals/test_claim_benchmark.py -k frozen --no-cov` fails
- [x] 1.2 Add thresholds, development/holdout selection and hash check; gitignore `evals/claim_benchmark/holdout/` — verify: test passes
- [x] 1.3 Commit the freeze — verify: commit SHA recorded in `docs/plans/BM.md`

## 2. BM-2: Decisions client (D2, D6)

- [x] 2.1 Write failing HTTP-boundary tests for the request body, choice and refusal parsing, HTTP errors and the 30,000-byte split — verify: `uv run pytest evals/test_claim_benchmark.py -k decisions --no-cov` fails
- [x] 2.2 Implement the client with one URL constant and the pinned model — verify: tests pass; `uv run mypy` and `uv run ruff check .` clean
- [x] 2.3 Review TypeSafe's data policy; make one live one-source call with `data_collection: deny` — verify: policy finding in `docs/plans/BM.md`; `usage.cost` above 0, or refusal documented; cost under US$0.01

## 3. BM-3: Tuned methods (D3)

- [x] 3.1 Write failing tests that advice is excluded and attributed predictions kept, at the prompt-template and routing seam — verify: tests fail
- [x] 3.2 Add `plain_tuned` and `per_claim_v2` — verify: tests pass
- [x] 3.3 Tune on development only, at most 3 revisions each — verify: per-claim misses on attributed predictions are 0 in development runs

## 4. BM-4: Cascade (D3, D5)

- [x] 4.1 Write failing tests for all acceptance and escalation routes, evidence-passage selection and threshold selection — verify: tests fail
- [x] 4.2 Add `jev_judge` and `jev_cascade`; extend reports with D5 fields — verify: tests pass
- [x] 4.3 Run 3 development runs and fit the threshold — verify: 0 failed articles; threshold and run SHAs in `docs/plans/BM.md`; cost under US$0.50

## 5. BM-5: Holdout (D4)

- [x] 5.1 Write failing test that the composition rules reject an undersized dataset — verify: test fails, then passes after implementation
- [x] 5.2 Build the holdout from real public articles and have a person review all labels — verify: composition report passes; SHA-256 committed
- [x] 5.3 Freeze methods and threshold — verify: commit precedes the first holdout run

## 6. BM-6: Measure and decide (W1 to W9, D7)

- [x] 6.1 Write failing tests for `evaluate-kr` on hand-worked reports, including every outcome — verify: tests fail, then pass after implementation
- [x] 6.2 Run all methods 3 times on the holdout — verify: 3 complete reports; total program spend under US$5
- [x] 6.3 Complete the 50-claim evidence audit — verify: audit result recorded for W7
- [x] 6.4 Run `evaluate-kr` — verify: every KR printed PASS or FAIL with one outcome
- [x] 6.5 Update ADR-026 with results and move to Accepted or Rejected — verify: `sh scripts/check.sh` passes and `openspec validate --all --strict` passes
