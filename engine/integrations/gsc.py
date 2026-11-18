"""Hybrid Google Search Console AnalyticsSourcePort adapter (M6-03 D1, ADR-028)."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

import psycopg
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import BaseModel, ConfigDict

from engine import db, events, safe_http
from engine.integrations.config import _resolve_keys, decrypt_secrets
from engine.integrations.registry import IntegrationMissing

TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"
URL_INSPECT_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"


class GSCAuthError(RuntimeError):
    """Raised when GSC service-account JWT exchange or OAuth2 refresh fails."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign_service_account_jwt(sa_info: dict[str, Any], token_uri: str) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    claims = _b64url(
        json.dumps(
            {
                "iss": str(sa_info["client_email"]),
                "scope": GSC_SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            }
        ).encode("utf-8")
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    private_key = serialization.load_pem_private_key(
        str(sa_info["private_key"]).encode("utf-8"),
        password=None,
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        msg = "GSC service account private_key must be RSA"
        raise GSCAuthError(msg)
    sig = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(sig)}"


class GSCAnalyticsSource:
    """Google Search Console integration supporting hybrid Service Account + OAuth2 auth."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")

        site_url: str = "https://example.com/"
        auth_mode: Literal["service_account", "oauth2"] = "service_account"
        service_account_json: str | None = None
        client_id: str | None = None
        client_secret: str | None = None
        refresh_token: str | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()
        self._access_token: str | None = None

    @classmethod
    def from_workspace_integration(
        cls,
        conn: psycopg.Connection[Any],
        *,
        workspace_id: uuid.UUID,
        encryption_key: str | list[str] | None = None,
    ) -> GSCAnalyticsSource:
        """Load and decrypt workspace GSC settings/secrets from Postgres."""
        row = db.get_workspace_integration(conn, workspace_id=workspace_id, port="analytics_source")
        if row is None or row.get("plugin") != "gsc":
            raise IntegrationMissing("analytics_source")
        merged: dict[str, Any] = dict(row.get("settings") or {})
        raw_secrets = row.get("secrets")
        if raw_secrets is not None:
            merged.update(decrypt_secrets(raw_secrets, _resolve_keys(encryption_key)))
        return cls(cls.Settings.model_validate(merged))

    def _is_configured(self) -> bool:
        if self.settings.auth_mode == "service_account":
            return bool(self.settings.service_account_json)
        return bool(
            self.settings.client_id and self.settings.client_secret and self.settings.refresh_token
        )

    def _emit_auth_failure(
        self,
        conn: psycopg.Connection[Any] | None,
        workspace_id: uuid.UUID | None,
        reason: str,
    ) -> None:
        if conn is not None and workspace_id is not None:
            events.emit(
                conn,
                workspace_id=workspace_id,
                event_type="integration.auth_failed",
                entity_type="workspace_integration",
                entity_id=workspace_id,
                payload={"port": "analytics_source", "plugin": "gsc", "detail": reason},
            )

    def _acquire_access_token(
        self,
        *,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> str:
        if self._access_token:
            return self._access_token

        if self.settings.auth_mode == "service_account":
            sa_info = json.loads(self.settings.service_account_json or "{}")
            token_uri = str(sa_info.get("token_uri") or TOKEN_URI)
            assertion = _sign_service_account_jwt(sa_info, token_uri)
            form_data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        else:
            token_uri = TOKEN_URI
            form_data = {
                "client_id": self.settings.client_id or "",
                "client_secret": self.settings.client_secret or "",
                "refresh_token": self.settings.refresh_token or "",
                "grant_type": "refresh_token",
            }

        resp = safe_http.safe_request(
            "POST",
            token_uri,
            data=urllib.parse.urlencode(form_data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )
        if not 200 <= resp.status_code < 300:
            reason = f"GSC token exchange HTTP {resp.status_code}: {resp.text[:160]}"
            self._emit_auth_failure(conn, workspace_id, reason)
            raise GSCAuthError(reason)
        data = resp.json()
        tok = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(tok, str) or not tok:
            reason = "GSC token response omitted access_token"
            self._emit_auth_failure(conn, workspace_id, reason)
            raise GSCAuthError(reason)
        self._access_token = tok
        return tok

    def healthcheck(self) -> None:
        """Verify GSC connectivity when credentials exist; no-op on default unconfigured fake."""
        if not self._is_configured():
            return
        token = self._acquire_access_token()
        resp = safe_http.safe_request(
            "GET",
            f"{GSC_API_BASE}/sites",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if not 200 <= resp.status_code < 300:
            msg = f"GSC healthcheck returned HTTP {resp.status_code}"
            raise GSCAuthError(msg)

    def query_search_analytics(  # noqa: PLR0913
        self,
        start_date: date,
        end_date: date,
        *,
        dimensions: Sequence[str] | None = None,
        row_limit: int = 250,
        page_filter: str | None = None,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Query Google Search Console Search Analytics API."""
        token = self._acquire_access_token(conn=conn, workspace_id=workspace_id)
        encoded_site = urllib.parse.quote(self.settings.site_url, safe="")
        body: dict[str, Any] = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": list(dimensions or ["page"]),
            "rowLimit": row_limit,
        }
        if page_filter:
            body["dimensionFilterGroups"] = [
                {"filters": [{"dimension": "page", "expression": page_filter}]}
            ]
        resp = safe_http.safe_request(
            "POST",
            f"{GSC_API_BASE}/sites/{encoded_site}/searchAnalytics/query",
            headers={"Authorization": f"Bearer {token}"},
            json_data=body,
            timeout=10.0,
        )
        data = resp.json() if resp.status_code == 200 else {}
        return list(data.get("rows", [])) if isinstance(data, dict) else []

    def get_metrics(
        self,
        external_id: str,
        since: date,
        *,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Fetch Search Analytics metrics for external_id (URL) per AnalyticsSourcePort."""
        base: dict[str, Any] = {
            "external_id": external_id,
            "since": since.isoformat(),
            "views": 0,
            "clicks": 0,
            "impressions": 0,
            "ctr": 0.0,
            "position": 0.0,
        }
        if not self._is_configured():
            return base
        try:
            rows = self.query_search_analytics(
                since,
                date.today(),
                dimensions=["page"],
                row_limit=1,
                page_filter=external_id,
                conn=conn,
                workspace_id=workspace_id,
            )
        except GSCAuthError:
            return {**base, "auth_failed": True}
        if not rows:
            return base
        row = rows[0]
        impressions = int(row.get("impressions", 0))
        clicks = int(row.get("clicks", 0))
        return {
            **base,
            "views": impressions,
            "clicks": clicks,
            "impressions": impressions,
            "ctr": float(row.get("ctr", 0.0)),
            "position": float(row.get("position", 0.0)),
        }

    def inspect_url(
        self,
        url: str,
        *,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Inspect URL indexation status with 12-hour Postgres cache protection."""
        if conn is not None and workspace_id is not None:
            cached = db.get_gsc_inspection_cache(
                conn,
                workspace_id=workspace_id,
                site_url=self.settings.site_url,
                inspected_url=url,
                max_age_hours=12,
            )
            if cached is not None:
                return {**cached, "cached": True}

        token = self._acquire_access_token(conn=conn, workspace_id=workspace_id)
        resp = safe_http.safe_request(
            "POST",
            URL_INSPECT_ENDPOINT,
            headers={"Authorization": f"Bearer {token}"},
            json_data={"inspectionUrl": url, "siteUrl": self.settings.site_url},
            timeout=10.0,
        )
        payload: dict[str, Any] = dict(resp.json()) if resp.status_code == 200 else {}
        if conn is not None and workspace_id is not None and payload:
            db.upsert_gsc_inspection_cache(
                conn,
                workspace_id=workspace_id,
                site_url=self.settings.site_url,
                inspected_url=url,
                payload=payload,
            )
        return {**payload, "cached": False}
