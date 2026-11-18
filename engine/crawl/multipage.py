"""Multipage SEO issue detectors across crawled PageAuditReport sets (M6-02 D2)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from engine.contracts import PageAuditReport


def _find_duplicates(pairs: list[tuple[str | None, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, url in pairs:
        cleaned = (key or "").strip()
        if cleaned:
            grouped[cleaned].append(url)
    return {text: urls for text, urls in grouped.items() if len(urls) >= 2}


def analyze_multipage_issues(
    *,
    pages: list[PageAuditReport],
    start_url: str,
    sitemap_urls: Iterable[str],
    incoming_links: Mapping[str, int],
    redirect_chains: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Detect duplicate metadata, broken internal URLs, redirect chains, and orphan pages."""
    duplicate_titles = _find_duplicates([(p.title, p.url) for p in pages])
    duplicate_metas = _find_duplicates([(p.meta_description, p.url) for p in pages])
    broken_internal = [
        {"url": p.url, "status_code": p.status_code}
        for p in pages
        if p.status_code >= 400 or p.status_code == 0
    ]
    orphans = [
        url
        for url in dict.fromkeys(sitemap_urls)
        if url != start_url and incoming_links.get(url, 0) == 0
    ]
    return {
        "duplicate_titles": duplicate_titles,
        "duplicate_meta_descriptions": duplicate_metas,
        "broken_internal_links": broken_internal,
        "orphan_pages": orphans,
        "redirect_chains": list(redirect_chains or []),
    }
