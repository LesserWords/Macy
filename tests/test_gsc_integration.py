"""Tests for hybrid Google Search Console AnalyticsSourcePort adapter (M6-03 D1)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from engine import db
from engine.integrations.config import encrypt_secrets
from engine.integrations.ports import AnalyticsSourcePort
from engine.integrations.registry import get_plugin
from engine.safe_http import SafeResponse


def _make_rsa_private_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _seed_workspace_and_user(
    conn: psycopg.Connection[Any], tag: str
) -> tuple[uuid.UUID, uuid.UUID]:
    ts = int(datetime.now(UTC).timestamp() * 1000)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, name, ui_locale) VALUES (%s, 'GSC U', 'pt-BR') RETURNING id",
            (f"gsc-{tag}-{ts}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = uuid.UUID(str(row[0]))
    ws_id = db.create_workspace(
        conn, slug=f"gsc-{tag}-{ts}", name=f"GSC {tag}", industry_pack="general"
    )
    return ws_id, user_id


def test_gsc_entry_point_service_account_and_analytics_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify gsc entry point, RS256 service_account auth, and searchAnalytics querying."""
    from engine.integrations.gsc import GSCAnalyticsSource

    plugin_cls = get_plugin("analytics_source", "gsc")
    assert plugin_cls is GSCAnalyticsSource

    pem = _make_rsa_private_pem()
    sa_json = json.dumps(
        {
            "client_email": "gsc-bot@example.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    src = GSCAnalyticsSource(
        GSCAnalyticsSource.Settings(
            site_url="https://example.com/",
            auth_mode="service_account",
            service_account_json=sa_json,
        )
    )
    assert isinstance(src, AnalyticsSourcePort)

    calls: list[tuple[str, str]] = []

    def _fake_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        calls.append((method, url))
        if "oauth2.googleapis.com/token" in url:
            return SafeResponse(200, {}, json.dumps({"access_token": "sa-tok-1"}).encode())
        if "sites/" in url and url.endswith("/searchAnalytics/query"):
            return SafeResponse(
                200,
                {},
                json.dumps(
                    {
                        "rows": [
                            {
                                "keys": ["https://example.com/blog/oticas"],
                                "clicks": 42,
                                "impressions": 1200,
                                "ctr": 0.035,
                                "position": 6.4,
                            }
                        ]
                    }
                ).encode(),
            )
        return SafeResponse(
            200, {}, json.dumps({"siteEntry": [{"siteUrl": "https://example.com/"}]}).encode()
        )

    monkeypatch.setattr("engine.safe_http.safe_request", _fake_request)
    src.healthcheck()
    metrics = src.get_metrics("https://example.com/blog/oticas", date(2026, 11, 1))
    assert metrics["clicks"] == 42
    assert metrics["impressions"] == 1200
    assert metrics["position"] == 6.4

    rows = src.query_search_analytics(date(2026, 10, 1), date(2026, 11, 1), dimensions=["query"])
    assert len(rows) == 1
    assert any("oauth2.googleapis.com/token" in u for _, u in calls)


def test_gsc_oauth2_workspace_decrypt_12h_inspection_cache_and_auth_failure(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify OAuth2 workspace decryption, 12h Postgres inspection cache, and revocation event."""
    from engine.integrations.gsc import GSCAnalyticsSource

    fernet_key = Fernet.generate_key().decode("utf-8")
    ws_id, user_id = _seed_workspace_and_user(db_conn, "oauth")
    secrets_blob = encrypt_secrets(
        {
            "auth_mode": "oauth2",
            "client_id": "cid-123",
            "client_secret": "csec-456",
            "refresh_token": "rtok-789",
        },
        fernet_key,
    )
    db.upsert_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="analytics_source",
        plugin="gsc",
        settings={"site_url": "https://example.com/", "auth_mode": "oauth2"},
        secrets=secrets_blob,
        updated_by=user_id,
    )

    gsc = GSCAnalyticsSource.from_workspace_integration(
        db_conn, workspace_id=ws_id, encryption_key=fernet_key
    )
    assert gsc.settings.auth_mode == "oauth2"

    inspect_calls: list[str] = []

    def _oauth_and_inspect(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if "oauth2.googleapis.com/token" in url:
            return SafeResponse(200, {}, json.dumps({"access_token": "oa-tok"}).encode())
        if "urlInspection/index:inspect" in url:
            inspect_calls.append(url)
            return SafeResponse(
                200,
                {},
                json.dumps(
                    {
                        "inspectionResult": {
                            "indexStatusResult": {
                                "verdict": "PASS",
                                "coverageState": "Submitted and indexed",
                            }
                        }
                    }
                ).encode(),
            )
        return SafeResponse(404, {}, b"{}")

    monkeypatch.setattr("engine.safe_http.safe_request", _oauth_and_inspect)
    first = gsc.inspect_url("https://example.com/blog/oticas", conn=db_conn, workspace_id=ws_id)
    assert first["cached"] is False
    assert first["inspectionResult"]["indexStatusResult"]["verdict"] == "PASS"

    # Second call within 12h hits Postgres cache -> zero extra inspect HTTP calls
    second = gsc.inspect_url("https://example.com/blog/oticas", conn=db_conn, workspace_id=ws_id)
    assert second["cached"] is True
    assert len(inspect_calls) == 1

    # Expired DB cache entry older than 12h re-fetches
    with db_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE gsc_url_inspection_cache
            SET fetched_at = %s
            WHERE workspace_id = %s
            """,
            (datetime.now(UTC) - timedelta(hours=13), ws_id),
        )
    third = gsc.inspect_url("https://example.com/blog/oticas", conn=db_conn, workspace_id=ws_id)
    assert third["cached"] is False
    assert len(inspect_calls) == 2

    # Revoked OAuth2 token emits integration.auth_failed cleanly without crashing
    def _revoked_oauth(method: str, url: str, **kwargs: Any) -> SafeResponse:
        return SafeResponse(400, {}, json.dumps({"error": "invalid_grant"}).encode())

    fresh_gsc = GSCAnalyticsSource.from_workspace_integration(
        db_conn, workspace_id=ws_id, encryption_key=fernet_key
    )
    monkeypatch.setattr("engine.safe_http.safe_request", _revoked_oauth)
    degraded = fresh_gsc.get_metrics(
        "https://example.com/blog/oticas",
        date(2026, 11, 1),
        conn=db_conn,
        workspace_id=ws_id,
    )
    assert degraded["auth_failed"] is True
    ev_rows = db.list_events(db_conn, workspace_id=ws_id, event_type="integration.auth_failed")
    assert len(ev_rows) == 1


def test_gsc_edge_cases_for_full_branch_coverage(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover non-RSA key, missing/no-secrets integration, omitted token, empty rows."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from engine.integrations.gsc import GSCAnalyticsSource, GSCAuthError, _sign_service_account_jwt
    from engine.integrations.registry import IntegrationMissing

    ec_pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("utf-8")
    )
    with pytest.raises(GSCAuthError, match="RSA"):
        _sign_service_account_jwt(
            {"client_email": "x@y", "private_key": ec_pem},
            "https://oauth2.googleapis.com/token",
        )

    ws_id, user_id = _seed_workspace_and_user(db_conn, "edge")
    with pytest.raises(IntegrationMissing):
        GSCAnalyticsSource.from_workspace_integration(db_conn, workspace_id=ws_id)

    db.upsert_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="analytics_source",
        plugin="gsc",
        settings={"site_url": "https://example.com/", "auth_mode": "oauth2"},
        secrets=None,
        updated_by=user_id,
    )
    no_sec = GSCAnalyticsSource.from_workspace_integration(db_conn, workspace_id=ws_id)
    assert no_sec.settings.refresh_token is None

    # Token response missing access_token & conn=None
    gsc = GSCAnalyticsSource(
        GSCAnalyticsSource.Settings(
            site_url="https://example.com/",
            auth_mode="oauth2",
            client_id="cid",
            client_secret="sec",  # noqa: S106
            refresh_token="ref",  # noqa: S106
        )
    )
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda m, u, **kw: SafeResponse(200, {}, b"{}"),
    )
    with pytest.raises(GSCAuthError, match="access_token"):
        gsc.healthcheck()

    # healthcheck 500 error
    gsc._access_token = "tok"
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda m, u, **kw: SafeResponse(500, {}, b"{}"),
    )
    with pytest.raises(GSCAuthError, match="500"):
        gsc.healthcheck()

    # Empty search analytics rows + inspect_url with conn=None
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda m, u, **kw: SafeResponse(200, {}, json.dumps({"rows": []}).encode()),
    )
    assert gsc.get_metrics("https://example.com/none", date(2026, 11, 1))["views"] == 0
    assert gsc.inspect_url("https://example.com/none")["cached"] is False
