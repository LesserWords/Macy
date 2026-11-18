# Design

## Architecture & Data Flow

Per ADR-002 and ADR-004:
1. `engine/strategy/score.py` executes a single structured call per research record using prompt `prompts/score.md`.
2. The prompt receives:
   - System: `prompts/score.md` + industry pack audience/terminology + active brand profile positioning/voice.
   - User: the `ResearchRecord` observation (headline, summary, facts, entities, source, url).
3. The LLM produces a structured `Opportunity` contract containing:
   - `novelty`, `audience_fit`, `brand_fit`, `competitor_gap`, `trend`, `urgency` (each a `SubScore` with 0–10 score, evidence, and `is_estimate`).
   - `funnel_stage` (`tofu`, `mofu`, `bofu`), `confidence` (`low`, `medium`, `high`), `angle`.
4. Code computes `total`:
   $$\text{total} = \frac{\sum (\text{sub\_score} \times \text{weight})}{10}$$
   (since sub-scores are 0–10 and weights sum to 100, the maximum total is 100.0).
5. The scored opportunity is saved into the `opportunities` table in PostgreSQL with composite tenant isolation `(workspace_id, id)`.
6. Hypothesis property tests ensure that:
   - Total is always bounded strictly in $[0.0, 100.0]$.
   - Total is monotonically non-decreasing with respect to any sub-score increase.
