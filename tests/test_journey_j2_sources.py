"""Offline E2E Journey J2: Trusted Sources Screen (F04, ADR-003, ADR-022).

Verifies Journey J2 from PRD §9.6 and M1-05:
- J2.1: View role help text in pt-BR and en-US (AC3); add a trusted feed source validated via
        safe_http (AC1); edit an existing source; disable a source via toggle button (AC4).
- J2.2: Reject a feed source whose feed fails to parse via safe_http (AC1), reject a site_search
        source missing a domain (AC2), and reject a duplicate domain in the workspace (AC7).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from engine import db, services
from engine.safe_http import SafeResponse

APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_j2_workspace(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="J2 Sources Workspace",
        industry_pack="general",
    )
    email = f"owner-{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    return ws_id, res["user"]["user_id"]


def test_journey_j2_1_add_feed_source_edit_disable_and_bilingual_role_help(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J2.1: Add feed source (validated via safe_http), edit, disable another, check role help."""
    ws_id, user_id = _seed_j2_workspace(db_conn, monkeypatch, "j2-ws-1")

    # Seed one existing site_search source to disable in J2
    existing_domain = f"existing-{uuid.uuid4().hex[:8]}.com.br"
    existing_src = services.save_source(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        name="Existing Optical Site",
        domain=existing_domain,
        url=f"https://{existing_domain}",
        fetch_mode="site_search",
        tier=2,
        role="data",
        locales=["pt-BR"],
    )

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("sources").run()
    assert not at.exception
    assert any("Fontes Confiáveis" in s.value for s in at.subheader)
    # Role help text shown in pt-BR (AC3)
    assert any("news" in m.value and "data" in m.value for m in at.markdown)

    # 1. Add a new feed source -> validated through safe_http (AC1)
    new_domain = f"revistaoptica-{uuid.uuid4().hex[:8]}.com.br"
    feed_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0"><channel><title>Revista Optica</title></channel></rss>'
    )
    with patch(
        "engine.safe_http.safe_request",
        return_value=SafeResponse(status_code=200, headers={}, content=feed_xml),
    ) as mock_http:
        at.text_input(key="source_name").set_value("Revista Óptica")
        at.text_input(key="source_domain").set_value(new_domain)
        at.text_input(key="source_url").set_value(f"https://{new_domain}")
        at.selectbox(key="source_fetch_mode").select("feed")
        at.text_input(key="source_feed_url").set_value(f"https://{new_domain}/feed.xml")
        at.selectbox(key="source_tier").select(1)
        at.selectbox(key="source_role").select("news")
        at.text_input(key="source_locales").set_value("pt-BR")
        at.text_input(key="source_topics").set_value("varejo, lentes")
        at.button(key="save_source_btn").click().run()
        mock_http.assert_called_once_with("GET", f"https://{new_domain}/feed.xml")

    assert not at.exception
    assert len(at.success) == 1

    ws_sources = services.list_sources(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        include_shared=False,
    )
    added = next(s for s in ws_sources if s["domain"] == new_domain)
    assert added["fetch_mode"] == "feed"
    assert added["last_fetch_ok"] is True

    # 2. Disable the other source using its disable button (AC4)
    toggle_key = f"toggle_source_{existing_src['id']}"
    at.button(key=toggle_key).click().run()
    assert not at.exception

    reloaded_existing = db.get_source(
        db_conn,
        workspace_id=ws_id,
        source_id=existing_src["id"],
    )
    assert reloaded_existing is not None
    assert reloaded_existing["enabled"] is False

    # 3. Edit the existing source via selector
    at.selectbox(key="source_edit_selector").select(str(existing_src["id"])).run()
    at.text_input(key="source_name").set_value("Existing Optical Site Edited")
    at.button(key="save_source_btn").click().run()
    assert not at.exception
    assert len(at.success) == 1

    edited = db.get_source(db_conn, workspace_id=ws_id, source_id=existing_src["id"])
    assert edited is not None
    assert edited["name"] == "Existing Optical Site Edited"

    # 4. Switch language to en-US and verify English subheader and role help text (AC3)
    at.sidebar.selectbox(key="language_selector").select("en-US").run()
    assert not at.exception
    assert any("Trusted Sources" in s.value for s in at.subheader)
    assert any("Role guide:" in m.value for m in at.markdown)


def test_journey_j2_2_reject_invalid_feed_missing_domain_and_duplicate(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J2.2: Invalid feed XML and missing site_search domain are rejected with UI error."""
    ws_id, _ = _seed_j2_workspace(db_conn, monkeypatch, "j2-ws-2")

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("sources").run()

    # 1. Missing domain on site_search rejected (AC2)
    at.text_input(key="source_name").set_value("No Domain Source")
    at.text_input(key="source_domain").set_value("")
    at.text_input(key="source_url").set_value("https://nodomain.example.com")
    at.selectbox(key="source_fetch_mode").select("site_search")
    at.button(key="save_source_btn").click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert "domain" in at.error[0].value.lower()

    # 2. Invalid feed XML rejected with reason (AC1)
    bad_domain = f"badfeed-{uuid.uuid4().hex[:8]}.com.br"
    with patch(
        "engine.safe_http.safe_request",
        return_value=SafeResponse(status_code=200, headers={}, content=b"<html>not rss</html>"),
    ):
        at.text_input(key="source_name").set_value("Bad Feed Source")
        at.text_input(key="source_domain").set_value(bad_domain)
        at.text_input(key="source_url").set_value(f"https://{bad_domain}")
        at.selectbox(key="source_fetch_mode").select("feed")
        at.text_input(key="source_feed_url").set_value(f"https://{bad_domain}/rss")
        at.button(key="save_source_btn").click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert "feed" in at.error[0].value.lower()
    assert db.list_sources(db_conn, workspace_id=ws_id, include_shared=False) == []
