# F12 — Editorial brief generation

**Status:** Done (M3-02 AC1–AC4 delivered in PR #30; M3-03 Docling trial AC5 dropped with evidence in PR #31) · **Milestone:** M3 · **Plan items:** M3-02, M3-03 · **ADRs:** 002, 022 · **OpenSpec capability:** `briefing`

## User story
As an owner, each approved topic becomes a structured brief (angle, audience, search intent, outline, required facts with sources, CTA). This makes the article focused and grounded.

## Scope
- In:
  - `EditorialBrief` contract, matching the blueprint §6 fields plus `required_facts[]` with `document_id`;
  - evidence lookup from `data` and `management` sources;
  - linking the brand version used;
  - brief versioning on revision.
- Out: a brief editing UI. The owner rejects a brief at gate 2 instead.

## Acceptance criteria
- **AC1** WHEN an opportunity is approved, THEN a brief (version 1) is created and validates against `EditorialBrief`, and its `brand_profile_id` is the current brand.
- **AC2** WHEN the brief lists required facts, THEN each references an existing document in the workspace (enforced by an FK through claims later, and checked in code here).
- **AC3** WHEN evidence sources (role `data` or `management`) are relevant, THEN supporting statistics are searched for and attached as documents.
- **AC4** WHEN the evaluator routes a REVISION to `brief`, THEN a new brief version is created and the old one remains.
- **AC5** WHEN the Docling trial runs, THEN PDF statistics from at least 3 seed data sources are extracted correctly, or the trial is recorded as dropped.

## Data
`briefs` (version, `brand_profile_id`, `prompt_version`, keywords, payload), `documents`

## Interfaces
- `strategy.brief(opportunity)`
- `EditorialBrief` contract

## Tests
| Seam | Test | E2E |
|---|---|---|
| LLM step | Fake LLM → valid brief; invalid `document_id` rejected | — |
| eval | Brief cases within regression cases 6–7 | — |
| UI / pipeline | Brief exists and validates after approval | **J4** |

## Dependencies
F10, F11, F03
