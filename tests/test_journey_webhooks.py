"""Offline E2E tests for Webhooks admin UI journey (M2-04, F06 AC4, AC7)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from streamlit.testing.v1 import AppTest

from engine import db as engine_db
from engine import events as engine_events
from engine import services as engine_services

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _bootstrap_webhooks_journey(
    conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[uuid.UUID, uuid.UUID]:
    unique_tag = uuid.uuid4().hex[:8]
    ws_slug = f"journey-wh-{unique_tag}"
    owner_mail = f"wh-owner-{unique_tag}@example.com"

    ws_id = engine_db.create_workspace(
        conn,
        slug=ws_slug,
        name=f"Webhooks E2E {unique_tag}",
        industry_pack="general",
    )
    owner_payload = engine_services.add_workspace_owner(
        conn,
        workspace_slug=ws_slug,
        email=owner_mail,
    )
    user_id = owner_payload["user"]["user_id"]

    monkeypatch.setenv("MACY_TEST_USER_EMAIL", owner_mail)
    monkeypatch.setenv("MACY_TEST_WORKSPACE_SLUG", ws_slug)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    return ws_id, user_id


def test_journey_webhooks_create_and_view(monkeypatch: pytest.MonkeyPatch) -> None:
    with engine_db.get_connection() as conn:
        ws_id, user_id = _bootstrap_webhooks_journey(conn, monkeypatch)

    app = AppTest.from_file(APP_PATH, default_timeout=15)
    app.run()
    assert not app.exception

    # Navigate to Webhooks page
    app.sidebar.radio(key="main_nav").set_value("webhooks").run()
    assert not app.exception

    # Fill target webhook url and submit
    app.text_input(key="wh_url_input").input("https://example.com/n8n-hook").run()
    app.button(key="wh_create_btn").click().run()
    assert not app.exception

    # Acknowledge signing secret displayed once
    ack_btn = app.button(key="ack_secret_btn")
    assert ack_btn is not None
    ack_btn.click().run()
    assert not app.exception

    # Verify subscription row exists
    subs = engine_services.list_subscriptions(workspace_id=ws_id, user_id=user_id)
    assert len(subs) == 1
    assert subs[0]["url"] == "https://example.com/n8n-hook"


def test_journey_webhooks_resend_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    with engine_db.get_connection() as conn:
        ws_id, user_id = _bootstrap_webhooks_journey(conn, monkeypatch)
        sub = engine_services.save_subscription(
            conn,
            workspace_id=ws_id,
            user_id=user_id,
            url="https://example.com/delivery-test",
            event_types=["topic.decided"],
            is_production=False,
        )
        sub_id = sub["id"]
        event_pk = engine_events.emit(
            conn,
            workspace_id=ws_id,
            event_type="topic.decided",
            entity_type="opportunity",
            entity_id=uuid.uuid4(),
            payload={"decision": "approved"},
            actor_user_id=user_id,
        )

    app = AppTest.from_file(APP_PATH, default_timeout=15)
    app.run()
    assert not app.exception

    app.sidebar.radio(key="main_nav").set_value("webhooks").run()
    assert not app.exception

    # Click resend button on the delivery
    resend_key = f"resend_{sub_id}_{event_pk}"
    resend_btn = app.button(key=resend_key)
    assert resend_btn is not None
    resend_btn.click().run()
    assert not app.exception

    # Delivery status was re-sent and recorded
    deliveries = engine_services.list_deliveries(workspace_id=ws_id, user_id=user_id)
    assert len(deliveries) == 1
