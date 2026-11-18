# F04 — Trusted source management

**Status:** Done · **Milestone:** M1 · **Plan items:** M1-05, M1-12 · **ADRs:** 003, 022 · **OpenSpec capability:** `sources`

## User story
As an owner, I manage the list of trusted sources (add, edit, disable) with their tier, role and fetch mode, so research uses what I trust.

## Scope
- In:
  - Sources screen;
  - one-time seed of the 18 pack sources (`workspace_id NULL`);
  - private sources for the workspace;
  - validation;
  - seed verification run.
- Out: automatic source discovery, per-campaign source lists.

## Acceptance criteria
- **AC1** WHEN the owner adds a `feed` source, THEN the feed URL is fetched once through `safe_http` and must parse, or saving fails with the reason.
- **AC2** WHEN a `site_search` source is added without a domain, THEN saving fails.
- **AC3** WHEN tier or role is missing, THEN saving fails. The role descriptions are shown in the UI.
- **AC4** WHEN a source is disabled, THEN the next run doesn't fetch it (asserted in J3).
- **AC5** WHEN the seed runs, THEN all 18 sources from PRD §5.8 exist with the specified tier, role, locale and fetch mode.
- **AC6** WHEN the seed verification runs over the last 30 days, THEN each source either returns at least one dated record or is reclassified or disabled, with a note recorded.
- **AC7** WHEN two sources share a domain within a workspace, or as shared pack sources, THEN the second is rejected (`NULLS NOT DISTINCT`).

## Data
`sources`

## Interfaces
- `services.save_source()`
- `services.list_sources()`
- `cli.py seed-sources`
- `cli.py verify-sources`

## Tests
| Seam | Test | E2E |
|---|---|---|
| services | Validation matrix; feed fetch uses `safe_http` (fake) | — |
| plugins | Feed parse via recorded fixtures | — |
| UI | Add a real feed source; disable another | **J2** |

## Dependencies
F01, M0-08 (`safe_http`), M1-07 (`rss` plugin, for validation)
