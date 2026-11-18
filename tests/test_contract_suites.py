"""Contract test suites verifying that all plugins satisfy their port protocols (ADR-005)."""

import base64
import json
import secrets
import threading
import uuid
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from standardwebhooks import Webhook

from engine import safe_http
from engine.contracts import ContentDraft, DraftClaim, PublishedContent, PublishRequest
from engine.integrations.brave import BraveNewsSearch
from engine.integrations.ports import (
    AnalyticsSourcePort,
    ExtractorPort,
    FeedSourcePort,
    KeywordProviderPort,
    LLMPort,
    NewsSearchPort,
    PublisherPort,
)
from engine.integrations.registry import UnsupportedLocale, discover_plugins
from engine.integrations.rss import RSSFeedSource
from engine.integrations.trafilatura_extractor import TrafilaturaExtractor

DISCOVERED = discover_plugins()


@pytest.mark.vcr
@pytest.mark.parametrize("name,cls", DISCOVERED.get("llm", {}).items())
def test_llm_contract_suite(name: str, cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, LLMPort)
    assert hasattr(cls, "Settings")
    adapter.healthcheck()

    resp = adapter.complete("Generate ideas for myopia")
    assert isinstance(resp, dict)
    assert len(resp) > 0


@pytest.mark.vcr
@pytest.mark.parametrize("name,cls", DISCOVERED.get("feed_source", {}).items())
def test_feed_source_contract_suite(
    name: str,
    cls: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )
    adapter = cls()
    assert isinstance(adapter, FeedSourcePort)
    assert hasattr(cls, "Settings")
    adapter.healthcheck()

    items = adapter.fetch_feed(
        "https://example.com/rss",
        since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert isinstance(items, list)
    assert len(items) > 0
    for record in items:
        assert record.headline
        assert record.url
        assert record.summary


@pytest.mark.vcr
def test_rss_seed_sources_contract_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )
    adapter = RSSFeedSource()
    since = datetime(2026, 10, 1, tzinfo=UTC)
    seed_feeds = [
        "https://opticanet.com.br/feed",
        "https://eyewearintelligence.com/feed",
        "https://abioptica.com.br/feed",
    ]
    for feed_url in seed_feeds:
        records = adapter.fetch_feed(feed_url, since=since)
        assert len(records) >= 1
        rec = records[0]
        assert rec.headline
        assert rec.canonical_url is not None
        assert "utm_" not in rec.canonical_url
        assert "fbclid" not in rec.canonical_url
        assert rec.published_at is not None
        assert rec.verification_status == "verified"


@pytest.mark.vcr
def test_trafilatura_seed_sources_contract_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )
    extractor = TrafilaturaExtractor()
    seed_articles = [
        (
            "https://opticanet.com.br/2026/11/01/varejo-optico-crescimento",
            "Redação Opticanet",
            "https://abioptica.com.br/estudos-setoriais",
        ),
        (
            "https://eyewearintelligence.com/2026/11/02/myopia-control-portfolios",
            "Sophie Laurent",
            "https://thevisioncouncil.org/research-reports",
        ),
        (
            "https://abioptica.com.br/2026/11/03/balanco-anual-setor-optico",
            "Assessoria Abióptica",
            "https://sebrae.com.br/setor-optico",
        ),
    ]
    for article_url, expected_author, expected_outbound in seed_articles:
        record = extractor.extract(article_url)
        assert record.headline
        assert record.published_at is not None
        assert record.verification_status == "verified"
        assert record.raw is not None
        assert record.raw["fetch_status"] == "ok"
        assert record.raw["author"] == expected_author
        assert record.raw["extracted_text"]
        assert expected_outbound in record.raw["outbound_links"]


@pytest.mark.vcr
@pytest.mark.parametrize("name,cls", DISCOVERED.get("news_search", {}).items())
def test_news_search_contract_suite(
    name: str,
    cls: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )
    adapter = cls()
    assert isinstance(adapter, NewsSearchPort)
    assert hasattr(cls, "Settings")
    adapter.healthcheck()

    results = adapter.search(
        query="optometry",
        locale="pt-BR",
        market="br",
        since=date(2026, 10, 1),
    )
    assert isinstance(results, list)
    assert len(results) > 0
    for record in results:
        assert record.headline
        assert record.url


@pytest.mark.vcr
def test_brave_domain_and_open_search_contract_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )
    adapter = BraveNewsSearch()
    domain_results = adapter.search(
        query="varejo optico",
        locale="pt-BR",
        market="br",
        since=date(2026, 10, 1),
        domain="sebrae.com.br",
    )
    assert len(domain_results) >= 1
    assert domain_results[0].canonical_url is not None
    assert "sebrae.com.br" in domain_results[0].canonical_url
    assert "utm_" not in domain_results[0].canonical_url
    assert domain_results[0].published_at is not None
    assert domain_results[0].verification_status == "verified"

    open_results = adapter.search(
        query="myopia control",
        locale="en-US",
        market="us",
        since=date(2026, 10, 1),
    )
    assert len(open_results) >= 1
    assert open_results[0].headline
    assert open_results[0].canonical_url is not None

    with pytest.raises(UnsupportedLocale, match="fr-FR"):
        adapter.search(
            query="optometry",
            locale="fr-FR",
            market="fr",
            since=date(2026, 10, 1),
        )


@pytest.mark.parametrize("name,cls", DISCOVERED.get("extractor", {}).items())
def test_extractor_contract_suite(name: str, cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, ExtractorPort)
    assert hasattr(cls, "Settings")
    adapter.healthcheck()

    record = adapter.extract(
        "https://example.com/article", html="<html><body>Article</body></html>"
    )
    assert record.headline
    assert record.url == "https://example.com/article"
    assert record.summary


@pytest.mark.vcr
@pytest.mark.parametrize("name,cls", DISCOVERED.get("keyword_provider", {}).items())
def test_keyword_provider_contract_suite(
    name: str,
    cls: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda host: ["93.184.216.34"],
    )
    adapter = cls()
    assert isinstance(adapter, KeywordProviderPort)
    assert hasattr(cls, "Settings")
    adapter.healthcheck()

    keywords = adapter.lookup(["lentes", "oculos"], locale="pt-BR", market="br")
    assert isinstance(keywords, list)
    assert len(keywords) == 2
    for kw in keywords:
        assert kw.keyword in ["lentes", "oculos"]
        assert kw.search_volume >= 0
        assert kw.cpc_usd >= 0.0


class _ContractWebhookServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], secret: str) -> None:
        self.secret = secret
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        super().__init__(server_address, _ContractWebhookHandler)


class _ContractWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, _ContractWebhookServer)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        headers = {k.lower(): v for k, v in self.headers.items()}
        verified = Webhook(server.secret).verify(raw_body, headers)
        assert verified["mode"] == "draft"
        key = (str(verified["draft_id"]), str(verified["destination"]))
        record = server.records.get(key)
        if record is None:
            slug = str(verified["draft"]["slug"])
            record = {
                "schema_version": 1,
                "destination": str(verified["destination"]),
                "external_id": f"wh-{len(server.records) + 1}-{verified['draft_id']}",
                "url": f"https://cms.example.com/{slug}",
                "status": "published",
                "published_at": "2026-11-09T12:00:00Z",
                "raw": {"mode": str(verified["mode"]), "is_live": False},
            }
            server.records[key] = record
        body_bytes = json.dumps(record).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def verify_publisher_contract(
    adapter: PublisherPort,
    *,
    destination: str = "fake-cms",
) -> tuple[PublishedContent, PublishedContent]:
    """Verify PublisherPort idempotence per draft, non-live draft mode, and recorded URL."""
    assert isinstance(adapter, PublisherPort)
    assert hasattr(type(adapter), "Settings")
    adapter.healthcheck()

    draft_id = uuid.uuid4()
    draft = ContentDraft(
        title="Test Draft",
        meta_description="Meta description for test",
        slug="test-draft",
        body_md="# Test Body\n\nContent paragraph.",
        claims=[DraftClaim(text="Analysis claim", kind="analysis")],
    )
    request = PublishRequest(
        draft_id=draft_id,
        draft=draft,
        locale="pt-BR",
        destination=destination,
        mode="draft",
    )
    first = adapter.publish(request)
    second = adapter.publish(request)

    assert first.destination == destination
    assert first.external_id
    assert first.url
    assert first.status in ("published", "scheduled", "draft")
    assert first.status != "live"

    assert second.external_id == first.external_id
    assert second.url == first.url
    assert second.destination == first.destination

    first_raw = first.raw or {}
    assert first_raw.get("mode") == "draft"
    assert first_raw.get("is_live") is False
    assert first_raw.get("live") is not True
    assert first_raw.get("public") is not True
    return first, second


def _build_contract_publisher(
    name: str,
    cls: type[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PublisherPort, _ContractWebhookServer | None]:
    if name == "markdown_export":
        export_dir = tmp_path / "exports"
        adapter: PublisherPort = cls(settings=cls.Settings(export_dir=str(export_dir)))
        return adapter, None
    if name == "http_webhook":
        monkeypatch.setattr(safe_http, "is_ip_allowed", lambda _ip: True)
        secret = "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode("ascii")
        srv = _ContractWebhookServer(("127.0.0.1", 0), secret=secret)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        endpoint = f"http://127.0.0.1:{srv.server_port}/publish"
        adapter = cls(settings=cls.Settings(endpoint_url=endpoint, signing_secret=secret))
        return adapter, srv
    return cls(), None


@pytest.mark.parametrize("name,cls", DISCOVERED.get("publisher", {}).items())
def test_publisher_contract_suite(
    name: str,
    cls: type,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, server = _build_contract_publisher(name, cls, tmp_path, monkeypatch)
    try:
        verify_publisher_contract(adapter, destination="fake-cms")
        if name == "markdown_export":
            md_files = list((tmp_path / "exports").rglob("*.md"))
            assert len(md_files) == 1
        elif name == "http_webhook":
            assert server is not None
            assert len(server.records) == 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def test_non_idempotent_fake_publisher_fails_contract() -> None:
    """M4-05 Step 1: A deliberately non-idempotent or draft-leaking fake fails contract suite."""

    class _NonIdempotentFakePublisher:
        class Settings(BaseModel):
            model_config = ConfigDict(extra="forbid")

        def __init__(self, settings: Settings | None = None) -> None:
            self.settings = settings or self.Settings()
            self._calls = 0

        def healthcheck(self) -> None:
            pass

        def publish(self, request: PublishRequest) -> PublishedContent:
            self._calls += 1
            return PublishedContent(
                destination=request.destination,
                external_id=f"non-idemp-{self._calls}-{uuid.uuid4().hex}",
                url=f"https://example.com/{request.draft.slug}-{self._calls}",
                status="published",
                published_at=datetime.now(UTC),
                raw={"mode": request.mode, "is_live": False},
            )

    class _LiveInDraftFakePublisher:
        class Settings(BaseModel):
            model_config = ConfigDict(extra="forbid")

        def healthcheck(self) -> None:
            pass

        def publish(self, request: PublishRequest) -> PublishedContent:
            return PublishedContent(
                destination=request.destination,
                external_id=f"live-leak-{request.draft_id}",
                url=f"https://example.com/{request.draft.slug}",
                status="published",
                published_at=datetime.now(UTC),
                raw={"mode": "live", "is_live": True},
            )

    with pytest.raises(AssertionError):
        verify_publisher_contract(_NonIdempotentFakePublisher())

    with pytest.raises(AssertionError):
        verify_publisher_contract(_LiveInDraftFakePublisher())


@pytest.mark.parametrize("name,cls", DISCOVERED.get("analytics_source", {}).items())
def test_analytics_source_contract_suite(name: str, cls: type) -> None:
    adapter = cls()
    assert isinstance(adapter, AnalyticsSourcePort)
    assert hasattr(cls, "Settings")
    adapter.healthcheck()

    metrics = adapter.get_metrics("ext-123", since=date.today())
    assert isinstance(metrics, dict)
    assert "views" in metrics
    assert "clicks" in metrics
