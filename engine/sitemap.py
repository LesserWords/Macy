"""Sitemap parsing, SSRF-guarded fetching, and title extraction (F08 AC3)."""

from __future__ import annotations

import html
import logging
import os
import re
import urllib.parse
import xml.etree.ElementTree as std_ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as ET

from engine import config, safe_http

logger = logging.getLogger(__name__)


def slug_to_title(url: str) -> str:
    """Generate human-readable title from the URL slug as fallback."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return parsed.netloc or "Article"
    slug = path.split("/")[-1]
    # Remove file extension if present (e.g., .html)
    slug = re.sub(r"\.[a-zA-Z0-9]+$", "", slug)
    words = [w for w in re.split(r"[-_]+", slug) if w]
    if not words:
        return "Article"
    # Capitalize words preserving numbers and common capitalization
    return " ".join(words).strip().capitalize()


def clean_page_title(raw_title: str) -> str:
    """Unescape HTML entities and strip site-branding suffixes from page titles."""
    unescaped = html.unescape(raw_title).strip()
    # Strip site name suffixes: 'Title &ndash; Site Name', 'Title | Site', 'Title - Site'
    cleaned = re.split(r"\s+[–\-|—]\s+", unescaped)[0].strip()
    return cleaned if cleaned else unescaped


def extract_title_from_html(html_content: str) -> str | None:
    """Extract article title from OpenGraph metadata or <title> tag in HTML."""
    # 1. Check og:title
    og_match = re.search(
        r'<meta\s+[^>]*property=["\']og:title["\']\s+content=["\'](.*?)["\']',
        html_content,
        re.IGNORECASE,
    )
    if not og_match:
        og_match = re.search(
            r'<meta\s+[^>]*content=["\'](.*?)["\']\s+property=["\']og:title["\']',
            html_content,
            re.IGNORECASE,
        )
    if og_match and og_match.group(1).strip():
        return clean_page_title(og_match.group(1))

    # 2. Check <title> tag
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.IGNORECASE | re.DOTALL)
    if title_match and title_match.group(1).strip():
        return clean_page_title(title_match.group(1))

    # 3. Check <h1> tag
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html_content, re.IGNORECASE | re.DOTALL)
    if h1_match and h1_match.group(1).strip():
        # Remove inner tags from h1 if any
        h1_text = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
        if h1_text:
            return clean_page_title(h1_text)

    return None


def parse_iso_datetime(date_str: str | None) -> datetime | None:
    """Parse ISO 8601 date or datetime string into UTC datetime."""
    if not date_str:
        return None
    val = date_str.strip()
    try:
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _load_content(
    url_or_path: str,
    *,
    timeout: float = 10.0,
    is_production: bool | None = None,
) -> bytes:
    """Load content safely from HTTP/HTTPS URL or local fixture file."""
    if url_or_path.startswith("file://"):
        local_path = url_or_path[7:]
        return Path(local_path).read_bytes()
    if os.path.exists(url_or_path):
        return Path(url_or_path).read_bytes()

    safe_http.validate_safe_url(url_or_path, is_production=is_production)
    resp = safe_http.safe_request(
        "GET",
        url_or_path,
        headers={"User-Agent": config.get_user_agent()},
        timeout=timeout,
        is_production=is_production,
    )
    if resp.status_code >= 400:
        msg = f"Failed to fetch sitemap {url_or_path}: HTTP {resp.status_code}"
        raise RuntimeError(msg)
    return resp.content


def _extract_child_text(parent: std_ET.Element, suffix: str) -> str | None:
    for child in parent:
        if child.tag.endswith(suffix) and child.text:
            text = child.text.strip()
            if text:
                return str(text)
    return None


def _parse_urlset(root: std_ET.Element) -> list[dict[str, Any]]:
    """Parse <urlset> entries into raw dicts."""
    results: list[dict[str, Any]] = []
    for el in root.iter():
        if el.tag.endswith("url"):
            loc = _extract_child_text(el, "loc")
            if loc:
                results.append({"loc": loc, "lastmod": _extract_child_text(el, "lastmod")})
    return results


def _parse_sitemapindex(root: std_ET.Element) -> list[str]:
    """Parse <sitemapindex> child sitemap URLs."""
    sub_urls: list[str] = []
    for el in root.iter():
        if el.tag.endswith("sitemap"):
            loc = _extract_child_text(el, "loc")
            if loc:
                sub_urls.append(loc)
    return sub_urls


def _expand_sitemapindex(  # noqa: PLR0913
    sub_sitemaps: list[str],
    *,
    timeout: float,
    is_production: bool | None,
    fetch_titles: bool,
    max_depth: int,
) -> list[dict[str, Any]]:
    aggregated: list[dict[str, Any]] = []
    for sub_url in sub_sitemaps:
        try:
            entries = fetch_sitemap_articles(
                sub_url,
                timeout=timeout,
                is_production=is_production,
                fetch_titles=fetch_titles,
                max_depth=max_depth - 1,
            )
            aggregated.extend(entries)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to expand sub-sitemap %s: %s", sub_url, exc)
    return aggregated


def _resolve_article_title(
    loc: str,
    *,
    timeout: float,
    is_production: bool | None,
    fetch_titles: bool,
) -> str:
    fallback = slug_to_title(loc)
    if not fetch_titles or not loc.startswith(("http://", "https://")):
        return fallback
    try:
        resp = safe_http.safe_request(
            "GET",
            loc,
            headers={"User-Agent": config.get_user_agent()},
            timeout=min(timeout, 5.0),
            is_production=is_production,
        )
        if 200 <= resp.status_code < 300:
            extracted = extract_title_from_html(resp.text)
            if extracted:
                return extracted
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Could not fetch page title for %s, falling back to slug: %s",
            loc,
            exc,
        )
    return fallback


def fetch_sitemap_articles(  # noqa: PLR0913
    sitemap_url_or_path: str,
    *,
    timeout: float = 10.0,
    is_production: bool | None = None,
    fetch_titles: bool = False,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """Fetch, parse sitemap (expanding index files), and extract article metadata (F08 AC3).

    Returns list of article dicts:
        url: article URL
        title: article title (extracted from HTML if fetch_titles=True, else slug)
        published_at: UTC datetime if lastmod present, else None
        raw: metadata dict
    """
    raw_content = _load_content(sitemap_url_or_path, timeout=timeout, is_production=is_production)
    try:
        root = ET.fromstring(raw_content)
    except ET.ParseError as err:
        msg = f"Invalid sitemap XML at {sitemap_url_or_path}: {err}"
        raise ValueError(msg) from err

    tag = root.tag.lower()
    if tag.endswith("sitemapindex") and max_depth > 0:
        sub_sitemaps = _parse_sitemapindex(root)
        return _expand_sitemapindex(
            sub_sitemaps,
            timeout=timeout,
            is_production=is_production,
            fetch_titles=fetch_titles,
            max_depth=max_depth,
        )

    url_entries = _parse_urlset(root)
    return [
        {
            "url": entry["loc"],
            "title": _resolve_article_title(
                entry["loc"],
                timeout=timeout,
                is_production=is_production,
                fetch_titles=fetch_titles,
            ),
            "published_at": parse_iso_datetime(entry["lastmod"]),
            "raw": {"lastmod": entry["lastmod"]},
        }
        for entry in url_entries
    ]
