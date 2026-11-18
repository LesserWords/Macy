# F09 — Opportunity scoring and weekly report

**Status:** Done (M2-01 & M2-02 delivered in PR #22, #23) · **Milestone:** M2 · **Plan items:** M2-01, M2-02 · **ADRs:** 002, 004 · **OpenSpec capability:** `scoring`

## User story
As an owner, every week I get a ranked report of the best story opportunities for my audience, with evidence for each score, so I can choose topics quickly.

## Scope
- In:
  - an LLM scoring step (criteria scores + evidence + `is_estimate`);
  - code-computed total using the campaign weights;
  - funnel stage, angle and confidence;
  - report screen (top 10, top N recommendations from articles/week, minimum 3);
  - `topics.awaiting_approval` event;
  - regression cases 1–5.
- Out: email digest (users can wire one through webhooks and n8n).

## Acceptance criteria
- **AC1** WHEN scoring runs, THEN each non-duplicate, verified record gets an `Opportunity` with 6 criterion scores (0–10, each with evidence), and `total` = Σ(score × weight) × 10 / 100, computed in code.
- **AC2** WHEN no keyword data exists yet, THEN SEO potential is flagged `is_estimate` and never shown as a search count.
- **AC3** WHEN the report is shown, THEN it lists the top 10. Each item has the original URL, publication date, date status, summary, angle, funnel stage, score and confidence; the top N are marked as recommended.
- **AC4** WHEN a record's date is unverified, THEN it is never displayed as "verified".
- **AC5** WHEN the report is ready, THEN a `topics.awaiting_approval` event is emitted.
- **AC6** WHEN regression cases 1–5 run against the real LLM, THEN each lands in its expected priority band.

## Data
`opportunities` (scores, total, rank, `funnel_stage`, confidence, status)

## Interfaces
- `strategy.score(run)`
- `strategy.report(run)`
- `Opportunity` contract

## Tests
| Seam | Test | E2E |
|---|---|---|
| core | Total maths (Hypothesis: bounds, monotonic in each score) | — |
| LLM step | Fake LLM → valid `Opportunity` | — |
| eval | Regression cases 1–5 (`@pytest.mark.eval`) | — |
| UI | Report shows ≤ N recommendations with all fields | **J3** |

## Dependencies
F07, F08, F02 (weights)
