# ADR-002: LLM calls — single structured calls in a code-controlled workflow, no agents

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F09, F12, F13, F14, ADR-007 · PRD §5.2

## Context
Every MVP step (score, brief, write, evaluate) has a fixed input contract and a fixed output contract. The blueprint's "agents" and "skills" can be mapped onto plain functions and versioned prompts.

## Options considered
| Option | Complexity | Fit |
|---|---|---|
| **A. One structured call per step; the workflow lives in code** | Low | Deterministic, testable at contract seams, cheap |
| B. Agent framework (LangChain, CrewAI, PydanticAI agents) | Med–High | Open-ended loops; harder to test and cost-bound; nothing in the MVP needs it |
| C. Multi-agent | High | Same problems as B, compounded |

## Decision
A. Each step:
1. **Prompt:**
   - system prompt = prompt file + industry pack + brand profile, kept as a stable prefix so it can be cached;
   - user content = the input contract as JSON.
2. **Output:** constrained to the step's Pydantic model and validated (ADR-007).
3. **Settings:** reasoning effort is set per step; the writer and evaluator start at high.
4. **Provenance:** carried in our own `claims[].source_id` and checked in code (F14). It never relies on provider-native citations.

## Consequences
- Easier: each step is tested as "contract in → contract out" with a fake LLM, and cost per step is predictable.
- Harder: open-ended exploration, such as hunting for the original source across unknown sites, isn't possible.

## Revisit when
One step needs open-ended exploration. In that case give that one step a tool loop, and keep the rest as they are.

## Action items
- [x] M0-07: `llm.py` structured-call helper
- [x] M2-01, M3-02, M4-01, M4-03: each step built on the helper
