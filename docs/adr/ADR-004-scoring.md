# ADR-004: Scoring — the LLM judges each criterion, code computes the total

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F09, F02 · Blueprint §7

## Context
Topic priority uses the blueprint §7 rubric, which weights six criteria 30/20/20/15/10/5. The weights must be tunable per campaign, and later the Phase 4 analytics loop should be able to re-fit them.

## Options considered
| Option | Fit |
|---|---|
| **A. LLM gives each criterion 0–10 + evidence + `is_estimate`; Python computes the weighted total** | Deterministic and tunable; the weights live in data |
| B. LLM returns the total | Opaque, can't be re-weighted, and drifts with the prompt |

## Decision
A.
- **Weights:** stored in `campaigns.config` (the `CampaignConfig` model, which holds the defaults).
- **Total:** a `numeric(5,2)` from 0 to 100, computed in code and covered by property tests (ADR-025).
- **SEO potential:** uses DataForSEO search volume when available. Otherwise it is an LLM estimate flagged `is_estimate=true`, and the UI never shows it as a number of searches.

## Consequences
- Easier: weights can be tuned per campaign or re-fit from analytics without touching prompts.
- Harder: two places to keep consistent, the prompt (criteria definitions) and the config (weights).

## Revisit when
Phase 4 analytics data exists (8 or more weeks of published articles).

## Action items
- [ ] M2-01: scoring step, `CampaignConfig` weights and regression cases 1–5
