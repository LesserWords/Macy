# Proposal

## Why

Plan item **M1-11** (`docs/plans/M1.md`). Conduct the time-boxed trials for RSSHub and GDELT DOC 2.0 to decide whether either technology satisfies its empirical criteria to reduce paid search calls and discover timely optical retail news. Features: F07, F08. ADRs: 003, 025.

Out of scope:
- M1-12 live seed source verification run.
- Docling extractor trial (scheduled for M3-03).
- Adding unproven trial dependencies to the production stack.

## What Changes

- **Trial 1 (RSSHub)**: Evaluate route availability and dated item extraction for the 13 `site_search` seed sources from `packs/industries/general/sources.yaml`. Verify whether $\ge 5$ yield dated items. If $< 5$, remove the `rsshub` service profile from `compose.yaml` and record the drop decision.
- **Trial 2 (GDELT DOC 2.0)**: Implement a `gdelt` plugin satisfying `NewsSearchPort` with rate limiting (1 request / 5 seconds) and run a 14-day comparison against Brave Search News for `pt-BR`. Evaluate unique relevant optical stories; if $< 20\%$, drop the plugin and record the drop decision.
- **Architecture & Tech Radar**: Record trial methodology, empirical findings, and decisions in `docs/architecture/TECH-RADAR.md` and `docs/adr/ADR-003-research-sources.md`.
- **Trial Spike & Tests**: Provide trial scripts and contract tests in `spikes/trials/` and `tests/test_spike_trials.py` validating the RSSHub route evaluation, GDELT `NewsSearchPort` compliance, rate-limiting enforcement, and comparison metrics with 100% test coverage.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `integrations`: Add requirement and scenarios for research source trial evaluations: RSSHub route evaluation for `site_search` sources and GDELT DOC 2.0 rate-limited `NewsSearchPort` plugin evaluation compared against Brave for `pt-BR`.

## Impact

- Compose: `compose.yaml` (removal of `rsshub` service and profile).
- Code & Spikes: `spikes/trials/` (`rsshub_trial.py`, `gdelt_plugin.py`, `comparison.py`).
- Tests: `tests/test_spike_trials.py`.
- Documentation: `docs/architecture/TECH-RADAR.md`, `docs/adr/ADR-003-research-sources.md`, `docs/plans/M1.md`.
- Specifications: `openspec/changes/m1-11-trials-rsshub-gdelt/specs/integrations/spec.md`.
