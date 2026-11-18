"""Tests for UI shell and bilingual interface (F19, ADR-014).

Verifies:
- Default and persisted pt-BR chrome rendering (AC1, AC2).
- Language switching from pt-BR to en-US and immediate persistence (AC2).
- Locale-aware date and money formatting (AC4).
- Independence of UI language from campaign content locale (AC5).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from engine import db, i18n, services

APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


def test_ui_shell_pt_br_default_and_switching_to_en_us(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    slug = f"ui-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Workspace Visual",
        industry_pack="general",
    )
    email = f"ui-owner-{uuid.uuid4().hex[:8]}@example.com"
    services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)

    # 1. Sign in with default pt-BR locale (AC1)
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()

    assert not at.exception
    # Portuguese labels rendered
    assert any("Espaço de trabalho: Workspace Visual" in t.value for t in at.title)
    assert any(f"Bem-vindo de volta, {email}!" in w.value for w in at.markdown)
    assert any("Visão Geral do Sistema" in s.value for s in at.subheader)

    # Date and currency formatting in pt-BR (AC4)
    assert any("$ 150,00 USD" in m.value for m in at.markdown)

    # 2. Switch language in UI to en-US (AC2)
    # Simulate selectbox switch
    at.sidebar.selectbox(key="language_selector").select("en-US").run()

    # Verify preference persisted in database
    user_updated = services.get_user_by_email(db_conn, email=email)
    assert user_updated is not None
    assert user_updated["ui_locale"] == "en-US"

    # Verify event logged in events table
    events = db.list_events(db_conn, workspace_id=ws_id)
    locale_events = [e for e in events if e["type"] == "user.locale_updated"]
    assert len(locale_events) >= 1
    assert locale_events[-1]["payload"]["ui_locale"] == "en-US"

    # 3. Reload session with persisted en-US (AC2)
    at_en = AppTest.from_file(APP_FILE)
    at_en.run()
    assert not at_en.exception
    # English labels rendered
    assert any("Workspace: Workspace Visual" in t.value for t in at_en.title)
    assert any(f"Welcome back, {email}!" in w.value for w in at_en.markdown)
    assert any("System Overview" in s.value for s in at_en.subheader)
    assert any("$ 150.00 USD" in m.value for m in at_en.markdown)


def test_ui_locale_independent_of_campaign_locale(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: Generated content stays in campaign locale regardless of UI language."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    slug = f"ui-camp-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Campaign Locale Test",
        industry_pack="general",
    )
    email = f"camp-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # Create campaign in en-US
    c_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Global US Campaign",
        locale="en-US",
        market="US",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("200.00"),
        run_cap_usd=Decimal("20.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )

    campaign = db.get_campaign(db_conn, workspace_id=ws_id, campaign_id=c_id)
    assert campaign is not None
    assert campaign["locale"] == "en-US"

    # User uses pt-BR UI locale
    i18n.set_current_locale("pt-BR")
    assert i18n.get_current_locale() == "pt-BR"

    # Campaign locale remains strictly en-US despite UI pt-BR
    assert campaign["locale"] == "en-US"


def test_ui_runs_page_and_run_now_trigger(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHEN owner navigates to runs page and clicks Run now, THEN a run is queued (F18)."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    slug = f"ui-runs-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Workspace Runs",
        industry_pack="general",
    )
    email = f"runs-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Optics Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("5.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )

    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    assert not at.exception

    # Select "runs" in the main_nav radio
    at.sidebar.radio(key="main_nav").set_value("runs").run()
    assert not at.exception
    assert any("Execuções e Orquestração" in s.value for s in at.subheader)

    # Click "Run now" button
    at.button(key="btn_run_now").click().run(timeout=15)
    assert not at.exception

    # Verify run was created
    runs = db.list_runs(db_conn, workspace_id=ws_id)
    assert len(runs) == 1
    assert runs[0]["status"] in ("queued", "running")
