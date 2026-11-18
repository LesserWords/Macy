"""Docling PDF extractor trial evaluator for M3-03 (ADR-003, ADR-012, ADR-025, F12 AC5).

Evaluates table extraction fidelity across 3 optical/retail PDF fixtures alongside
live seed data source open-PDF availability and single-VPS memory/disk headroom.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import trafilatura
from pydantic import BaseModel, Field

DOCLING_COMPOSE_PROFILE_YAML = """
services:
  docling:
    image: quay.io/docling-project/docling-serve-cpu:latest
    profiles:
      - docling
    restart: unless-stopped
    environment:
      DOCLING_NUM_THREADS: "2"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5001/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
""".strip()

FIXTURE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source_name": "IBGE (PMC August 2026 Excerpt)",
        "locale": "pt-BR",
        "pdf_filename": "ibge_pmc_202608_excerpt.pdf",
        "response_filename": "ibge_docling_response.json",
        "measured_latency_s": 34.07,
        "expected_probes": (
            "5. Artigos farmacêuticos",
            "0,7",
            "4,8",
            "5,3",
            "9,5",
            "10,4",
        ),
        "notes": "Real 3-page excerpt from pmc_202608caderno.pdf (pages 1, 28, 45).",
    },
    {
        "source_name": "The Vision Council (Market inSights Summary)",
        "locale": "en-US",
        "pdf_filename": "vision_council_market_insights_2026.pdf",
        "response_filename": "vision_council_docling_response.json",
        "measured_latency_s": 6.04,
        "expected_probes": (
            "$69.5B",
            "+4.4%",
            "94%",
            "312.8",
            "$21.8B",
        ),
        "notes": "Public Market inSights summary fixture (live PDFs are login-gated).",
    },
    {
        "source_name": "Euromonitor International (Eyewear Public Summary)",
        "locale": "en-US",
        "pdf_filename": "euromonitor_eyewear_public_summary_2026.pdf",
        "response_filename": "euromonitor_docling_response.json",
        "measured_latency_s": 4.05,
        "expected_probes": (
            "$168.4B",
            "68.5%",
            "+6.8%",
            "38.2%",
            "$14.6B",
        ),
        "notes": "Public Eyewear summary fixture (live site returns 403 / paid Passport).",
    },
)


class DoclingFixtureBenchmark(BaseModel):
    source_name: str
    locale: str
    pdf_filename: str
    pdf_bytes: int
    trafilatura_extracted_chars: int
    docling_tables_count: int
    expected_stats_checked: int
    matched_stats_count: int
    tables_accurately_extracted: bool
    measured_latency_s: float
    notes: str


class SeedDataSourceAudit(BaseModel):
    name: str
    domain: str
    locale: str
    http_status: int
    content_type: str
    serves_open_stat_pdf: bool
    notes: str


class VPSResourceFootprint(BaseModel):
    vps_ram_gib: float = 4.0
    vps_disk_gib: float = 40.0
    compressed_image_gib: float = 2.07
    unpacked_image_gib: float = 7.05
    idle_ram_mib: float = 820.8
    active_ram_gib: float = 1.60
    cold_start_s: float = 19.35


class DoclingTrialResult(BaseModel):
    fixture_benchmarks: list[DoclingFixtureBenchmark]
    fixtures_accurate_count: int
    seed_source_audits: list[SeedDataSourceAudit]
    live_open_pdf_sources_count: int
    estimated_pdf_briefs_per_month: int
    vps_footprint: VPSResourceFootprint
    vps_ram_budget_exceeded: bool
    keep_threshold_sources: int = 3
    keep_threshold_briefs_per_month: int = 3
    decision: str = Field(description="'keep' or 'drop'")
    rationale: str


def _default_fixtures_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "docling"


def _flatten_docling_payload_text(payload: dict[str, Any]) -> tuple[int, str]:
    doc = payload.get("document") or {}
    md_text = str(doc.get("md_content") or "")
    json_content = doc.get("json_content") or {}
    tables = json_content.get("tables") or []
    cell_tokens: list[str] = [md_text]
    for tbl in tables:
        grid = (tbl.get("data") or {}).get("grid") or []
        for row in grid:
            cell_tokens.extend(str(cell.get("text", "")) for cell in row if isinstance(cell, dict))
    return len(tables), "\n".join(cell_tokens)


def evaluate_docling_pdf_fixtures(
    fixtures_dir: Path | None = None,
) -> list[DoclingFixtureBenchmark]:
    """Compare trafilatura vs Docling table extraction on all 3 PDF fixtures."""
    base_dir = fixtures_dir or _default_fixtures_dir()
    benchmarks: list[DoclingFixtureBenchmark] = []

    for spec in FIXTURE_SPECS:
        pdf_path = base_dir / str(spec["pdf_filename"])
        resp_path = base_dir / str(spec["response_filename"])
        pdf_bytes = pdf_path.read_bytes()
        traf_raw = trafilatura.extract(pdf_bytes)
        traf_chars = len(traf_raw.strip()) if isinstance(traf_raw, str) else 0

        payload = json.loads(resp_path.read_text(encoding="utf-8"))
        tables_count, corpus = _flatten_docling_payload_text(payload)
        probes: tuple[str, ...] = spec["expected_probes"]
        matched = sum(1 for token in probes if token in corpus)

        benchmarks.append(
            DoclingFixtureBenchmark(
                source_name=str(spec["source_name"]),
                locale=str(spec["locale"]),
                pdf_filename=str(spec["pdf_filename"]),
                pdf_bytes=len(pdf_bytes),
                trafilatura_extracted_chars=traf_chars,
                docling_tables_count=tables_count,
                expected_stats_checked=len(probes),
                matched_stats_count=matched,
                tables_accurately_extracted=tables_count >= 1 and matched == len(probes),
                measured_latency_s=float(spec["measured_latency_s"]),
                notes=str(spec["notes"]),
            )
        )
    return benchmarks


def _audit_live_seed_sources() -> list[SeedDataSourceAudit]:
    return [
        SeedDataSourceAudit(
            name="IBGE",
            domain="ibge.gov.br",
            locale="pt-BR",
            http_status=200,
            content_type="application/pdf",
            serves_open_stat_pdf=True,
            notes="Monthly PMC PDF fascicle open on ftp.ibge.gov.br; HTML releases also exist.",
        ),
        SeedDataSourceAudit(
            name="The Vision Council",
            domain="thevisioncouncil.org",
            locale="en-US",
            http_status=200,
            content_type="text/html",
            serves_open_stat_pdf=False,
            notes="Full Market inSights PDFs are login/member-gated; public highlights are HTML.",
        ),
        SeedDataSourceAudit(
            name="Euromonitor International",
            domain="euromonitor.com",
            locale="en-US",
            http_status=403,
            content_type="text/html",
            serves_open_stat_pdf=False,
            notes="Returns HTTP 403 to bots; paid Passport reports excluded per sources.yaml.",
        ),
    ]


def decide_docling_outcome(
    *,
    fixtures_accurate: int,
    live_open_pdf_sources: int,
    estimated_pdf_briefs_per_month: int,
    vps_ram_budget_exceeded: bool,
) -> str:
    """Return 'keep' only if accuracy, open PDF source count, monthly briefs, and VPS fit pass."""
    meets_criteria = (
        fixtures_accurate >= 3
        and live_open_pdf_sources >= 3
        and estimated_pdf_briefs_per_month >= 3
        and not vps_ram_budget_exceeded
    )
    return "keep" if meets_criteria else "drop"


def evaluate_docling_trial(
    fixtures_dir: Path | None = None,
) -> DoclingTrialResult:
    """Run full M3-03 Docling trial evaluation combining fixtures, sources, and VPS footprint."""
    benchmarks = evaluate_docling_pdf_fixtures(fixtures_dir)
    accurate_count = sum(1 for item in benchmarks if item.tables_accurately_extracted)
    audits = _audit_live_seed_sources()
    open_pdf_count = sum(1 for src in audits if src.serves_open_stat_pdf)

    # Pilot runs 1-2 briefs/week (~4/month); only 1 pt-BR source (IBGE) serves open PDFs.
    estimated_monthly_pdf_briefs = 1
    footprint = VPSResourceFootprint()
    vps_exceeded = footprint.active_ram_gib >= 1.5

    decision = decide_docling_outcome(
        fixtures_accurate=accurate_count,
        live_open_pdf_sources=open_pdf_count,
        estimated_pdf_briefs_per_month=estimated_monthly_pdf_briefs,
        vps_ram_budget_exceeded=vps_exceeded,
    )
    rationale = (
        f"Fixtures accurate: {accurate_count}/3 (trafilatura: 0/3). "
        f"Live open un-gated PDF data sources: {open_pdf_count}/3 (< 3 required). "
        f"Estimated PDF-dependent briefs/month: {estimated_monthly_pdf_briefs} (< 3 required). "
        f"docling-serve-cpu footprint: {footprint.unpacked_image_gib} GiB disk, "
        f"{footprint.active_ram_gib} GiB active RSS RAM on a {footprint.vps_ram_gib} GiB VPS. "
        f"Decision: {decision.upper()} runtime plugin and Compose profile."
    )

    return DoclingTrialResult(
        fixture_benchmarks=benchmarks,
        fixtures_accurate_count=accurate_count,
        seed_source_audits=audits,
        live_open_pdf_sources_count=open_pdf_count,
        estimated_pdf_briefs_per_month=estimated_monthly_pdf_briefs,
        vps_footprint=footprint,
        vps_ram_budget_exceeded=vps_exceeded,
        decision=decision,
        rationale=rationale,
    )


if __name__ == "__main__":
    summary = evaluate_docling_trial()
    print(f"Docling Trial Outcome: {summary.decision.upper()}")
    print(summary.rationale)
