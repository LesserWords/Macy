"""Stateless website metadata and sitemap discovery for URL auto-seed onboarding (ADR-031)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from engine.audit.page_checks import parse_html
from engine.contracts import BrandProfile
from engine.safe_http import SafeHTTPError, safe_request, validate_safe_url


def _discover_sitemap_url(origin: str, *, timeout: float) -> str:
    robots_url = f"{origin}/robots.txt"
    try:
        resp = safe_request("GET", robots_url, timeout=timeout)
    except SafeHTTPError:
        return f"{origin}/sitemap.xml"

    if resp.status_code < 400:
        for line in resp.text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("sitemap:"):
                candidate = stripped.split(":", 1)[1].strip()
                parsed = urlsplit(candidate)
                if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                    return candidate
    return f"{origin}/sitemap.xml"


def extract_site_seed(site_url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch a homepage URL safely and derive BrandProfile, source config, and sitemap URL."""
    clean_url = site_url.strip()
    try:
        validate_safe_url(clean_url)
    except SafeHTTPError as exc:
        msg = f"Unsafe site URL '{site_url}': {exc}"
        raise ValueError(msg) from exc

    resp = safe_request("GET", clean_url, timeout=timeout)
    if resp.status_code >= 400:
        msg = f"Website request failed with HTTP {resp.status_code} for {clean_url}"
        raise ValueError(msg)

    parsed_url = urlsplit(clean_url)
    domain = (parsed_url.hostname or "").strip().lower()
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    html = parse_html(resp.text)

    title = html.title.strip() or domain
    meta_desc = (
        html.meta_name.get("description") or html.meta_prop.get("og:description") or ""
    ).strip()
    first_h1 = html.h1_texts[0].strip() if html.h1_texts else ""

    voice = meta_desc or first_h1 or title
    positioning = first_h1 or title
    cap_items = [item.strip() for item in html.h1_texts if item.strip()][:5] or [title]
    locale = "pt-BR" if html.html_lang.lower().startswith("pt") else "en-US"

    brand_profile = BrandProfile(
        voice=voice,
        positioning=positioning,
        capabilities=cap_items,
    )
    return {
        "brand_profile": brand_profile,
        "source": {
            "name": title[:80],
            "domain": domain,
            "url": clean_url,
            "locale": locale,
        },
        "sitemap_url": _discover_sitemap_url(origin, timeout=timeout),
    }
