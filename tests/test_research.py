"""Tests for M1-07: feed collection, extraction, URL canonicalisation, and date verification."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import db, research
from engine.contracts import ResearchRecord
from engine.integrations.brave import (
    BraveNewsSearch,
    build_site_search_query,
    map_brave_locale,
)
from engine.integrations.registry import UnsupportedLocale
from engine.integrations.rss import RSSFeedSource, _extract_entry_datetime
from engine.integrations.trafilatura_extractor import TrafilaturaExtractor
from engine.safe_http import SafeHTTPError, SafeResponse

TRACKING_KEYS = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "_hsenc",
]


@given(
    scheme=st.sampled_from(["http", "https", "HTTP", "HTTPS"]),
    host=st.sampled_from(["OpticaNet.com.br", "eyewearintelligence.com", "ABIOPTICA.COM.BR"]),
    segments=st.lists(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_",
            min_size=1,
            max_size=10,
        ),
        min_size=1,
        max_size=4,
    ),
    trailing_slash=st.booleans(),
    keep_params=st.dictionaries(
        keys=st.sampled_from(["id", "page", "category", "lang"]),
        values=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
        max_size=3,
    ),
    tracking_params=st.dictionaries(
        keys=st.sampled_from(TRACKING_KEYS),
        values=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=10),
        min_size=1,
        max_size=4,
    ),
    fragment=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", max_size=10),
)
def test_canonicalize_url_is_idempotent_and_strips_tracking(  # noqa: PLR0913
    scheme: str,
    host: str,
    segments: list[str],
    trailing_slash: bool,
    keep_params: dict[str, str],
    tracking_params: dict[str, str],
    fragment: str,
) -> None:
    path = "/" + "/".join(segments) + ("/" if trailing_slash else "")
    clean_query = urlencode(sorted(keep_params.items()))
    clean_url = urlunsplit((scheme, host, path, clean_query, ""))

    noisy_pairs = list(keep_params.items()) + list(tracking_params.items())
    noisy_query = urlencode(noisy_pairs)
    noisy_url = urlunsplit((scheme, host, path, noisy_query, fragment))

    canon_clean = research.canonicalize_url(clean_url)
    canon_noisy = research.canonicalize_url(noisy_url)

    # Property 1: Idempotence
    assert research.canonicalize_url(canon_noisy) == canon_noisy
    # Property 2: Tracking parameters and fragments are stripped
    assert canon_noisy == canon_clean
    # Property 3: No tracking keys remain in canonical URL query
    parsed_canon = urlsplit(canon_noisy)
    remaining_keys = {k.lower() for k, _ in parse_qsl(parsed_canon.query)}
    assert remaining_keys.isdisjoint({k.lower() for k in TRACKING_KEYS})


def test_canonicalize_url_edge_cases() -> None:
    assert (
        research.canonicalize_url("HTTPS://Example.COM:443/a//b/../c/?utm_source=x&b=2&a=1#frag")
        == "https://example.com/a/c?a=1&b=2"
    )
    assert research.canonicalize_url("http://example.com:80/") == "http://example.com/"
    assert research.canonicalize_url("http://example.com") == "http://example.com/"
    assert (
        research.canonicalize_url("https://example.com:8443/path/")
        == "https://example.com:8443/path"
    )
    with pytest.raises(ValueError, match="Invalid URL"):
        research.canonicalize_url("   ")
    with pytest.raises(ValueError, match="Invalid URL"):
        research.canonicalize_url("ftp://example.com/file")


def test_resolve_date_status_and_confidence_capping() -> None:
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)

    # 1. Explicit metadata date -> verified
    pub = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    resolved_dt, status = research.resolve_date_status(
        published_at=pub,
        url="https://opticanet.com.br/noticia",
        now=now,
    )
    assert resolved_dt == pub
    assert status == "verified"

    # 2. Naive explicit date is normalized to UTC -> verified
    naive_pub = datetime(2026, 11, 1, 9, 30)
    resolved_naive, status_naive = research.resolve_date_status(
        published_at=naive_pub,
        url="https://opticanet.com.br/noticia",
        now=now,
    )
    assert resolved_naive == pub
    assert status_naive == "verified"

    # 3. Missing explicit date, URL contains YYYY/MM/DD -> inferred
    inferred_dt, status_inf = research.resolve_date_status(
        published_at=None,
        url="https://opticanet.com.br/2026/10/24/lentes-miopia-infantil",
        now=now,
    )
    assert inferred_dt == datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
    assert status_inf == "inferred"

    # Invalid calendar date in URL (Feb 31) falls back to unverified
    assert research.infer_date_from_url("https://opticanet.com.br/2026/02/31/bad") is None

    # 4. Missing explicit date, text contains explicit inferred_date -> inferred
    inferred_dt2, status_inf2 = research.resolve_date_status(
        published_at=None,
        inferred_at=datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
        url="https://opticanet.com.br/sem-data",
        now=now,
    )
    assert inferred_dt2 == datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    assert status_inf2 == "inferred"

    # 5. Future date beyond 24h tolerance -> unverified
    future_dt, status_fut = research.resolve_date_status(
        published_at=now + timedelta(days=5),
        url="https://opticanet.com.br/futuro",
        now=now,
    )
    assert future_dt is None
    assert status_fut == "unverified"

    # 6. No date anywhere -> unverified
    none_dt, status_none = research.resolve_date_status(
        published_at=None,
        url="https://opticanet.com.br/artigo-institucional",
        now=now,
    )
    assert none_dt is None
    assert status_none == "unverified"

    # 7. Confidence capping rules (F07 AC3)
    assert research.cap_confidence("high", "unverified") == "medium"
    assert research.cap_confidence("medium", "unverified") == "medium"
    assert research.cap_confidence("low", "unverified") == "low"
    assert research.cap_confidence("high", "verified") == "high"
    assert research.cap_confidence("high", "inferred") == "high"
    assert research.cap_confidence(0.9, "unverified") == 0.6
    assert research.cap_confidence(0.4, "unverified") == 0.4
    assert research.cap_confidence(0.9, "verified") == 0.9


def test_detect_language_pt_and_en(monkeypatch: pytest.MonkeyPatch) -> None:
    pt_text = (
        "O mercado óptico brasileiro cresceu expressivamente no último trimestre com "
        "a adoção de novas lentes oftálmicas para controle de miopia infantil."
    )
    en_text = (
        "Global eyewear retail sales expanded significantly in North America driven by "
        "prescription lenses and independent optical store diagnostics."
    )
    assert research.detect_language(pt_text) == "pt"
    assert research.detect_language(en_text) == "en"
    assert research.detect_language("   ") is None

    class _NoneDetector:
        def detect_language_of(self, text: str) -> None:
            return None

    monkeypatch.setattr("engine.research._get_language_detector", lambda: _NoneDetector())
    assert research.detect_language("12345") is None


def test_rss_plugin_parsing_and_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Opticanet News</title>
        <item>
          <title>Nova lente para controle de miopia chega ao Brasil</title>
          <link>https://opticanet.com.br/2026/11/02/nova-lente?utm_source=rss</link>
          <description>Lançamento traz tecnologia avançada para óticas independentes.</description>
          <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Artigo antigo de 2025</title>
          <link>https://opticanet.com.br/2025/01/10/antigo</link>
          <description>Notícia antiga fora da janela.</description>
          <pubDate>Fri, 10 Jan 2025 10:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Item sem link ignorado</title>
        </item>
        <item>
          <title>Comunicado sem data explícita</title>
          <link>https://opticanet.com.br/comunicado-geral</link>
          <description>Resumo do comunicado.</description>
        </item>
      </channel>
    </rss>
    """

    def fake_safe_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if "error" in url:
            return SafeResponse(status_code=500, headers={}, content=b"Error")
        return SafeResponse(
            status_code=200,
            headers={"Content-Type": "application/rss+xml"},
            content=xml_feed.encode("utf-8"),
        )

    monkeypatch.setattr("engine.integrations.rss.safe_request", fake_safe_request)
    adapter = RSSFeedSource()
    adapter.healthcheck()

    with pytest.raises(ValueError, match="RSS user_agent must not be empty"):
        RSSFeedSource(RSSFeedSource.Settings(user_agent="   ")).healthcheck()

    with pytest.raises(ValueError, match="Feed request failed with HTTP 500"):
        adapter.fetch_feed("https://opticanet.com.br/error-feed")

    # Fetch with since=None returns all 3 linked items
    all_items = adapter.fetch_feed("https://opticanet.com.br/feed", since=None)
    assert len(all_items) == 3

    # Fetch with naive since filters out 2025 item
    since = datetime(2026, 10, 25, 0, 0)
    items = adapter.fetch_feed("https://opticanet.com.br/feed", since=since)

    assert len(items) == 2
    assert items[0].headline == "Nova lente para controle de miopia chega ao Brasil"
    assert items[0].canonical_url == "https://opticanet.com.br/2026/11/02/nova-lente"
    assert items[0].verification_status == "verified"
    assert items[0].published_at == datetime(2026, 11, 2, 10, 0, tzinfo=UTC)

    assert items[1].headline == "Comunicado sem data explícita"
    assert items[1].verification_status == "unverified"
    assert items[1].published_at is None

    # Test _extract_entry_datetime fallback branches
    assert _extract_entry_datetime(
        {"published_parsed": "invalid", "published": "2026-11-01T10:00:00Z"}
    ) == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert _extract_entry_datetime({"published": "2026-11-01T10:00:00"}) == datetime(
        2026, 11, 1, 10, 0, tzinfo=UTC
    )
    assert _extract_entry_datetime({"published": "not-a-date"}) is None


def test_trafilatura_extractor_robots_paywall_and_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched_urls: list[str] = []

    robots_txt = "User-agent: *\nDisallow: /private/\nAllow: /\n"
    article_html = """<!DOCTYPE html>
    <html lang="pt-BR">
      <head>
        <title>Tendências do Varejo Óptico em 2026</title>
        <meta name="author" content="" />
        <meta name="author" content="Carlos Mendes" />
        <meta property="og:site_name" content="Opticanet" />
      </head>
      <body>
        <article>
          <h1>Tendências do Varejo Óptico em 2026</h1>
          <time datetime=""></time>
          <time datetime="2026-11-01T14:30:00"></time>
          <p>O setor óptico brasileiro registrou alta de 12% segundo dados recentes.</p>
          <p>Confira o relatório da
             <a href="https://abioptica.com.br/relatorio-2026?utm_source=opticanet">Abióptica</a>,
             duplicado <a href="https://abioptica.com.br/relatorio-2026">Abióptica 2</a>,
             estatísticas do <a href="https://ibge.gov.br/pmc">IBGE</a>,
             <a href="mailto:contato@opticanet.com.br">email</a>,
             <a href="">vazio</a>,
             <a href="ftp://files.example.com/doc">ftp</a>,
             e <a href="/interna">link interno</a>.</p>
        </article>
      </body>
    </html>
    """
    paywall_html = """<!DOCTYPE html>
    <html>
      <head>
        <title>Exclusive Report</title>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "NewsArticle", "isAccessibleForFree": false}
        </script>
      </head>
      <body><div class="paywall"><p>Subscribe now to read the full article.</p></div></body>
    </html>
    """

    def fake_safe_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        fetched_urls.append(url)
        if "robots-err.com/robots.txt" in url:
            raise SafeHTTPError("Robots network error")
        if "robots-404.com/robots.txt" in url:
            return SafeResponse(status_code=404, headers={}, content=b"Not Found")
        if url.endswith("/robots.txt"):
            return SafeResponse(status_code=200, headers={}, content=robots_txt.encode("utf-8"))
        if "safe-err" in url:
            raise SafeHTTPError("Connection failed")
        if "paywalled" in url:
            return SafeResponse(status_code=200, headers={}, content=paywall_html.encode("utf-8"))
        if "http-error" in url:
            return SafeResponse(status_code=503, headers={}, content=b"Service Unavailable")
        return SafeResponse(status_code=200, headers={}, content=article_html.encode("utf-8"))

    monkeypatch.setattr(
        "engine.integrations.trafilatura_extractor.safe_request",
        fake_safe_request,
    )

    extractor = TrafilaturaExtractor()
    assert extractor.settings.respect_robots_txt is True
    extractor.healthcheck()

    with pytest.raises(ValueError, match="Trafilatura user_agent must not be empty"):
        TrafilaturaExtractor(TrafilaturaExtractor.Settings(user_agent="  ")).healthcheck()

    # 1. Disallowed by robots.txt -> NOT fetched (F07 AC6)
    blocked_url = "https://opticanet.com.br/private/secret-report"
    blocked_rec = extractor.extract(blocked_url)
    assert blocked_url not in fetched_urls
    assert blocked_rec.raw is not None
    assert blocked_rec.raw["fetch_status"] == "blocked"

    # 2. Allowed URL -> extracts text, date, author, outbound links
    ok_url = "https://opticanet.com.br/2026/11/01/tendencias-varejo"
    ok_rec = extractor.extract(ok_url)
    assert ok_url in fetched_urls
    assert ok_rec.raw is not None
    assert ok_rec.raw["fetch_status"] == "ok"
    assert ok_rec.raw["author"] == "Carlos Mendes"
    assert "setor óptico brasileiro" in ok_rec.raw["extracted_text"]
    assert ok_rec.raw["outbound_links"] == [
        "https://abioptica.com.br/relatorio-2026",
        "https://ibge.gov.br/pmc",
    ]
    assert ok_rec.published_at == datetime(2026, 11, 1, 14, 30, tzinfo=UTC)
    assert ok_rec.verification_status == "verified"

    # 3. Paywalled URL -> fetch_status = 'paywalled' (F07 AC4)
    pw_rec = extractor.extract("https://opticanet.com.br/paywalled-story")
    assert pw_rec.raw is not None
    assert pw_rec.raw["fetch_status"] == "paywalled"


def test_trafilatura_extractor_error_and_fallback_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if "robots-err.com/robots.txt" in url:
            raise SafeHTTPError("Robots network error")
        if "robots-oserr.com/robots.txt" in url:
            raise OSError("TLS handshake error")
        if "robots-404.com/robots.txt" in url:
            return SafeResponse(status_code=404, headers={}, content=b"Not Found")
        if "safe-err" in url:
            raise SafeHTTPError("Connection failed")
        if "os-err" in url:
            raise OSError("SSL certificate verify failed")
        if "http-error" in url:
            return SafeResponse(status_code=503, headers={}, content=b"Service Unavailable")
        return SafeResponse(
            status_code=200,
            headers={},
            content=b"<html><body><p>Valid body</p></body></html>",
        )

    monkeypatch.setattr(
        "engine.integrations.trafilatura_extractor.safe_request",
        fake_safe_request,
    )
    extractor = TrafilaturaExtractor()

    # HTTP 503, SafeHTTPError, and OSError/TLS -> fetch_status = 'http_error' (F07 AC4)
    err_rec = extractor.extract("https://opticanet.com.br/http-error")
    assert err_rec.raw is not None
    assert err_rec.raw["fetch_status"] == "http_error"
    assert err_rec.raw["http_status"] == 503

    net_err_rec = extractor.extract("https://robots-err.com/safe-err")
    assert net_err_rec.raw is not None
    assert net_err_rec.raw["fetch_status"] == "http_error"

    os_err_rec = extractor.extract("https://robots-oserr.com/os-err")
    assert os_err_rec.raw is not None
    assert os_err_rec.raw["fetch_status"] == "http_error"

    # Non-200 robots.txt allows fetch; invalid meta date falls back to URL date
    r404_rec = extractor.extract("https://robots-404.com/2026/11/01/story")
    assert r404_rec.raw is not None
    assert r404_rec.raw["fetch_status"] == "ok"

    bad_date_html = (
        '<html><head><meta name="date" content="not-iso"/></head>'
        "<body><p>Body text</p></body></html>"
    )
    bad_date_rec = extractor.extract(
        "https://opticanet.com.br/2026/11/02/story",
        html=bad_date_html,
    )
    assert bad_date_rec.verification_status == "inferred"

    empty_rec = extractor.extract(
        "https://opticanet.com.br/empty",
        html="<html><body></body></html>",
    )
    assert empty_rec.raw is not None
    assert empty_rec.raw["fetch_status"] == "extraction_failed"


def test_llm_extraction_and_collection_pipeline_isolation(
    db_conn: psycopg.Connection[Any],
) -> None:
    actor = db.create_or_get_user(
        db_conn,
        email=f"res-{uuid.uuid4().hex[:8]}@example.com",
        name="Pipeline Researcher",
    )
    ws_id = db.create_workspace(
        db_conn,
        slug=f"res-ws-{uuid.uuid4().hex[:8]}",
        name="Pipeline Workspace",
        industry_pack="general",
    )
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name=f"Pipeline Campaign {uuid.uuid4().hex[:6]}",
        locale="pt-BR",
        market="BR",
        articles_per_week=4,
        schedule_cron="0 8 * * 1",
        monthly_cap_usd=Decimal("250.00"),
        run_cap_usd=Decimal("25.00"),
        config={"goal": "authority"},
        config_schema_version=1,
        created_by=actor["user_id"],
    )
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="scheduled",
        window_start=now - timedelta(days=14),
        window_end=now,
        run_cap_usd=Decimal("25.00"),
        code_version="sha-m1-07",
        pack_version="pack-m1-07",
        created_by=actor["user_id"],
    )
    src = db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack="general",
        name="Opticanet",
        domain=f"opticanet-{uuid.uuid4().hex[:6]}.com.br",
        url="https://opticanet.com.br",
        feed_url="https://opticanet.com.br/feed",
        fetch_mode="feed",
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["varejo"],
    )

    feed_items = [
        ResearchRecord(
            headline="Item 1: Crescimento das óticas no Brasil",
            source="Opticanet",
            url="https://opticanet.com.br/item-1?utm_source=rss",
            canonical_url="https://opticanet.com.br/item-1",
            published_at=now - timedelta(days=1),
            summary="Resumo do item 1",
            verification_status="verified",
        ),
        ResearchRecord(
            headline="Item 2: Duplicata canônica do Item 1",
            source="Opticanet",
            url="https://opticanet.com.br/item-1?fbclid=123",
            canonical_url="https://opticanet.com.br/item-1",
            published_at=now - timedelta(days=1),
            summary="Mesmo artigo com outro parâmetro de tracking",
            verification_status="verified",
        ),
        ResearchRecord(
            headline="Item 3: Artigo sem data e com falha no LLM",
            source="Opticanet",
            url="https://opticanet.com.br/item-llm-fail",
            canonical_url="https://opticanet.com.br/item-llm-fail",
            published_at=None,
            summary="Artigo que causará erro no LLM",
            verification_status="unverified",
        ),
        ResearchRecord(
            headline="Item 4: Artigo com paywall",
            source="Opticanet",
            url="https://opticanet.com.br/item-paywall",
            canonical_url="https://opticanet.com.br/item-paywall",
            published_at=now - timedelta(days=2),
            summary="Artigo fechado para assinantes",
            verification_status="verified",
        ),
    ]

    class StubFeedSource:
        def healthcheck(self) -> None:
            return None

        def fetch_feed(self, url: str, since: datetime | None = None) -> list[ResearchRecord]:
            return feed_items

    class StubExtractor:
        def healthcheck(self) -> None:
            return None

        def extract(self, url: str, html: str | None = None) -> ResearchRecord:
            if "paywall" in url:
                return ResearchRecord(
                    headline="Paywalled",
                    source="Opticanet",
                    url=url,
                    canonical_url=research.canonicalize_url(url),
                    summary="Paywalled",
                    raw={
                        "fetch_status": "paywalled",
                        "http_status": 200,
                        "extracted_text": None,
                        "author": None,
                        "site_name": "Opticanet",
                        "outbound_links": [],
                        "date_status": "unverified",
                    },
                )
            has_date = "llm-fail" not in url
            return ResearchRecord(
                headline="Extracted Title",
                source="Opticanet",
                url=url,
                canonical_url=research.canonicalize_url(url),
                published_at=(now - timedelta(days=1)) if has_date else None,
                summary="O mercado óptico brasileiro cresceu 15% em vendas de lentes.",
                verification_status="inferred" if has_date else "unverified",
                raw={
                    "fetch_status": "ok",
                    "http_status": 200,
                    "extracted_text": (
                        "O mercado óptico brasileiro cresceu 15% em vendas de lentes "
                        "oftálmicas multifocais no último trimestre."
                    ),
                    "author": "Ana Silva",
                    "site_name": "Opticanet",
                    "outbound_links": ["https://ibge.gov.br/pmc"],
                    "date_status": "inferred" if has_date else "unverified",
                },
            )

    class SelectiveFailingLLM:
        def healthcheck(self) -> None:
            return None

        def complete(
            self,
            prompt: str,
            schema: type[Any] | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if "item-llm-fail" in prompt:
                msg = "Invalid JSON output from LLM"
                raise ValueError(msg)
            return {
                "headline": "Mercado óptico cresce 15% no Brasil",
                "summary": "Vendas de lentes multifocais impulsionam resultado trimestral.",
                "facts": ["Crescimento de 15% nas vendas de lentes multifocais."],
                "entities": ["Opticanet", "IBGE"],
                "event_date": "2026-11-04",
                "source_type": "news",
            }

    records = research.collect_feed_sources(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        since=now - timedelta(days=7),
        feed_adapter=StubFeedSource(),
        extractor_adapter=StubExtractor(),
        llm_adapter=SelectiveFailingLLM(),
        sources=[src, {"id": None, "name": "NoFeed", "feed_url": None}],
    )

    # Also test default sources=None loading from db.list_sources
    records_default = research.collect_feed_sources(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        since=now - timedelta(days=7),
        feed_adapter=StubFeedSource(),
        extractor_adapter=StubExtractor(),
        llm_adapter=SelectiveFailingLLM(),
    )
    assert len(records_default) >= 3

    # 3 distinct canonical URLs were processed (item-1 deduplicated by canonical_url!)
    assert len(records) == 3

    doc_item1 = db.get_document_by_canonical_url(
        db_conn,
        workspace_id=ws_id,
        canonical_url="https://opticanet.com.br/item-1",
    )
    assert doc_item1 is not None
    assert doc_item1["fetch_status"] == "ok"
    assert doc_item1["lang"] == "pt"
    assert doc_item1["content_hash"] is not None
    assert doc_item1["date_status"] == "inferred"
    assert doc_item1["meta"]["author"] == "Ana Silva"

    doc_pw = db.get_document_by_canonical_url(
        db_conn,
        workspace_id=ws_id,
        canonical_url="https://opticanet.com.br/item-paywall",
    )
    assert doc_pw is not None
    assert doc_pw["fetch_status"] == "paywalled"

    stored_rrs = db.list_research_records(db_conn, workspace_id=ws_id, run_id=run_id)
    by_doc_id = {r["document_id"]: r for r in stored_rrs}

    # Item 1 succeeded with valid ResearchRecord payload and schema_version = 1 (AC5)
    rr_ok = by_doc_id[doc_item1["id"]]
    assert rr_ok["status"] == "extracted"
    assert rr_ok["schema_version"] == 1
    validated = ResearchRecord.model_validate(rr_ok["payload"])
    assert validated.headline == "Mercado óptico cresce 15% no Brasil"
    assert validated.facts == ["Crescimento de 15% nas vendas de lentes multifocais."]
    assert validated.event_date == date(2026, 11, 4)

    # Item 3 (LLM failure) marked status='failed' and run continued (Step 4)
    doc_fail = db.get_document_by_canonical_url(
        db_conn,
        workspace_id=ws_id,
        canonical_url="https://opticanet.com.br/item-llm-fail",
    )
    assert doc_fail is not None
    assert doc_fail["date_status"] == "unverified"
    rr_fail = by_doc_id[doc_fail["id"]]
    assert rr_fail["status"] == "failed"

    # Item 4 (Paywalled) marked status='failed' in research_records
    rr_pw = by_doc_id[doc_pw["id"]]
    assert rr_pw["status"] == "failed"


def test_brave_locale_mapping_and_query_builder() -> None:
    assert map_brave_locale("pt-BR", "br") == ("BR", "pt-br", "pt-BR")
    assert map_brave_locale("en-US", "us") == ("US", "en", "en-US")

    with pytest.raises(UnsupportedLocale, match="es-ES"):
        map_brave_locale("es-ES", "es")
    with pytest.raises(UnsupportedLocale, match="pt-BR/PT"):
        map_brave_locale("pt-BR", "PT")

    assert build_site_search_query("sebrae.com.br") == "site:sebrae.com.br"
    assert build_site_search_query("sebrae.com.br", "varejo") == "site:sebrae.com.br varejo"
    assert (
        build_site_search_query("sebrae.com.br", "site:sebrae.com.br gestao")
        == "site:sebrae.com.br gestao"
    )
    assert build_site_search_query(None, "myopia control") == "myopia control"

    with pytest.raises(ValueError, match="empty"):
        build_site_search_query(None, "   ")


def test_brave_search_error_paths_and_date_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_key = BraveNewsSearch(settings=BraveNewsSearch.Settings(api_key=None))
    with pytest.raises(ValueError, match="API key"):
        empty_key.healthcheck()

    adapter = BraveNewsSearch()
    adapter.healthcheck()
    monkeypatch.setattr(
        "engine.integrations.brave.safe_request",
        lambda *_args, **_kwargs: SafeResponse(
            status_code=429,
            headers={},
            content=b"rate limited",
        ),
    )
    with pytest.raises(ValueError, match="HTTP 429"):
        adapter.search("optometry", locale="en-US", market="us")

    payload = (
        b'{"results": ['
        b'"not-a-dict",'
        b'{"title": "Missing URL", "url": ""},'
        b'{"title": "Old Item", "url": "https://sebrae.com.br/2020/01/01/old", '
        b'"page_age": "2020-01-01T10:00:00", "description": "Old"},'
        b'{"title": "No Age Item", "url": "https://sebrae.com.br/sem-idade"},'
        b'{"title": "Bad Date Item", "url": "https://sebrae.com.br/artigo-novo", '
        b'"page_age": "not-a-date", "description": "Sem data", '
        b'"meta_url": {"hostname": "sebrae.com.br"}}'
        b"]}"
    )
    monkeypatch.setattr(
        "engine.integrations.brave.safe_request",
        lambda *_args, **_kwargs: SafeResponse(
            status_code=200,
            headers={},
            content=payload,
        ),
    )
    recs = adapter.search(
        "varejo",
        locale="pt-BR",
        market="br",
        since=date(2026, 10, 1),
        domain="sebrae.com.br",
    )
    assert len(recs) == 2
    assert recs[0].headline == "No Age Item"
    assert recs[1].headline == "Bad Date Item"
    assert recs[1].verification_status == "unverified"
    assert recs[1].source == "sebrae.com.br"


def test_brave_usage_ledger_unit_cost(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = db.create_or_get_user(
        db_conn,
        email=f"brave-{uuid.uuid4().hex[:6]}@example.com",
        name="Brave Tester",
    )
    user_id = actor["user_id"]
    ws_id = db.create_workspace(
        db_conn,
        name="Brave WS",
        slug=f"brave-ws-{uuid.uuid4().hex[:6]}",
        industry_pack="eyewear",
    )
    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Brave Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=3,
        schedule_cron="0 7 * * 1",
        monthly_cap_usd=Decimal("60.00"),
        run_cap_usd=Decimal("6.00"),
        config={"segment": "optical"},
        config_schema_version=1,
        created_by=user_id,
    )
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("6.00"),
        code_version="sha-m1-08",
        pack_version="pack-m1-08",
        created_by=user_id,
    )

    body = (
        b'{"results": [{'
        b'"title": "Varejo optico em alta", '
        b'"url": "https://sebrae.com.br/2026/11/02/varejo-optico?utm_source=brave", '
        b'"description": "Resumo Sebrae", '
        b'"page_age": "2026-11-02T14:00:00Z"'
        b"}]}"
    )
    monkeypatch.setattr(
        "engine.integrations.brave.safe_request",
        lambda *_args, **_kwargs: SafeResponse(
            status_code=200,
            headers={},
            content=body,
        ),
    )

    adapter = BraveNewsSearch(
        settings=BraveNewsSearch.Settings(unit_cost_usd=Decimal("0.005000")),
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
    )
    records = adapter.search(
        "site:sebrae.com.br varejo",
        locale="pt-BR",
        market="br",
        since=date(2026, 10, 29),
    )
    assert len(records) == 1
    assert records[0].canonical_url == "https://sebrae.com.br/2026/11/02/varejo-optico"

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT stage, kind, vendor, model, units, cost_usd, run_id
            FROM usage_ledger
            WHERE workspace_id = %s AND vendor = 'brave'
            """,
            (ws_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "research"
    assert row[1] == "api"
    assert row[2] == "brave"
    assert row[3] == "news_search"
    assert row[4] == Decimal("1.000")
    assert row[5] == Decimal("0.005000")
    assert row[6] == run_id


def test_collect_feed_and_site_search_f07_ac1(
    db_conn: psycopg.Connection[Any],
) -> None:
    actor = db.create_or_get_user(
        db_conn,
        email=f"ac1-{uuid.uuid4().hex[:6]}@example.com",
        name="AC1 Tester",
    )
    user_id = actor["user_id"]
    ws_id = db.create_workspace(
        db_conn,
        name="AC1 WS",
        slug=f"ac1-ws-{uuid.uuid4().hex[:6]}",
        industry_pack="eyewear",
    )
    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="AC1 Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 10 * * 2",
        monthly_cap_usd=Decimal("90.00"),
        run_cap_usd=Decimal("9.00"),
        config={"focus": "retail"},
        config_schema_version=1,
        created_by=user_id,
    )
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    win_start = now - timedelta(days=7)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=win_start,
        window_end=now,
        run_cap_usd=Decimal("9.00"),
        code_version="sha-ac1",
        pack_version="pack-ac1",
        created_by=user_id,
    )

    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack="eyewear",
        name="Opticanet Feed",
        domain="opticanet.com.br",
        url="https://opticanet.com.br",
        feed_url="https://opticanet.com.br/feed",
        fetch_mode="feed",
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["varejo"],
        enabled=True,
        added_by=user_id,
    )
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack="eyewear",
        name="Sebrae Site Search",
        domain="sebrae.com.br",
        url="https://sebrae.com.br",
        feed_url=None,
        fetch_mode="site_search",
        tier=1,
        role="management",
        locales=["pt-BR"],
        topics=["gestao", "varejo"],
        enabled=True,
        added_by=user_id,
    )
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack="eyewear",
        name="Disabled Site Search",
        domain="disabled.example.com",
        url="https://disabled.example.com",
        feed_url=None,
        fetch_mode="site_search",
        tier=2,
        role="news",
        locales=["pt-BR"],
        topics=[],
        enabled=False,
        added_by=user_id,
    )

    feed_calls: list[tuple[str, datetime | None]] = []
    search_calls: list[tuple[str, str, str, date | None]] = []

    class SpyFeed:
        def healthcheck(self) -> None:
            return None

        def fetch_feed(self, url: str, since: datetime | None = None) -> list[ResearchRecord]:
            feed_calls.append((url, since))
            return [
                ResearchRecord(
                    headline="Opticanet Feed Story",
                    source="Opticanet",
                    url="https://opticanet.com.br/2026/11/03/story-1",
                    canonical_url="https://opticanet.com.br/2026/11/03/story-1",
                    published_at=now - timedelta(days=2),
                    summary="Resumo feed",
                    verification_status="verified",
                )
            ]

    class SpySearch:
        def healthcheck(self) -> None:
            return None

        def search(
            self,
            query: str,
            locale: str,
            market: str,
            since: date | None = None,
        ) -> list[ResearchRecord]:
            search_calls.append((query, locale, market, since))
            return [
                ResearchRecord(
                    headline="Sebrae Gestao Story",
                    source="Sebrae",
                    url="https://sebrae.com.br/2026/11/04/gestao-optica?utm_source=search",
                    canonical_url="https://sebrae.com.br/2026/11/04/gestao-optica",
                    published_at=now - timedelta(days=1),
                    summary="Resumo sebrae",
                    verification_status="verified",
                ),
                # Duplicate of the feed URL to verify cross-mode canonical deduplication
                ResearchRecord(
                    headline="Opticanet Feed Story Duplicate",
                    source="Opticanet",
                    url="https://opticanet.com.br/2026/11/03/story-1?fbclid=99",
                    canonical_url="https://opticanet.com.br/2026/11/03/story-1",
                    published_at=now - timedelta(days=2),
                    summary="Duplicado",
                    verification_status="verified",
                ),
            ]

    class SimpleExtractor:
        def healthcheck(self) -> None:
            return None

        def extract(self, url: str, html: str | None = None) -> ResearchRecord:
            return ResearchRecord(
                headline=f"Title for {url}",
                source="Source",
                url=url,
                canonical_url=research.canonicalize_url(url),
                published_at=now - timedelta(days=1),
                summary="Artigo completo em português sobre gestão de óticas no Brasil.",
                verification_status="verified",
                raw={
                    "fetch_status": "ok",
                    "http_status": 200,
                    "extracted_text": (
                        "Artigo completo em português sobre gestão de óticas e varejo no Brasil."
                    ),
                    "author": "Editor",
                    "site_name": "Portal",
                    "outbound_links": [],
                    "date_status": "verified",
                },
            )

    class SimpleLLM:
        def healthcheck(self) -> None:
            return None

        def complete(
            self,
            prompt: str,
            schema: type[Any] | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "headline": "Manchete Extraída",
                "summary": "Resumo estruturado.",
                "facts": ["Fato 1"],
                "entities": ["Sebrae"],
                "event_date": "2026-11-04",
                "source_type": "news",
            }

    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None

    records = research.collect(
        db_conn,
        run_row,
        feed_adapter=SpyFeed(),
        news_search_adapter=SpySearch(),
        extractor_adapter=SimpleExtractor(),
        llm_adapter=SimpleLLM(),
        include_shared=False,
    )

    assert feed_calls == [("https://opticanet.com.br/feed", win_start)]
    assert search_calls == [("site:sebrae.com.br gestao varejo", "pt-BR", "BR", win_start.date())]
    assert len(records) == 2
    assert all(r["status"] == "extracted" for r in records)

    # Also verify collect_site_search_sources with sources=None and empty domain handling
    extra_records = research.collect_site_search_sources(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        locale="pt-BR",
        market="BR",
        since=win_start.date(),
        news_search_adapter=SpySearch(),
        extractor_adapter=SimpleExtractor(),
        llm_adapter=SimpleLLM(),
        sources=[{"id": None, "name": "NoDomain", "domain": ""}],
    )
    assert extra_records == []

    default_ss_records = research.collect_site_search_sources(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        locale="pt-BR",
        market="BR",
        since=win_start.date(),
        news_search_adapter=SpySearch(),
        extractor_adapter=SimpleExtractor(),
        llm_adapter=SimpleLLM(),
    )
    assert len(default_ss_records) == 2


LABELLED_DEDUP_TITLE_PAIRS: list[tuple[str, str, bool]] = [
    # 15 near-duplicate title pairs (expected True at threshold >= 0.6)
    (
        "Vendas do varejo óptico brasileiro crescem 15% no terceiro trimestre de 2026",
        "Vendas no varejo óptico brasileiro crescem 15% no 3º trimestre de 2026",
        True,
    ),
    (
        "Abióptica divulga balanço anual do setor óptico no Brasil em 2026",
        "Abióptica divulga o balanço anual do setor óptico no Brasil em 2026",
        True,
    ),
    (
        "Novas lentes para controle de miopia infantil chegam às óticas brasileiras",
        "Novas lentes de controle de miopia infantil chegam às óticas brasileiras",
        True,
    ),
    (
        "Sebrae lança guia prático de gestão financeira para pequenas óticas",
        "Sebrae lança guia prático de gestão financeira para as pequenas óticas",
        True,
    ),
    (
        "Anvisa atualiza regras sanitárias para comercialização de lentes de contato",
        "Anvisa atualiza as regras sanitárias para comercialização de lentes de contato",
        True,
    ),
    (
        "Mercado de óculos solares polarizados registra alta demanda no verão brasileiro",
        "Mercado de óculos solares polarizados registra alta demanda no verão do Brasil",
        True,
    ),
    (
        "Tecnologia de medição pupilar digital reduz erros de adaptação em multifocais",
        "Tecnologia de medição pupilar digital reduz erro de adaptação em multifocais",
        True,
    ),
    (
        "Inadimplência no crediário próprio de óticas cai pelo quarto mês seguido",
        "Inadimplência no crediário próprio das óticas cai pelo quarto mês seguido",
        True,
    ),
    (
        "FDA clears next-generation spectacle lenses for pediatric myopia management",
        "FDA clears next-generation spectacle lenses for pediatric myopia control",
        True,
    ),
    (
        "Independent optical retailers report strong Q3 progressive lens revenue growth",
        "Independent optical retailers report strong Q3 progressive lens sales growth",
        True,
    ),
    (
        "The Vision Council releases 2026 consumer eyewear purchasing trends report",
        "The Vision Council publishes 2026 consumer eyewear purchasing trends report",
        True,
    ),
    (
        "European eyewear manufacturers expand sustainable bio-acetate frame collections",
        "European eyewear manufacturers expand sustainable bio-acetate frame lines",
        True,
    ),
    (
        "Blue light filtering coatings remain top add-on for prescription glasses in 2026",
        "Blue light filtering coatings remain top add-on for prescription eyewear in 2026",
        True,
    ),
    (
        "Tele-optometry platforms increase patient exam capacity in rural optical clinics",
        "Tele-optometry platforms increase patient exam capacity at rural optical clinics",
        True,
    ),
    (
        "Luxury eyewear licensing deals drive margin expansion for global frame groups",
        "Luxury eyewear licensing deals drive margin expansion at global frame groups",
        True,
    ),
    # 15 distinct story pairs (expected False at threshold >= 0.6)
    (
        "Vendas do varejo óptico brasileiro crescem 15% no terceiro trimestre de 2026",
        "Anvisa proíbe lote falsificado de solução multipropósito para lentes gelatinosas",
        False,
    ),
    (
        "Abióptica anuncia datas da Expo Óptica Brasil 2027 em São Paulo",
        "Como calcular capital de giro e fluxo de caixa em óticas independentes",
        False,
    ),
    (
        "Novas lentes para controle de miopia infantil chegam às óticas brasileiras",
        "Tendências de armações em titânio minimalista para o público executivo",
        False,
    ),
    (
        "Sebrae oferece consultoria gratuita de vitrinismo para comércio varejista",
        "Pesquisa do IBGE aponta desaceleração no setor de vestuário e calçados",
        False,
    ),
    (
        "Como treinar consultores ópticos para venda consultiva de lentes antirreflexo",
        "Importação de blocos oftálmicos da Ásia sofre atraso nos portos brasileiros",
        False,
    ),
    (
        "Receita Federal altera alíquota de importação para maquinário de surfaçagem",
        "Cuidados essenciais na higienização diária de lentes de contato rígidas",
        False,
    ),
    (
        "Franquias de óticas populares aceleram expansão no interior do Nordeste",
        "Sociedade Brasileira de Oftalmologia alerta sobre riscos do glaucoma silencioso",
        False,
    ),
    (
        "Inteligência artificial no diagnóstico precoce de retinopatia diabética",
        "Planejamento tributário para óticas optantes pelo Simples Nacional em 2027",
        False,
    ),
    (
        "FDA clears next-generation spectacle lenses for pediatric myopia management",
        "Supply chain bottlenecks impact titanium hinge deliveries across Asian factories",
        False,
    ),
    (
        "Independent optical retailers report strong Q3 progressive lens revenue growth",
        "New dry eye syndrome drops receive reimbursement coverage in North America",
        False,
    ),
    (
        "The Vision Council releases 2026 consumer eyewear purchasing trends report",
        "How optical practice owners can reduce patient no-show rates with SMS reminders",
        False,
    ),
    (
        "European eyewear manufacturers expand sustainable bio-acetate frame collections",
        "Private equity acquisitions consolidate regional optometry clinic networks",
        False,
    ),
    (
        "Smart glasses with audio assistants gain traction among commuter demographics",
        "Occupational safety eyewear standards updated for high-impact construction sites",
        False,
    ),
    (
        "Contact lens subscription models boost recurring revenue for independent practices",
        "Automated lens edgers cut in-house optical lab turnaround times by half",
        False,
    ),
    (
        "Pediatric vision screening mandates expand across public school districts",
        "Holiday merchandising strategies to maximize sunglass impulse purchases",
        False,
    ),
]


def test_dedup_labelled_30_title_pairs_fixture(
    db_conn: psycopg.Connection[Any],
) -> None:
    assert len(LABELLED_DEDUP_TITLE_PAIRS) == 30
    assert sum(1 for _, _, expected in LABELLED_DEDUP_TITLE_PAIRS if expected) == 15
    assert sum(1 for _, _, expected in LABELLED_DEDUP_TITLE_PAIRS if not expected) == 15

    for idx, (title_a, title_b, expected_is_dup) in enumerate(LABELLED_DEDUP_TITLE_PAIRS, 1):
        sim = research.trigram_similarity(db_conn, title_a, title_b)
        actual_is_dup = research.is_near_duplicate_title(
            db_conn,
            title_a,
            title_b,
            threshold=research.DEFAULT_TRIGRAM_THRESHOLD,
        )
        assert actual_is_dup is expected_is_dup, (
            f"Pair #{idx} misclassified (sim={sim:.3f}, expected={expected_is_dup}): "
            f"'{title_a}' vs '{title_b}'"
        )


def test_dedup_exact_canonical_and_trigram_window_f08_ac1_ac2(
    db_conn: psycopg.Connection[Any],
) -> None:
    actor = db.create_or_get_user(
        db_conn,
        email=f"dedup-{uuid.uuid4().hex[:6]}@example.com",
        name="Dedup Tester",
    )
    user_id = actor["user_id"]
    ws_id = db.create_workspace(
        db_conn,
        name="Dedup WS",
        slug=f"dedup-ws-{uuid.uuid4().hex[:6]}",
        industry_pack="eyewear",
    )
    other_ws_id = db.create_workspace(
        db_conn,
        name="Other Dedup WS",
        slug=f"other-dedup-ws-{uuid.uuid4().hex[:6]}",
        industry_pack="eyewear",
    )
    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Dedup Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 6 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        config={"goal": "dedup"},
        config_schema_version=1,
        created_by=user_id,
    )
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    old_run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="scheduled",
        window_start=now - timedelta(days=30),
        window_end=now - timedelta(days=20),
        run_cap_usd=Decimal("10.00"),
        code_version="sha-old",
        pack_version="pack-old",
        created_by=user_id,
    )
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=old_run_id,
        expected_status="queued",
        new_status="completed",
    )
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("10.00"),
        code_version="sha-m1-09",
        pack_version="pack-m1-09",
        created_by=user_id,
    )

    # 0. Old document (> 14 days ago, 21 days old) with similar title to verify 14-day window
    doc_old = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://opticanet.com.br/2026/10/15/guia-sebrae-pequenas-oticas",
        canonical_url="https://opticanet.com.br/2026/10/15/guia-sebrae-pequenas-oticas",
        title="Sebrae lança guia prático de gestão financeira para pequenas óticas",
        published_at=now - timedelta(days=21),
        date_status="verified",
        fetch_status="ok",
    )
    db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=old_run_id,
        document_id=doc_old["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": doc_old["title"]},
    )

    # 1. Exact canonical URL deduplication (F08 AC1): two URLs with same canonical URL
    doc_exact_1 = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://opticanet.com.br/2026/11/01/varejo-cresce?utm_source=rss",
        canonical_url="https://opticanet.com.br/2026/11/01/varejo-cresce",
        title="Vendas do varejo óptico brasileiro crescem 15% no terceiro trimestre de 2026",
        published_at=now - timedelta(days=4),
        date_status="verified",
        fetch_status="ok",
    )
    doc_exact_2 = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://opticanet.com.br/2026/11/01/varejo-cresce?fbclid=123",
        canonical_url="https://opticanet.com.br/2026/11/01/varejo-cresce",
        title="Vendas do varejo óptico brasileiro crescem 15% no terceiro trimestre de 2026",
        published_at=now - timedelta(days=4),
        date_status="verified",
        fetch_status="ok",
    )
    assert doc_exact_1["id"] == doc_exact_2["id"]

    rr_earliest = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_exact_1["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": doc_exact_1["title"]},
    )

    # 2. Near-duplicate document within 14 days (2 days later, similarity >= 0.6) -> F08 AC2
    doc_near_dup = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://abioptica.com.br/2026/11/03/varejo-optico-3tri",
        canonical_url="https://abioptica.com.br/2026/11/03/varejo-optico-3tri",
        title="Vendas no varejo óptico brasileiro crescem 15% no 3º trimestre de 2026",
        published_at=now - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
    )
    rr_near_dup = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_near_dup["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": doc_near_dup["title"]},
    )

    # 3. Record matching only the 21-day-old story (> 14 days) -> NOT marked duplicate
    doc_recent_sebrae = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://sebrae.com.br/2026/11/04/guia-pequenas-oticas",
        canonical_url="https://sebrae.com.br/2026/11/04/guia-pequenas-oticas",
        title="Sebrae lança guia prático de gestão financeira para as pequenas óticas",
        published_at=now - timedelta(days=1),
        date_status="verified",
        fetch_status="ok",
    )
    rr_recent_sebrae = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_recent_sebrae["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": doc_recent_sebrae["title"]},
    )

    # Cross-tenant isolation check on find_earlier_near_duplicate_record and mark_duplicate
    assert (
        db.find_earlier_near_duplicate_record(
            db_conn,
            workspace_id=other_ws_id,
            record_id=rr_near_dup["id"],
        )
        is None
    )
    assert (
        db.mark_research_record_duplicate(
            db_conn,
            workspace_id=other_ws_id,
            record_id=rr_near_dup["id"],
            duplicate_of=rr_earliest["id"],
        )
        is None
    )

    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    updated_records = research.dedup(db_conn, run_row)
    by_id = {r["id"]: r for r in updated_records}

    assert by_id[rr_earliest["id"]]["status"] == "extracted"
    assert by_id[rr_earliest["id"]]["duplicate_of"] is None

    assert by_id[rr_near_dup["id"]]["status"] == "duplicate"
    assert by_id[rr_near_dup["id"]]["duplicate_of"] == rr_earliest["id"]

    assert by_id[rr_recent_sebrae["id"]]["status"] == "extracted"
    assert by_id[rr_recent_sebrae["id"]]["duplicate_of"] is None

    # Calling dedup again tests the idempotency branch where status == 'duplicate' is skipped
    second_pass = research.dedup(db_conn, run_row)
    assert len(second_pass) == len(updated_records)
    by_id_second = {r["id"]: r for r in second_pass}
    assert by_id_second[rr_near_dup["id"]]["status"] == "duplicate"
