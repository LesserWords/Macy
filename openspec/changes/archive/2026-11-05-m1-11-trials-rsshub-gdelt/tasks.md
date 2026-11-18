# Tasks

## 1. RSSHub Trial

- [x] 1.1 Implement RSSHub route evaluator `spikes/trials/rsshub_trial.py` against the 13 `site_search` sources from `packs/industries/general/sources.yaml`, verify 0 sources yield dated feeds, and remove `rsshub` service and profile from `compose.yaml`.
- [x] 1.2 Verify `docker compose config` passes and `tests/test_compose_and_docker.py` passes without the `rsshub` service profile.

## 2. GDELT DOC 2.0 Plugin Trial & Comparison

- [x] 2.1 Implement `GDELTNewsSearch` plugin conforming to `NewsSearchPort` in `spikes/trials/gdelt_plugin.py` with rate limiting (1 request / 5 seconds), locale mapping, safe HTTP calls, and date extraction.
- [x] 2.2 Implement 14-day comparison script `spikes/trials/comparison.py` comparing Brave vs GDELT on `pt-BR` optical queries, calculating unique relevant stories percentage.
- [x] 2.3 Add tests in `tests/test_spike_trials.py` validating `NewsSearchPort` contract compliance, rate limiting throttle, locale mapping, error handling, and comparison results (confirming unique relevant stories < 20%).

## 3. Architecture Documentation & Decision Records

- [x] 3.1 Update `docs/architecture/TECH-RADAR.md` recording trials, empirical findings, and drop decisions for RSSHub and GDELT.
- [x] 3.2 Update `docs/adr/ADR-003-research-sources.md` marking M1-11 trial action item complete and recording outcomes.

## Workflow follow-up

- Archive the change after quality gates and GitHub Actions CI pass.
- Squash-merge PR to dev branch.
