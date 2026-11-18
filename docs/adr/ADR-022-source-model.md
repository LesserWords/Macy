# ADR-022: Source model — user-managed list with roles, tiers and fetch modes

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F04, F07, ADR-003 · PRD §5.8 (seed list of 18 sources)

## Context
Users choose and add trusted sources. Your list mixes three kinds of source:
- news sites (Abióptica, Opticanet);
- companies (EssilorLuxottica);
- data and management sources (IBGE, Sebrae, Euromonitor).

Most of them have no RSS feed.

## Options considered
| Option | Fit |
|---|---|
| **A. `sources` table with tier 1–3, role, fetch mode and locales; seeded once from the pack's `sources.yaml`** | Each source's role decides how it is used; users can extend the list |
| B. A flat list of URLs | Data sources would get recommended as topics; no fallback when a site has no feed |

## Decision
A.
- **Tier:** from your priority labels (very high → 1, high → 2, medium → 3).
- **Role:**
  - `news` and `company` sources provide topics;
  - `data` and `management` sources only provide evidence at brief time.
- **Fetch mode:** `feed` (needs `feed_url`) or `site_search` (domain-scoped search).
- **Ownership:** `workspace_id NULL` marks a shared pack source.
- **Validation on save:**
  - a feed must parse;
  - a `site_search` source needs a domain;
  - tier and role are required.
- **Seed check in M1:** each seed source must return at least one dated record in 30 days, or be reclassified or disabled with a note.

## Consequences
- Easier: precise use of each source, and RSSHub can later turn `site_search` sources into feeds.
- Harder: users must pick a role when adding a source. The UI explains each role.

## Revisit when
RSSHub passes its trial: switch the affected sources to `feed`.

## Action items
- [x] M1-05: Sources screen, seed list, validation
- [ ] M1-12: seed verification run
