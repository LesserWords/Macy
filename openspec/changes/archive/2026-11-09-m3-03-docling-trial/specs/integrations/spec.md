# integrations Specification Delta

## ADDED Requirements

### Requirement: Docling PDF extractor trial evaluation
The system SHALL evaluate a candidate `docling` `ExtractorPort` plugin scoped exclusively to `application/pdf` documents in an optional Compose image/profile against 3 seed-source PDF fixtures, live open-PDF availability across `data` sources, monthly PDF brief volume (`>= 3 briefs/month`), and the single-VPS resource ceiling (`ADR-012`).

#### Scenario: Docling PDF Extractor evaluation across 3 seed data PDF fixtures and VPS criteria
- **WHEN** the `docling` `ExtractorPort` plugin is tested on 3 statistical PDF fixtures (IBGE, The Vision Council, public Euromonitor summary) and evaluated against live seed source PDF accessibility and the `2 vCPU / 4 GB RAM / 40 GB disk` VPS budget
- **THEN** if fewer than 3 seed `data` sources publish un-gated statistical PDFs, `< 3` briefs/month require PDF extraction, or the container footprint compromises the shared VPS memory budget, the `docling` Compose profile and production entry point are removed/omitted while the trial plugin, fixtures, comparator, and drop rationale are preserved in `spikes/trials/` and recorded in `TECH-RADAR.md` and `ADR-003`
