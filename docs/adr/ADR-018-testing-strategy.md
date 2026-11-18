# ADR-018: Testing strategy — TDD at seams, offline E2E per PR, live E2E nightly

**Status:** Accepted (seams approved 2026-11-03) · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** all features, ADR-006, ADR-019, ADR-025 · PRD §9.2, §9.6

## Context
You want test-driven development (TDD) for all code, and live end-to-end (E2E) tests that prove the user flow works against the real world.

## Options considered
| Option | Fit |
|---|---|
| **A. Red → green → refactor at agreed seams; contract suites per port; offline Playwright E2E on every PR; live E2E on staging nightly and before each release** | Fast feedback plus a real-world guarantee |
| B. Unit tests + manual QA | No guarantee for the user flow |
| C. Live E2E on every PR | Slow, costly and flaky |

## Decision
A.
- **Seams (approved):**
  - contracts/config;
  - core logic;
  - LLM steps (fake LLM);
  - plugins (recorded HTTP + contract suite);
  - integrations & webhooks (real Postgres + test receiver);
  - pipeline (all fakes);
  - prompt regression (real LLM, `@pytest.mark.eval`);
  - offline E2E;
  - live E2E.
- **Rules:**
  - Bug fix: a failing reproduction test comes first.
  - Prompt change: a regression case comes first.
  - Never mock internals.
  - Expected values are literals or fixtures.
- **Property-based tests:** Hypothesis, for pure logic (ADR-025).
- **Live E2E journeys J0–J8:**
  - Run on the staging Compose stack, with a budget guard per run.
  - Staging publishes in draft mode only.
  - Failures keep Playwright traces.
  - One retry; a journey that flakes twice in 7 days gets a fix change.

## Consequences
- Easier: each PR proves its own behaviour, and releases are proven against real APIs.
- Harder: fakes and contract suites must be kept up to date, and the live E2E has a small recurring cost.

## Revisit when
Live E2E costs more than its cap or flakes repeatedly. Then trim journeys to the critical path.

## Action items
- [x] M0-02: coverage gates
- [x] M0-06: contract-suite harness
- [x] M1-01 onward: offline E2E journeys added per feature
- [x] M2-07: live E2E J0–J4 nightly on staging
- [x] M4-07: J5–J8
