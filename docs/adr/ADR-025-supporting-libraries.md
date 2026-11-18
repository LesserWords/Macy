# ADR-025: Supporting libraries from the tech radar (Adopt list)

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** [TECH-RADAR.md](../architecture/TECH-RADAR.md), ADR-007, ADR-011, ADR-017, ADR-018

## Context
Several small, mature libraries remove code we would otherwise write, or reduce risk, without adding a service to run. Instructor, dbmate/squawk and Standard Webhooks have their own ADRs (007, 011 and 017).

## Options considered
| Need | Chosen | Rejected |
|---|---|---|
| Draft language check (pt / en) | **lingua-py**, restricted to pt and en | fastText (model file), langdetect (less accurate on short text) |
| Edge cases in pure logic (scoring maths, checks, URL canonicalisation, SSRF guard) | **Hypothesis** property tests | Example-only tests |
| Markdown + YAML frontmatter draft import (`M5-07`) | **python-frontmatter** | Custom regex/string splitting |
| Lightweight CPU PDF-to-Markdown import (`M5-07`) | **pymupdf4llm** (~25 MB wheel, 0 ML models) | `docling-serve-cpu` (7.05 GiB disk, 1.60 GiB RAM on 4 GB VPS) |
| Headless external MCP agent runner for **Agent-Driven Mode** (`M5-08`) | **strands-agents** (external MCP client only, outside `engine/`) | LangChain / CrewAI / PydanticAI inside `engine/` (`ADR-002`) |

## Decision
Adopt lingua-py in the deterministic checks (F14) and in E2E J5. Adopt Hypothesis as a dev dependency, required for every pure function that has more than 2 branches.

Trials and holds are tracked in TECH-RADAR:
- **Trials:** DBOS, RSSHub, GDELT, Docling.
- **Holds:** Langfuse self-hosted, agent frameworks, Crawl4AI/Firecrawl, Svix, Temporal/Hatchet.

## Consequences
- Easier: fewer hand-written heuristics, and bugs are found earlier.
- Harder: Hypothesis tests need deterministic seeds in CI (`derandomize=True` profile).

## Revisit when
The tech radar is reviewed at each milestone end.

## Action items
- [x] M0-02: Hypothesis CI profile
- [x] M4-02: lingua-py check
- [ ] Each milestone end: radar review, recorded in TECH-RADAR
