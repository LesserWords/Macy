"""Scheduled route monitor runner: 30s retry, TLS check, regression outbox (M6-03 D3)."""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from engine import db, events, safe_http
from engine.audit import audit_url
from engine.contracts import PageAuditReport

TLSCheckerFn = Callable[[str], int | None]


def check_tls_days_left(url: str, *, timeout: float = 5.0) -> int | None:
    """Return remaining whole days before HTTPS TLS certificate expires, or None."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    port = parsed.port or 443
    try:
        ctx = ssl.create_default_context()
        with (
            socket.create_connection((parsed.hostname, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=parsed.hostname) as ssock,
        ):
            cert = ssock.getpeercert() or {}
        not_after = cert.get("notAfter")
        if not isinstance(not_after, str) or not not_after:
            return None
        expiry_ts = ssl.cert_time_to_seconds(not_after)
        return int((expiry_ts - datetime.now(UTC).timestamp()) // 86400)
    except (OSError, ssl.SSLError, ValueError):
        return None


def _audit_with_single_retry(
    url: str,
    *,
    retry_delay_seconds: float,
    sleep_fn: Callable[[float], None],
) -> PageAuditReport:
    try:
        return audit_url(url)
    except (safe_http.SafeHTTPError, urllib.error.URLError, OSError, TimeoutError):
        sleep_fn(retry_delay_seconds)
        return audit_url(url)


def _detect_regressions(  # noqa: PLR0913
    *,
    url: str,
    report: PageAuditReport,
    prev_score: float | None,
    prev_checks: list[dict[str, Any]] | None,
    score_drop_threshold: float,
    tls_days_left: int | None,
    tls_expiry_warn_days: int,
) -> list[tuple[str, dict[str, Any]]]:
    alerts: list[tuple[str, dict[str, Any]]] = []
    if prev_score is not None:
        drop = round(float(prev_score) - report.overall_score, 1)
        if drop >= score_drop_threshold:
            alerts.append(
                (
                    "audit.score_dropped",
                    {
                        "url": url,
                        "previous_score": float(prev_score),
                        "new_score": report.overall_score,
                        "drop": drop,
                    },
                )
            )
    if prev_checks is not None:
        prev_errors = {
            str(c.get("check", "")) for c in prev_checks if str(c.get("status", "")) == "error"
        }
        new_errors = [
            c.check for c in report.checks if c.status == "error" and c.check not in prev_errors
        ]
        if new_errors:
            alerts.append(("audit.critical_issue", {"url": url, "new_errors": new_errors}))
    if tls_days_left is not None and tls_days_left <= tls_expiry_warn_days:
        alerts.append(("monitor.tls_expiring", {"url": url, "tls_days_left": tls_days_left}))
    return alerts


@dataclass(frozen=True)
class MonitorOptions:
    """Optional overrides and threshold defaults for route monitoring."""

    score_drop_threshold: float = 5.0
    tls_expiry_warn_days: int = 14
    retry_delay_seconds: float = 30.0
    sleep_fn: Callable[[float], None] = time.sleep
    tls_checker: TLSCheckerFn = check_tls_days_left


def _load_or_create_monitor(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
    opts: MonitorOptions,
) -> dict[str, Any]:
    existing = next(
        (m for m in db.list_url_monitors(conn, workspace_id=workspace_id) if m["url"] == url),
        None,
    )
    if existing is not None:
        return existing
    mon_id = db.upsert_url_monitor(
        conn,
        workspace_id=workspace_id,
        url=url,
        score_drop_threshold=opts.score_drop_threshold,
        tls_expiry_warn_days=opts.tls_expiry_warn_days,
    )
    return {
        "id": mon_id,
        "last_score": None,
        "last_checks": None,
        "score_drop_threshold": opts.score_drop_threshold,
        "tls_expiry_warn_days": opts.tls_expiry_warn_days,
    }


def run_route_monitor(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
    score_drop_threshold: float = 5.0,
    tls_expiry_warn_days: int = 14,
    retry_delay_seconds: float = 30.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    tls_checker: TLSCheckerFn = check_tls_days_left,
) -> dict[str, Any]:
    """Execute scheduled URL re-audit with 1x30s retry, TLS probe, and Standard Webhook emission."""
    opts = MonitorOptions(
        score_drop_threshold=score_drop_threshold,
        tls_expiry_warn_days=tls_expiry_warn_days,
        retry_delay_seconds=retry_delay_seconds,
        sleep_fn=sleep_fn,
        tls_checker=tls_checker,
    )
    mon = _load_or_create_monitor(conn, workspace_id=workspace_id, url=url, opts=opts)
    mon_id = uuid.UUID(str(mon["id"]))

    try:
        report = _audit_with_single_retry(
            url, retry_delay_seconds=retry_delay_seconds, sleep_fn=sleep_fn
        )
    except (safe_http.SafeHTTPError, urllib.error.URLError, OSError, TimeoutError) as exc:
        db.update_url_monitor_result(
            conn,
            workspace_id=workspace_id,
            monitor_id=mon_id,
            last_score=float(mon["last_score"]) if mon["last_score"] is not None else None,
            last_checks=mon["last_checks"],
            last_tls_days_left=None,
            last_status="error",
        )
        return {
            "monitor_id": mon_id,
            "url": url,
            "status": "error",
            "error": str(exc),
            "events_emitted": [],
        }

    tls_days = tls_checker(url)
    prev_score = float(mon["last_score"]) if mon["last_score"] is not None else None
    alerts = _detect_regressions(
        url=url,
        report=report,
        prev_score=prev_score,
        prev_checks=mon["last_checks"],
        score_drop_threshold=float(mon.get("score_drop_threshold") or score_drop_threshold),
        tls_days_left=tls_days,
        tls_expiry_warn_days=int(mon.get("tls_expiry_warn_days") or tls_expiry_warn_days),
    )
    emitted: list[str] = []
    for ev_type, ev_payload in alerts:
        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type=ev_type,
            entity_type="url_monitor",
            entity_id=mon_id,
            payload=ev_payload,
        )
        emitted.append(ev_type)

    serialized_checks = [{"check": c.check, "status": c.status} for c in report.checks]
    db.update_url_monitor_result(
        conn,
        workspace_id=workspace_id,
        monitor_id=mon_id,
        last_score=report.overall_score,
        last_checks=serialized_checks,
        last_tls_days_left=tls_days,
        last_status="ok",
    )
    return {
        "monitor_id": mon_id,
        "url": url,
        "status": "ok",
        "overall_score": report.overall_score,
        "grade": report.grade,
        "tls_days_left": tls_days,
        "events_emitted": emitted,
    }
