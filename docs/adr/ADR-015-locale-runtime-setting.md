# ADR-015: Locale and market are runtime settings; pt-BR and en-US are supported first

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F02, F07, F11, F13, F14

## Context
Language and market must be a user choice. Prompts, regression cases and test data still have to be built and proven for each locale.

## Options considered
| Option | Fit |
|---|---|
| **A. `locale` (BCP-47) + `market` (ISO-3166) per campaign, passed on every contract and plugin call; a locale is "supported" only once its regression cases pass** | Nothing hard-coded; quality stays gated |
| B. Locale in plugin or YAML config | Hard-coded per deploy; rejected |

## Decision
A.
- **Supported first:** `pt-BR` and `en-US`.
- **Plugins:** each plugin maps the locale to its vendor's codes and raises `UnsupportedLocale` if it can't serve it. The error appears when the user saves the setting.
- **Cross-language sources:** international (en-US) sources also feed the pt-BR campaign. The writer writes in the campaign locale and cites the source in its original language.
- **Draft check:** the draft's language is verified with lingua-py (ADR-025).

## Consequences
- Easier: a new market is configuration plus regression cases.
- Harder: every locale needs eval cases and source coverage.

## Revisit when
A third locale is requested. Add its regression cases before marking it supported.

## Action items
- [x] M1-03: campaign locale/market settings + validation against plugins
- [ ] M2-01 / M4-03: regression cases parametrised by locale
