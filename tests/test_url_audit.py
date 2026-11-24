"""Tests for single-URL SEO + GEO audit, scoring, AI checks, and links (M6-02 D1)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.error import URLError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.contracts import AuditCheckResult, PageAuditReport
from engine.safe_http import BlockedIPError, SafeResponse


def _sample_html(  # noqa: PLR0913
    *,
    title: str = "Guia Completo de Gestao para Oticas Modernas",
    description: str = (
        "Aprenda a otimizar o estoque, atendimento e conversao da sua otica com "
        "indicadores praticos e checklist completo."
    ),
    lang: str = 'lang="pt-BR"',
    include_canonical: bool = True,
    include_social: bool = True,
    include_viewport: bool = True,
    include_jsonld: bool = True,
    include_landmarks: bool = True,
    alt_attr: str = 'alt="Painel de gestao"',
    body_extra: str | None = None,
    headings: str = "<h1>Gestao de Oticas</h1><h2>Controle de Estoque</h2>",
) -> str:
    canonical = (
        '<link rel="canonical" href="https://example.com/guia">' if include_canonical else ""
    )
    social = (
        '<meta property="og:title" content="Guia de Gestao">'
        '<meta property="og:description" content="Checklist completo para oticas.">'
        '<meta name="twitter:card" content="summary_large_image">'
        if include_social
        else ""
    )
    viewport = (
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        if include_viewport
        else ""
    )
    jsonld = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Article",'
        '"headline":"Guia de Gestao","author":{"@type":"Person","name":"Ana"},'
        '"publisher":{"@type":"Organization","name":"Macy"},'
        '"description":"Guia completo","inLanguage":"pt-BR"}'
        "</script>"
        if include_jsonld
        else ""
    )
    filler = body_extra if body_extra is not None else ("Conteudo pratico sobre oticas. " * 65)
    body_inner = (
        f'{headings}<p>{filler}</p><img src="/img/painel.png" {alt_attr}>'
        '<a href="/contato">Contato</a>'
        '<a href="https://externo.org/estudo">Estudo</a>'
        '<a href="mailto:oi@example.com">Email</a>'
        '<a href="javascript:void(0)">JS</a>'
        '<a href="#topo">Topo</a>'
    )
    wrapped = (
        "<header><nav><a href='/'>Home</a></nav></header>"
        f"<main><article>{body_inner}</article></main>"
        if include_landmarks
        else body_inner
    )
    return (
        f"<!doctype html><html {lang}><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        f'<meta name="description" content="{description}">'
        f"{canonical}{social}{viewport}{jsonld}</head>"
        f"<body>{wrapped}</body></html>"
    )


def test_weighted_scoring_normalization_severities_and_grades() -> None:
    """Verify 7-category weights, dynamic denominator, info exclusion, and letter grades."""
    from engine.audit.scoring import GRADE_THRESHOLDS, score_audit_checks

    all_pass = [
        AuditCheckResult(check=f"c_{cat}", category=cat, status="pass", score=1.0, detail="ok")
        for cat in ("onpage", "performance", "usability", "security", "links", "social", "ai")
    ]
    total, grade, cats = score_audit_checks(all_pass)
    assert total == 100.0
    assert grade == "A+"
    assert set(cats) == {"onpage", "performance", "usability", "security", "links", "social", "ai"}

    # Warning = 0.5, info excluded; omitting usability/social/security re-normalizes weights
    partial = [
        AuditCheckResult(check="title", category="onpage", status="pass", score=1.0, detail="ok"),
        AuditCheckResult(check="meta", category="onpage", status="warning", score=0.5, detail="w"),
        AuditCheckResult(check="note", category="onpage", status="info", score=0.0, detail="i"),
        AuditCheckResult(
            check="ttfb", category="performance", status="error", score=0.0, detail="b"
        ),
        AuditCheckResult(check="ai_info", category="ai", status="info", score=0.0, detail="info"),
    ]
    p_total, p_grade, p_cats = score_audit_checks(partial)
    # onpage=75.0 (weight 30), performance=0.0 (weight 20) -> 75*30 / 50 = 45.0
    assert p_cats["onpage"].score == 75.0
    assert p_cats["onpage"].info == 1
    assert p_cats["performance"].score == 0.0
    assert p_total == 45.0
    assert p_grade == "F"
    assert "ai" not in p_cats

    # Empty checks -> 0.0 F
    empty_score, empty_grade, empty_cats = score_audit_checks([])
    assert empty_score == 0.0
    assert empty_grade == "F"
    assert empty_cats == {}

    # Verify all grade boundaries exist
    assert GRADE_THRESHOLDS[0] == (95.0, "A+")


@given(
    statuses=st.lists(
        st.tuples(
            st.sampled_from(
                ["onpage", "performance", "usability", "security", "links", "social", "ai"]
            ),
            st.sampled_from(["pass", "warning", "error", "info"]),
        ),
        min_size=0,
        max_size=30,
    )
)
def test_hypothesis_scoring_bounds(statuses: list[tuple[str, str]]) -> None:
    """Property: overall score always lies in [0.0, 100.0] for arbitrary check sets."""
    from engine.audit.scoring import score_audit_checks

    mult = {"pass": 1.0, "warning": 0.5, "error": 0.0, "info": 0.0}
    checks = [
        AuditCheckResult(
            check=f"chk_{i}",
            category=cat,
            status=st_name,
            score=mult[st_name],
            detail="hyp",
        )
        for i, (cat, st_name) in enumerate(statuses)
    ]
    score, grade, _ = score_audit_checks(checks)
    assert 0.0 <= score <= 100.0
    assert grade in {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "D", "F"}


def test_page_checks_onpage_security_usability_social_and_performance() -> None:
    """Verify page_checks evaluates on-page, usability, social, security, and local performance."""
    from engine.audit.page_checks import run_page_checks

    html = _sample_html()
    headers = {
        "Strict-Transport-Security": "max-age=31536000",
        "Content-Security-Policy": "default-src 'self'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }
    good = run_page_checks(
        url="https://example.com/guia",
        html_text=html,
        headers=headers,
        ttfb_ms=180.0,
        byte_weight=len(html.encode("utf-8")),
    )
    assert all(c.status in {"pass", "info"} for c in good.checks)
    assert good.title == "Guia Completo de Gestao para Oticas Modernas"

    # Bad HTML: missing title, bad description, no canonical, multiple h1s + h3 jump,
    # missing alt, missing viewport/lang/social, http:// + slow TTFB + huge bytes
    bad_html = _sample_html(
        title="",
        description="Curta",
        lang="",
        include_canonical=False,
        include_social=False,
        include_viewport=False,
        alt_attr="",
        headings="<h1>Um</h1><h1>Dois</h1><h3>Pulo</h3>",
        body_extra="Poucas palavras",
    )
    bad = run_page_checks(
        url="http://example.com/guia",
        html_text=bad_html,
        headers={},
        ttfb_ms=2600.0,
        byte_weight=3_200_000,
    )
    by_name = {c.check: c for c in bad.checks}
    assert by_name["title"].status == "error"
    assert by_name["meta_description"].status == "warning"
    assert by_name["canonical"].status == "warning"
    assert by_name["h1_presence"].status == "warning"
    assert by_name["heading_hierarchy"].status == "warning"
    assert by_name["image_alt"].status == "warning"
    assert by_name["viewport"].status == "error"
    assert by_name["html_lang"].status == "warning"
    assert by_name["open_graph"].status == "warning"
    assert by_name["twitter_card"].status == "warning"
    assert by_name["https_enabled"].status == "error"
    assert by_name["security_headers"].status == "warning"
    assert by_name["local_ttfb"].status == "error"
    assert by_name["byte_weight"].status == "error"


def test_ai_checks_robots_llms_ssr_landmarks_and_jsonld(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify AI/GEO checks separate search vs training bots, check llms.txt, SSR, and JSON-LD."""
    from engine.audit import ai_checks

    robots_txt = (
        "User-agent: GPTBot\nDisallow: /\n\n"
        "User-agent: OAI-SearchBot\nDisallow: /private\n\n"
        "User-agent: PerplexityBot\nAllow: /\n"
    )
    bot_status = ai_checks.parse_robots_ai_bots(robots_txt, target_path="/blog")
    assert bot_status["GPTBot"] is False
    assert bot_status["OAI-SearchBot"] is True
    assert bot_status["PerplexityBot"] is True

    # Blocking OAI-SearchBot flags an error
    blocked_robots = "User-agent: OAI-SearchBot\nDisallow: /\n"
    blocked_status = ai_checks.parse_robots_ai_bots(blocked_robots, target_path="/")
    assert blocked_status["OAI-SearchBot"] is False

    html = _sample_html(
        include_landmarks=False,
        include_jsonld=True,
        body_extra="Only five words here",
    )

    def _fake_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if url.endswith("/robots.txt"):
            return SafeResponse(status_code=200, headers={}, content=blocked_robots.encode())
        if url.endswith("/llms.txt"):
            return SafeResponse(status_code=200, headers={}, content=b"# Example LLMs\n")
        return SafeResponse(status_code=404, headers={}, content=b"")

    monkeypatch.setattr("engine.safe_http.safe_request", _fake_request)
    res, ld_types = ai_checks.run_ai_checks("https://example.com/", html)
    by_name = {c.check: c for c in res}
    assert by_name["ai_search_bots"].status == "error"
    assert by_name["llms_txt"].status == "pass"
    assert by_name["ssr_content"].status == "error"
    assert by_name["semantic_landmarks"].status == "warning"
    assert by_name["structured_data"].status == "pass"
    assert ld_types == ["Article"]


def test_links_extraction_and_bounded_broken_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify link classification and bounded HEAD-with-GET-fallback external checks."""
    from engine.audit.links import check_external_links, extract_links

    html = _sample_html()
    internal, external = extract_links("https://example.com/guia", html)
    assert internal == ["https://example.com/", "https://example.com/contato"]
    assert external == ["https://externo.org/estudo"]

    calls: list[tuple[str, str]] = []

    def _fake_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        calls.append((method, url))
        if method == "HEAD" and "fallback" in url:
            return SafeResponse(status_code=405, headers={}, content=b"")
        if "broken" in url:
            return SafeResponse(status_code=404, headers={}, content=b"")
        if "timeout" in url:
            raise URLError("timed out")
        return SafeResponse(status_code=200, headers={}, content=b"")

    monkeypatch.setattr("engine.safe_http.safe_request", _fake_request)
    broken = check_external_links(
        [
            "https://ok.org/1",
            "https://fallback.org/2",
            "https://broken.org/404",
            "https://timeout.org/err",
        ],
        max_urls=100,
    )
    assert ("HEAD", "https://fallback.org/2") in calls
    assert ("GET", "https://fallback.org/2") in calls
    assert {b["url"] for b in broken} == {"https://broken.org/404", "https://timeout.org/err"}

    # External cap respected
    calls.clear()
    many = [f"https://site.org/p{i}" for i in range(150)]
    check_external_links(many, max_urls=100)
    assert len(calls) == 100


def test_audit_url_end_to_end_and_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify audit_url orchestrator combines checks and propagates safe_http SSRF guard."""
    from engine.audit import audit_url

    good_html = _sample_html()
    bad_html = _sample_html(description="", include_jsonld=False)

    def _make_fetcher(html_body: str, robots_body: str) -> Callable[..., SafeResponse]:
        def _fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
            if url.endswith("/robots.txt"):
                return SafeResponse(status_code=200, headers={}, content=robots_body.encode())
            if url.endswith("/llms.txt"):
                return SafeResponse(status_code=200, headers={}, content=b"# llms")
            if "externo.org" in url:
                return SafeResponse(status_code=200, headers={}, content=b"")
            return SafeResponse(
                status_code=200,
                headers={"Strict-Transport-Security": "max-age=31536000"},
                content=html_body.encode(),
            )

        return _fetch

    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        _make_fetcher(good_html, "User-agent: OAI-SearchBot\nAllow: /\n"),
    )
    report = audit_url("https://example.com/guia", check_external=True)
    assert isinstance(report, PageAuditReport)
    assert report.overall_score >= 70.0
    assert report.grade in {"A+", "A", "A-", "B+", "B"}
    assert "Article" in report.json_ld_types

    # Spec scenario: blocked OAI-SearchBot + missing meta description reduces AI & OnPage scores
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        _make_fetcher(bad_html, "User-agent: OAI-SearchBot\nDisallow: /\n"),
    )
    bad_report = audit_url("https://example.com/guia")
    assert bad_report.categories["ai"].score < report.categories["ai"].score
    assert bad_report.categories["onpage"].score < report.categories["onpage"].score
    assert bad_report.recommendations

    def _ssrf_fail(method: str, url: str, **kwargs: Any) -> SafeResponse:
        raise BlockedIPError("blocked")

    monkeypatch.setattr("engine.safe_http.safe_request", _ssrf_fail)
    with pytest.raises(BlockedIPError):
        audit_url("http://127.0.0.1/admin")


def test_audit_edge_cases_for_full_branch_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover parser, robots, llms-full, JSON-LD errors, warning tiers, and link edge cases."""
    from engine.audit import ai_checks, audit_url, links, page_checks

    # Robots blank lines, unknown directive, empty disallow, and fetch errors
    robots = (
        "Crawl-delay: 5\n\nUser-agent: GPTBot\nDisallow:\nAllow: /public\nIgnored-line-no-colon\n"
    )
    policy = ai_checks.parse_robots_ai_bots(robots, target_path="/blog")
    assert policy["GPTBot"] is True

    # llms.txt -> 404, fallback to llms-full.txt -> 200; robots fetch throws URLError
    def _llms_full_fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if url.endswith("/robots.txt"):
            raise URLError("network down")
        if url.endswith("/llms.txt"):
            return SafeResponse(status_code=404, headers={}, content=b"")
        if url.endswith("/llms-full.txt"):
            return SafeResponse(status_code=200, headers={}, content=b"# Full\n")
        return SafeResponse(status_code=404, headers={}, content=b"")

    monkeypatch.setattr("engine.safe_http.safe_request", _llms_full_fetch)

    edge_html = (
        "<!doctype html><html><head>"
        "<title>Short</title>"
        "<meta charset='utf-8'>"
        '<link rel="icon" href="/fav.ico">'
        '<link rel="canonical" href="">'
        "<style>body { color: red; }</style>"
        "<script>console.log(1)</script>"
        '<script type="application/ld+json"></script>'
        '<script type="application/ld+json">{bad json</script>'
        '<script type="application/ld+json">{"@type":"Article"}</script>'
        "</head><body>"
        "<noscript>enable js</noscript>"
        "<h1></h1>" + (" ".join(["word"] * 80)) + '<a href="/dup">1</a><a href="/dup">2</a>'
        '<a href="https://ext.org/a">E1</a><a href="https://ext.org/a">E2</a>'
        '<a href="http:///missing-host">Bad</a>'
        "</body></html>"
    )
    rep = audit_url(
        "https://example.com/edge",
        html_text=edge_html,
        headers={"Strict-Transport-Security": "max-age=1"},
        ttfb_ms=1200.0,
    )
    by_name = {c.check: c for c in rep.checks}
    assert by_name["llms_txt"].status == "pass"
    assert by_name["ssr_content"].status == "warning"
    assert by_name["structured_data"].status == "warning"
    assert by_name["title"].status == "warning"
    assert by_name["local_ttfb"].status == "warning"
    assert by_name["canonical"].status == "warning"

    # Zero headings -> h1_presence error; byte_weight warning tier (1.5 MB); no-links page
    mid_bundle = page_checks.run_page_checks(
        url="https://example.com/mid",
        html_text="<html><body><p>Hello world</p></body></html>",
        headers={},
        ttfb_ms=100.0,
        byte_weight=1_500_000,
    )
    mid_checks = {c.check: c for c in mid_bundle.checks}
    assert mid_checks["h1_presence"].status == "error"
    assert mid_checks["byte_weight"].status == "warning"

    # Pre-supplied robots_txt + has_llms_txt avoids HTTP requests; broken external missing case
    no_links_rep = audit_url(
        "https://example.com/empty",
        html_text="<html><body><main><p>" + " ".join(["texto"] * 170) + "</p></main></body></html>",
        robots_txt="",
        has_llms_txt=False,
    )
    nl_checks = {c.check: c for c in no_links_rep.checks}
    assert nl_checks["internal_links"].status == "warning"
    assert nl_checks["ai_training_bots"].status == "info"
    assert links.evaluate_links_checks([], ["https://x.org"], [])[1].status == "pass"
    assert links.canonicalize_href("https://example.com/", "http:///only-path") is None

    # Also test fetch_robots_and_llms when both llms.txt and llms-full.txt return 404
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda m, u, **kw: SafeResponse(status_code=404, headers={}, content=b""),
    )
    assert ai_checks.fetch_robots_and_llms("https://example.com/") == ("", False)
