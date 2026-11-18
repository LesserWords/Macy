# ADR-020: Spec-driven development with OpenSpec

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** all features, ADR-018, ADR-019 · PRD §9.1

## Context
You want OpenSpec for quality. Specs must stay in step with the code and drive the tests.

## Options considered
| Option | Fit |
|---|---|
| **A. OpenSpec in the repo: 1 feature doc → 1 capability spec; 1 PR → 1 OpenSpec change** | Specs are versioned with the code; scenarios become tests |
| B. Specs in a wiki | Drift away from the code |

## Decision
A.
- **Init:** `openspec init` in M0.
- **Specs:** each `docs/features/F*.md` seeds `openspec/specs/<capability>/spec.md`, written as WHEN/THEN scenarios. Each scenario has at least one test.
- **Changes:** each plan item (PR) runs `openspec propose` → `apply` → `archive`.
  - `tasks.md` lists vertical slices, each starting with "write a failing test".
  - CI runs `openspec validate`.
- **PR rule:** a PR touching `engine/`, `plugins/`, `prompts/`, `packs/` or `db/` must reference an OpenSpec change id.
- **Feature docs** are the product view. Once code exists, the OpenSpec spec is the source of truth for behaviour.

## Consequences
- Easier: traceability from feature → scenario → test → PR.
- Harder: some ceremony per PR. It's kept proportionate, since a small PR has a small change.

## Revisit when
The ceremony exceeds about 10 % of PR time. Then simplify the templates.

## Action items
- [x] M0-01: `openspec init` + CI validate
- [x] M0-05: capability specs seeded from F01–F20
