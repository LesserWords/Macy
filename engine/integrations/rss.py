"""RSS and Atom feed source plugin using feedparser and safe_http (F07, ADR-003, ADR-005)."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import feedparser
from pydantic import BaseModel, ConfigDict, Field

from engine.config import get_user_agent
from engine.contracts import ResearchRecord
from engine.research import canonicalize_url, resolve_date_status
from engine.safe_http import safe_request

ACCEPT_FEED_HEADER = (
    "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
)


def _extract_entry_datetime(entry: Any) -> datetime | None:
    st_val = entry.get("published_parsed") or entry.get("updated_parsed")
    if st_val is not None:
        try:
            ts = calendar.timegm(st_val)
            return datetime.fromtimestamp(ts, tz=UTC)
        except (TypeError, ValueError, OverflowError):
            pass
    raw_str = entry.get("published") or entry.get("updated")
    if not raw_str or not str(raw_str).strip():
        return None
    try:
        iso_dt = datetime.fromisoformat(str(raw_str).strip().replace("Z", "+00:00"))
        return iso_dt.astimezone(UTC) if iso_dt.tzinfo else iso_dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _resolve_entry_source(entry: Any, default_source: str, *, prefer_entry_source: bool) -> str:
    if prefer_entry_source:
        src = entry.get("source")
        if isinstance(src, dict) and src.get("title"):
            return str(src["title"]).strip()
    return default_source


def _entry_to_research_record(
    entry: Any,
    *,
    feed_title: str,
    since_utc: datetime | None,
    prefer_entry_source: bool,
) -> ResearchRecord | None:
    link = str(entry.get("link") or "").strip()
    if not link:
        return None
    raw_dt = _extract_entry_datetime(entry)
    resolved_dt, date_status = resolve_date_status(raw_dt, url=link)
    if since_utc is not None and resolved_dt is not None and resolved_dt < since_utc:
        return None

    canonical = canonicalize_url(link)
    title = str(entry.get("title") or canonical).strip()
    summary = str(entry.get("summary") or entry.get("description") or title).strip()
    source_name = _resolve_entry_source(entry, feed_title, prefer_entry_source=prefer_entry_source)
    return ResearchRecord(
        headline=title,
        source=source_name,
        url=link,
        canonical_url=canonical,
        published_at=resolved_dt,
        event_date=resolved_dt.date() if resolved_dt else None,
        summary=summary,
        source_type="news",
        verification_status=date_status,
    )


def parse_rss_records(
    feed_text: str,
    *,
    default_source: str,
    since: datetime | None = None,
    limit: int | None = None,
    prefer_entry_source: bool = False,
) -> tuple[str, list[ResearchRecord]]:
    """Parse RSS/Atom XML into deduplicated ResearchRecord items."""
    parsed = feedparser.parse(feed_text)
    feed_meta = getattr(parsed, "feed", {})
    feed_title = str(feed_meta.get("title") or default_source).strip()

    since_utc: datetime | None = None
    if since is not None:
        since_utc = since.astimezone(UTC) if since.tzinfo else since.replace(tzinfo=UTC)

    records: list[ResearchRecord] = []
    for entry in getattr(parsed, "entries", []):
        record = _entry_to_research_record(
            entry,
            feed_title=feed_title,
            since_utc=since_utc,
            prefer_entry_source=prefer_entry_source,
        )
        if record is None:
            continue
        records.append(record)
        if limit is not None and len(records) >= limit:
            break

    return feed_title, records


class RSSFeedSource:
    """FeedSourcePort plugin polling RSS/Atom feeds via feedparser."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        user_agent: str = Field(default_factory=get_user_agent)
        timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify RSS feed adapter configuration."""
        if not self.settings.user_agent.strip():
            msg = "RSS user_agent must not be empty"
            raise ValueError(msg)

    def fetch_feed(
        self,
        url: str,
        since: datetime | None = None,
    ) -> list[ResearchRecord]:
        """Fetch RSS/Atom XML via safe_http and parse entries within the window."""
        resp = safe_request(
            "GET",
            url,
            timeout=self.settings.timeout_seconds,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": ACCEPT_FEED_HEADER,
            },
        )
        if resp.status_code >= 400:
            msg = f"Feed request failed with HTTP {resp.status_code} for {url}"
            raise ValueError(msg)

        default_source = urlsplit(url).hostname or "rss"
        _title, records = parse_rss_records(resp.text, default_source=default_source, since=since)
        return records
