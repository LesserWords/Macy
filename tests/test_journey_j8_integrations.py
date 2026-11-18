"""Offline E2E tests for Journey J8: Integrations & secret hygiene (M1-06, F05 AC1-AC5, AC7)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from cryptography.fernet import Fernet
from streamlit.testing.v1 import AppTest

from engine import db, services

APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_j8_workspace(
    conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    slug_prefix: str = "j8-integrations",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a workspace + owner and configure E2E environment variables."""
    slug = f"{slug_prefix}-{uuid.uuid4().hex[:8]}"
    email = f"owner-{slug}@example.com"
    ws_id = db.create_workspace(
        conn,
        slug=slug,
        name=f"Workspace {slug}",
        industry_pack="general",
    )
    res = services.add_workspace_owner(
        conn,
        workspace_slug=slug,
        email=email,
    )
    user_id: uuid.UUID = res["user"]["user_id"]

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)
    return ws_id, user_id


def _collect_all_page_strings(at: AppTest) -> str:
    """Gather all rendered text, widget values, and session state strings from AppTest."""
    parts: list[str] = []
    for group in (at.markdown, at.subheader, at.title, at.success, at.error, at.info, at.warning):
        parts.extend(str(el.value) for el in group)
    for ti in at.text_input:
        parts.append(str(ti.value))
    for ta in at.text_area:
        parts.append(str(ta.value))
    for k in at.session_state:
        parts.append(f"{k}={at.session_state[k]}")
    return "\n".join(parts)


def test_journey_j8_1_configure_openrouter_stage_models_test_connection_and_secret_hygiene(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """J8.1: Render schema form, save OpenRouter key + stage models, test connection & hygiene."""
    ws_id, _ = _seed_j8_workspace(db_conn, monkeypatch, "j8-ws-1")
    planted_key = f"sk-or-v1-planted-secret-{uuid.uuid4().hex}"

    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("integrations").run()
    assert not at.exception
    assert any("Integrações" in s.value for s in at.subheader)

    # Select port 'llm' and plugin 'openrouter' (AC1)
    at.selectbox(key="integration_port").select("llm").run()
    at.selectbox(key="integration_plugin").select("openrouter").run()
    assert not at.exception

    # Fill OpenRouter secret key and change 'brief' stage model (AC1, AC5)
    with patch("engine.safe_http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        at.text_input(key="int_secret_llm_openrouter_api_key").set_value(planted_key)
        at.text_input(key="int_stage_llm_openrouter_brief").set_value("anthropic/claude-sonnet-4.5")
        at.selectbox(key="int_field_llm_openrouter_data_policy").select("deny")
        at.button(key="save_integration_btn").click().run()

    assert not at.exception
    assert len(at.success) >= 1

    # Prove secret hygiene (Step 3 / AC2):
    # 1) Planted secret is absent from page elements, widget state, and session_state
    page_dump = _collect_all_page_strings(at)
    assert planted_key not in page_dump
    # Masked indicator is shown
    assert any("********" in m.value for m in at.markdown)

    # 2) Planted secret is absent from logs
    assert planted_key not in caplog.text

    # 3) Planted secret is absent from events table rows
    events = db.list_events(db_conn, workspace_id=ws_id)
    int_events = [e for e in events if e["entity_type"] == "integration"]
    assert len(int_events) == 1
    assert planted_key not in str(int_events[0])
    assert int_events[0]["payload"]["after"]["masked_secrets"]["api_key"] == "********"
    assert (
        int_events[0]["payload"]["after"]["settings"]["models_by_stage"]["brief"]
        == "anthropic/claude-sonnet-4.5"
    )

    # Click Test connection (AC3) -> runs healthcheck() and stores last_test_ok=True
    with patch("engine.safe_http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        at.button(key="test_integration_btn").click().run()

    assert not at.exception
    assert len(at.success) >= 1
    row = db.get_workspace_integration(db_conn, workspace_id=ws_id, port="llm")
    assert row is not None
    assert row["last_test_ok"] is True
    assert row["last_tested_at"] is not None


def test_journey_j8_2_configure_publisher_ssrf_refusal_and_other_field_types(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J8.2: Refuse private/metadata URL on save (AC7) and configure int/float/json fields."""
    ws_id, _ = _seed_j8_workspace(db_conn, monkeypatch, "j8-ws-2")

    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("integrations").run()

    # Switch to publisher port and test SSRF private IP refusal (AC7)
    at.selectbox(key="integration_port").select("publisher").run()
    at.selectbox(key="integration_plugin").select("fake").run()
    at.text_input(key="int_field_publisher_fake_endpoint_url").set_value(
        "http://127.0.0.1:8000/webhook"
    )
    at.button(key="save_integration_btn").click().run()
    assert len(at.error) >= 1
    assert any("Unsafe URL" in e.value for e in at.error)
    assert db.get_workspace_integration(db_conn, workspace_id=ws_id, port="publisher") is None

    # Switch to keyword_provider port (integer + float + password fields) and save
    at.selectbox(key="integration_port").select("keyword_provider").run()
    at.selectbox(key="integration_plugin").select("fake").run()
    at.number_input(key="int_field_keyword_provider_fake_default_volume").set_value(2500)
    at.number_input(key="int_field_keyword_provider_fake_default_cpc").set_value(2.5)
    at.text_input(key="int_secret_keyword_provider_fake_api_key").set_value("kw-secret-123")
    at.button(key="save_integration_btn").click().run()
    assert not at.exception
    assert len(at.success) >= 1

    kw_row = db.get_workspace_integration(db_conn, workspace_id=ws_id, port="keyword_provider")
    assert kw_row is not None
    assert kw_row["settings"]["default_volume"] == 2500
    assert kw_row["settings"]["default_cpc"] == 2.5
