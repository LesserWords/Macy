# Proposal

## Why

Plan item **M3-03** (`docs/plans/M3.md`). Execute the time-boxed Docling trial to evaluate whether adding an optional `docling` PDF `ExtractorPort` plugin and `docling` Docker Compose profile improves evidence grounding for `EditorialBrief` (F12 AC5) without violating the single-VPS resource envelope (`ADR-012`) or Tech Radar simplicity rule (`TECH-RADAR.md`). Features: F12 (AC5). ADRs: 003, 012, 025.

Out of scope:
- Modifying core `app`/`worker` Docker image to bundle PyTorch/Docling ML weights (`TECH-RADAR.md`).
- Adding paid external PDF-parsing SaaS vendors.
- M4 writer, evaluator, and publisher implementation.

## What Changes

- **Docling Extractor Plugin (`spikes/trials/docling_plugin.py`)**: Implement a `docling` `ExtractorPort` plugin gated exclusively to `application/pdf` documents, interfacing with the optional `docling-serve` REST API (`/v1/convert/file`) and validating URLs via `safe_http`.
- **Empirical 3-PDF Fixture & Live Benchmark (`spikes/trials/docling_trial.py`, `tests/fixtures/docling/`)**: Benchmark real `quay.io/docling-project/docling-serve-cpu` extraction across three PDF fixtures (real IBGE *Pesquisa Mensal de Comércio* excerpt `Tabela 1` & `Tabela 6`, The Vision Council *Market inSights* summary, and Euromonitor *World Market for Eyewear* public summary) vs `trafilatura`, and evaluate keep/drop criteria against `M3.md`, `F12 AC5`, `TECH-RADAR.md` (`>= 3 briefs/month` cite PDF data), live seed `data` source open-PDF accessibility, and `ADR-012` (`2 vCPU, 4 GB RAM, 40 GB disk`).
- **Keep/Drop Execution (`DROP` for MVP)**: Because only **1 of 3** named seed `data` sources (`IBGE`) publishes open un-gated `application/pdf` statistics (Vision Council PDFs are login-gated with public stats in HTML; Euromonitor returns HTTP `403` / paid Passport), yielding `< 3 briefs/month` citing PDF-only data while `docling-serve-cpu` demands **7.05 GiB** unpacked image storage (**2.07 GiB** compressed), **820.8 MiB** idle RAM, and **1.60 GiB** active RSS (~40% of total 4 GB VPS RAM), remove the optional `docling` Compose profile and omit `docling` from production `pyproject.toml` entry points while preserving the plugin, fixtures, recorded outputs, and comparator in `spikes/trials/` + `tests/test_spike_trials.py`.
- **Architecture & Tech Radar (`docs/architecture/TECH-RADAR.md`, `docs/adr/ADR-003-research-sources.md`)**: Record full empirical metrics, fixture comparison accuracy (`3/3`), live source audit (`1/3`), VPS sizing analysis, Drop decision, and clear revisit triggers.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `integrations`: Add scenario covering Docling PDF `ExtractorPort` evaluation across 3 PDF fixtures and operational/VPS keep-drop criteria.
- `briefing`: Add PDF evidence extraction trial requirement and scenario addressing F12 AC5.

## Impact

- Code & Spikes: `spikes/trials/docling_plugin.py`, `spikes/trials/docling_trial.py`.
- Tests & Fixtures: `tests/test_spike_trials.py`, `tests/fixtures/docling/` (compact PDFs <70 KB total and recorded `docling-serve` JSON responses).
- Documentation: `docs/architecture/TECH-RADAR.md`, `docs/adr/ADR-003-research-sources.md`, `docs/plans/M3.md`, `docs/features/F12-editorial-brief.md`.
- Specifications: `openspec/changes/m3-03-docling-trial/specs/integrations/spec.md`, `openspec/changes/m3-03-docling-trial/specs/briefing/spec.md`.
