# ADR-007: LLM gateway — OpenRouter, with structured outputs through Instructor

**Status:** Accepted (M0 spike confirmed) · **Date:** 2026-11-03 (updated 2026-11-04) · **Deciders:** Product owner
**Related:** F05, F17, ADR-002, ADR-016 · PRD §6

## Context
You want OpenRouter as the LLM API. The pipeline needs three things from it:
- schema-valid JSON for every step;
- prompt caching for the long pack and brand prefix;
- the cost of every call, for spend caps and per-customer costing.

## Spike Findings (M0-07)
The M0-07 spike (`scripts/spike_openrouter.py`) verified against the live OpenRouter catalog:
- **Exact model IDs:**
  - `anthropic/claude-opus-5.5`: Flagship model for brief, write, and evaluate. 1,000,000 context length, reasoning parameters, pricing \$4/M prompt, \$20/M completion, \$0.2/M cache read.
  - `anthropic/claude-haiku-5.5`: High-volume model for research and scoring. 1,000,000 context length, pricing \$0.1/M prompt, \$0.5/M completion, \$0.01/M cache read.
- **Structured output & validation:** Instructor over OpenRouter with `provider.require_parameters: true` successfully constrains outputs to Pydantic contracts (`Opportunity`, etc.) with validation retries and JSON fallback.
- **Prompt caching:** Supported via `cache_control: {"type": "ephemeral"}` in messages and sticky routing via `session_id = run_id`.
- **Cost accounting:** Per-request cost returned directly in `usage.cost` (parsed via `model_extra` / `cost` attribute) and stored as exact USD `numeric(12,6)` in `usage_ledger`.

## Options considered
| Option | Cost | Fit |
|---|---|---|
| **A. OpenRouter (OpenAI-compatible) behind the `LLM` port + Instructor** | List price + ~5.5 % fee on credit top-ups; no confirmed batch discount | One key, any model, model chosen per stage by the user, `usage.cost` on every call, Claude prompt caching supported |
| B. Anthropic API direct | List price; Batch API at −50 % | Native Claude features; single vendor |
| C. Self-hosted gateway (LiteLLM) | Infrastructure + ops | Not needed |

## Decision
A.
- **Client:** Instructor over the `openai` SDK, with `base_url=https://openrouter.ai/api/v1`.
- **Output:** a Pydantic response model per step, with `provider.require_parameters: true`.
- **Validation:** up to 2 retries with the validation error fed back, then a fallback to JSON mode.
- **Caching:** `cache_control` on the stable prefix, plus `session_id` per run for sticky routing.
- **Cost:** `usage.cost` is written to `usage_ledger.cost_usd`, so no local price table is needed.
- **Privacy:** provider data collection is set to deny.

Model per stage is a user setting (ADR-008), and the regression suite is the gate for any model change.

## Consequences
- Easier: switching models, for example to Haiku for bulk scoring.
- Harder: the ~5.5 % fee, and no 50 % batch discount. Strict schema support varies by endpoint, so Pydantic validation is mandatory.

## Revisit when
The M0 spike fails, meaning strict schemas or caching don't work for the Claude endpoints. Then add an `anthropic_direct` plugin behind the same port. Also revisit if monthly LLM spend exceeds about $200, where the batch discount would matter.

## Action items
- [x] M0-07: spike checks:
  - [x] exact model ids (`anthropic/claude-opus-5.5`, `anthropic/claude-haiku-5.5`);
  - [x] strict schemas work with our contracts on Opus 5.5 and Haiku 5.5;
  - [x] `cached_tokens` > 0 on a repeat call;
  - [x] `usage.cost` is present.
- [x] M0-07: `openrouter` plugin, Instructor helper and usage logging
