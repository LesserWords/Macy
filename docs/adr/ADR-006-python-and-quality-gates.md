# ADR-006: Python, with fallow's checks mapped to Python quality gates

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** ADR-018, ADR-019 · PRD §9.3, §9.4

## Context
You use fallow, but it supports only TypeScript and JavaScript. The project's extraction, data and SEO ecosystem is strongest in Python (trafilatura, Pydantic).

## Options considered
| Option | Pros | Cons |
|---|---|---|
| **A. Python + equivalent gates** | trafilatura; Pydantic contracts; best data libraries | 5 tools instead of 1; no single health score |
| B. TypeScript, to use fallow | One tool you already know | Weaker article extraction; would need a Python sidecar |

## Decision
A. All gates run from one `scripts/check.sh`, called by both pre-commit and CI. Thresholds live in `pyproject.toml`, and changing one requires an OpenSpec change.

| Check | Tool | Threshold |
|---|---|---|
| Cyclomatic complexity | ruff C901 | ≤ 10 |
| Cognitive complexity | complexipy (ratchet) | ≤ 15 |
| Branches / statements / arguments | ruff PLR0912 / PLR0915 / PLR0913 | ≤ 12 / 50 / 5 |
| Dead code | vulture (confidence ≥ 60, so unused functions/classes at Vulture's 60% default are caught) + ruff F401/F841 | 0 |
| Duplication | jscpd | 0 % (≤ 3 % cap, 0 new clones) |
| Boundaries and cycles | import-linter | 0 violations |
| Types | mypy `--strict` | 0 errors |
| Coverage | diff-cover / pytest-cov | 100 % of changed lines / ≥ 90 % of branches |
| Migrations | squawk | 0 violations |
| Translations | Babel completeness check | 0 untranslated |
| Hotspots (complexity × low coverage, CRAP-style) | complexipy + coverage, joined by a small script | report only |

## Consequences
- Easier: quality is enforced from an empty repo, so nothing has to be retrofitted.
- Harder: early velocity is slower, and there are several tools to keep updated.

## Revisit when
A gate blocks legitimate code repeatedly. In that case, change the threshold through an OpenSpec change with its reasoning, never with ad-hoc skips.

## Action items
- [x] M0-01: repo, ruff, mypy, pre-commit and CI skeleton
- [x] M0-02: remaining gates, plus a "bad sample" that proves each gate fails
