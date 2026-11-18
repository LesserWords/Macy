"""Tests for bounded async site crawler, multipage detectors, and DB persistence (M6-02 D2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from engine.contracts import CrawlConfig, SiteAuditReport
from engine.safe_http import BlockedIPError, SafeResponse


def test_crawl_config_plan_limits_and_url_normalization() -> None:
    """Verify free/paid/hard_cap page bounds and UTM/fragment/www normalization."""
    from engine.crawl.crawler import normalize_crawl_url

    assert CrawlConfig(plan="free").effective_max_pages == 20
    assert CrawlConfig(plan="free", max_pages=50).effective_max_pages == 20
    assert CrawlConfig(plan="paid").effective_max_pages == 50
    assert CrawlConfig(plan="paid", paid_max_pages=150, max_pages=120).effective_max_pages == 120
    assert CrawlConfig(plan="paid", paid_max_pages=200, max_pages=200).effective_max_pages == 200
    assert CrawlConfig(plan="free", max_pages=7).effective_max_pages == 7

    assert (
        normalize_crawl_url(
            "https://www.Example.COM/blog?utm_source=x&id=2&utm_medium=cpc#sec",
            canonical_host="example.com",
        )
        == "https://example.com/blog?id=2"
    )
    assert (
        normalize_crawl_url("https://example.com/", canonical_host="example.com")
        == "https://example.com/"
    )
    assert normalize_crawl_url("ftp://example.com/a", canonical_host="example.com") is None


def test_multipage_detectors_duplicates_redirects_orphans_and_broken() -> None:
    """Verify multipage detectors flag duplicate meta, redirect chains, orphans, and 4xx/5xx."""
    from engine.audit import audit_url
    from engine.crawl.multipage import analyze_multipage_issues

    dup_title = "Titulo Repetido Para Teste SEO"
    dup_desc = (
        "Descricao exatamente repetida em duas paginas para acionar detector de duplicidade "
        "no crawler do site."
    )
    p1 = audit_url(
        "https://example.com/",
        html_text=(
            f"<html><head><title>{dup_title}</title>"
            f'<meta name="description" content="{dup_desc}"></head>'
            "<body><h1>Home</h1><a href='/p2'>P2</a><a href='/missing'>404</a></body></html>"
        ),
        robots_txt="",
        has_llms_txt=True,
    )
    p2 = audit_url(
        "https://example.com/p2",
        html_text=(
            f"<html><head><title>{dup_title}</title>"
            f'<meta name="description" content="{dup_desc}"></head>'
            "<body><h1>P2</h1></body></html>"
        ),
        robots_txt="",
        has_llms_txt=True,
    )
    broken = audit_url(
        "https://example.com/missing",
        html_text="<html><head><title>Not Found</title></head><body><h1>404</h1></body></html>",
        status_code=404,
        robots_txt="",
        has_llms_txt=True,
    )
    issues = analyze_multipage_issues(
        pages=[p1, p2, broken],
        start_url="https://example.com/",
        sitemap_urls=["https://example.com/", "https://example.com/orphan"],
        incoming_links={
            "https://example.com/": 0,
            "https://example.com/p2": 1,
            "https://example.com/missing": 1,
            "https://example.com/orphan": 0,
        },
        redirect_chains=[{"url": "https://example.com/old", "hops": ["https://example.com/p2"]}],
    )
    assert issues["duplicate_titles"] == {
        dup_title: ["https://example.com/", "https://example.com/p2"]
    }
    assert issues["duplicate_meta_descriptions"] == {
        dup_desc: ["https://example.com/", "https://example.com/p2"]
    }
    assert issues["orphan_pages"] == ["https://example.com/orphan"]
    assert issues["broken_internal_links"] == [
        {"url": "https://example.com/missing", "status_code": 404}
    ]
    assert len(issues["redirect_chains"]) == 1


def test_crawl_site_bfs_robots_sitemap_cap_and_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify bounded async BFS crawl obeys max_pages, robots.txt, sitemap, and SSRF guards."""
    from engine.crawl import crawl_site

    pages: dict[str, tuple[int, str]] = {
        "https://example.com/robots.txt": (
            200,
            "User-agent: *\nDisallow: /blocked\n",
        ),
        "https://example.com/llms.txt": (200, "# Example LLMs\n"),
        "https://example.com/sitemap.xml": (
            200,
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/</loc></url>"
            "<url><loc>https://example.com/orphan</loc></url>"
            "</urlset>",
        ),
        "https://example.com/": (
            200,
            "<html><head><title>Home</title><meta name='description' content='Home desc'></head>"
            "<body><h1>Home</h1><a href='/page-1?utm_source=a#top'>P1</a>"
            "<a href='/blocked'>Blocked</a><a href='/broken'>Broken</a></body></html>",
        ),
        "https://example.com/page-1": (
            200,
            "<html><head><title>Page 1</title><meta name='description' content='P1 desc'></head>"
            "<body><h1>P1</h1><a href='/page-2'>P2</a></body></html>",
        ),
        "https://example.com/page-2": (
            200,
            "<html><head><title>Page 2</title><meta name='description' content='P2 desc'></head>"
            "<body><h1>P2</h1></body></html>",
        ),
        "https://example.com/broken": (404, "<html><body>Not found</body></html>"),
        "https://example.com/orphan": (
            200,
            "<html><head><title>Orphan</title></head><body><h1>Orphan</h1></body></html>",
        ),
    }

    def _fake_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if "127.0.0.1" in url:
            raise BlockedIPError("blocked")
        code, body = pages.get(url, (404, ""))
        return SafeResponse(status_code=code, headers={}, content=body.encode("utf-8"))

    monkeypatch.setattr("engine.safe_http.safe_request", _fake_request)

    report = crawl_site("https://www.example.com/", config=CrawlConfig(plan="free", max_pages=3))
    assert isinstance(report, SiteAuditReport)
    assert report.pages_crawled == 3
    crawled_urls = [p.url for p in report.pages]
    assert "https://example.com/blocked" not in crawled_urls
    assert "https://example.com/page-1" in crawled_urls

    # With higher max_pages, sitemap orphan is crawled and reported as orphan (0 incoming links)
    full_report = crawl_site("https://example.com/", config=CrawlConfig(plan="paid"))
    assert "https://example.com/orphan" in full_report.orphan_pages
    assert any(b["url"] == "https://example.com/broken" for b in full_report.broken_internal_links)

    with pytest.raises(BlockedIPError):
        crawl_site("http://127.0.0.1/")


def test_site_audit_db_persistence_and_tenant_scoping(db_conn: psycopg.Connection[Any]) -> None:
    """Verify site_audits & audit_pages Postgres helpers enforce workspace_id tenant isolation."""
    from engine import db
    from engine.audit import audit_url

    ts = int(datetime.now(UTC).timestamp() * 1000)
    ws1 = db.create_workspace(
        db_conn, slug=f"crawl-ws1-{ts}", name="Crawl WS1", industry_pack="general"
    )
    ws2 = db.create_workspace(
        db_conn, slug=f"crawl-ws2-{ts}", name="Crawl WS2", industry_pack="general"
    )
    page = audit_url(
        "https://example.com/",
        html_text="<html><head><title>Title</title></head><body><h1>H</h1></body></html>",
        robots_txt="",
        has_llms_txt=False,
    )
    site_rep = SiteAuditReport(
        start_url="https://example.com/",
        pages_crawled=1,
        max_pages=20,
        overall_score=page.overall_score,
        grade=page.grade,
        pages=[page],
    )
    audit_id = db.insert_site_audit(db_conn, workspace_id=ws1, report=site_rep)
    page_id = db.insert_audit_page(db_conn, workspace_id=ws1, site_audit_id=audit_id, page=page)
    assert isinstance(audit_id, uuid.UUID)
    assert isinstance(page_id, uuid.UUID)

    ws1_rows = db.list_site_audits(db_conn, workspace_id=ws1)
    ws2_rows = db.list_site_audits(db_conn, workspace_id=ws2)
    assert len(ws1_rows) == 1
    assert ws1_rows[0]["id"] == audit_id
    assert ws2_rows == []

    # Plain-dict payload also persists cleanly
    dict_audit_id = db.insert_site_audit(
        db_conn, workspace_id=ws1, report=site_rep.model_dump(mode="json")
    )
    assert isinstance(dict_audit_id, uuid.UUID)


def test_crawl_redirects_loops_subpage_errors_and_invalid_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover redirect chains/loops, secondary page errors, enqueue skips, and invalid start URL."""
    from collections import deque
    from urllib.error import URLError

    from engine.crawl import crawler

    with pytest.raises(ValueError, match="Invalid crawl start URL"):
        crawler.crawl_site("mailto:bad@example.com")

    state = crawler._CrawlState(queue=deque(), queued=set())
    crawler._enqueue_internal_links(
        ["mailto:skip@example.com"], state, canonical_host="example.com"
    )
    assert len(state.queue) == 0

    def _redirect_fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
        routes: dict[str, SafeResponse] = {
            "https://example.com/robots.txt": SafeResponse(200, {}, b""),
            "https://example.com/llms.txt": SafeResponse(404, {}, b""),
            "https://example.com/llms-full.txt": SafeResponse(404, {}, b""),
            "https://example.com/sitemap.xml": SafeResponse(404, {}, b""),
            "https://example.com/": SafeResponse(
                200,
                {},
                b"<html><head><title>R</title></head><body><h1>R</h1>"
                b"<a href='/chain-1'>C</a><a href='/loop-a'>L</a>"
                b"<a href='/long-1'>H</a><a href='/sub-ssrf'>S</a>"
                b"<a href='/sub-net'>N</a></body></html>",
            ),
            "https://example.com/chain-1": SafeResponse(301, {"Location": "/chain-2"}, b""),
            "https://example.com/chain-2": SafeResponse(302, {"Location": "/chain-ok"}, b""),
            "https://example.com/chain-ok": SafeResponse(
                200, {}, b"<html><head><title>OK</title></head><body><h1>OK</h1></body></html>"
            ),
            "https://example.com/loop-a": SafeResponse(301, {"Location": "/loop-b"}, b""),
            "https://example.com/loop-b": SafeResponse(301, {"Location": "/loop-a"}, b""),
            "https://example.com/long-1": SafeResponse(302, {"Location": "/long-2"}, b""),
            "https://example.com/long-2": SafeResponse(302, {"Location": "/long-3"}, b""),
            "https://example.com/long-3": SafeResponse(302, {"Location": "/long-4"}, b""),
            "https://example.com/long-4": SafeResponse(302, {"Location": "/chain-ok"}, b""),
        }
        if "sub-ssrf" in url:
            raise BlockedIPError("subpage ssrf skipped")
        if "sub-net" in url:
            raise URLError("subpage timeout skipped")
        return routes.get(url, SafeResponse(404, {}, b""))

    monkeypatch.setattr("engine.safe_http.safe_request", _redirect_fetch)
    rep = crawler.crawl_site("https://example.com/", config=CrawlConfig(plan="paid"))
    assert len(rep.redirect_chains) >= 3
    assert any(c.get("loop") for c in rep.redirect_chains)

    def _seed_network_fail(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if url.endswith(("/robots.txt", "/llms.txt", "/llms-full.txt", "/sitemap.xml")):
            return SafeResponse(404, {}, b"")
        raise URLError("seed connection reset")

    monkeypatch.setattr("engine.safe_http.safe_request", _seed_network_fail)
    with pytest.raises(URLError):
        crawler.crawl_site("https://example.com/")
