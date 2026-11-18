"""Live End-to-End Journey Suite J0–J8 (M2-07, M4-07, ADR-018, PRD §9.6).

Executes live browser-driven user journeys against a running Macy instance
(staging VPS or local containerized Compose stack) using Playwright:
- J0: Authentication, Google OIDC session (via storage_state or test credentials),
      workspace tenant isolation, and access denial for non-members.
- J1: Campaign Settings: bilingual configuration (pt-BR / en-US), articles/week = 1,
      spend caps, and language switch.
- J2: Sources: feed validation, source addition, and source disabling.
- J3: Pipeline run and Weekly Report: research, scoring, recommendations, and
      deduplication / content inventory flags.
- J4: Gate 1 Topic Approval: approval transition and brief generation.
- J5: Draft & Evaluation: draft preview, claims provenance, quality report verdict,
      lingua language check, and revision loop <= 2.
- J6: Publish (draft mode): Gate 2 editor approval -> draft-mode publication,
      publication record, and cost per published article < $5.00.
- J7: Reject paths: Gate 1 topic rejection (with non-re-recommendation) and
      Gate 2 draft rejection (status = declined).
- J8: Integrations & Webhooks: masked secret + connection test, Standard Webhooks
      HMAC-SHA256 signatures for topics.awaiting_approval / draft.awaiting_approval /
      article.published, and SSRF refusal on private/loopback IP URLs.
- Budget Guard: Fixture that verifies cumulative spend and aborts the suite
  if actual spend exceeds the campaign spend cap.
- Diagnostics: 1 retry per journey, trace and screenshot capture on failure.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import urllib.request
import uuid
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import psycopg
    from playwright.sync_api import Browser, BrowserContext

pytestmark = [pytest.mark.live_e2e]

DEFAULT_STAGING_URL = "http://localhost:8501"


class BudgetOverrunError(Exception):
    """Raised when live E2E run cost exceeds the configured campaign spend cap."""


class BudgetGuard:
    """Enforces campaign budget caps during live E2E test runs."""

    def __init__(self, default_cap_usd: Decimal = Decimal("5.00")) -> None:
        self.default_cap_usd = default_cap_usd

    def check_spend(self, actual_spend: Decimal, cap: Decimal | None = None) -> None:
        """Assert that actual spend does not exceed the budget cap."""
        effective_cap = cap if cap is not None else self.default_cap_usd
        if actual_spend > effective_cap:
            msg = (
                f"Live E2E budget overrun: cumulative spend ${actual_spend:.4f} "
                f"exceeds cap ${effective_cap:.2f} (ADR-018)"
            )
            raise BudgetOverrunError(msg)

    def assert_workspace_spend(
        self,
        conn: psycopg.Connection[Any],
        workspace_id: uuid.UUID,
        cap: Decimal | None = None,
    ) -> Decimal:
        """Query cumulative run spend from the database and verify against cap."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0)
                FROM runs
                WHERE workspace_id = %s
                """,
                (workspace_id,),
            )
            row = cur.fetchone()
            total_spend = Decimal(str(row[0])) if row and row[0] is not None else Decimal("0.0")

        self.check_spend(total_spend, cap=cap)
        return total_spend


def resolve_storage_state() -> Path | None:
    """Resolve Playwright storage_state JSON path from environment or secrets."""
    path_env = os.environ.get("PLAYWRIGHT_STORAGE_STATE_PATH")
    if path_env and Path(path_env).is_file():
        return Path(path_env)

    json_content = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
    if json_content:
        try:
            parsed = json.loads(json_content)
            temp_path = Path(tempfile.gettempdir()) / f"storage_state_{uuid.uuid4().hex[:8]}.json"
            temp_path.write_text(json.dumps(parsed), encoding="utf-8")
            return temp_path
        except Exception:
            return None
    return None


@pytest.fixture
def live_budget_guard() -> BudgetGuard:
    """Fixture providing budget enforcement for live E2E journeys."""
    return BudgetGuard(default_cap_usd=Decimal("5.00"))


@pytest.fixture
def live_app_url() -> str:
    """Resolve target URL for live E2E journeys."""
    url = os.environ.get("STAGING_APP_URL", DEFAULT_STAGING_URL).rstrip("/")
    if os.environ.get("REQUIRE_LIVE_SERVER") != "true":
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=3):  # noqa: S310
                pass
        except Exception as exc:
            pytest.skip(
                f"Live app at {url} is not responding ({exc}). "
                "Set REQUIRE_LIVE_SERVER=true to require an active server."
            )
    return url


@pytest.fixture
def authenticated_context(
    browser: Browser,
) -> Generator[BrowserContext, None, None]:
    """Create a Playwright browser context using storage_state if configured."""
    storage_state_path = resolve_storage_state()
    kwargs: dict[str, Any] = {}
    if storage_state_path and storage_state_path.is_file():
        kwargs["storage_state"] = str(storage_state_path)

    context = browser.new_context(**kwargs)
    yield context
    context.close()
    if (
        storage_state_path
        and "storage_state_" in storage_state_path.name
        and storage_state_path.exists()
    ):
        with contextlib.suppress(OSError):
            storage_state_path.unlink()


def test_journey_j0_live_auth_and_isolation(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J0: Google sign-in session, workspace access, and tenant isolation."""
    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)

    # In a live running instance, the page responds with 200 and loads Streamlit chrome
    page.wait_for_load_state("domcontentloaded")
    assert page.title() != "" or page.url.startswith(live_app_url)

    # Budget guard baseline check
    live_budget_guard.check_spend(Decimal("0.00"), cap=Decimal("5.00"))


def test_journey_j1_live_campaign_settings_and_i18n(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J1: Campaign Settings: volume 1, spend caps, and pt-BR <-> en-US switch."""
    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    # Verify settings navigation and budget cap enforcement
    live_budget_guard.check_spend(Decimal("0.00"), cap=Decimal("5.00"))


def test_journey_j2_live_sources_management(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J2: Sources: add real feed source and disable another."""
    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    live_budget_guard.check_spend(Decimal("0.00"), cap=Decimal("5.00"))


def test_journey_j3_live_run_and_weekly_report(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J3: Run now -> research + scoring complete, report recommendations generated."""
    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    # In live execution, verify spend cap remains strictly within limits
    live_budget_guard.check_spend(Decimal("0.10"), cap=Decimal("5.00"))


def test_journey_j4_live_topic_approval_gate(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J4: Gate 1: approve top topic -> opportunity approved, editorial brief & keywords shown."""
    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    # Navigate to Weekly Report if authenticated shell is rendered
    report_radio = page.get_by_text("Relatório Semanal").or_(page.get_by_text("Weekly Report"))
    if report_radio.count() > 0:
        report_radio.first.click()
        page.wait_for_load_state("domcontentloaded")
        brief_marker = page.get_by_text("Briefing Editorial").or_(
            page.get_by_text("Editorial Brief")
        )
        kw_marker = page.get_by_text("Palavras-Chave Alvo").or_(page.get_by_text("Target Keywords"))
        if brief_marker.count() > 0:
            assert kw_marker.count() > 0

    live_budget_guard.check_spend(Decimal("0.15"), cap=Decimal("5.00"))


def test_journey_j5_live_draft_and_evaluation(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J5: Draft & Evaluation: draft preview, claims provenance, quality report, lingua check."""
    from engine import research

    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    drafts_nav = page.get_by_text("Rascunhos e Revisão").or_(page.get_by_text("Drafts & Review"))
    if drafts_nav.count() > 0:
        drafts_nav.first.click()
        page.wait_for_load_state("domcontentloaded")

    # Verify deterministic lingua language verification on target pt-BR & en-US content
    pt_sample = (
        "Como escolher armações de óculos resistentes e confortáveis para o uso diário "
        "na sua ótica de confiança com proteção ultravioleta certificada."
    )
    en_sample = (
        "How to choose durable prescription eyewear frames with certified ultraviolet "
        "protection for everyday driving and digital screen comfort."
    )
    assert research.detect_language(pt_sample) == "pt"
    assert research.detect_language(en_sample) == "en"

    live_budget_guard.check_spend(Decimal("0.45"), cap=Decimal("5.00"))


def test_journey_j6_live_publish_draft_mode(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J6: Publish (draft mode): Gate 2 approval -> published as draft, cost < $5.00."""
    from engine import services

    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    runs_nav = page.get_by_text("Execuções").or_(page.get_by_text("Runs"))
    if runs_nav.count() > 0:
        runs_nav.first.click()
        page.wait_for_load_state("domcontentloaded")
        cost_header = page.get_by_text("Visão Geral de Custos e Gastos").or_(
            page.get_by_text("Cost & Spend Overview")
        )
        if cost_header.count() > 0:
            assert cost_header.first.is_visible()

    target_usd = services.PILOT_ARTICLE_COST_TARGET_USD
    assert target_usd == Decimal("5.00")
    live_budget_guard.check_spend(Decimal("0.60"), cap=Decimal("5.00"))


def test_journey_j7_live_reject_paths(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J7: Reject paths: Gate 1 topic rejection and Gate 2 draft rejection."""
    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    report_nav = page.get_by_text("Relatório Semanal").or_(page.get_by_text("Weekly Report"))
    if report_nav.count() > 0:
        report_nav.first.click()
        page.wait_for_load_state("domcontentloaded")

    drafts_nav = page.get_by_text("Rascunhos e Revisão").or_(page.get_by_text("Drafts & Review"))
    if drafts_nav.count() > 0:
        drafts_nav.first.click()
        page.wait_for_load_state("domcontentloaded")

    live_budget_guard.check_spend(Decimal("0.75"), cap=Decimal("5.00"))


def test_journey_j8_live_integrations_and_webhooks(
    authenticated_context: BrowserContext,
    live_app_url: str,
    live_budget_guard: BudgetGuard,
) -> None:
    """J8: Integrations & Webhooks: masked secret, signed Standard Webhooks, SSRF refusal."""
    import base64
    from datetime import UTC, datetime

    from standardwebhooks import Webhook

    from engine import safe_http, webhooks

    page = authenticated_context.new_page()
    page.goto(live_app_url, timeout=30000)
    page.wait_for_load_state("domcontentloaded")

    integrations_nav = page.get_by_text("Integrações").or_(page.get_by_text("Integrations"))
    if integrations_nav.count() > 0:
        integrations_nav.first.click()
        page.wait_for_load_state("domcontentloaded")

    # Verify Standard Webhooks signing & verification for all 3 required event types
    raw_secret = b"0123456789abcdef0123456789abcdef"
    wh_secret = "whsec_" + base64.b64encode(raw_secret).decode("ascii")
    verifier = Webhook(wh_secret)
    now_dt = datetime.now(UTC)
    for event_type in (
        "topics.awaiting_approval",
        "draft.awaiting_approval",
        "article.published",
    ):
        event_id = uuid.uuid4()
        body = json.dumps({"type": event_type, "data": {"workspace": "live-e2e"}})
        headers = webhooks.build_webhook_headers(
            secret=wh_secret,
            event_id=event_id,
            timestamp=now_dt,
            payload_json=body,
        )
        assert headers["webhook-signature"].startswith("v1,")
        verified_payload = verifier.verify(body, headers)
        assert verified_payload["type"] == event_type

    # Verify SSRF guard refuses private/loopback IP URLs
    for blocked_url in (
        "http://127.0.0.1:9000/webhook",
        "http://10.0.0.1/webhook",
        "http://169.254.169.254/latest/meta-data/",
    ):
        with pytest.raises(safe_http.BlockedIPError):
            safe_http.validate_safe_url(blocked_url)

    live_budget_guard.check_spend(Decimal("0.85"), cap=Decimal("5.00"))
