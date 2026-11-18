# Tasks

## 1. Source Verification Service & CLI

- [x] 1.1 Implement `services.verify_sources()` in `engine/services.py` testing each shared pack source across `feed` and `site_search` modes for a given day window, calculating records found, dated %, and fetch errors.
- [x] 1.2 Implement `cli.py verify-sources --days 30 [--industry-pack general]` formatting and printing the verification table report.
- [x] 1.3 Add unit tests in `tests/test_services.py` and `tests/test_cli.py` covering `verify_sources` with feeds, site search, error handling, and CLI output.

## 2. Seed Source Reclassification & Notes

- [x] 2.1 Update `packs/industries/general/sources.yaml` with notes for Fenóptica (`enabled: false`, biennial trade fair notes) and Fique de Olho (verified trends blog).
- [x] 2.2 Verify `seed_sources` in `engine/services.py` loads and updates sources with `enabled` status and notes from `sources.yaml`.

## 3. 14-Day Research Run & M1 Exit Gate Evidence

- [x] 3.1 Run 14-day research run for both `pt-BR` and `en-US` campaigns and perform manual check of 20 sample records (verifying correct publication dates, source attribution, and duplicate handling).
- [x] 3.2 Update `docs/plans/M1.md` attaching the 20-record sample verification evidence, source verification summary, and confirming the M1 exit gate holds.
- [x] 3.3 Update `docs/features/F04-source-management.md` marking AC6 complete and F04 status as Done.

## Workflow follow-up

- Archive the change after quality gates and GitHub Actions CI pass.
- Squash-merge PR to dev branch.
