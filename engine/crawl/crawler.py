"""Bounded BFS site crawler seeded from root URL and XML sitemap (M6-02 D2, ADR-028)."""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from engine import safe_http, sitemap
from engine.audit import audit_url
from engine.audit.ai_checks import fetch_robots_and_llms, is_url_allowed_by_robots
from engine.audit.links import check_external_links
from engine.audit.scoring import score_to_grade
from engine.contracts import CrawlConfig, PageAuditReport, SiteAuditReport
from engine.crawl.multipage import analyze_multipage_issues

logger = logging.getLogger(__name__)
USER_AGENT = "MacyAuditBot/1.0 (+https://lesserwords.github.io/Macy)"


def normalize_crawl_url(raw_url: str, *, canonical_host: str | None = None) -> str | None:
    """Normalize HTTP(S) URL by stripping fragments, utm_* params, and optional www. prefix."""
    parsed = urllib.parse.urlparse(raw_url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    if canonical_host is not None:
        canon_bare = canonical_host.lower().removeprefix("www.")
        if host.removeprefix("www.") == canon_bare:
            host = canonical_host.lower()

    kept_params = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    query = urllib.parse.urlencode(kept_params)
    path = parsed.path or "/"
    return urllib.parse.urlunparse((scheme, host, path, "", query, ""))


def _discover_sitemap_urls(
    origin: str,
    *,
    canonical_host: str,
    explicit_sitemap: str | None,
    timeout: float,
) -> list[str]:
    target = explicit_sitemap or f"{origin}/sitemap.xml"
    try:
        articles = sitemap.fetch_sitemap_articles(
            target,
            timeout=min(timeout, 5.0),
            fetch_titles=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Sitemap discovery skipped for %s: %s", target, exc)
        return []

    discovered: list[str] = []
    for item in articles:
        norm = normalize_crawl_url(str(item.get("url", "")), canonical_host=canonical_host)
        if norm is not None:
            discovered.append(norm)
    return list(dict.fromkeys(discovered))


def _fetch_page_with_redirects(
    url: str,
    *,
    canonical_host: str,
    cfg: CrawlConfig,
    timeout: float,
) -> tuple[str, safe_http.SafeResponse, float, dict[str, Any] | None]:
    current = url
    hops: list[str] = []
    for _ in range(4):
        started = time.perf_counter()
        resp = safe_http.safe_request(
            "GET",
            current,
            timeout=timeout,
            max_bytes=cfg.max_bytes,
            headers={"User-Agent": USER_AGENT},
        )
        ttfb_ms = round((time.perf_counter() - started) * 1000.0, 1)
        loc = resp.headers.get("Location") or resp.headers.get("location")
        if resp.status_code not in {301, 302, 307, 308} or not loc:
            chain = {"url": url, "hops": hops} if len(hops) >= 2 else None
            return current, resp, ttfb_ms, chain
        next_url = normalize_crawl_url(
            urllib.parse.urljoin(current, loc), canonical_host=canonical_host
        )
        if not next_url or next_url in hops:
            return current, resp, ttfb_ms, {"url": url, "hops": [*hops, loc], "loop": True}
        hops.append(next_url)
        current = next_url
    return current, resp, ttfb_ms, {"url": url, "hops": hops}


def _crawl_single_url(  # noqa: PLR0913
    current: str,
    *,
    canonical_host: str,
    cfg: CrawlConfig,
    robots_txt: str,
    has_llms_txt: bool,
    timeout: float,
    is_seed: bool,
) -> tuple[PageAuditReport | None, dict[str, Any] | None]:
    try:
        final_url, resp, ttfb_ms, chain = _fetch_page_with_redirects(
            current,
            canonical_host=canonical_host,
            cfg=cfg,
            timeout=timeout,
        )
    except (safe_http.BlockedIPError, safe_http.UnsafeURLError):
        if is_seed:
            raise
        return None, None
    except (safe_http.SafeHTTPError, urllib.error.URLError, OSError, TimeoutError):
        if is_seed:
            raise
        return None, None

    report = audit_url(
        final_url,
        html_text=resp.text,
        headers=resp.headers,
        status_code=resp.status_code,
        ttfb_ms=ttfb_ms,
        max_bytes=cfg.max_bytes,
        timeout=timeout,
        check_external=False,
        robots_txt=robots_txt,
        has_llms_txt=has_llms_txt,
    )
    return report, chain


@dataclass
class _CrawlState:
    queue: deque[str]
    queued: set[str]
    visited: set[str] = field(default_factory=set)
    incoming_links: Counter[str] = field(default_factory=Counter)
    pages: list[PageAuditReport] = field(default_factory=list)
    redirect_chains: list[dict[str, Any]] = field(default_factory=list)
    external_urls: list[str] = field(default_factory=list)


def _enqueue_internal_links(
    links: list[str],
    state: _CrawlState,
    *,
    canonical_host: str,
) -> None:
    for link in links:
        norm = normalize_crawl_url(link, canonical_host=canonical_host)
        if norm is None:
            continue
        state.incoming_links[norm] += 1
        if norm not in state.visited and norm not in state.queued:
            state.queue.append(norm)
            state.queued.add(norm)


def _run_bfs_loop(  # noqa: PLR0913
    state: _CrawlState,
    *,
    seed: str,
    canonical_host: str,
    cfg: CrawlConfig,
    robots_txt: str,
    has_llms_txt: bool,
    max_pages: int,
    timeout: float,
) -> None:
    while state.queue and len(state.pages) < max_pages:
        current = state.queue.popleft()
        if current in state.visited:
            continue
        state.visited.add(current)
        path = urllib.parse.urlparse(current).path or "/"
        if current != seed and not is_url_allowed_by_robots(robots_txt, path):
            continue

        page, chain = _crawl_single_url(
            current,
            canonical_host=canonical_host,
            cfg=cfg,
            robots_txt=robots_txt,
            has_llms_txt=has_llms_txt,
            timeout=timeout,
            is_seed=(current == seed),
        )
        if chain is not None:
            state.redirect_chains.append(chain)
        if page is None:
            continue
        state.pages.append(page)
        state.external_urls.extend(page.external_links)
        _enqueue_internal_links(
            page.internal_links,
            state,
            canonical_host=canonical_host,
        )


def crawl_site(
    start_url: str,
    *,
    config: CrawlConfig | None = None,
    sitemap_url: str | None = None,
    timeout: float = 10.0,
) -> SiteAuditReport:
    """Run bounded BFS crawl adhering to robots.txt, safe_http SSRF guard, and plan caps."""
    cfg = config or CrawlConfig()
    parsed_start = urllib.parse.urlparse(start_url)
    canonical_host = parsed_start.netloc.lower().removeprefix("www.")
    seed = normalize_crawl_url(start_url, canonical_host=canonical_host)
    if seed is None:
        msg = f"Invalid crawl start URL: {start_url}"
        raise ValueError(msg)

    seed_parsed = urllib.parse.urlparse(seed)
    origin = f"{seed_parsed.scheme}://{seed_parsed.netloc}"
    robots_txt, has_llms_txt = fetch_robots_and_llms(seed, timeout=min(timeout, 5.0))
    sitemap_urls = _discover_sitemap_urls(
        origin, canonical_host=canonical_host, explicit_sitemap=sitemap_url, timeout=timeout
    )

    max_pages = cfg.effective_max_pages
    initial_q: deque[str] = deque([seed, *sitemap_urls])
    state = _CrawlState(queue=initial_q, queued=set(initial_q))
    _run_bfs_loop(
        state,
        seed=seed,
        canonical_host=canonical_host,
        cfg=cfg,
        robots_txt=robots_txt,
        has_llms_txt=has_llms_txt,
        max_pages=max_pages,
        timeout=timeout,
    )

    broken_ext = (
        check_external_links(
            state.external_urls,
            max_urls=cfg.external_link_cap,
            timeout=min(timeout, 5.0),
        )
        if state.external_urls and cfg.external_link_cap > 0
        else []
    )
    multi = analyze_multipage_issues(
        pages=state.pages,
        start_url=seed,
        sitemap_urls=sitemap_urls,
        incoming_links=state.incoming_links,
        redirect_chains=state.redirect_chains,
    )
    overall = (
        round(sum(p.overall_score for p in state.pages) / len(state.pages), 1)
        if state.pages
        else 0.0
    )
    return SiteAuditReport(
        start_url=seed,
        pages_crawled=len(state.pages),
        max_pages=max_pages,
        overall_score=overall,
        grade=score_to_grade(overall),
        pages=state.pages,
        broken_internal_links=multi["broken_internal_links"],
        broken_external_links=broken_ext,
        redirect_chains=multi["redirect_chains"],
        duplicate_titles=multi["duplicate_titles"],
        duplicate_meta_descriptions=multi["duplicate_meta_descriptions"],
        orphan_pages=multi["orphan_pages"],
    )
