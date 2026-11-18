"""Tests for M1-11 trials: RSSHub route evaluation and GDELT DOC 2.0 NewsSearch plugin."""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from engine.contracts import ResearchRecord
from engine.integrations.ports import ExtractorPort, NewsSearchPort
from engine.integrations.registry import UnsupportedLocale, discover_plugins
from spikes.trials.comparison import (
    ComparisonMetrics,
    run_14day_comparison,
)
from spikes.trials.docling_plugin import DoclingExtractor
from spikes.trials.docling_trial import (
    DOCLING_COMPOSE_PROFILE_YAML,
    DoclingTrialResult,
    decide_docling_outcome,
    evaluate_docling_pdf_fixtures,
    evaluate_docling_trial,
)
from spikes.trials.gdelt_plugin import (
    GDELTNewsSearch,
    RateLimiter,
    _parse_gdelt_seendate,
    map_gdelt_locale,
)
from spikes.trials.rsshub_trial import (
    RSSHubTrialResult,
    evaluate_rsshub_sources,
)

# --- RSSHub Trial Tests ---


def test_rsshub_trial_evaluation_on_seed_sources() -> None:
    """RSSHub trial evaluates 13 site_search seed sources and resolves to drop."""
    result = evaluate_rsshub_sources()
    assert isinstance(result, RSSHubTrialResult)
    assert result.total_site_search_sources == 13
    assert result.dated_feeds_count == 0
    assert result.dated_feeds_count < result.keep_threshold
    assert result.decision == "drop"
    assert "cannot generate feeds" in result.rationale
    assert len(result.evaluated_sources) == 13

    for src in result.evaluated_sources:
        assert src.fetch_mode == "site_search"
        assert not src.yields_dated_feed
        assert src.rsshub_route is None


def test_rsshub_trial_keep_branch(tmp_path: Any) -> None:
    """RSSHub trial decides keep if >= 5 sources have radar routes."""
    mock_yaml = tmp_path / "mock_sources.yaml"
    mock_yaml.write_text(
        """
sources:
  - {domain: "abioptica.com.br", fetch_mode: "site_search"}
  - {domain: "feed.com", fetch_mode: "feed"}
  - {domain: "route1.com", fetch_mode: "site_search"}
  - {domain: "route2.com", fetch_mode: "site_search"}
  - {domain: "route3.com", fetch_mode: "site_search"}
  - {domain: "route4.com", fetch_mode: "site_search"}
  - {domain: "route5.com", fetch_mode: "site_search"}
""",
        encoding="utf-8",
    )
    from unittest.mock import patch

    with patch.dict(
        "spikes.trials.rsshub_trial.KNOWN_RSSHUB_RADAR_ROUTES",
        {
            "route1.com": "/route1",
            "route2.com": "/route2",
            "route3.com": "/route3",
            "route4.com": "/route4",
            "route5.com": "/route5",
        },
    ):
        result = evaluate_rsshub_sources(mock_yaml, seed_only=False)
        assert result.dated_feeds_count == 5
        assert result.decision == "keep"


# --- GDELT DOC 2.0 Plugin Tests ---


def test_gdelt_plugin_port_conformance() -> None:
    """GDELT plugin implements NewsSearchPort protocol and has Settings."""
    plugin = GDELTNewsSearch()
    assert isinstance(plugin, NewsSearchPort)
    assert hasattr(GDELTNewsSearch, "Settings")
    plugin.healthcheck()


def test_gdelt_locale_mapping() -> None:
    """GDELT maps pt-BR and en-US and raises on unsupported locales."""
    assert map_gdelt_locale("pt-BR", "br") == ("por", "Brazil")
    assert map_gdelt_locale("en-US", "us") == ("eng", "US")

    with pytest.raises(UnsupportedLocale, match="fr-FR"):
        map_gdelt_locale("fr-FR", "fr")

    with pytest.raises(UnsupportedLocale, match="pt-BR/pt"):
        map_gdelt_locale("pt-BR", "pt")


def test_rate_limiter_throttling() -> None:
    """RateLimiter ensures specified delay between consecutive requests."""
    limiter = RateLimiter(interval_seconds=0.05)
    first_wait = limiter.wait_if_needed()
    assert first_wait == 0.0

    second_wait = limiter.wait_if_needed()
    assert second_wait >= 0.0


def test_parse_gdelt_seendate() -> None:
    """Parse various GDELT seendate formats."""
    # ISO-like with T and Z
    dt1 = _parse_gdelt_seendate("20261105T150000Z")
    assert dt1 == datetime(2026, 11, 5, 15, 0, 0, tzinfo=UTC)

    # ISO-like with T without Z
    dt2 = _parse_gdelt_seendate("20261105T150000")
    assert dt2 == datetime(2026, 11, 5, 15, 0, 0, tzinfo=UTC)

    # Compact 8 digits YYYYMMDD
    dt3 = _parse_gdelt_seendate("20261105")
    assert dt3 == datetime(2026, 11, 5, 0, 0, 0, tzinfo=UTC)

    # Empty / None / Invalid
    assert _parse_gdelt_seendate(None) is None
    assert _parse_gdelt_seendate("") is None
    assert _parse_gdelt_seendate("invalid-date") is None
    assert _parse_gdelt_seendate("2026") is None


def test_gdelt_search_success_and_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    """GDELT search parses articles, filters by since, and returns ResearchRecords."""
    mock_articles = [
        {
            "url": "https://opticanet.com.br/noticia-1",
            "title": "Mercado Óptico em Alta",
            "seendate": "20261101T100000Z",
            "domain": "opticanet.com.br",
        },
        {
            "url": "https://opticanet.com.br/noticia-antiga",
            "title": "Notícia Antiga de Setembro",
            "seendate": "20260901T100000Z",
            "domain": "opticanet.com.br",
        },
        {
            "url": "",  # Empty URL should be ignored
            "title": "Empty URL article",
        },
        "not a dict",  # Non-dict item should be ignored
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"articles": mock_articles})

    monkeypatch.setattr(
        "spikes.trials.gdelt_plugin.safe_request",
        lambda *args, **kwargs: mock_resp,
    )
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )

    plugin = GDELTNewsSearch(settings=GDELTNewsSearch.Settings(rate_limit_seconds=0.0))

    records = plugin.search(
        query="varejo optico",
        locale="pt-BR",
        market="br",
        since=date(2026, 10, 1),
        domain="opticanet.com.br",
    )

    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, ResearchRecord)
    assert rec.headline == "Mercado Óptico em Alta"
    assert rec.url == "https://opticanet.com.br/noticia-1"
    assert rec.source == "opticanet.com.br"
    assert rec.published_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def test_gdelt_search_usage_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    """GDELT search records usage when DB connection and workspace_id are provided."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"articles": []})

    monkeypatch.setattr(
        "spikes.trials.gdelt_plugin.safe_request",
        lambda *args, **kwargs: mock_resp,
    )
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )

    mock_record_usage = MagicMock()
    monkeypatch.setattr("engine.db.record_usage", mock_record_usage)

    conn = MagicMock()
    ws_id = uuid.uuid4()
    run_id = uuid.uuid4()

    plugin = GDELTNewsSearch(
        settings=GDELTNewsSearch.Settings(rate_limit_seconds=0.0),
        conn=conn,
        workspace_id=ws_id,
        run_id=run_id,
    )

    plugin.search(query="optica", locale="pt-BR", market="br")

    mock_record_usage.assert_called_once()
    call_kwargs = mock_record_usage.call_args[1]
    assert call_kwargs["workspace_id"] == ws_id
    assert call_kwargs["run_id"] == run_id
    assert call_kwargs["vendor"] == "gdelt"
    assert call_kwargs["model"] == "doc2"
    assert call_kwargs["cost_usd"] == Decimal("0.000000")


def test_gdelt_search_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """GDELT search handles HTTP errors and invalid JSON."""
    plugin = GDELTNewsSearch(settings=GDELTNewsSearch.Settings(rate_limit_seconds=0.0))

    # 429 Rate Limit
    mock_429 = MagicMock()
    mock_429.status_code = 429
    monkeypatch.setattr(
        "spikes.trials.gdelt_plugin.safe_request",
        lambda *args, **kwargs: mock_429,
    )

    with pytest.raises(ValueError, match="HTTP 429"):
        plugin.search(query="optica", locale="pt-BR", market="br")

    # Non-JSON payload
    mock_bad_json = MagicMock()
    mock_bad_json.status_code = 200
    mock_bad_json.text = "<html><body>Error</body></html>"
    monkeypatch.setattr(
        "spikes.trials.gdelt_plugin.safe_request",
        lambda *args, **kwargs: mock_bad_json,
    )

    with pytest.raises(ValueError, match="non-JSON"):
        plugin.search(query="optica", locale="pt-BR", market="br")


# --- 14-day Comparison Trial Tests ---


def test_14day_comparison_trial_drop_decision() -> None:
    """Comparison shows GDELT unique relevant stories ratio < 20% and resolves to drop."""
    metrics = run_14day_comparison()
    assert isinstance(metrics, ComparisonMetrics)
    assert metrics.brave_relevant_count == 12
    assert metrics.gdelt_relevant_count == 1
    assert metrics.gdelt_unique_relevant_count == 0
    assert metrics.unique_relevant_ratio == 0.0
    assert metrics.unique_relevant_ratio < metrics.keep_threshold
    assert metrics.decision == "drop"
    assert "Outcome: DROP GDELT" in metrics.summary


def test_14day_comparison_trial_keep_scenario() -> None:
    """Comparison evaluates to keep when GDELT unique relevant ratio is >= 20%."""
    hypothetical_brave = [
        {"url": "https://brave.com/article1", "is_optical_relevant": True},
        {"url": "https://brave.com/article2", "is_optical_relevant": True},
    ]
    hypothetical_gdelt = [
        {"url": "https://gdelt.com/unique1", "is_optical_relevant": True},
    ]
    metrics = run_14day_comparison(
        brave_stories=hypothetical_brave,
        gdelt_stories=hypothetical_gdelt,
    )
    assert metrics.brave_relevant_count == 2
    assert metrics.gdelt_unique_relevant_count == 1
    assert metrics.unique_relevant_ratio == 0.50
    assert metrics.decision == "keep"


# --- Docling PDF Extractor Trial Tests (M3-03) ---

DOCLING_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "docling"


def test_docling_plugin_port_conformance_and_settings() -> None:
    """Docling plugin implements ExtractorPort with validated Settings."""
    plugin = DoclingExtractor()
    assert isinstance(plugin, ExtractorPort)
    assert hasattr(DoclingExtractor, "Settings")
    plugin.healthcheck()

    public_only = DoclingExtractor(
        settings=DoclingExtractor.Settings(
            docling_url="http://docling:5001",
            allow_private_service_url=False,
        )
    )
    with pytest.raises(ValueError):
        public_only.healthcheck()


def test_docling_plugin_rejects_non_pdf_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoclingExtractor operates on application/pdf only and skips HTML inputs."""
    plugin = DoclingExtractor()

    html_rec = plugin.extract("https://example.com/article", html="<html><body>HTML</body></html>")
    assert isinstance(html_rec, ResearchRecord)
    assert html_rec.raw is not None
    assert html_rec.raw["fetch_status"] == "unsupported_content_type"

    non_pdf_resp = MagicMock(
        status_code=200,
        headers={"Content-Type": "text/html; charset=UTF-8"},
        content=b"<html><body>Not a PDF</body></html>",
    )
    monkeypatch.setattr("spikes.trials.docling_plugin.safe_request", lambda *a, **k: non_pdf_resp)
    remote_html_rec = plugin.extract("https://thevisioncouncil.org/insights")
    assert remote_html_rec.raw is not None
    assert remote_html_rec.raw["fetch_status"] == "unsupported_content_type"

    err_resp = MagicMock(status_code=403, headers={"Content-Type": "text/html"}, content=b"")
    monkeypatch.setattr("spikes.trials.docling_plugin.safe_request", lambda *a, **k: err_resp)
    err_rec = plugin.extract("https://www.euromonitor.com/eyewear")
    assert err_rec.raw is not None
    assert err_rec.raw["fetch_status"] == "http_error"
    assert err_rec.raw["http_status"] == 403


@pytest.mark.parametrize(
    ("source_url", "pdf_file", "resp_json", "expected_snippet"),
    [
        (
            "https://ftp.ibge.gov.br/Comercio_e_Servicos/pmc_202608caderno.pdf",
            "ibge_pmc_202608_excerpt.pdf",
            "ibge_docling_response.json",
            "Artigos farmacêuticos",
        ),
        (
            "https://thevisioncouncil.org/fixtures/market-insights-2026.pdf",
            "vision_council_market_insights_2026.pdf",
            "vision_council_docling_response.json",
            "$69.5B",
        ),
        (
            "https://euromonitor.com/fixtures/eyewear-summary-2026.pdf",
            "euromonitor_eyewear_public_summary_2026.pdf",
            "euromonitor_docling_response.json",
            "$168.4B",
        ),
    ],
)
def test_docling_plugin_extracts_pdf_fixtures_and_tables(
    source_url: str,
    pdf_file: str,
    resp_json: str,
    expected_snippet: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DoclingExtractor extracts structured tables and statistical facts for 3 PDF fixtures."""
    pdf_bytes = (DOCLING_FIXTURES_DIR / pdf_file).read_bytes()
    docling_payload = (DOCLING_FIXTURES_DIR / resp_json).read_bytes()

    pdf_resp = MagicMock(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        content=pdf_bytes,
    )
    monkeypatch.setattr("spikes.trials.docling_plugin.safe_request", lambda *a, **k: pdf_resp)

    conv_resp = MagicMock(status_code=200, text=docling_payload.decode("utf-8"))
    monkeypatch.setattr("spikes.trials.docling_plugin._post_to_docling", lambda *a, **k: conv_resp)

    plugin = DoclingExtractor()
    rec = plugin.extract(source_url)
    assert rec.raw is not None
    assert rec.raw["fetch_status"] == "ok"
    assert rec.raw["content_type"] == "application/pdf"
    assert rec.raw["tables_count"] >= 1
    assert expected_snippet in (rec.raw["extracted_text"] or "")
    assert len(rec.facts) >= 1


def test_docling_plugin_handles_converter_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoclingExtractor maps service, JSON, and network errors cleanly."""
    from engine.safe_http import SafeHTTPError

    def _raise_http(*_args: Any, **_kwargs: Any) -> Any:
        raise SafeHTTPError("blocked")

    monkeypatch.setattr("spikes.trials.docling_plugin.safe_request", _raise_http)
    plugin = DoclingExtractor()
    net_rec = plugin.extract("https://example.com/blocked.pdf")
    assert net_rec.raw is not None
    assert net_rec.raw["fetch_status"] == "http_error"

    pdf_bytes = (DOCLING_FIXTURES_DIR / "vision_council_market_insights_2026.pdf").read_bytes()
    pdf_resp = MagicMock(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        content=pdf_bytes,
    )
    monkeypatch.setattr("spikes.trials.docling_plugin.safe_request", lambda *a, **k: pdf_resp)

    for bad_resp in (
        MagicMock(status_code=500, text="Internal Server Error"),
        MagicMock(status_code=200, text="{not-json"),
        MagicMock(status_code=200, text='{"status": "failure", "document": {}}'),
    ):
        monkeypatch.setattr(
            "spikes.trials.docling_plugin._post_to_docling",
            lambda *_a, _r=bad_resp, **_k: _r,
        )
        rec = plugin.extract("https://example.com/report.pdf")
        assert rec.raw is not None
        assert rec.raw["fetch_status"] == "extraction_failed"


def test_docling_trial_table_accuracy_and_drop_verdict() -> None:
    """Trial verifies 3/3 fixture accuracy yet drops runtime due to source & VPS fit."""
    fixture_eval = evaluate_docling_pdf_fixtures(DOCLING_FIXTURES_DIR)
    assert len(fixture_eval) == 3
    assert all(item.trafilatura_extracted_chars == 0 for item in fixture_eval)
    assert all(item.tables_accurately_extracted for item in fixture_eval)

    result = evaluate_docling_trial(fixtures_dir=DOCLING_FIXTURES_DIR)
    assert isinstance(result, DoclingTrialResult)
    assert result.fixtures_accurate_count == 3
    assert result.live_open_pdf_sources_count == 1
    assert result.estimated_pdf_briefs_per_month < 3
    assert result.vps_ram_budget_exceeded is True
    assert result.decision == "drop"

    profile_yaml = yaml.safe_load(DOCLING_COMPOSE_PROFILE_YAML)
    assert "docling" in profile_yaml["services"]
    assert "docling" in profile_yaml["services"]["docling"]["profiles"]

    repo_root = Path(__file__).resolve().parent.parent
    compose_text = (repo_root / "infra" / "compose.yaml").read_text(encoding="utf-8")
    assert "docling" not in compose_text.lower()
    assert "docling" not in discover_plugins("extractor").get("extractor", {})


def test_docling_trial_keep_counterfactual() -> None:
    """Decision gate resolves to keep only when sources, briefs/month, and VPS headroom pass."""
    assert (
        decide_docling_outcome(
            fixtures_accurate=3,
            live_open_pdf_sources=3,
            estimated_pdf_briefs_per_month=3,
            vps_ram_budget_exceeded=False,
        )
        == "keep"
    )
    assert (
        decide_docling_outcome(
            fixtures_accurate=2,
            live_open_pdf_sources=3,
            estimated_pdf_briefs_per_month=4,
            vps_ram_budget_exceeded=False,
        )
        == "drop"
    )


def test_docling_docs_recorded_in_radar_and_adr() -> None:
    """TECH-RADAR and ADR-003 record the empirical M3-03 Docling drop outcome."""
    repo_root = Path(__file__).resolve().parent.parent
    radar_text = (repo_root / "docs" / "architecture" / "TECH-RADAR.md").read_text(encoding="utf-8")
    adr_text = (repo_root / "docs" / "adr" / "ADR-003-research-sources.md").read_text(
        encoding="utf-8"
    )

    assert "Pending (M3)" not in radar_text
    assert "Dropped" in radar_text and "Docling" in radar_text
    assert "M3-03" in radar_text
    assert "1/3" in radar_text or "1 of 3" in radar_text

    assert "- [ ] M3-03" not in adr_text
    assert "Docling Trial" in adr_text
    assert "Drop" in adr_text
