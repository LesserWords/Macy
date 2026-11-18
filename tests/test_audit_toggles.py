"""Tests for modular optional audit toggles: BrowserAuditPort & PageSpeedPort (M6-02 D3)."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError

import pytest

from engine.contracts import CrawlConfig
from engine.safe_http import SafeResponse


def test_ports_and_fakes_satisfy_runtime_protocols() -> None:
    """Verify BrowserAuditPort and PageSpeedPort runtime contracts and Fakes."""
    from engine.integrations.fakes import FakeBrowserAudit, FakePageSpeed
    from engine.integrations.ports import BrowserAuditPort, PageSpeedPort

    browser = FakeBrowserAudit()
    psi = FakePageSpeed()
    assert isinstance(browser, BrowserAuditPort)
    assert isinstance(psi, PageSpeedPort)

    browser.healthcheck()
    psi.healthcheck()

    axe_checks = browser.audit_accessibility("https://example.com/")
    assert len(axe_checks) == 7
    assert {c.category for c in axe_checks} == {"usability"}

    psi_checks = psi.fetch_metrics("https://example.com/", strategy="mobile")
    assert {c.check for c in psi_checks} == {"psi_lcp", "psi_cls", "psi_inp"}
    assert {c.category for c in psi_checks} == {"performance"}


def test_optional_toggles_disabled_missing_extra_and_timeout_graceful_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify disabled, missing-extra, or timed-out adapters never fail audit or hurt score."""
    from engine.audit import audit_url
    from engine.audit.toggles import (
        GooglePageSpeedAdapter,
        PlaywrightAxeAdapter,
        run_optional_toggles,
    )

    html = (
        "<!doctype html><html lang='pt-BR'><head><title>Titulo de Teste Completo Aqui</title>"
        '<meta name="description" content="' + ("A" * 120) + '">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        "<body><main><h1>Titulo</h1><p>"
        + (" ".join(["conteudo"] * 170))
        + "</p></main></body></html>"
    )

    base = audit_url("https://example.com/", html_text=html, robots_txt="", has_llms_txt=True)

    # Disabled toggles -> zero extra checks, identical score
    disabled_extra = run_optional_toggles(
        "https://example.com/",
        config=CrawlConfig(axe_enabled=False, psi_enabled=False),
    )
    assert disabled_extra == []

    # Enabled PlaywrightAxeAdapter without optional package -> graceful info fallback
    axe_adapter = PlaywrightAxeAdapter(runner=None)
    monkeypatch.setattr(
        "engine.audit.toggles._import_axe_sync_runner",
        lambda: (_ for _ in ()).throw(ImportError("no playwright")),
    )
    with pytest.raises(RuntimeError, match="audit-browser"):
        axe_adapter.healthcheck()
    axe_fallback = axe_adapter.audit_accessibility("https://example.com/")
    assert len(axe_fallback) == 1
    assert axe_fallback[0].status == "info"

    # Custom failing runner also degrades cleanly to info diagnostic via run_optional_toggles
    class _BrokenBrowser:
        def healthcheck(self) -> None:
            raise TimeoutError("browser timed out")

        def audit_accessibility(self, url: str) -> list[Any]:
            raise TimeoutError("browser timed out")

    class _BrokenPSI:
        def healthcheck(self) -> None:
            raise URLError("psi down")

        def fetch_metrics(self, url: str, strategy: str = "mobile") -> list[Any]:
            raise TimeoutError("psi timeout")

    cfg_on = CrawlConfig(axe_enabled=True, psi_enabled=True)
    with_broken = audit_url(
        "https://example.com/",
        html_text=html,
        robots_txt="",
        has_llms_txt=True,
        crawl_config=cfg_on,
        browser_auditor=_BrokenBrowser(),
        pagespeed_client=_BrokenPSI(),
    )
    assert with_broken.overall_score == base.overall_score
    assert any(c.status == "info" and "browser" in c.check for c in with_broken.checks)
    assert any(c.status == "info" and "psi" in c.check for c in with_broken.checks)

    # GooglePageSpeedAdapter without API key raises on healthcheck and emits info check on fetch
    no_key_psi = GooglePageSpeedAdapter(api_key="")
    with pytest.raises(ValueError, match="API key"):
        no_key_psi.healthcheck()
    no_key_checks = no_key_psi.fetch_metrics("https://example.com/")
    assert len(no_key_checks) == 1
    assert no_key_checks[0].status == "info"


def test_google_pagespeed_and_playwright_axe_adapters_parse_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PSI metric thresholds & Playwright axe rule evaluation map cleanly to checks."""
    from engine.audit import audit_url
    from engine.audit.toggles import GooglePageSpeedAdapter, PlaywrightAxeAdapter
    from engine.integrations.fakes import FakeBrowserAudit

    psi_payload = {
        "lighthouseResult": {
            "audits": {
                "largest-contentful-paint": {"numericValue": 2100.0},
                "cumulative-layout-shift": {"numericValue": 0.18},
                "interaction-to-next-paint": {"numericValue": 650.0},
            }
        }
    }

    def _psi_fetch(method: str, url: str, **kwargs: Any) -> SafeResponse:
        if "broken-psi" in url:
            return SafeResponse(500, {}, b"err")
        if "net-err" in url:
            raise URLError("timeout")
        return SafeResponse(200, {}, json.dumps(psi_payload).encode("utf-8"))

    monkeypatch.setattr("engine.safe_http.safe_request", _psi_fetch)
    psi = GooglePageSpeedAdapter(api_key="test-key")
    psi.healthcheck()
    cwv = psi.fetch_metrics("https://example.com/ok")
    by_name = {c.check: c for c in cwv}
    assert by_name["psi_lcp"].status == "pass"
    assert by_name["psi_cls"].status == "warning"
    assert by_name["psi_inp"].status == "error"

    assert psi.fetch_metrics("https://broken-psi.example/")[0].status == "info"
    assert psi.fetch_metrics("https://net-err.example/")[0].status == "info"

    # PlaywrightAxeAdapter with injected callable runner evaluates curated rule list
    def _fake_runner(url: str) -> dict[str, Any]:
        return {
            "violations": [
                {"id": "color-contrast", "impact": "serious", "nodes": [1, 2]},
                {"id": "image-alt", "impact": "moderate", "nodes": [1]},
                {"id": "unrelated-rule", "impact": "minor", "nodes": [1]},
                "malformed-entry",
            ]
        }

    axe = PlaywrightAxeAdapter(runner=_fake_runner)
    axe.healthcheck()
    axe_res = axe.audit_accessibility("https://example.com/")
    axe_map = {c.check: c for c in axe_res}
    assert axe_map["axe_color_contrast"].status == "error"
    assert axe_map["axe_image_alt"].status == "warning"
    assert axe_map["axe_document_title"].status == "pass"

    # End-to-end audit_url with FakeBrowserAudit + GooglePageSpeedAdapter
    html = (
        "<!doctype html><html lang='pt-BR'><head><title>Titulo Completo Test</title>"
        '<meta name="description" content="' + ("B" * 120) + '">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        "<body><main><h1>H1</h1><p>" + (" ".join(["texto"] * 170)) + "</p></main></body></html>"
    )
    rep = audit_url(
        "https://example.com/ok",
        html_text=html,
        robots_txt="",
        has_llms_txt=True,
        crawl_config=CrawlConfig(axe_enabled=True, psi_enabled=True),
        browser_auditor=FakeBrowserAudit(),
        pagespeed_client=psi,
    )
    names = {c.check for c in rep.checks}
    assert "psi_lcp" in names
    assert "axe_image_alt" in names


def test_optional_toggle_runner_import_and_non_mapping_psi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover _import_axe_sync_runner execution, default adapter paths, and non-dict PSI."""
    import sys
    import types

    from engine.audit.toggles import (
        GooglePageSpeedAdapter,
        PlaywrightAxeAdapter,
        run_optional_toggles,
    )

    class _FakeBrowser:
        def new_page(self) -> Any:
            return types.SimpleNamespace(goto=lambda *a, **k: None)

        def close(self) -> None:
            return None

    class _FakePW:
        def __init__(self) -> None:
            self.chromium = types.SimpleNamespace(launch=lambda **k: _FakeBrowser())

        def __enter__(self) -> _FakePW:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    pw_sync = types.ModuleType("playwright.sync_api")
    pw_sync.sync_playwright = lambda: _FakePW()  # type: ignore[attr-defined]
    axe_sync = types.ModuleType("axe_playwright_python.sync_playwright")

    class _FakeAxe:
        def run(self, page: Any) -> Any:
            return types.SimpleNamespace(response={"violations": []})

    axe_sync.Axe = _FakeAxe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", pw_sync)
    monkeypatch.setitem(
        sys.modules, "axe_playwright_python", types.ModuleType("axe_playwright_python")
    )
    monkeypatch.setitem(sys.modules, "axe_playwright_python.sync_playwright", axe_sync)

    adapter = PlaywrightAxeAdapter()
    adapter.healthcheck()
    default_checks = run_optional_toggles(
        "https://example.com/",
        config=CrawlConfig(axe_enabled=True, psi_enabled=True),
    )
    assert any(c.check == "axe_image_alt" and c.status == "pass" for c in default_checks)
    assert any(c.check == "psi_metrics" and c.status == "info" for c in default_checks)

    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda m, u, **kw: SafeResponse(200, {}, b"[]"),
    )
    psi = GooglePageSpeedAdapter(api_key="k")
    assert psi.fetch_metrics("https://example.com/")[0].status == "info"
