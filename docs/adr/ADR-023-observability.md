# ADR-023: Observability — usage ledger, Sentry, heartbeat; no self-hosted Langfuse

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F17, F20, ADR-016 · TECH-RADAR

## Context
We need to debug failures, see the cost and latency of each call, and know when a weekly run didn't happen. All of this has to fit on a single VPS.

## Options considered
| Option | Fit |
|---|---|
| **A. `usage_ledger` + `llm_payloads` (90 days) + Sentry free tier + a Healthchecks-style heartbeat** | Covers the need; no extra infrastructure |
| B. Langfuse self-hosted | Needs ClickHouse, Redis and S3, too heavy for the VPS |
| C. OpenRouter Broadcast → Langfuse Cloud | Viable later; data leaves our infrastructure, so it needs a terms review |

## Decision
A.
- **Every LLM and paid API call** gets a ledger row with stage, model, tokens, cached tokens, cost, latency, `prompt_version` and `generation_id`.
- **Request and response bodies** go in `llm_payloads`, which is purged after 90 days.
- **Sentry** captures exceptions, tagged with `workspace_id` and `run_id`.
- **A heartbeat ping** is sent at the end of each run; a missed ping triggers an alert.
- **Streamlit** shows a run-history and cost view.

## Consequences
- Easier: cheap, and all the data is in our database.
- Harder: no ready-made prompt-diff UI.

## Revisit when
Prompt iteration volume makes ledger + Streamlit insufficient. Then trial Langfuse Cloud via OpenRouter Broadcast.

## Action items
- [x] M0-07: ledger writes
- [x] M0-08: Sentry + heartbeat
- [x] M4-07: cost view
