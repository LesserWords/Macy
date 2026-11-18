# F13 — Article writing with provenance

**Status:** Done (M4-01 in PR #32; AC4 verified in F14 / M4-02) · **Milestone:** M4 · **Plan items:** M4-01 · **ADRs:** 002, 010, 015 · **OpenSpec capability:** `writing`

## User story
As an owner, I get a blog draft in my campaign's language and brand voice, where every factual claim points to a source I can check.

## Scope
- In:
  - `ContentDraft` contract (title, meta, slug, `body_md`, FAQ, CTA, `claims[]`);
  - `draft_claims` rows;
  - revision drafts (0–2).
- Out: images and ALT text (Phase 2), other formats (Phase 3).

## Acceptance criteria
- **AC1** WHEN a brief is final, THEN a draft at revision 0 is created and validates against `ContentDraft`.
- **AC2** WHEN the draft is stored, THEN each claim is written to `draft_claims`, and a `fact` claim without `document_id` is rejected by the database.
- **AC3** WHEN the writer is called for a revision, THEN the evaluator's findings are included and a new draft row with `revision+1` is created. Revision 3 is impossible (CHECK constraint).
- **AC4** WHEN the draft is written, THEN it is in the campaign locale (verified in F14) and follows the brand rules (verified in F14).
- **AC5** WHEN the writer calls the LLM, THEN the call is recorded in `usage_ledger` with stage `write` and a `prompt_version`.

## Data
`drafts`, `draft_claims`, `usage_ledger`

## Interfaces
- `production.write(brief, findings?)`
- `ContentDraft` contract

## Tests
| Seam | Test | E2E |
|---|---|---|
| LLM step | Fake LLM → draft + claims; a fact without a source → DB error surfaced | — |
| eval | Regression cases 6–7 | — |
| UI / pipeline | Draft exists in the chosen locale | **J5** |

## Dependencies
F12
