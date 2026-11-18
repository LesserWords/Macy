"""Tests for scheduled URL/TLS monitors, 30s retry, and Standard Webhooks (M6-03 D3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import URLError

import psycopg
import pytest

from engine import db, scheduler
from engine.safe_http import SafeResponse


def _seed_ws(conn: psycopg.Connection[Any], tag: str) -> uuid.UUID:
    ts = int(datetime.now(UTC).timestamp() * 1000)
    return db.create_workspace(
        conn, slug=f"mon-{tag}-{ts}", name=f"Mon {tag}", industry_pack="general"
    )


def test_monitor_30s_retry_and_failure_after_second_attempt(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify transient monitor fetch errors sleep 30s and retry once before failing."""
    from engine.monitors.runner import run_route_monitor

    ws_id = _seed_ws(db_conn, "retry")
    db.upsert_url_monitor(db_conn, workspace_id=ws_id, url="https://example.com/")

    sleeps: list[float] = []
    attempts = {"count": 0}

    def _flaky_fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if url.endswith(("/robots.txt", "/llms.txt", "/llms-full.txt")):
            return SafeResponse(404, {}, b"")
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise URLError("transient reset")
        return SafeResponse(
            200,
            {"Strict-Transport-Security": "max-age=31536000"},
            b"<html><head><title>Recovered Page Title</title>"
            b'<meta name="description" content="' + (b"R" * 100) + b'">'
            b'<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            b"<body><main><h1>Recovered</h1><p>"
            + b" ".join([b"conteudo"] * 170)
            + b"</p></main></body></html>",
        )

    monkeypatch.setattr("engine.safe_http.safe_request", _flaky_fetch)
    res = run_route_monitor(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/",
        sleep_fn=sleeps.append,
        tls_checker=lambda u: 60,
    )
    assert res["status"] == "ok"
    assert sleeps == [30.0]

    # Second call always failing records status='error' after single 30s retry
    sleeps.clear()

    def _always_fail(method: str, url: str, **kwargs: Any) -> SafeResponse:
        raise URLError("hard down")

    monkeypatch.setattr("engine.safe_http.safe_request", _always_fail)
    down = run_route_monitor(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/",
        sleep_fn=sleeps.append,
        tls_checker=lambda u: 60,
    )
    assert down["status"] == "error"
    assert sleeps == [30.0]


def test_monitor_diffing_tls_expiry_webhook_outbox_and_scheduler(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify score drops, new critical issues, TLS <=14d emit outbox webhook events."""
    from engine.monitors.runner import check_tls_days_left, run_route_monitor

    ws_id = _seed_ws(db_conn, "diff")
    ts = int(datetime.now(UTC).timestamp() * 1000)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, name, ui_locale) "
            "VALUES (%s, 'Mon User', 'pt-BR') RETURNING id",
            (f"mon-u-{ts}@example.com",),
        )
        user_row = cur.fetchone()
        assert user_row is not None
        user_id = uuid.UUID(str(user_row[0]))

    mon_id = db.upsert_url_monitor(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/",
        score_drop_threshold=5.0,
        tls_expiry_warn_days=14,
    )
    db.create_webhook_subscription(
        db_conn,
        workspace_id=ws_id,
        url="https://hooks.example.com/seo",
        secret=b"enc-placeholder",
        created_by=user_id,
        event_types=["audit.score_dropped", "audit.critical_issue", "monitor.tls_expiring"],
    )

    good_html = (
        b"<!doctype html><html lang='pt-BR'><head><title>Pagina Saudavel Para SEO</title>"
        b'<meta name="description" content="' + (b"G" * 110) + b'">'
        b'<meta name="viewport" content="width=device-width, initial-scale=1">'
        b'<meta property="og:title" content="OG"><meta property="og:description" content="D">'
        b'<meta name="twitter:card" content="summary"><link rel="canonical" href="https://example.com/">'
        b"</head><body><main><h1>Titulo</h1><p>"
        + b" ".join([b"conteudo"] * 170)
        + b"</p><a href='/a'>A</a></main></body></html>"
    )
    bad_html = b"<html><body><p>Broken bare page</p></body></html>"
    active_html = {"body": good_html, "robots": b"User-agent: *\nAllow: /\n"}

    def _fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if url.endswith("/robots.txt"):
            return SafeResponse(200, {}, active_html["robots"])
        if url.endswith("/llms.txt"):
            return SafeResponse(200, {}, b"# llms")
        return SafeResponse(
            200,
            {"Strict-Transport-Security": "max-age=31536000"},
            active_html["body"],
        )

    monkeypatch.setattr("engine.safe_http.safe_request", _fetch)
    baseline = run_route_monitor(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/",
        sleep_fn=lambda _: None,
        tls_checker=lambda u: 45,
    )
    assert baseline["overall_score"] >= 80.0
    assert baseline["events_emitted"] == []

    # Regress HTML + expiring TLS cert -> emits all three Standard Webhook outbox events
    active_html["body"] = bad_html
    active_html["robots"] = b"User-agent: OAI-SearchBot\nDisallow: /\n"
    regressed = scheduler.run_due_url_monitors(
        db_conn,
        workspace_id=ws_id,
        sleep_fn=lambda _: None,
        tls_checker=lambda u: 7,
    )
    assert len(regressed) == 1
    assert set(regressed[0]["events_emitted"]) == {
        "audit.score_dropped",
        "audit.critical_issue",
        "monitor.tls_expiring",
    }
    deliveries = db.list_webhook_deliveries(db_conn, workspace_id=ws_id)
    assert len(deliveries) >= 3
    assert regressed[0]["monitor_id"] == mon_id

    # TLS helper parses fake cert expiry cleanly and skips plain http://
    assert check_tls_days_left("http://example.com/") is None
    expiry_str = (datetime.now(UTC) + timedelta(days=10)).strftime("%b %d %H:%M:%S %Y GMT")

    class _FakeSSLSock:
        def __enter__(self) -> _FakeSSLSock:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def getpeercert(self) -> dict[str, Any]:
            return {"notAfter": expiry_str}

    class _FakeCtx:
        def wrap_socket(self, sock: Any, **kw: Any) -> _FakeSSLSock:
            return _FakeSSLSock()

    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: _FakeSSLSock())
    monkeypatch.setattr("ssl.create_default_context", lambda: _FakeCtx())
    days = check_tls_days_left("https://example.com/")
    assert days is not None and 8 <= days <= 11

    # Cover missing notAfter & OSError TLS paths + auto-created monitor path
    class _EmptyCertSock(_FakeSSLSock):
        def getpeercert(self) -> dict[str, Any]:
            return {}

    class _EmptyCtx:
        def wrap_socket(self, sock: Any, **kw: Any) -> _EmptyCertSock:
            return _EmptyCertSock()

    monkeypatch.setattr("ssl.create_default_context", lambda: _EmptyCtx())
    assert check_tls_days_left("https://example.com/") is None

    monkeypatch.setattr(
        "socket.create_connection",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("conn refused")),
    )
    assert check_tls_days_left("https://example.com/") is None

    auto_res = run_route_monitor(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/new-route",
        sleep_fn=lambda _: None,
        tls_checker=lambda u: 45,
    )
    assert auto_res["status"] == "ok"
