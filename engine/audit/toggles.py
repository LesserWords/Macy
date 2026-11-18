"""Modular optional audit adapters: BrowserAuditPort (axe-core) and PageSpeedPort (M6-02 D3)."""

from __future__ import annotations

import importlib
import json
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

from engine import safe_http
from engine.contracts import AuditCheckResult, AuditStatus, CrawlConfig
from engine.integrations.ports import BrowserAuditPort, PageSpeedPort

CURATED_AXE_RULES: tuple[str, ...] = (
    "image-alt",
    "document-title",
    "html-has-lang",
    "heading-order",
    "link-name",
    "color-contrast",
    "meta-viewport",
)
PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _import_axe_sync_runner() -> Callable[[str], Mapping[str, Any]]:
    pw_mod = importlib.import_module("playwright.sync_api")
    axe_mod = importlib.import_module("axe_playwright_python.sync_playwright")

    def _execute(url: str) -> Mapping[str, Any]:
        with pw_mod.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                results = axe_mod.Axe().run(page)
                return dict(getattr(results, "response", results))
            finally:
                browser.close()

    return _execute


class PlaywrightAxeAdapter:
    """BrowserAuditPort backed by optional [audit-browser] Playwright + axe-core extra."""

    def __init__(self, *, runner: Callable[[str], Mapping[str, Any]] | None = None) -> None:
        self._runner = runner

    def healthcheck(self) -> None:
        """Verify runner or optional [audit-browser] dependency is available."""
        if self._runner is not None:
            return
        try:
            _import_axe_sync_runner()
        except ImportError as exc:
            msg = (
                "Optional [audit-browser] extra (playwright, axe-playwright-python) not installed."
            )
            raise RuntimeError(msg) from exc

    def audit_accessibility(self, url: str) -> list[AuditCheckResult]:
        """Evaluate curated axe-core accessibility rules, degrading cleanly on missing/timeout."""
        try:
            runner = self._runner if self._runner is not None else _import_axe_sync_runner()
            raw = runner(url)
        except Exception as exc:  # noqa: BLE001
            return [
                AuditCheckResult(
                    check="browser_axe_audit",
                    category="usability",
                    status="info",
                    score=0.0,
                    detail=f"Optional axe-core browser check skipped: {exc}",
                )
            ]

        violations_by_rule: dict[str, dict[str, Any]] = {
            str(item["id"]): dict(item)
            for item in raw.get("violations", [])
            if isinstance(item, Mapping) and item.get("id")
        }
        return [
            _evaluate_axe_rule(rule, violations_by_rule.get(rule)) for rule in CURATED_AXE_RULES
        ]


def _evaluate_axe_rule(rule: str, violation: Mapping[str, Any] | None) -> AuditCheckResult:
    check_id = f"axe_{rule.replace('-', '_')}"
    if violation is None:
        return AuditCheckResult(
            check=check_id,
            category="usability",
            status="pass",
            score=1.0,
            detail=f"axe-core rule '{rule}' passed.",
        )
    impact = str(violation.get("impact", "moderate")).lower()
    is_err = impact in {"critical", "serious"}
    return AuditCheckResult(
        check=check_id,
        category="usability",
        status="error" if is_err else "warning",
        score=0.0 if is_err else 0.5,
        detail=f"axe-core '{rule}' violation (impact: {impact}).",
        recommendation=f"Fix accessibility rule '{rule}'.",
    )


def _grade_cwv(value: float, pass_max: float, warn_max: float) -> AuditStatus:
    if value <= pass_max:
        return "pass"
    return "warning" if value <= warn_max else "error"


def _extract_psi_checks(payload: Mapping[str, Any], strategy: str) -> list[AuditCheckResult]:
    audits = payload.get("lighthouseResult", {}).get("audits", {})
    specs = (
        ("psi_lcp", "largest-contentful-paint", 2500.0, 4000.0, "ms"),
        ("psi_cls", "cumulative-layout-shift", 0.1, 0.25, ""),
        ("psi_inp", "interaction-to-next-paint", 200.0, 500.0, "ms"),
    )
    score_map = {"pass": 1.0, "warning": 0.5, "error": 0.0}
    checks: list[AuditCheckResult] = []
    for check_name, audit_key, pass_max, warn_max, unit in specs:
        metric = audits.get(audit_key, {})
        val = float(metric.get("numericValue", 0.0))
        status = _grade_cwv(val, pass_max, warn_max)
        checks.append(
            AuditCheckResult(
                check=check_name,
                category="performance",
                status=status,
                score=score_map[status],
                detail=f"{audit_key}: {val:.2f}{unit} ({strategy}).",
                recommendation=(
                    f"Improve {audit_key} below {pass_max}{unit}." if status != "pass" else None
                ),
                metadata={"value": val, "strategy": strategy},
            )
        )
    return checks


class GooglePageSpeedAdapter:
    """Optional Google PageSpeed Insights v5 API adapter implementing PageSpeedPort."""

    def __init__(self, *, api_key: str, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def healthcheck(self) -> None:
        """Ensure PSI API key is configured."""
        if not self.api_key.strip():
            msg = "Google PageSpeed Insights API key is required when PSI is enabled."
            raise ValueError(msg)

    def fetch_metrics(self, url: str, strategy: str = "mobile") -> list[AuditCheckResult]:
        """Fetch Core Web Vitals via PSI API, falling back to info diagnostic on error/missing."""
        if not self.api_key.strip():
            return [
                AuditCheckResult(
                    check="psi_metrics",
                    category="performance",
                    status="info",
                    score=0.0,
                    detail="PSI API key not configured; using local performance metrics.",
                )
            ]
        query = urllib.parse.urlencode({"url": url, "strategy": strategy, "key": self.api_key})
        try:
            resp = safe_http.safe_request("GET", f"{PSI_ENDPOINT}?{query}", timeout=self.timeout)
            if not 200 <= resp.status_code < 300:
                msg = f"PSI returned HTTP {resp.status_code}"
                raise ValueError(msg)
            payload = json.loads(resp.text)
            if not isinstance(payload, Mapping):
                msg = "Invalid PSI response format"
                raise ValueError(msg)
            return _extract_psi_checks(payload, strategy)
        except (
            safe_http.SafeHTTPError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            ValueError,
        ) as exc:
            return [
                AuditCheckResult(
                    check="psi_metrics",
                    category="performance",
                    status="info",
                    score=0.0,
                    detail=f"Optional PSI metrics unavailable: {exc}",
                )
            ]


def run_optional_toggles(
    url: str,
    *,
    config: CrawlConfig | None,
    browser_auditor: BrowserAuditPort | None = None,
    pagespeed_client: PageSpeedPort | None = None,
) -> list[AuditCheckResult]:
    """Execute enabled optional browser/PSI audit adapters with safe timeout isolation."""
    if config is None:
        return []
    extras: list[AuditCheckResult] = []
    if config.axe_enabled:
        auditor = browser_auditor if browser_auditor is not None else PlaywrightAxeAdapter()
        try:
            extras.extend(auditor.audit_accessibility(url))
        except Exception as exc:  # noqa: BLE001
            extras.append(
                AuditCheckResult(
                    check="browser_axe_audit",
                    category="usability",
                    status="info",
                    score=0.0,
                    detail=f"Optional browser audit timed out/failed: {exc}",
                )
            )
    if config.psi_enabled:
        psi = (
            pagespeed_client if pagespeed_client is not None else GooglePageSpeedAdapter(api_key="")
        )
        try:
            extras.extend(psi.fetch_metrics(url))
        except Exception as exc:  # noqa: BLE001
            extras.append(
                AuditCheckResult(
                    check="psi_metrics",
                    category="performance",
                    status="info",
                    score=0.0,
                    detail=f"Optional psi metrics timed out/failed: {exc}",
                )
            )
    return extras
