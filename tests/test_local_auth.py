"""Tests for M7-02 D2 local PBKDF2-HMAC-SHA256 admin auth and automatic OIDC switch."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest


def test_pbkdf2_password_hashing_and_verification() -> None:
    """D2: Password hashing uses PBKDF2-HMAC-SHA256 (600,000 iterations) and constant-time check."""
    from engine.auth import hash_password, verify_password

    encoded = hash_password("correct-horse-battery-staple")
    assert encoded.startswith("pbkdf2_sha256$600000$")
    assert verify_password("correct-horse-battery-staple", encoded) is True
    assert verify_password("wrong-password", encoded) is False
    assert verify_password("anything", None) is False
    assert verify_password("anything", "malformed-hash") is False

    with pytest.raises(ValueError, match="Password"):
        hash_password("   ")


def test_resolve_auth_mode_switches_automatically() -> None:
    """D2: AUTH_MODE=auto / POCKET_MODE picks local without GOOGLE_CLIENT_ID and oidc with it."""
    from engine.auth import resolve_auth_mode

    assert resolve_auth_mode(env={}) == "oidc"
    assert resolve_auth_mode(env={"POCKET_MODE": "true"}) == "local"
    assert resolve_auth_mode(env={"AUTH_MODE": "auto"}) == "local"
    assert resolve_auth_mode(env={"AUTH_MODE": "auto", "GOOGLE_CLIENT_ID": "cid"}) == "oidc"
    assert resolve_auth_mode(env={"AUTH_MODE": "local", "GOOGLE_CLIENT_ID": "cid"}) == "local"
    assert resolve_auth_mode(env={"AUTH_MODE": "oidc"}) == "oidc"


def test_local_auth_service_and_rate_limiting(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: Local auth verifies DB PBKDF2 hash, enforces 5/15m lockout, and blocks prod fakes."""
    from engine import auth, db, services

    auth.reset_local_login_rate_limits()
    email = f"local-admin-{uuid.uuid4().hex[:8]}@example.com"
    slug = f"ws-local-{uuid.uuid4().hex[:8]}"
    services.create_workspace(db_conn, slug=slug, name="Local Auth WS")
    owner = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = owner["user"]["user_id"]

    strong_pw = "strong-password-123"
    wrong_pw = "wrong-password"
    second_pw = "second-password-123"

    services.set_local_password(db_conn, user_id=user_id, password=strong_pw)

    # Wrong password increments failure counter up to 5 attempts, then locks out
    for _ in range(5):
        with pytest.raises(auth.AuthError, match="Invalid email or password"):
            services.authenticate_local_user(db_conn, email=email, password=wrong_pw)

    with pytest.raises(auth.RateLimitExceededError, match="Too many failed login attempts"):
        services.authenticate_local_user(db_conn, email=email, password=strong_pw)

    # Clearing rate limit allows valid login and sets session state email
    auth.reset_local_login_rate_limits()
    authed = services.authenticate_local_user(db_conn, email=email, password=strong_pw)
    assert authed["user_id"] == user_id
    assert authed["email"] == email

    # Unknown user also fails cleanly
    with pytest.raises(auth.AuthError, match="Invalid email or password"):
        services.authenticate_local_user(db_conn, email="nobody@example.com", password=strong_pw)

    # Production still refuses MACY_TEST_USER_EMAIL even with AUTH_MODE=local
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    with pytest.raises(auth.FakeIdentityForbiddenError):
        auth.get_authenticated_email()

    # Verify DB row directly has password_hash populated
    row = db.get_user_auth_by_email(db_conn, email=email)
    assert row is not None
    assert str(row["password_hash"]).startswith("pbkdf2_sha256$600000$")

    # Managed-connection wrappers (conn=None) and invalid hash decoding edge cases
    assert services.set_local_password(user_id=user_id, password=second_pw) is True
    assert services.authenticate_local_user(email=email, password=second_pw)["user_id"] == user_id
    assert auth.verify_password("x", "pbkdf2_sha256$notanint$a$b") is False


def test_local_auth_ui_login_and_signout(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: Local UI login card authenticates email/password and clears session on sign-out."""
    from unittest.mock import patch

    import streamlit as st

    from engine import auth, services

    auth.reset_local_login_rate_limits()
    email = f"local-ui-{uuid.uuid4().hex[:8]}@example.com"
    slug = f"ws-ui-{uuid.uuid4().hex[:8]}"
    second_pw = "second-password-123"
    services.create_workspace(db_conn, slug=slug, name="Local Auth UI WS")
    owner = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    services.set_local_password(user_id=owner["user"]["user_id"], password=second_pw)

    monkeypatch.delenv("MACY_TEST_USER_EMAIL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "local")
    fake_state: dict[str, Any] = {}
    with (
        patch.object(st, "session_state", fake_state, create=True),
        patch("streamlit.title"),
        patch("streamlit.write"),
        patch("streamlit.text_input", side_effect=[email, "wrong-pw"]),
        patch("streamlit.button", return_value=True),
        patch("streamlit.error") as mock_err,
    ):
        auth.render_login_screen()
        mock_err.assert_called_once()

    with (
        patch.object(st, "session_state", fake_state, create=True),
        patch("streamlit.title"),
        patch("streamlit.write"),
        patch("streamlit.text_input", side_effect=[email, "second-password-123"]),
        patch("streamlit.button", return_value=True),
        patch("streamlit.rerun") as mock_rerun,
    ):
        auth.render_login_screen()
        assert fake_state["local_auth_email"] == email
        mock_rerun.assert_called_once()
        assert auth.get_authenticated_email() == email

    with (
        patch.object(st, "session_state", fake_state, create=True),
        patch("streamlit.sidebar.markdown"),
        patch("streamlit.sidebar.button", return_value=True),
        patch("streamlit.rerun") as mock_rerun,
    ):
        auth.render_user_header(
            user={"email": email},
            workspace={"name": "Local WS", "slug": slug},
        )
        assert "local_auth_email" not in fake_state
        mock_rerun.assert_called_once()

    # Attribute-based session_state sign-out and missing session_state helper branch
    from types import SimpleNamespace

    attr_state = SimpleNamespace(local_auth_email=email)
    with (
        patch.object(st, "session_state", attr_state, create=True),
        patch("streamlit.rerun") as mock_rerun,
    ):
        auth._perform_sign_out()
        assert not hasattr(attr_state, "local_auth_email")
        mock_rerun.assert_called_once()

    with patch("engine.auth.st", SimpleNamespace()):
        assert auth._get_local_session_email() is None
