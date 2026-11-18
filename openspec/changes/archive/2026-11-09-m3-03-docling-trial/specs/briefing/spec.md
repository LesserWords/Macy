# briefing Specification Delta

## ADDED Requirements

### Requirement: PDF statistical extraction trial (M3-03)
The system SHALL evaluate PDF statistical table extraction across seed evidence (`data`) sources during M3 and either retain an optional PDF extractor for `application/pdf` or record the trial as dropped with empirical justification (F12 AC5).

#### Scenario: Docling PDF extraction trial evaluates accuracy, seed source availability, and resource fit
- **WHEN** the M3-03 Docling trial executes against general seed evidence sources (`IBGE`, `The Vision Council`, `Euromonitor`) using the optional `docling-serve-cpu` container and compares extracted tables against ground truth and operational criteria
- **THEN** the trial records both fixture-level extraction accuracy (`3/3` PDFs) and operational rejection criteria (`1/3` un-gated open PDF seed sources, `< 3 briefs/month`, `7.05 GiB` image / `1.60 GiB` RSS RAM on a `4 GB` VPS), documenting the Drop verdict cleanly in `TECH-RADAR.md` and `ADR-003`
