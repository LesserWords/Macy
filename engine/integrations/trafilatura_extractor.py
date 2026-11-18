"""Trafilatura HTML extractor plugin with robots.txt and paywall handling (F07, ADR-003)."""

from __future__ import annotations

import re
import urllib.robotparser
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import trafilatura
from pydantic import BaseModel, ConfigDict, Field

from engine.config import get_user_agent
from engine.contracts import ResearchRecord
from engine.research import canonicalize_url, resolve_date_status
from engine.safe_http import SafeHTTPError, safe_request

PAYWALL_JSONLD_RE = re.compile(
    r'"isAccessibleForFree"\s*:\s*(?:false|"false"|"False")',
    re.IGNORECASE,
)
PAYWALL_CLASS_RE = re.compile(
    r'class\s*=\s*["\'][^"\']*\bpaywall\b[^"\']*["\']',
    re.IGNORECASE,
)


class _MetadataAndLinkParser(HTMLParser):
    """Lightweight HTML parser extracting meta dates/authors and outbound anchor hrefs."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.base_host = (urlsplit(base_url).hostname or "").lower()
        self.published_raw: str | None = None
        self.author: str | None = None
        self.site_name: str | None = None
        self.outbound_links: list[str] = []
        self._seen_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "").strip() for k, v in attrs}
        tag_lower = tag.lower()
        if tag_lower == "meta":
            self._handle_meta(attr_map)
        elif tag_lower == "time" and not self.published_raw:
            dt_val = attr_map.get("datetime", "")
            if dt_val:
                self.published_raw = dt_val
        elif tag_lower == "a":
            self._handle_anchor(attr_map.get("href", ""))

    def _handle_meta(self, attr_map: dict[str, str]) -> None:
        key = (attr_map.get("property") or attr_map.get("name") or "").lower()
        content = attr_map.get("content", "")
        if not content:
            return
        if (
            key in ("article:published_time", "og:published_time", "pubdate", "date")
            and not self.published_raw
        ):
            self.published_raw = content
        elif key in ("author", "article:author") and not self.author:
            self.author = content
        elif key == "og:site_name" and not self.site_name:
            self.site_name = content

    def _handle_anchor(self, href: str) -> None:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return
        joined = urljoin(self.base_url, href)
        try:
            canon = canonicalize_url(joined)
        except ValueError:
            return
        link_host = (urlsplit(canon).hostname or "").lower()
        if link_host == self.base_host or link_host.endswith("." + self.base_host):
            return
        if canon not in self._seen_links:
            self._seen_links.add(canon)
            self.outbound_links.append(canon)


def _parse_datetime_value(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _is_paywalled_html(html: str) -> bool:
    return bool(PAYWALL_JSONLD_RE.search(html) or PAYWALL_CLASS_RE.search(html))


def _check_robots_allowed(url: str, *, user_agent: str, timeout: float) -> bool:
    parsed = urlsplit(url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    try:
        resp = safe_request(
            "GET",
            robots_url,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
    except (SafeHTTPError, OSError):
        return True

    if resp.status_code != 200:
        return True

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(user_agent, url)


def _build_status_record(  # noqa: PLR0913
    *,
    url: str,
    canonical: str,
    source_name: str,
    headline: str,
    fetch_status: str,
    http_status: int | None,
) -> ResearchRecord:
    return ResearchRecord(
        headline=headline,
        source=source_name,
        url=url,
        canonical_url=canonical,
        published_at=None,
        summary=headline,
        verification_status="unverified",
        raw={
            "fetch_status": fetch_status,
            "http_status": http_status,
            "extracted_text": None,
            "author": None,
            "site_name": source_name,
            "outbound_links": [],
            "date_status": "unverified",
        },
    )


class TrafilaturaExtractor:
    """ExtractorPort plugin extracting clean article text, metadata, and outbound links."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        respect_robots_txt: bool = True
        include_links: bool = True
        timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
        user_agent: str = Field(default_factory=get_user_agent)

    extractor_settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.extractor_settings = settings or self.Settings()

    @property
    def settings(self) -> Settings:
        return self.extractor_settings

    def healthcheck(self) -> None:
        """Verify extractor configuration."""
        if not self.extractor_settings.user_agent.strip():
            msg = "Trafilatura user_agent must not be empty"
            raise ValueError(msg)

    def _fetch_html(self, url: str, canonical: str, host: str) -> tuple[str, int] | ResearchRecord:
        if self.extractor_settings.respect_robots_txt and not _check_robots_allowed(
            url,
            user_agent=self.extractor_settings.user_agent,
            timeout=self.extractor_settings.timeout_seconds,
        ):
            return _build_status_record(
                url=url,
                canonical=canonical,
                source_name=host,
                headline=canonical,
                fetch_status="blocked",
                http_status=None,
            )

        try:
            resp = safe_request(
                "GET",
                url,
                timeout=self.extractor_settings.timeout_seconds,
                headers={"User-Agent": self.extractor_settings.user_agent},
            )
        except (SafeHTTPError, OSError):
            return _build_status_record(
                url=url,
                canonical=canonical,
                source_name=host,
                headline=canonical,
                fetch_status="http_error",
                http_status=None,
            )

        if resp.status_code >= 400:
            status_kind = "paywalled" if resp.status_code == 402 else "http_error"
            return _build_status_record(
                url=url,
                canonical=canonical,
                source_name=host,
                headline=canonical,
                fetch_status=status_kind,
                http_status=resp.status_code,
            )
        return resp.text, resp.status_code

    def extract(self, url: str, html: str | None = None) -> ResearchRecord:
        """Extract clean text, date, author, and outbound links from URL or HTML."""
        canonical = canonicalize_url(url)
        host = (urlsplit(canonical).hostname or "unknown").lower()
        http_status: int | None = 200

        if html is None:
            fetch_res = self._fetch_html(url, canonical, host)
            if isinstance(fetch_res, ResearchRecord):
                return fetch_res
            html_content, http_status = fetch_res
        else:
            html_content = html

        if _is_paywalled_html(html_content):
            return _build_status_record(
                url=url,
                canonical=canonical,
                source_name=host,
                headline=canonical,
                fetch_status="paywalled",
                http_status=http_status,
            )

        parser = _MetadataAndLinkParser(base_url=url)
        parser.feed(html_content)

        doc: Any = trafilatura.bare_extraction(
            html_content,
            url=url,
            include_links=self.extractor_settings.include_links,
            with_metadata=True,
        )
        extracted_text = (getattr(doc, "text", None) or "").strip() if doc is not None else ""
        if not extracted_text:
            return _build_status_record(
                url=url,
                canonical=canonical,
                source_name=host,
                headline=canonical,
                fetch_status="extraction_failed",
                http_status=http_status,
            )

        title = (getattr(doc, "title", None) or canonical).strip()
        author = parser.author or getattr(doc, "author", None)
        site_name = parser.site_name or getattr(doc, "sitename", None) or host
        explicit_dt = _parse_datetime_value(parser.published_raw or getattr(doc, "date", None))
        resolved_dt, date_status = resolve_date_status(explicit_dt, url=canonical)

        return ResearchRecord(
            headline=title,
            source=str(site_name),
            url=url,
            canonical_url=canonical,
            published_at=resolved_dt,
            event_date=resolved_dt.date() if resolved_dt else None,
            summary=extracted_text[:500],
            source_type="news",
            verification_status=date_status,
            raw={
                "fetch_status": "ok",
                "http_status": http_status,
                "extracted_text": extracted_text,
                "author": author,
                "site_name": str(site_name),
                "outbound_links": parser.outbound_links,
                "date_status": date_status,
            },
        )
