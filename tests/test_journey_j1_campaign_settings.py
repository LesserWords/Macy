"""Offline E2E Journey J1: Campaign Settings and Bilingual UI (F02, F17, F19, ADR-004, ADR-015).

Verifies Journey J1 from PRD §9.6 and M1-03:
- J1.1: Pick locale (pt-BR, then en-US), set articles/week = 1, set spend caps, save;
        verify live estimated monthly cost update when volume changes before saving (AC6);
        verify saved + audited in events (before/after); switch UI language pt-BR <-> en-US.
- J1.2: Missing spend cap and run_cap > monthly_cap rejected in UI (AC3).
- J1.3: Unsupported locale rejected showing active plugin name (AC2), and invalid weights (AC4).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from engine import db, services

APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_j1_workspace(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
    name: str,
) -> tuple[uuid.UUID, str]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name=name,
        industry_pack="general",
    )
    email = f"owner-{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    return ws_id, email


def test_journey_j1_1_save_pt_br_then_en_us_and_switch_ui_language(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J1.1: Configure pt-BR then en-US, volume=1, caps, live estimate, and UI language switch."""
    ws_id, _ = _seed_j1_workspace(db_conn, monkeypatch, "j1-ws-1", "Acme Demo")

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("campaigns").run()
    assert not at.exception
    assert any("Configurações da Campanha" in s.value for s in at.subheader)
    assert any("$ 16,00 USD" in m.value for m in at.markdown)

    # AC6: Change volume to 3 -> estimated monthly cost updates BEFORE saving ($28.00)
    at.number_input(key="campaign_articles_per_week").set_value(3).run()
    assert any("$ 28,00 USD" in m.value for m in at.markdown)
    assert db.list_campaigns(db_conn, workspace_id=ws_id) == []

    # Set volume = 1, locale = pt-BR, market = BR, spend caps, and save
    at.number_input(key="campaign_articles_per_week").set_value(1).run()
    at.text_input(key="campaign_locale").set_value("pt-BR")
    at.text_input(key="campaign_market").set_value("BR")
    at.text_input(key="campaign_monthly_cap").set_value("150.00")
    at.text_input(key="campaign_run_cap").set_value("20.00")
    at.button(key="save_campaign_btn").click().run()

    assert not at.exception
    assert len(at.success) == 1
    campaigns = db.list_campaigns(db_conn, workspace_id=ws_id)
    assert len(campaigns) == 1
    assert campaigns[0]["locale"] == "pt-BR"
    assert campaigns[0]["monthly_cap_usd"] == Decimal("150.00")
    assert campaigns[0]["run_cap_usd"] == Decimal("20.00")

    # Update locale to en-US and market to US, then save
    at.text_input(key="campaign_locale").set_value("en-US")
    at.text_input(key="campaign_market").set_value("US")
    at.button(key="save_campaign_btn").click().run()
    assert not at.exception
    assert len(at.success) == 1

    events_updated = [
        e for e in db.list_events(db_conn, workspace_id=ws_id) if e["type"] == "settings.changed"
    ]
    assert len(events_updated) == 2
    assert events_updated[0]["payload"]["before"]["locale"] == "pt-BR"
    assert events_updated[0]["payload"]["after"]["locale"] == "en-US"
    assert events_updated[1]["payload"]["before"] is None

    # Switch UI language pt-BR -> en-US and verify labels change and persist on reload
    at.sidebar.selectbox(key="language_selector").select("en-US").run()
    assert not at.exception
    assert any("Campaign Settings" in s.value for s in at.subheader)
    assert any("$ 16.00 USD" in m.value for m in at.markdown)

    at_reload = AppTest.from_file(APP_FILE, default_timeout=15)
    at_reload.run()
    at_reload.sidebar.radio(key="main_nav").set_value("campaigns").run()
    assert any("Campaign Settings" in s.value for s in at_reload.subheader)
    assert at_reload.text_input(key="campaign_locale").value == "en-US"
    assert at_reload.text_input(key="campaign_monthly_cap").value == "150.00"


def test_journey_j1_2_missing_and_invalid_caps_rejected(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J1.2: Missing spend caps and run_cap > monthly_cap are rejected with UI error (AC3)."""
    ws_id, _ = _seed_j1_workspace(db_conn, monkeypatch, "j1-ws-2", "Caps Validation WS")

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("campaigns").run()

    # 1. Click Save with missing spend caps (empty by default) -> rejected
    at.button(key="save_campaign_btn").click().run()
    assert not at.exception
    assert len(at.error) == 1
    assert "spend cap" in at.error[0].value.lower()
    assert db.list_campaigns(db_conn, workspace_id=ws_id) == []

    # 2. Set run_cap > monthly_cap -> rejected
    at.text_input(key="campaign_monthly_cap").set_value("50.00")
    at.text_input(key="campaign_run_cap").set_value("75.00")
    at.button(key="save_campaign_btn").click().run()
    assert not at.exception
    assert len(at.error) == 1
    assert "run_cap_usd" in at.error[0].value
    assert db.list_campaigns(db_conn, workspace_id=ws_id) == []


def test_journey_j1_3_unsupported_locale_and_invalid_weights_rejected(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J1.3: Unsupported locale shows plugin error (AC2); invalid weights rejected (AC4)."""
    ws_id, _ = _seed_j1_workspace(db_conn, monkeypatch, "j1-ws-3", "Plugin Error WS")

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("campaigns").run()

    # 1. Unsupported locale 'fr-FR' shows plugin error naming 'openrouter' (AC2)
    at.text_input(key="campaign_locale").set_value("fr-FR")
    at.text_input(key="campaign_market").set_value("FR")
    at.text_input(key="campaign_monthly_cap").set_value("100.00")
    at.text_input(key="campaign_run_cap").set_value("15.00")
    at.button(key="save_campaign_btn").click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert "openrouter" in at.error[0].value
    assert db.list_campaigns(db_conn, workspace_id=ws_id) == []

    # 2. Valid locale 'pt-BR' but weights summing to 120 != 100 -> rejected (AC4)
    at.text_input(key="campaign_locale").set_value("pt-BR")
    at.text_input(key="campaign_market").set_value("BR")
    at.number_input(key="weight_novelty").set_value(50)
    at.button(key="save_campaign_btn").click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert "100" in at.error[0].value
    assert db.list_campaigns(db_conn, workspace_id=ws_id) == []


def test_journey_j1_4_brand_profile_edit_save_versioning_and_per_field_errors(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J1.4: Edit and save brand profile (v1 -> v2) and show per-field validation errors (F03)."""
    ws_id, _ = _seed_j1_workspace(db_conn, monkeypatch, "j1-ws-4", "Brand Profile WS")

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("brand_profile").run()
    assert not at.exception
    assert any("Perfil da Marca" in s.value for s in at.subheader)

    # 1. Submit invalid brand profile: empty voice + invalid regex in prohibited_phrases (AC3)
    at.text_area(key="brand_voice").set_value("   ")
    at.text_area(key="brand_positioning").set_value("ERP para óticas")
    at.text_area(key="brand_prohibited_phrases").set_value("[unclosed")
    at.button(key="save_brand_btn").click().run()

    assert not at.exception
    error_messages = [e.value for e in at.error]
    assert any("voice" in msg for msg in error_messages)
    assert any("prohibited_phrases" in msg for msg in error_messages)
    assert db.get_current_brand_profile(db_conn, workspace_id=ws_id) is None

    # 2. Save valid brand profile v1 (AC1)
    at.text_area(key="brand_voice").set_value("Consultivo e direto")
    at.text_area(key="brand_positioning").set_value("ERP especializado para óticas")
    at.text_area(key="brand_capabilities").set_value("Controle de estoque\nOrdens de serviço")
    at.text_area(key="brand_prohibited_claims").set_value("Lucro 100% garantido")
    at.text_area(key="brand_prohibited_phrases").set_value(r"\bmelhor do mundo\b")
    at.text_area(key="brand_cta_rules").set_value("TOFU: educativo\nBOFU: demonstração")
    at.button(key="save_brand_btn").click().run()

    assert not at.exception
    assert len(at.success) == 1
    current_v1 = db.get_current_brand_profile(db_conn, workspace_id=ws_id)
    assert current_v1 is not None
    assert current_v1["version"] == 1
    assert current_v1["payload"]["capabilities"] == ["Controle de estoque", "Ordens de serviço"]

    # 3. Edit and save again -> creates v2, v1 stays readable (AC1)
    at.text_area(key="brand_voice").set_value("Consultivo, técnico e acessível")
    at.button(key="save_brand_btn").click().run()

    assert not at.exception
    assert len(at.success) == 1
    current_v2 = db.get_current_brand_profile(db_conn, workspace_id=ws_id)
    assert current_v2 is not None
    assert current_v2["version"] == 2
    assert current_v2["payload"]["voice"] == "Consultivo, técnico e acessível"

    versions = db.list_brand_profiles(db_conn, workspace_id=ws_id)
    assert len(versions) == 2
    assert versions[1]["version"] == 1
    assert versions[1]["is_current"] is False

    # 4. Switch UI language to en-US and verify Brand Profile screen in English
    at.sidebar.selectbox(key="language_selector").select("en-US").run()
    assert not at.exception
    assert any("Brand Profile" in s.value for s in at.subheader)
    assert any("v2" in m.value for m in at.markdown)
