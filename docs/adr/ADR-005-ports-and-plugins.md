# ADR-005: Integrations — Protocol ports + entry-point plugins

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F05, F07, F11, F16, ADR-007, ADR-008, ADR-021 · PRD §5.6

## Context
Vendors for search, extraction, keywords, the LLM, the CMS and analytics will change for reasons of price, quality or market. Blueprint §15 asks for new capabilities without rewriting the core, and you want nothing hard-coded.

## Options considered
| Option | Complexity | Fit |
|---|---|---|
| **A. `typing.Protocol` ports + `importlib.metadata` entry points; plugin chosen per workspace (ADR-008)** | Low (standard library) | Swappable, type-checked, third parties can add plugins |
| B. pluggy hook system | Med | Built for many hooks on many events; here there is one adapter per port |
| C. `if vendor == …` in core | Lowest | Hard-coded; rejected |
| D. n8n or Zapier for I/O | Med | Moves logic out of tested code and loses the contracts |

## Decision
A.
- **Ports:** `LLM`, `FeedSource`, `NewsSearch`, `Extractor`, `KeywordProvider`, `Publisher`, `AnalyticsSource` (later).
- **Plugin contract:** each plugin declares a Pydantic `Settings` model, a `healthcheck()`, and returns contract types only.
- **Contract test suite:** shared per port, run against every registered plugin and the fake.
- **Discovery:** first-party plugins register through `pyproject.toml` entry points, exactly like third-party ones.
- **Events:** pipeline events go out as webhooks (ADR-017), not as in-process hooks.

## Consequences
- Easier: adding a vendor means a new folder, an entry point and a passing contract suite, with zero edits to `engine/`.
- Harder: each port needs a fake and a contract suite. That work is front-loaded in M0.

## Revisit when
In-process extension points (not I/O) are needed. Then consider pluggy for those only.

## Action items
- [ ] M0-06: ports, registry, fakes and the contract-suite harness
