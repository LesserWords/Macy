"""Offline E2E Journey J0: Google OIDC Sign-In and Workspace Membership (F01, ADR-013, ADR-014).

Verifies the complete authentication journey:
- J0.1: Workspace member signs in via simulated identity, sees workspace data and email (AC1).
- J0.2: Non-member user signs in, access is denied and no workspace data is rendered (AC2).
- J0.3: Workspace B member is refused access to Workspace A data (AC3, AC4).
- J0.4: CLI owner provisioning (`add-owner`) seeds membership and logs audit event (AC5).
- J0.5: Fake test identity is strictly refused when APP_ENV=production or ENV=prod (AC6).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from engine import auth, db, services
from engine.cli import main as cli_main

APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


def test_journey_j0_1_member_signs_in(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J0.1: Member of workspace signs in and sees workspace data (AC1)."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    slug = f"j0-ws-1-{uuid.uuid4().hex[:8]}"
    db.create_workspace(
        db_conn,
        slug=slug,
        name="Workspace One",
        industry_pack="general",
    )
    email = f"alice-{uuid.uuid4().hex[:8]}@example.com"
    services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)

    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()

    assert not at.exception
    assert any("Workspace One" in t.value for t in at.title)
    assert any(email in w.value for w in at.markdown)


def test_journey_j0_2_non_member_access_denied(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J0.2: Non-member user signs in -> Refused with zero workspace data rendered (AC2)."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    non_member_email = f"stranger-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", non_member_email)
    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()

    assert not at.exception
    assert len(at.title) == 0  # No workspace title rendered
    assert any(non_member_email in e.value for e in at.error)


def test_journey_j0_3_cross_tenant_isolation(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J0.3: Member B cannot access Workspace A data (AC3, AC4)."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    slug_a = f"j0-iso-a-{uuid.uuid4().hex[:8]}"
    ws_a_id = db.create_workspace(
        db_conn,
        slug=slug_a,
        name="Workspace Alpha",
        industry_pack="general",
    )
    slug_b = f"j0-iso-b-{uuid.uuid4().hex[:8]}"
    db.create_workspace(
        db_conn,
        slug=slug_b,
        name="Workspace Beta",
        industry_pack="general",
    )

    email_b = f"bob-{uuid.uuid4().hex[:8]}@example.com"
    res_b = services.add_workspace_owner(db_conn, workspace_slug=slug_b, email=email_b)
    user_b_id = res_b["user"]["user_id"]

    # Member B cannot get workspace A
    with pytest.raises(PermissionError, match="not a member"):
        services.get_workspace_for_member(db_conn, workspace_id=ws_a_id, user_id=user_b_id)

    # Bob logging in sees only Workspace Beta
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email_b)
    at_b = AppTest.from_file(APP_FILE, default_timeout=15)
    at_b.run()
    assert not at_b.exception
    assert any("Workspace Beta" in t.value for t in at_b.title)
    assert not any("Workspace Alpha" in t.value for t in at_b.title)


def test_journey_j0_4_cli_owner_seeding_and_access(
    db_conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J0.4: CLI add-owner provisions owner, logs audit event, grants access (AC5)."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)

    slug = f"j0-cli-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Workspace Seeded",
        industry_pack="general",
    )

    cli_email = f"seeded-{uuid.uuid4().hex[:8]}@example.com"
    exit_code = cli_main(["add-owner", slug, cli_email])
    assert exit_code == 0

    events = db.list_events(db_conn, workspace_id=ws_id)
    membership_events = [
        e
        for e in events
        if e["type"] == "membership.created" and e["payload"].get("email") == cli_email
    ]
    assert len(membership_events) == 1
    assert membership_events[0]["payload"]["role"] == "owner"

    monkeypatch.setenv("MACY_TEST_USER_EMAIL", cli_email)
    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()
    assert not at.exception
    assert any("Workspace Seeded" in t.value for t in at.title)


def test_journey_j0_5_fake_identity_refused_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J0.5: Fake test identity strictly refused when APP_ENV=production or ENV=prod (AC6)."""
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", "fake@example.com")

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(auth.FakeIdentityForbiddenError):
        auth.get_authenticated_email()

    monkeypatch.delenv("APP_ENV")
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(auth.FakeIdentityForbiddenError):
        auth.get_authenticated_email()
