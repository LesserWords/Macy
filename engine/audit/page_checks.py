"""Deterministic HTML on-page, usability, social, security, and local performance checks."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser

from engine.contracts import AuditCategory, AuditCheckResult, AuditStatus

RECOMMENDED_SECURITY_HEADERS: tuple[str, ...] = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
)


@dataclass
class ParsedHTML:
    """Structured metadata extracted in a single pass over raw HTML."""

    title: str = ""
    meta_name: dict[str, str] = field(default_factory=dict)
    meta_prop: dict[str, str] = field(default_factory=dict)
    canonical: str | None = None
    html_lang: str = ""
    headings: list[int] = field(default_factory=list)
    h1_texts: list[str] = field(default_factory=list)
    images: list[tuple[str, bool]] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)
    json_ld_blocks: list[str] = field(default_factory=list)
    landmarks: set[str] = field(default_factory=set)
    anchors: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        joined = " ".join(self.text_chunks)
        return len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", joined))


@dataclass
class PageCheckBundle:
    """Extracted metadata plus deterministic page audit checks."""

    title: str | None
    meta_description: str | None
    word_count: int
    parsed: ParsedHTML
    checks: list[AuditCheckResult]


class _AuditHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parsed = ParsedHTML()
        self._in_title = False
        self._in_h1 = False
        self._in_jsonld = False
        self._skip_text_depth = 0
        self._title_buf: list[str] = []
        self._h1_buf: list[str] = []
        self._jsonld_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        self._handle_document_tag(low, attr_map)
        self._handle_body_tag(low, attr_map)

    def _handle_document_tag(self, tag: str, attr_map: dict[str, str]) -> None:
        if tag == "html" and attr_map.get("lang"):
            self.parsed.html_lang = attr_map["lang"].strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            content = attr_map.get("content", "").strip()
            if attr_map.get("name"):
                self.parsed.meta_name[attr_map["name"].lower()] = content
            if attr_map.get("property"):
                self.parsed.meta_prop[attr_map["property"].lower()] = content
        elif tag == "link" and "canonical" in attr_map.get("rel", "").lower().split():
            href = attr_map.get("href", "").strip()
            if href:
                self.parsed.canonical = href
        elif tag == "script":
            if attr_map.get("type", "").strip().lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []
            self._skip_text_depth += 1

    def _handle_body_tag(self, tag: str, attr_map: dict[str, str]) -> None:
        if tag in {"style", "noscript"}:
            self._skip_text_depth += 1
        elif tag in {"main", "article", "nav", "header", "footer"}:
            self.parsed.landmarks.add(tag)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parsed.headings.append(int(tag[1]))
            if tag == "h1":
                self._in_h1 = True
                self._h1_buf = []
        elif tag == "img":
            src = attr_map.get("src", "").strip()
            has_alt = bool(attr_map.get("alt", "").strip())
            self.parsed.images.append((src, has_alt))
        elif tag == "a" and attr_map.get("href"):
            self.parsed.anchors.append(attr_map["href"].strip())

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low == "title" and self._in_title:
            self._in_title = False
            self.parsed.title = " ".join("".join(self._title_buf).split())
        elif low == "h1" and self._in_h1:
            self._in_h1 = False
            self.parsed.h1_texts.append(" ".join("".join(self._h1_buf).split()))
        elif low == "script":
            if self._in_jsonld:
                self._in_jsonld = False
                block = "".join(self._jsonld_buf).strip()
                if block:
                    self.parsed.json_ld_blocks.append(block)
            self._skip_text_depth = max(0, self._skip_text_depth - 1)
        elif low in {"style", "noscript"}:
            self._skip_text_depth = max(0, self._skip_text_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        if self._in_h1:
            self._h1_buf.append(data)
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        elif self._skip_text_depth == 0 and data.strip():
            self.parsed.text_chunks.append(data.strip())


def parse_html(html_text: str) -> ParsedHTML:
    """Parse HTML into structured SEO/GEO metadata."""
    parser = _AuditHTMLParser()
    parser.feed(html_text)
    parser.close()
    return parser.parsed


def _make_check(
    check: str,
    category: AuditCategory,
    status: AuditStatus,
    detail: str,
    recommendation: str | None = None,
) -> AuditCheckResult:
    score_map = {"pass": 1.0, "warning": 0.5, "error": 0.0, "info": 0.0}
    return AuditCheckResult(
        check=check,
        category=category,
        status=status,
        score=score_map[status],
        detail=detail,
        recommendation=recommendation,
    )


def _check_title_and_meta(parsed: ParsedHTML) -> list[AuditCheckResult]:
    results: list[AuditCheckResult] = []
    title = parsed.title
    if not title:
        results.append(
            _make_check(
                "title",
                "onpage",
                "error",
                "Missing <title> tag.",
                "Add a unique <title> between 30 and 60 characters.",
            )
        )
    elif len(title) < 20 or len(title) > 65:
        results.append(
            _make_check(
                "title",
                "onpage",
                "warning",
                f"<title> length is {len(title)} chars (recommended 20-65).",
                "Adjust <title> length to 20-65 characters for SERP snippets.",
            )
        )
    else:
        results.append(_make_check("title", "onpage", "pass", f"Title OK ({len(title)} chars)."))

    desc = parsed.meta_name.get("description", "")
    if not desc:
        results.append(
            _make_check(
                "meta_description",
                "onpage",
                "error",
                "Missing <meta name='description'>.",
                "Add a concise meta description (70-160 characters).",
            )
        )
    elif len(desc) < 70 or len(desc) > 170:
        results.append(
            _make_check(
                "meta_description",
                "onpage",
                "warning",
                f"Meta description length is {len(desc)} chars (recommended 70-170).",
                "Refine meta description to 70-170 characters.",
            )
        )
    else:
        results.append(
            _make_check("meta_description", "onpage", "pass", f"Meta desc OK ({len(desc)} chars).")
        )

    if parsed.canonical:
        results.append(_make_check("canonical", "onpage", "pass", "Canonical link tag present."))
    else:
        results.append(
            _make_check(
                "canonical",
                "onpage",
                "warning",
                "Missing <link rel='canonical'>.",
                "Declare a self-referencing or canonical URL.",
            )
        )
    return results


def _check_headings_and_images(parsed: ParsedHTML) -> list[AuditCheckResult]:
    results: list[AuditCheckResult] = []
    h1_count = len(parsed.h1_texts)
    if h1_count == 0:
        results.append(
            _make_check("h1_presence", "onpage", "error", "Missing <h1>.", "Add one clear <h1>.")
        )
    elif h1_count > 1:
        results.append(
            _make_check(
                "h1_presence",
                "onpage",
                "warning",
                f"Found {h1_count} <h1> headings (expected 1).",
                "Consolidate multiple <h1> tags into a single primary heading.",
            )
        )
    else:
        results.append(_make_check("h1_presence", "onpage", "pass", "Single <h1> present."))

    skipped = any(
        parsed.headings[i] - parsed.headings[i - 1] > 1 for i in range(1, len(parsed.headings))
    )
    if skipped:
        results.append(
            _make_check(
                "heading_hierarchy",
                "onpage",
                "warning",
                "Heading hierarchy skips levels (e.g. h1 to h3).",
                "Keep sequential heading levels h1 -> h2 -> h3.",
            )
        )
    else:
        results.append(
            _make_check("heading_hierarchy", "onpage", "pass", "Heading hierarchy is sequential.")
        )

    missing_alt = sum(1 for _, has_alt in parsed.images if not has_alt)
    if missing_alt > 0:
        results.append(
            _make_check(
                "image_alt",
                "usability",
                "warning",
                f"{missing_alt} of {len(parsed.images)} image(s) lack descriptive alt text.",
                "Add descriptive alt attributes to content images.",
            )
        )
    else:
        results.append(
            _make_check("image_alt", "usability", "pass", "All images include alt text.")
        )
    return results


def _check_usability_and_social(parsed: ParsedHTML) -> list[AuditCheckResult]:
    results: list[AuditCheckResult] = []
    if parsed.meta_name.get("viewport"):
        results.append(
            _make_check("viewport", "usability", "pass", "Mobile viewport meta present.")
        )
    else:
        results.append(
            _make_check(
                "viewport",
                "usability",
                "error",
                "Missing responsive viewport meta tag.",
                "Add <meta name='viewport' content='width=device-width, initial-scale=1'>.",
            )
        )

    if parsed.html_lang:
        results.append(
            _make_check("html_lang", "usability", "pass", f"html lang='{parsed.html_lang}'.")
        )
    else:
        results.append(
            _make_check(
                "html_lang",
                "usability",
                "warning",
                "<html> tag is missing lang attribute.",
                "Set <html lang='pt-BR'> or target locale.",
            )
        )

    has_og = bool(parsed.meta_prop.get("og:title") and parsed.meta_prop.get("og:description"))
    results.append(
        _make_check(
            "open_graph",
            "social",
            "pass" if has_og else "warning",
            "OpenGraph metadata present." if has_og else "Incomplete OpenGraph metadata.",
            None if has_og else "Add og:title and og:description meta tags.",
        )
    )
    has_tw = bool(parsed.meta_name.get("twitter:card"))
    results.append(
        _make_check(
            "twitter_card",
            "social",
            "pass" if has_tw else "warning",
            "Twitter card meta present." if has_tw else "Missing twitter:card meta tag.",
            None if has_tw else "Add <meta name='twitter:card' content='summary_large_image'>.",
        )
    )
    return results


def _check_security(url: str, headers: Mapping[str, str]) -> list[AuditCheckResult]:
    scheme = urllib.parse.urlparse(url).scheme.lower()
    is_https = scheme == "https"
    low_headers = {k.lower(): v for k, v in headers.items()}
    present = [h for h in RECOMMENDED_SECURITY_HEADERS if low_headers.get(h)]
    return [
        _make_check(
            "https_enabled",
            "security",
            "pass" if is_https else "error",
            "HTTPS active." if is_https else "Page served over plain HTTP.",
            None if is_https else "Serve all pages exclusively over HTTPS.",
        ),
        _make_check(
            "security_headers",
            "security",
            "pass" if len(present) >= 2 else "warning",
            f"Security headers found: {len(present)}/4.",
            None if len(present) >= 2 else "Enable HSTS, CSP, and X-Content-Type-Options.",
        ),
    ]


def _classify_threshold(value: float, warn_above: float, err_above: float) -> AuditStatus:
    if value <= warn_above:
        return "pass"
    return "warning" if value <= err_above else "error"


def _check_local_perf(ttfb_ms: float, byte_weight: int) -> list[AuditCheckResult]:
    ttfb_status = _classify_threshold(ttfb_ms, 800.0, 2000.0)
    bw_status = _classify_threshold(float(byte_weight), 1_000_000.0, 2_500_000.0)
    return [
        _make_check(
            "local_ttfb",
            "performance",
            ttfb_status,
            f"Response TTFB {ttfb_ms:.0f}ms.",
            None if ttfb_status == "pass" else "Optimize server response / caching (< 800ms).",
        ),
        _make_check(
            "byte_weight",
            "performance",
            bw_status,
            f"HTML document size {byte_weight} bytes.",
            None if bw_status == "pass" else "Reduce initial HTML weight below 1 MB.",
        ),
    ]


def run_page_checks(  # noqa: PLR0913
    *,
    url: str,
    html_text: str,
    headers: Mapping[str, str],
    ttfb_ms: float,
    byte_weight: int,
    parsed: ParsedHTML | None = None,
) -> PageCheckBundle:
    """Execute deterministic HTML, usability, social, security, and local performance checks."""
    doc = parsed or parse_html(html_text)
    checks = [
        *_check_title_and_meta(doc),
        *_check_headings_and_images(doc),
        *_check_usability_and_social(doc),
        *_check_security(url, headers),
        *_check_local_perf(ttfb_ms, byte_weight),
    ]
    return PageCheckBundle(
        title=doc.title or None,
        meta_description=doc.meta_name.get("description") or None,
        word_count=doc.word_count,
        parsed=doc,
        checks=checks,
    )
