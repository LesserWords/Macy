"""Link extraction, canonicalization, and bounded external broken-link checking."""

from __future__ import annotations

import urllib.error
import urllib.parse
from typing import Any

from engine import safe_http
from engine.audit.page_checks import ParsedHTML, parse_html
from engine.contracts import AuditCheckResult

IGNORED_SCHEMES: frozenset[str] = frozenset({"javascript", "mailto", "tel", "data", "sms"})


def _normalize_host(netloc: str) -> str:
    host = netloc.strip().lower()
    return host[4:] if host.startswith("www.") else host


def canonicalize_href(base_url: str, href: str) -> str | None:
    """Resolve relative links and strip fragments; return None for non-HTTP(S) links."""
    raw = href.strip()
    if not raw or raw.startswith("#"):
        return None
    joined = urllib.parse.urljoin(base_url, raw)
    parsed = urllib.parse.urlparse(joined)
    if parsed.scheme.lower() in IGNORED_SCHEMES or parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    clean = parsed._replace(fragment="")
    return urllib.parse.urlunparse(clean)


def extract_links(
    base_url: str,
    html_text: str,
    *,
    parsed: ParsedHTML | None = None,
) -> tuple[list[str], list[str]]:
    """Return deduplicated (internal_links, external_links) lists preserving order."""
    doc = parsed or parse_html(html_text)
    base_host = _normalize_host(urllib.parse.urlparse(base_url).netloc)
    internal: list[str] = []
    external: list[str] = []
    seen_int: set[str] = set()
    seen_ext: set[str] = set()

    for href in doc.anchors:
        resolved = canonicalize_href(base_url, href)
        if not resolved:
            continue
        link_host = _normalize_host(urllib.parse.urlparse(resolved).netloc)
        if link_host == base_host:
            if resolved not in seen_int:
                seen_int.add(resolved)
                internal.append(resolved)
        elif resolved not in seen_ext:
            seen_ext.add(resolved)
            external.append(resolved)
    return internal, external


def _probe_single_external(url: str, *, timeout: float) -> dict[str, Any] | None:
    try:
        resp = safe_http.safe_request("HEAD", url, timeout=timeout, max_bytes=65_536)
        if resp.status_code in {405, 501}:
            resp = safe_http.safe_request("GET", url, timeout=timeout, max_bytes=65_536)
        if resp.status_code >= 400:
            return {
                "url": url,
                "status_code": resp.status_code,
                "error": f"HTTP {resp.status_code}",
            }
        return None
    except (safe_http.SafeHTTPError, urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"url": url, "status_code": 0, "error": str(exc)}


def check_external_links(
    urls: list[str],
    *,
    max_urls: int = 100,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """Probe up to max_urls unique external URLs using HEAD with GET fallback."""
    broken: list[dict[str, Any]] = []
    for url in list(dict.fromkeys(urls))[:max_urls]:
        issue = _probe_single_external(url, timeout=timeout)
        if issue is not None:
            broken.append(issue)
    return broken


def evaluate_links_checks(
    internal_links: list[str],
    external_links: list[str],
    broken_external: list[dict[str, Any]] | None = None,
) -> list[AuditCheckResult]:
    """Build AuditCheckResult findings for internal navigation and external link health."""
    results: list[AuditCheckResult] = [
        AuditCheckResult(
            check="internal_links",
            category="links",
            status="pass" if internal_links else "warning",
            score=1.0 if internal_links else 0.5,
            detail=(
                f"Found {len(internal_links)} internal links."
                if internal_links
                else "No internal links discovered on page."
            ),
            recommendation=None if internal_links else "Add contextual internal links.",
        )
    ]
    if broken_external is not None and external_links:
        bad_count = len(broken_external)
        results.append(
            AuditCheckResult(
                check="external_links_health",
                category="links",
                status="error" if bad_count else "pass",
                score=0.0 if bad_count else 1.0,
                detail=(
                    f"{bad_count} broken external link(s) detected."
                    if bad_count
                    else f"Checked external links ({len(external_links)} total) are healthy."
                ),
                recommendation="Replace or remove broken outbound links." if bad_count else None,
                metadata={"broken": broken_external},
            )
        )
    return results
