"""Zero-key Google News RSS news search adapter implementing NewsSearchPort (ADR-031)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field

from engine.contracts import ResearchRecord
from engine.integrations.brave import build_site_search_query
from engine.integrations.registry import UnsupportedLocale
from engine.integrations.rss import ACCEPT_FEED_HEADER, parse_rss_records
from engine.safe_http import safe_request, validate_safe_url

GNEWS_LOCALE_MAP: dict[tuple[str, str], tuple[str, str, str]] = {
    ("pt-BR", "br"): ("pt-BR", "BR", "BR:pt-419"),
    ("en-US", "us"): ("en-US", "US", "US:en"),
}


def map_gnews_locale(locale: str, market: str) -> tuple[str, str, str]:
    """Map campaign (locale, market) to Google News RSS (hl, gl, ceid) parameters."""
    norm_locale = locale.strip()
    norm_market = market.strip().lower()
    mapped = GNEWS_LOCALE_MAP.get((norm_locale, norm_market))
    if mapped is not None:
        return mapped
    if norm_locale not in ("pt-BR", "en-US"):
        raise UnsupportedLocale("gnews_rss", locale)
    raise UnsupportedLocale("gnews_rss", locale, market)


class GoogleNewsRSSSearch:
    """Zero-key NewsSearchPort plugin backed by Google News RSS search endpoint."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        base_url: str = "https://news.google.com/rss/search"
        max_results: int = Field(default=20, ge=1, le=100)
        timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify Google News RSS endpoint URL against SSRF policy."""
        validate_safe_url(self.settings.base_url)

    def search(
        self,
        query: str,
        locale: str,
        market: str,
        since: date | None = None,
        *,
        domain: str | None = None,
    ) -> list[ResearchRecord]:
        """Execute a zero-key Google News RSS search query."""
        hl, gl, ceid = map_gnews_locale(locale, market)
        effective_query = build_site_search_query(domain, query)
        params = urlencode({"q": effective_query, "hl": hl, "gl": gl, "ceid": ceid})
        url = f"{self.settings.base_url}?{params}"

        resp = safe_request(
            "GET",
            url,
            timeout=self.settings.timeout_seconds,
            headers={"Accept": ACCEPT_FEED_HEADER},
        )
        if resp.status_code >= 400:
            msg = f"Google News RSS search failed with HTTP {resp.status_code}"
            raise ValueError(msg)

        since_dt = datetime.combine(since, time.min, tzinfo=UTC) if since is not None else None
        _title, records = parse_rss_records(
            resp.text,
            default_source="news.google.com",
            since=since_dt,
            limit=self.settings.max_results,
            prefer_entry_source=True,
        )
        return records
