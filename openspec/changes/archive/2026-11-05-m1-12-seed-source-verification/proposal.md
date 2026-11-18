# Proposal

## Why

Plan item **M1-12** (`docs/plans/M1.md`). Complete the final step and exit gate of Milestone 1 by verifying the 18 optical retail seed sources over a 30-day window (`cli verify-sources`), reclassifying or disabling failing sources with explanatory notes (resolving Fenóptica and Fique de Olho per F04 AC6), and executing 14-day research runs for `pt-BR` and `en-US` with manual inspection of 20 records to confirm the M1 exit gate. Features: F04, F07. ADRs: 003, 022.

Out of scope:
- Milestone 2 work (M2-01 scoring rubrics, staging VPS deployment).
- Adding automatic feed discovery scrapers.

## What Changes

- **Source verification service & CLI**: Add `services.verify_sources()` and `cli.py verify-sources --days 30 [--industry-pack general]` producing a per-source verification report (records found, dated %, fetch errors) across both `feed` and `site_search` fetch modes (F04 AC6).
- **Seed list updates**: Update `packs/industries/general/sources.yaml` with notes for dormant/event sources (disabling off-season Fenóptica with note, verifying Fique de Olho) so no seed source is left unexplained.
- **14-day run and manual check**: Execute a 14-day collection and dedup run across `pt-BR` and `en-US` campaigns, verifying 20 records for publication dates, source attribution, and near-duplicate resolution.
- **M1 Exit Gate Documentation**: Document and attach verification evidence to `docs/plans/M1.md` confirming all three M1 exit gate criteria hold (Offline E2E J0–J3 green; 14-day run stored records for pt-BR and en-US with 20 records inspected; all 18 seed sources verified).

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `sources`: Add requirement and scenarios for seed source verification run (`cli verify-sources`), explanation notes for disabled/dormant sources, and M1 exit gate validation.

## Impact

- CLI & Services: `engine/cli.py`, `engine/services.py`.
- Packs: `packs/industries/general/sources.yaml`.
- Documentation: `docs/plans/M1.md`, `docs/features/F04-source-management.md`.
- Tests: `tests/test_services.py`, `tests/test_cli.py`, `tests/test_journey_j2_sources.py`.
