"""Brave Search News API plugin implementing NewsSearchPort (F07, ADR-003, ADR-005, ADR-016)."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlsplit

import psycopg
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from engine import db
from engine.contracts import ResearchRecord
from engine.integrations.registry import UnsupportedLocale
from engine.research import canonicalize_url, resolve_date_status
from engine.safe_http import safe_request, validate_safe_url

BRAVE_LOCALE_MAP: dict[tuple[str, str], tuple[str, str, str]] = {
    ("pt-BR", "br"): ("BR", "pt-br", "pt-BR"),
    ("en-US", "us"): ("US", "en", "en-US"),
}


def map_brave_locale(locale: str, market: str) -> tuple[str, str, str]:
    """Map campaign (locale, market) to Brave Search (country, search_lang, ui_lang)."""
    norm_locale = locale.strip()
    norm_market = market.strip().lower()
    mapped = BRAVE_LOCALE_MAP.get((norm_locale, norm_market))
    if mapped is not None:
        return mapped
    if norm_locale not in ("pt-BR", "en-US"):
        raise UnsupportedLocale("brave", locale)
    raise UnsupportedLocale("brave", locale, market)


def build_site_search_query(domain: str | None, query: str = "") -> str:
    """Format a domain-scoped ('site:<domain> ...') or open search query."""
    clean_domain = (domain or "").strip().lower()
    clean_query = query.strip()
    if clean_domain:
        site_prefix = f"site:{clean_domain}"
        if not clean_query:
            return site_prefix
        if clean_query.lower().startswith(site_prefix):
            return clean_query
        return f"{site_prefix} {clean_query}"
    if not clean_query:
        msg = "Brave search query must not be empty"
        raise ValueError(msg)
    return clean_query


def _parse_brave_page_age(raw_age: Any) -> datetime | None:
    if not raw_age or not str(raw_age).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_age).strip().replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _item_to_record(item: dict[str, Any], since: date | None) -> ResearchRecord | None:
    raw_url = str(item.get("url") or "").strip()
    if not raw_url:
        return None
    raw_dt = _parse_brave_page_age(item.get("page_age"))
    resolved_dt, date_status = resolve_date_status(raw_dt, url=raw_url)
    if since is not None and resolved_dt is not None and resolved_dt.date() < since:
        return None

    canonical = canonicalize_url(raw_url)
    title = str(item.get("title") or canonical).strip()
    description = str(item.get("description") or title).strip()
    meta_url = item.get("meta_url")
    hostname = meta_url.get("hostname") if isinstance(meta_url, dict) else None
    source_host = str(hostname or urlsplit(canonical).hostname or "brave").strip()

    return ResearchRecord(
        headline=title,
        source=source_host,
        url=raw_url,
        canonical_url=canonical,
        published_at=resolved_dt,
        event_date=resolved_dt.date() if resolved_dt else since,
        summary=description,
        source_type="news",
        verification_status=date_status,
    )


class BraveNewsSearch:
    """NewsSearchPort plugin querying Brave Search News API with cost accounting."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        unit_cost_usd: Decimal = Field(default=Decimal("0.005000"), ge=Decimal("0"))
        count: int = Field(default=20, ge=1, le=50)
        base_url: str = "https://api.search.brave.com/res/v1/news/search"
        timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
        api_key: SecretStr | None = Field(
            default_factory=lambda: SecretStr(os.environ.get("BRAVE_API_KEY", "test-brave-key"))
        )

    settings: Settings

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        stage: str = "research",
    ) -> None:
        self.settings = settings or self.Settings()
        self._conn = conn
        self._workspace_id = workspace_id
        self._run_id = run_id
        self._stage = stage

    def _raw_api_key(self) -> str:
        raw_key = (
            self.settings.api_key.get_secret_value()
            if isinstance(self.settings.api_key, SecretStr)
            else self.settings.api_key
        )
        if not raw_key or not str(raw_key).strip():
            msg = "Brave Search API key is not configured"
            raise ValueError(msg)
        return str(raw_key).strip()

    def healthcheck(self) -> None:
        """Verify Brave API key and endpoint URL safety."""
        self._raw_api_key()
        validate_safe_url(self.settings.base_url)

    def search(  # noqa: PLR0913
        self,
        query: str,
        locale: str,
        market: str,
        since: date | None = None,
        *,
        domain: str | None = None,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        stage: str | None = None,
    ) -> list[ResearchRecord]:
        """Execute Brave News search, record unit cost in usage_ledger, and return records."""
        country, search_lang, ui_lang = map_brave_locale(locale, market)
        effective_query = build_site_search_query(domain, query)
        token = self._raw_api_key()

        query_params = urlencode(
            {
                "q": effective_query,
                "country": country,
                "search_lang": search_lang,
                "ui_lang": ui_lang,
                "count": str(self.settings.count),
            }
        )
        request_url = f"{self.settings.base_url}?{query_params}"

        start_time = time.perf_counter()
        resp = safe_request(
            "GET",
            request_url,
            timeout=self.settings.timeout_seconds,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": token,
            },
        )
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if resp.status_code >= 400:
            msg = f"Brave Search API request failed with HTTP {resp.status_code}"
            raise ValueError(msg)

        active_conn = conn if conn is not None else self._conn
        active_ws = workspace_id if workspace_id is not None else self._workspace_id
        active_run = run_id if run_id is not None else self._run_id
        active_stage = stage or self._stage
        if active_conn is not None and active_ws is not None:
            db.record_usage(
                active_conn,
                workspace_id=active_ws,
                run_id=active_run,
                stage=active_stage,
                kind="api",
                vendor="brave",
                model="news_search",
                units=Decimal("1.000"),
                cost_usd=self.settings.unit_cost_usd,
                latency_ms=latency_ms,
            )

        payload = json.loads(resp.text)
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []

        records: list[ResearchRecord] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            record = _item_to_record(item, since)
            if record is not None:
                records.append(record)
        return records
