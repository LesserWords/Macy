"""Unit, integration, and sitemap tests for content inventory import (F08 AC3, AC4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from engine import db, services, sitemap

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_SITEMAP = str(FIXTURES_DIR / "sample_sitemap.xml")
SAMPLE_SITEMAP_INDEX = str(FIXTURES_DIR / "sample_sitemap_index.xml")


def _seed_workspace_and_owner() -> tuple[uuid.UUID, uuid.UUID]:
    with db.get_connection() as conn:
        ws_id = db.create_workspace(
            conn,
            slug=f"ws-inv-{uuid.uuid4().hex[:8]}",
            name="Inventory Workspace",
            industry_pack="general",
        )
        u_row = db.create_or_get_user(conn, email=f"user-inv-{uuid.uuid4().hex[:8]}@example.com")
        db.create_membership(conn, workspace_id=ws_id, user_id=u_row["user_id"], role="owner")
        return ws_id, u_row["user_id"]


# ---------------------------------------------------------------------------
# Section 1: Pure Functions & Title Parsing Unit Tests
# ---------------------------------------------------------------------------


def test_slug_to_title() -> None:
    assert (
        sitemap.slug_to_title("https://blog.example.com/blog/fim-da-escala-6x1-nas-oticas/")
        == "Fim da escala 6x1 nas oticas"
    )
    assert (
        sitemap.slug_to_title("https://example.com/noticias/nova_tecnologia_de_lentes.html")
        == "Nova tecnologia de lentes"
    )
    assert sitemap.slug_to_title("https://example.com/") == "example.com"
    assert sitemap.slug_to_title("https://example.com/---") == "Article"


def test_clean_page_title() -> None:
    assert (
        sitemap.clean_page_title("Fim da escala 6x1 nas óticas: equipes &ndash; Acme Blog")
        == "Fim da escala 6x1 nas óticas: equipes"
    )
    assert sitemap.clean_page_title("Tendências 2026 | Portal Óptico") == "Tendências 2026"
    assert sitemap.clean_page_title("Notícia Rápida — Blog Oficial") == "Notícia Rápida"
    assert sitemap.clean_page_title("Título Simples") == "Título Simples"


def test_extract_title_from_html() -> None:
    # 1. og:title
    html_og = (
        '<html><head><meta property="og:title" '
        'content="Artigo Óptica &amp; Lentes – Site" /></head></html>'
    )
    assert sitemap.extract_title_from_html(html_og) == "Artigo Óptica & Lentes"

    # 2. title tag
    html_title = "<html><head><title>Título no Head - Meu Blog</title></head></html>"
    assert sitemap.extract_title_from_html(html_title) == "Título no Head"

    # 3. h1 tag
    html_h1 = "<html><body><h1><span>Destaque Principal</span></h1></body></html>"
    assert sitemap.extract_title_from_html(html_h1) == "Destaque Principal"

    # 4. None found
    assert sitemap.extract_title_from_html("<html><body><p>Sem título</p></body></html>") is None


def test_parse_iso_datetime() -> None:
    dt1 = sitemap.parse_iso_datetime("2026-11-04T14:04:29+00:00")
    assert dt1 is not None
    assert dt1.tzinfo == UTC
    assert dt1.year == 2026

    # Naive string gets UTC
    dt2 = sitemap.parse_iso_datetime("2026-10-21T16:34:40")
    assert dt2 is not None
    assert dt2.tzinfo == UTC

    # Invalid / empty
    assert sitemap.parse_iso_datetime(None) is None
    assert sitemap.parse_iso_datetime("invalid-date") is None


# ---------------------------------------------------------------------------
# Section 2: Sitemap Ingestion & Idempotency (F08 AC3)
# ---------------------------------------------------------------------------


def test_sitemap_fixture_idempotent_import_ac3() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    # 1. First import: 3 articles inserted
    res1 = services.import_sitemap(
        workspace_id=ws_id,
        sitemap_url=SAMPLE_SITEMAP,
        user_id=user_id,
        fetch_titles=False,
    )
    assert res1["total"] == 3
    assert res1["inserted"] == 3
    assert res1["updated"] == 0

    with db.get_connection() as conn:
        pubs = db.list_publications(conn, workspace_id=ws_id)
        assert len(pubs) == 3
        urls = {p["url"] for p in pubs}
        assert "https://blog.example.com/blog/fim-da-escala-6x1-nas-oticas/" in urls
        for p in pubs:
            assert p["mode"] == "live"
            assert p["status"] == "published"
            assert p["destination"] == "sitemap"
            assert p["title"] != ""

    # 2. Re-running the import creates no duplicates (F08 AC3)
    res2 = services.import_sitemap(
        workspace_id=ws_id,
        sitemap_url=SAMPLE_SITEMAP,
        user_id=user_id,
        fetch_titles=False,
    )
    assert res2["total"] == 3
    assert res2["inserted"] == 0
    assert res2["updated"] == 3

    with db.get_connection() as conn:
        pubs_after = db.list_publications(conn, workspace_id=ws_id)
        assert len(pubs_after) == 3


def test_sitemapindex_expansion() -> None:
    articles = sitemap.fetch_sitemap_articles(
        SAMPLE_SITEMAP_INDEX,
        fetch_titles=False,
    )
    assert len(articles) == 3
    assert any("escala-6x1" in a["url"] for a in articles)


# ---------------------------------------------------------------------------
# Section 3: Opportunity Already-Covered Check (F08 AC4)
# ---------------------------------------------------------------------------


def test_already_covered_check_ac4() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    # 1. Import publication into workspace content inventory
    with db.get_connection() as conn:
        pub = db.create_publication(
            conn,
            workspace_id=ws_id,
            url="https://blog.example.com/blog/fim-da-escala-6x1-nas-oticas/",
            title="Fim da escala 6x1 nas óticas",
            published_at=datetime(2026, 11, 4, 14, 0, tzinfo=UTC),
        )
        assert pub["id"] is not None

        # Verify get_publication and get_publication_by_url
        assert db.get_publication(conn, workspace_id=ws_id, publication_id=pub["id"]) is not None
        assert db.get_publication_by_url(conn, workspace_id=ws_id, url=pub["url"]) is not None

        # 2. Setup campaign and completed run with 2 opportunities
        camp_id = db.create_campaign(
            conn,
            workspace_id=ws_id,
            name="Inventory Coverage Campaign 2026",
            locale="pt-BR",
            market="BR",
            schedule_cron="15 9 * * 2",
            articles_per_week=4,
            monthly_cap_usd=Decimal("320.00"),
            run_cap_usd=Decimal("40.00"),
            config={"theme": "inventory-check"},
            config_schema_version=1,
            created_by=user_id,
        )
        now_utc = datetime.now(UTC)
        run_id = db.create_run(
            conn,
            workspace_id=ws_id,
            campaign_id=camp_id,
            trigger="scheduled",
            window_start=now_utc - timedelta(days=10),
            window_end=now_utc,
            run_cap_usd=Decimal("40.00"),
            code_version="build-inventory-v1",
            pack_version="general-v2",
            created_by=user_id,
        )

        # Doc 1: Similar to existing publication
        doc1 = db.upsert_document(
            conn,
            workspace_id=ws_id,
            url=f"https://source1.example.com/{uuid.uuid4().hex[:8]}",
            canonical_url=f"https://source1.example.com/{uuid.uuid4().hex[:8]}",
            title="Fim da escala 6x1 nas óticas: impactos no varejo",
            content_hash=uuid.uuid4().hex.encode(),
            lang="pt",
            published_at=datetime.now(UTC),
            date_status="verified",
            fetch_status="ok",
        )
        rec1 = db.upsert_research_record(
            conn,
            workspace_id=ws_id,
            document_id=doc1["id"],
            run_id=run_id,
            schema_version=1,
            payload={
                "headline": "Fim da escala 6x1 nas óticas",
                "summary": "Mudanças trabalhistas e impacto operacional nas lojas de óptica.",
            },
            status="extracted",
        )
        opp1 = db.upsert_opportunity(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_id=rec1["id"],
            scores={"total": 90.0},
            total=90.0,
            funnel_stage="mofu",
            confidence="high",
            angle="Gestão de pessoas e escalas",
            status="scored",
        )
        opp1_id = opp1["id"]

        # Doc 2: Brand new story (not covered)
        doc2 = db.upsert_document(
            conn,
            workspace_id=ws_id,
            canonical_url=f"https://source2.example.com/{uuid.uuid4().hex[:8]}",
            url=f"https://source2.example.com/{uuid.uuid4().hex[:8]}",
            title="Nova Tecnologia de Polímeros para Armações Esportivas",
            content_hash=uuid.uuid4().hex.encode(),
            lang="pt",
            published_at=datetime.now(UTC),
            date_status="verified",
            fetch_status="ok",
        )
        rec2 = db.upsert_research_record(
            conn,
            workspace_id=ws_id,
            document_id=doc2["id"],
            run_id=run_id,
            schema_version=1,
            payload={
                "headline": "Nova Tecnologia de Polímeros para Armações",
                "summary": "Inovação em resistência e leveza nos óculos solares.",
            },
            status="extracted",
        )
        opp2 = db.upsert_opportunity(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_id=rec2["id"],
            scores={"total": 85.0},
            total=85.0,
            funnel_stage="tofu",
            confidence="high",
            angle="Inovação de produtos",
            status="scored",
        )
        opp2_id = opp2["id"]

    # 3. Generate report: Opp 1 is flagged already_covered=True, Opp 2 is already_covered=False
    report_data = services.report(workspace_id=ws_id, run_id=run_id)
    items = {item["id"]: item for item in report_data["items"]}

    assert opp1_id in items
    assert items[opp1_id]["already_covered"] is True
    assert items[opp1_id]["is_recommended"] is False

    assert opp2_id in items
    assert items[opp2_id]["already_covered"] is False
    assert items[opp2_id]["is_recommended"] is True


# ---------------------------------------------------------------------------
# Section 4: Permissions & Error Handling
# ---------------------------------------------------------------------------


def test_import_sitemap_permissions_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_id, owner_id = _seed_workspace_and_owner()

    # 1. Non-member user raises PermissionError
    non_member_id = uuid.uuid4()
    with pytest.raises(PermissionError, match="not a member"):
        services.import_sitemap(
            workspace_id=ws_id,
            sitemap_url=SAMPLE_SITEMAP,
            user_id=non_member_id,
        )

    # 2. Non-owner role raises PermissionError
    monkeypatch.setattr(services, "require_member", lambda *_, **__: "viewer")
    with pytest.raises(PermissionError, match="must have owner role to import sitemaps"):
        services.import_sitemap(
            workspace_id=ws_id,
            sitemap_url=SAMPLE_SITEMAP,
            user_id=owner_id,
        )
    monkeypatch.undo()

    # 3. Empty sitemap URL
    with pytest.raises(ValueError, match="Sitemap URL cannot be empty"):
        services.import_sitemap(
            workspace_id=ws_id,
            sitemap_url="",
            user_id=owner_id,
        )

    # 4. Invalid XML error
    with pytest.raises(ValueError, match="Invalid sitemap XML"):
        services.import_sitemap(
            workspace_id=ws_id,
            sitemap_url=__file__,  # Python file is not valid sitemap XML
            user_id=owner_id,
        )


# ---------------------------------------------------------------------------
# Section 5: Blog Sitemap Import (M2-05 Step 3)
# ---------------------------------------------------------------------------


def test_example_blog_sitemap_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify full sitemap import parsing >= 60 URLs from an HTTP post-sitemap."""
    from engine import safe_http

    urls_xml = "".join(
        f"<url><loc>https://blog.example.com/articles/post-{idx}/</loc>"
        "<lastmod>2026-11-04T14:04:29+00:00</lastmod></url>"
        for idx in range(60)
    )
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls_xml}</urlset>"
    ).encode()
    monkeypatch.setattr("engine.safe_http.validate_safe_url", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda *_a, **_kw: safe_http.SafeResponse(status_code=200, headers={}, content=payload),
    )

    articles = sitemap.fetch_sitemap_articles(
        "https://blog.example.com/post-sitemap.xml",
        timeout=10.0,
        fetch_titles=False,
    )
    assert len(articles) == 60
    assert articles[0]["url"].startswith("https://blog.example.com/")
    assert articles[0]["title"] != ""


# ---------------------------------------------------------------------------
# Section 6: Edge Cases & Diff-Cover Completeness
# ---------------------------------------------------------------------------


def test_extract_title_from_html_empty_h1() -> None:
    assert sitemap.extract_title_from_html("<html><body><h1>   </h1></body></html>") is None
    empty_h1 = "<html><body><h1><span></span></h1></body></html>"
    assert sitemap.extract_title_from_html(empty_h1) is None


def test_sitemap_load_file_protocol() -> None:
    articles = sitemap.fetch_sitemap_articles(f"file://{SAMPLE_SITEMAP}")
    assert len(articles) == 3


def test_sitemap_load_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import safe_http

    fake_resp = type("Resp", (), {"status_code": 404, "content": b""})()
    monkeypatch.setattr(safe_http, "safe_request", lambda *args, **kwargs: fake_resp)
    with pytest.raises(RuntimeError, match="Failed to fetch sitemap"):
        sitemap._load_content("https://example.com/sitemap.xml")


def test_extract_child_text_empty() -> None:
    import defusedxml.ElementTree as ET

    root = ET.fromstring("<root><loc>   </loc></root>")
    assert sitemap._extract_child_text(root, "loc") is None


def test_expand_sitemapindex_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("Sub-sitemap network failure")

    monkeypatch.setattr(sitemap, "fetch_sitemap_articles", _fail)
    res = sitemap._expand_sitemapindex(
        ["https://example.com/sub.xml"],
        timeout=5.0,
        is_production=False,
        fetch_titles=False,
        max_depth=1,
    )
    assert res == []


def test_resolve_article_title_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import safe_http

    # Success case
    fake_ok = type(
        "Resp",
        (),
        {
            "status_code": 200,
            "text": "<html><head><title>Resolved Title - Blog</title></head></html>",
        },
    )()
    monkeypatch.setattr(safe_http, "safe_request", lambda *args, **kwargs: fake_ok)
    title = sitemap._resolve_article_title(
        "https://example.com/blog/my-article",
        timeout=5.0,
        is_production=False,
        fetch_titles=True,
    )
    assert title == "Resolved Title"

    # Exception fallback case
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Network error")

    monkeypatch.setattr(safe_http, "safe_request", _raise)
    fallback = sitemap._resolve_article_title(
        "https://example.com/blog/my-article",
        timeout=5.0,
        is_production=False,
        fetch_titles=True,
    )
    assert fallback == "My article"


def test_resolve_workspace_without_identifier() -> None:
    _seed_workspace_and_owner()
    resolved = services.resolve_workspace()
    assert resolved["workspace_id"] is not None


def test_resolve_workspace_none_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import db

    monkeypatch.setattr(db, "list_workspaces", lambda conn: [])
    with pytest.raises(ValueError, match="No workspaces found"):
        services.resolve_workspace()


def test_import_sitemap_with_explicit_conn() -> None:
    ws_id, user_id = _seed_workspace_and_owner()
    with db.get_connection() as conn:
        res = services.import_sitemap(
            conn,
            workspace_id=ws_id,
            sitemap_url=SAMPLE_SITEMAP,
            user_id=user_id,
        )
        assert res["total"] == 3
