"""Tests for M6-02 D4 service, Streamlit UI, and MCP audit/crawl wiring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from engine import db, services
from engine.contracts import CrawlConfig
from engine.mcp.mock_store import DEMO_WS_ID
from engine.mcp.server import create_mcp_server
from engine.safe_http import SafeResponse
from tests.test_mcp_research_gate1 import _call_mcp_tool


def _mock_site_fetcher() -> Any:
    pages = {
        "https://example.com/robots.txt": SafeResponse(200, {}, b""),
        "https://example.com/llms.txt": SafeResponse(200, {}, b"# llms"),
        "https://example.com/sitemap.xml": SafeResponse(404, {}, b""),
        "https://example.com/": SafeResponse(
            200,
            {"Strict-Transport-Security": "max-age=31536000"},
            b"<html><head><title>Home Audit Page</title>"
            b'<meta name="description" content="' + (b"X" * 110) + b'">'
            b'<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            b"<body><main><h1>Home</h1><p>"
            + b" ".join([b"conteudo"] * 170)
            + b"</p><a href='/p2'>P2</a></main></body></html>",
        ),
        "https://example.com/p2": SafeResponse(
            200,
            {},
            b"<html><head><title>Second Page Here</title></head><body><h1>P2</h1></body></html>",
        ),
    }

    def _fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
        return pages.get(url, SafeResponse(404, {}, b""))

    return _fetch


def test_services_run_url_audit_and_site_crawl_with_db(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify services.run_url_audit and run_site_crawl persist tenant-scoped Postgres rows."""
    monkeypatch.setattr("engine.safe_http.safe_request", _mock_site_fetcher())

    # Standalone (no workspace_id) runs without DB persistence
    standalone = services.run_url_audit(url="https://example.com/")
    assert standalone["overall_score"] >= 50.0
    assert standalone["audit_id"] is None
    standalone_crawl = services.run_site_crawl(
        start_url="https://example.com/",
        config=CrawlConfig(plan="free", max_pages=2),
    )
    assert standalone_crawl["site_audit_id"] is None

    # Persisted workspace crawl
    ts = int(datetime.now(UTC).timestamp() * 1000)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, name, ui_locale) "
            "VALUES (%s, 'Audit U', 'pt-BR') RETURNING id",
            (f"audit-{ts}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]
    ws_id = db.create_workspace(
        db_conn, slug=f"audit-ws-{ts}", name="Audit WS", industry_pack="general"
    )
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
            (ws_id, user_id),
        )

    persisted_single = services.run_url_audit(
        db_conn,
        url="https://example.com/",
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert persisted_single["audit_id"] is not None

    persisted_crawl = services.run_site_crawl(
        db_conn,
        start_url="https://example.com/",
        config=CrawlConfig(plan="free", max_pages=5),
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert persisted_crawl["pages_crawled"] == 2
    assert uuid.UUID(str(persisted_crawl["site_audit_id"]))
    assert len(db.list_site_audits(db_conn, workspace_id=ws_id)) == 1

    # Persisted calls with user_id=None skip membership lookup while saving scoped rows
    assert (
        services.run_url_audit(db_conn, url="https://example.com/", workspace_id=ws_id)["audit_id"]
        is not None
    )
    assert (
        services.run_site_crawl(db_conn, start_url="https://example.com/", workspace_id=ws_id)[
            "site_audit_id"
        ]
        is not None
    )


def test_mcp_audit_url_and_crawl_site_mock_and_db(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify macy_audit_url and macy_crawl_site on MockStore and ServiceStoreAdapter."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr("engine.safe_http.safe_request", _mock_site_fetcher())

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    single_mock = _call_mcp_tool(
        mock_bundle,
        "macy_audit_url",
        {
            "workspace_id": str(DEMO_WS_ID),
            "url": "https://example.com/",
            "actor_email": "owner@example.com",
        },
    )
    assert single_mock["url"] == "https://example.com/"
    assert "grade" in single_mock

    crawl_mock = _call_mcp_tool(
        mock_bundle,
        "macy_crawl_site",
        {
            "workspace_id": str(DEMO_WS_ID),
            "start_url": "https://example.com/",
            "max_pages": 5,
            "actor_email": "owner@example.com",
        },
    )
    assert crawl_mock["pages_crawled"] == 2

    # ServiceStoreAdapter path
    ts = int(datetime.now(UTC).timestamp() * 1000)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, name, ui_locale) VALUES (%s, 'MCP U', 'pt-BR') RETURNING id",
            (f"mcp-audit-{ts}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]
    ws_id = db.create_workspace(
        db_conn, slug=f"mcp-audit-{ts}", name="MCP Audit", industry_pack="general"
    )
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
            (ws_id, user_id),
        )

    db_bundle = create_mcp_server(mock=False, transport="stdio")
    single_db = _call_mcp_tool(
        db_bundle,
        "macy_audit_url",
        {
            "workspace_id": str(ws_id),
            "url": "https://example.com/",
            "actor_user_id": str(user_id),
        },
    )
    assert single_db["audit_id"] is not None

    crawl_db = _call_mcp_tool(
        db_bundle,
        "macy_crawl_site",
        {
            "workspace_id": str(ws_id),
            "start_url": "https://example.com/",
            "max_pages": 5,
            "plan": "paid",
            "actor_user_id": str(user_id),
        },
    )
    assert crawl_db["pages_crawled"] == 2


def test_streamlit_render_audit_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify app._render_audit triggers URL audit and crawl cleanly."""
    import app

    messages: list[str] = []

    class _FakeSt:
        def subheader(self, txt: str) -> None:
            messages.append(txt)

        def text_input(self, label: str, **kw: Any) -> str:
            return "https://example.com/"

        def selectbox(self, label: str, options: list[str], **kw: Any) -> str:
            return options[0]

        def number_input(self, label: str, **kw: Any) -> int:
            return 5

        def button(self, label: str, **kw: Any) -> bool:
            return True

        def metric(self, label: str, value: Any) -> None:
            messages.append(f"{label}:{value}")

        def json(self, data: Any) -> None:
            messages.append("json")

    monkeypatch.setattr(app, "st", _FakeSt())
    monkeypatch.setattr(
        services,
        "run_url_audit",
        lambda **kw: {"overall_score": 90.0, "grade": "A", "checks": []},
    )
    monkeypatch.setattr(
        services,
        "run_site_crawl",
        lambda **kw: {"overall_score": 85.0, "grade": "A-", "pages_crawled": 2},
    )

    ws_id = uuid.uuid4()
    user_id = uuid.uuid4()
    app._render_audit({"user_id": user_id}, {"workspace_id": ws_id})
    assert any("90.0" in m for m in messages)
    assert any("85.0" in m for m in messages)
