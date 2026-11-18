"""GDELT DOC 2.0 NewsSearch plugin trial for M1-11 (ADR-003, ADR-025, F07).

Implements NewsSearchPort against GDELT DOC 2.0 Article Search API
with strict 1 request / 5 seconds rate limiting.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus, urlsplit

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from engine import db
from engine.contracts import ResearchRecord
from engine.integrations.registry import UnsupportedLocale
from engine.research import canonicalize_url, resolve_date_status
from engine.safe_http import safe_request, validate_safe_url

GDELT_LOCALE_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("pt-BR", "br"): ("por", "Brazil"),
    ("en-US", "us"): ("eng", "US"),
}


def map_gdelt_locale(locale: str, market: str) -> tuple[str, str]:
    """Map campaign (locale, market) to GDELT (sourcelang, sourcecountry)."""
    norm_locale = locale.strip()
    norm_market = market.strip().lower()
    mapped = GDELT_LOCALE_MAP.get((norm_locale, norm_market))
    if mapped is not None:
        return mapped
    if norm_locale not in ("pt-BR", "en-US"):
        raise UnsupportedLocale("gdelt", locale)
    raise UnsupportedLocale("gdelt", locale, market)


class RateLimiter:
    """Thread-safe rate limiter enforcing a minimum interval between requests."""

    def __init__(self, interval_seconds: float = 5.0) -> None:
        self.interval = interval_seconds
        self.last_call: float = 0.0
        self._lock = threading.Lock()

    def wait_if_needed(self) -> float:
        """Wait if interval has not elapsed; returns waited seconds."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_call
            wait_time = max(0.0, self.interval - elapsed)
            if wait_time > 0:
                time.sleep(wait_time)
            self.last_call = time.monotonic()
            return wait_time


def _parse_gdelt_seendate(raw_date: Any) -> datetime | None:
    """Parse GDELT seendate format (YYYYMMDDTHHMMSSZ, YYYYMMDDTHHMMSS, or YYYYMMDD)."""
    if not raw_date or not str(raw_date).strip():
        return None
    cleaned = str(raw_date).strip()
    try:
        if "T" in cleaned:
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        if len(cleaned) == 8 and cleaned.isdigit():
            # YYYYMMDD
            year = int(cleaned[:4])
            month = int(cleaned[4:6])
            day = int(cleaned[6:8])
            return datetime(year, month, day, 0, 0, 0, tzinfo=UTC)
        return None
    except (ValueError, IndexError):
        return None


def _gdelt_item_to_record(item: dict[str, Any], since: date | None) -> ResearchRecord | None:
    raw_url = str(item.get("url") or "").strip()
    if not raw_url:
        return None

    raw_dt = _parse_gdelt_seendate(item.get("seendate"))
    resolved_dt, date_status = resolve_date_status(raw_dt, url=raw_url)
    if since is not None and resolved_dt is not None and resolved_dt.date() < since:
        return None

    canonical = canonicalize_url(raw_url)
    title = str(item.get("title") or canonical).strip()
    source_domain = str(item.get("domain") or urlsplit(canonical).hostname or "gdelt").strip()

    return ResearchRecord(
        headline=title,
        source=source_domain,
        url=raw_url,
        canonical_url=canonical,
        published_at=resolved_dt,
        event_date=resolved_dt.date() if resolved_dt else since,
        summary=title,
        source_type="news",
        verification_status=date_status,
    )


class GDELTNewsSearch:
    """NewsSearchPort candidate implementation querying GDELT DOC 2.0 API."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
        timeout_seconds: float = Field(default=15.0, gt=0.0, le=60.0)
        max_records: int = Field(default=75, ge=1, le=250)
        rate_limit_seconds: float = Field(default=5.0, ge=0.0, le=60.0)
        unit_cost_usd: Decimal = Field(default=Decimal("0.000000"), ge=Decimal("0"))

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
        self.rate_limiter = RateLimiter(interval_seconds=self.settings.rate_limit_seconds)

    def healthcheck(self) -> None:
        """Verify endpoint URL safety (GDELT requires no API key)."""
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
        """Execute GDELT DOC 2.0 search respecting 1 request / 5 seconds rate limit."""
        lang, _ = map_gdelt_locale(locale, market)

        # Build GDELT query terms
        terms: list[str] = []
        if domain:
            terms.append(f"domain:{domain.strip().lower()}")
        if query.strip():
            terms.append(query.strip())
        terms.append(f"sourcelang:{lang}")
        combined_query = " ".join(terms)

        # Enforce rate limit (1 req / 5 s)
        self.rate_limiter.wait_if_needed()

        encoded_query = quote_plus(combined_query)
        request_url = (
            f"{self.settings.base_url}?query={encoded_query}&mode=artlist"
            f"&format=json&timespan=14d&maxrecords={self.settings.max_records}"
        )

        start_time = time.perf_counter()
        resp = safe_request(
            "GET",
            request_url,
            timeout=self.settings.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "Macy-ContentEngine/1.0",
            },
        )
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if resp.status_code >= 400:
            msg = f"GDELT DOC 2.0 request failed with HTTP {resp.status_code}"
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
                vendor="gdelt",
                model="doc2",
                units=Decimal("1.000"),
                cost_usd=self.settings.unit_cost_usd,
                latency_ms=latency_ms,
            )

        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            msg = f"GDELT returned non-JSON response: {exc}"
            raise ValueError(msg) from exc

        raw_articles = payload.get("articles", []) if isinstance(payload, dict) else []

        records: list[ResearchRecord] = []
        for item in raw_articles:
            if not isinstance(item, dict):
                continue
            rec = _gdelt_item_to_record(item, since)
            if rec is not None:
                records.append(rec)
        return records
