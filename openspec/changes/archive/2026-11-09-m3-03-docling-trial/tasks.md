# Tasks

## 1. Docling Extractor Plugin (`application/pdf` only)

- [x] 1.1 Write failing plugin contract and unit tests in `tests/test_spike_trials.py` verifying `DoclingExtractor` satisfies `ExtractorPort`, defines Pydantic `Settings`, rejects HTML/non-PDF inputs cleanly (`unsupported_content_type`), parses real `docling-serve` JSON/Markdown responses for `application/pdf` into structured `ResearchRecord` statistics, validates URLs with `safe_http`, and handles HTTP/JSON error states.
  → verify: `uv run pytest tests/test_spike_trials.py -k docling_plugin` fails red before implementation.
- [x] 1.2 Implement `DoclingExtractor` in `spikes/trials/docling_plugin.py` implementing `ExtractorPort` against the `docling-serve` `/v1/convert/file` contract.
  → verify: `uv run pytest tests/test_spike_trials.py -k docling_plugin` passes green.

## 2. 3-PDF Table Comparison, Seed Source Audit & Compose Profile Evaluation

- [x] 2.1 Write failing trial evaluation tests in `tests/test_spike_trials.py` comparing Docling vs `trafilatura` across the 3 optical/retail PDF fixtures (`IBGE PMC August 2026` excerpt, `The Vision Council Market inSights` summary, `Euromonitor Eyewear` public summary), auditing live open-PDF availability across seed `data`/`management` sources, checking `docling-serve-cpu` resource footprint against the `ADR-012` `4 GB RAM / 40 GB disk` VPS budget, and verifying `compose.yaml` + `pyproject.toml` remove/exclude the dropped `docling` profile and production entry point.
  → verify: `uv run pytest tests/test_spike_trials.py -k docling_trial` fails red before implementation.
- [x] 2.2 Add compact PDF fixtures and recorded live `docling-serve` outputs in `tests/fixtures/docling/`, implement `spikes/trials/docling_trial.py` evaluating table cell accuracy (`3/3`), live open-PDF availability (`1/3 < 3`), monthly PDF utilization (`< 3 briefs/month`), VPS resource fit (`7.05 GiB` disk / `1.60 GiB` active RAM), and Compose profile removal verification.
  → verify: `uv run pytest tests/test_spike_trials.py` passes green and all `docker compose config` validations pass without a `docling` profile.

## 3. Architecture & Tech Radar Documentation (Step 3)

- [x] 3.1 Write failing documentation assertion test in `tests/test_spike_trials.py` confirming `docs/architecture/TECH-RADAR.md` and `docs/adr/ADR-003-research-sources.md` record the M3-03 Docling empirical metrics (`3/3` fixture accuracy, `1/3` open seed PDF sources, `7.05 GiB` unpacked image, `1.60 GiB` active RSS RAM) and Drop decision.
  → verify: `uv run pytest tests/test_spike_trials.py -k docling_docs` fails red before docs updates.
- [x] 3.2 Update `docs/architecture/TECH-RADAR.md` and `docs/adr/ADR-003-research-sources.md` with M3-03 empirical findings, decision, and revisit trigger.
  → verify: `scripts/check.sh` and `npx --yes @fission-ai/openspec@1.16.0 validate --all --strict --no-interactive` pass green.
