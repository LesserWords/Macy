"""Single-URL deterministic SEO + GEO audit orchestration (M6-02, ADR-028)."""

from __future__ import annotations

import time
from collections.abc import Mapping

from engine import safe_http
from engine.audit.ai_checks import run_ai_checks
from engine.audit.links import check_external_links, evaluate_links_checks, extract_links
from engine.audit.page_checks import run_page_checks
from engine.audit.scoring import score_audit_checks
from engine.audit.toggles import run_optional_toggles
from engine.contracts import AuditCheckResult, CrawlConfig, PageAuditReport
from engine.integrations.ports import BrowserAuditPort, PageSpeedPort


def audit_url(  # noqa: PLR0913
    url: str,
    *,
    html_text: str | None = None,
    headers: Mapping[str, str] | None = None,
    status_code: int = 200,
    ttfb_ms: float | None = None,
    max_bytes: int = 3_000_000,
    timeout: float = 10.0,
    check_external: bool = False,
    external_link_cap: int = 100,
    robots_txt: str | None = None,
    has_llms_txt: bool | None = None,
    crawl_config: CrawlConfig | None = None,
    browser_auditor: BrowserAuditPort | None = None,
    pagespeed_client: PageSpeedPort | None = None,
    extra_checks: list[AuditCheckResult] | None = None,
) -> PageAuditReport:
    """Fetch a public URL (or analyze pre-fetched HTML) and return a scored PageAuditReport."""
    resolved_html = html_text
    resolved_headers = dict(headers or {})
    resolved_status = status_code
    resolved_ttfb = ttfb_ms if ttfb_ms is not None else 0.0

    if resolved_html is None:
        started = time.perf_counter()
        resp = safe_http.safe_request(
            "GET",
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            headers={"User-Agent": "MacyAuditBot/1.0 (+https://lesserwords.github.io/Macy)"},
        )
        resolved_ttfb = round((time.perf_counter() - started) * 1000.0, 1)
        resolved_html = resp.text
        resolved_headers = dict(resp.headers)
        resolved_status = resp.status_code

    byte_weight = len(resolved_html.encode("utf-8"))
    page_bundle = run_page_checks(
        url=url,
        html_text=resolved_html,
        headers=resolved_headers,
        ttfb_ms=resolved_ttfb,
        byte_weight=byte_weight,
    )
    ai_checks, ld_types = run_ai_checks(
        url,
        resolved_html,
        parsed=page_bundle.parsed,
        robots_txt=robots_txt,
        has_llms_txt=has_llms_txt,
        timeout=min(timeout, 5.0),
    )
    internal_links, external_links = extract_links(
        url,
        resolved_html,
        parsed=page_bundle.parsed,
    )
    broken_ext = (
        check_external_links(external_links, max_urls=external_link_cap, timeout=min(timeout, 5.0))
        if check_external and external_links
        else None
    )
    link_checks = evaluate_links_checks(internal_links, external_links, broken_ext)
    toggle_checks = run_optional_toggles(
        url,
        config=crawl_config,
        browser_auditor=browser_auditor,
        pagespeed_client=pagespeed_client,
    )

    all_checks = [
        *page_bundle.checks,
        *ai_checks,
        *link_checks,
        *toggle_checks,
        *(extra_checks or ()),
    ]
    overall, grade, categories = score_audit_checks(all_checks)
    recommendations = [c.recommendation for c in all_checks if c.recommendation]

    return PageAuditReport(
        url=url,
        final_url=url,
        status_code=resolved_status,
        title=page_bundle.title,
        meta_description=page_bundle.meta_description,
        ttfb_ms=resolved_ttfb,
        byte_weight=byte_weight,
        word_count=page_bundle.word_count,
        overall_score=overall,
        grade=grade,
        categories=categories,
        checks=all_checks,
        recommendations=recommendations,
        internal_links=internal_links,
        external_links=external_links,
        json_ld_types=ld_types,
    )


__all__ = ["audit_url"]
