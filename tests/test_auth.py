"""Tests for engine.auth: Google OIDC, fake identity checks,
env-driven Streamlit secrets generation, and production restrictions (F01, ADR-013).
"""

import os
import tomllib
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import streamlit as st
from streamlit import config as st_config
from streamlit.auth_util import is_authlib_installed, validate_auth_credentials
from streamlit.runtime.secrets import secrets_singleton

from engine.auth import (
    DEFAULT_GOOGLE_SERVER_METADATA_URL,
    AccessDeniedError,
    AuthConfigurationError,
    AuthError,
    FakeIdentityForbiddenError,
    build_streamlit_auth_secrets,
    default_secrets_output_path,
    derive_redirect_uri,
    ensure_runtime_oidc_secrets,
    get_authenticated_email,
    get_current_identity,
    is_production_environment,
    render_access_denied,
    render_login_screen,
    render_streamlit_secrets_toml,
    render_user_header,
    sync_streamlit_secrets_file,
)
from engine.auth import (
    main as auth_cli_main,
)
from engine.i18n import _


def test_is_production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert is_production_environment() is True

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENV", "prod")
    assert is_production_environment() is False

    monkeypatch.delenv("APP_ENV")
    assert is_production_environment() is True

    monkeypatch.setenv("ENV", "test")
    assert is_production_environment() is False


def test_fake_identity_accepted_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", "Dev-User@Example.Com")
    assert get_authenticated_email() == "dev-user@example.com"


def test_fake_identity_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", "dev-user@example.com")
    with pytest.raises(FakeIdentityForbiddenError, match="forbidden in production"):
        get_authenticated_email()

    monkeypatch.delenv("APP_ENV")
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(FakeIdentityForbiddenError, match="forbidden in production"):
        get_authenticated_email()


def test_fake_identity_from_session_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("MACY_TEST_USER_EMAIL", raising=False)
    with patch.object(st, "session_state", create=True) as mock_state:
        mock_state.test_user_email = "session@example.com"
        assert get_authenticated_email() == "session@example.com"


@pytest.mark.parametrize(
    ("is_logged_in", "email_val", "expected"),
    [
        (True, "Google-User@Example.com", "google-user@example.com"),
        (False, None, None),
        (True, "", None),
    ],
)
def test_authenticated_email_from_streamlit_user(
    monkeypatch: pytest.MonkeyPatch,
    is_logged_in: bool,
    email_val: str | None,
    expected: str | None,
) -> None:
    monkeypatch.delenv("MACY_TEST_USER_EMAIL", raising=False)
    with patch.object(st, "session_state", create=True) as mock_state:
        del mock_state.test_user_email
        mock_st_user = MagicMock(is_logged_in=is_logged_in, email=email_val)
        with patch.object(st, "user", mock_st_user, create=True):
            assert get_authenticated_email() == expected


def test_get_current_identity_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MACY_TEST_USER_EMAIL", raising=False)
    with patch("engine.auth.get_authenticated_email", return_value=None):
        assert get_current_identity() is None


def test_get_current_identity_user_not_in_db() -> None:
    with (
        patch("engine.auth.get_authenticated_email", return_value="unknown@example.com"),
        patch("engine.services.get_user_by_email", return_value=None),
    ):
        identity = get_current_identity()
        assert identity is not None
        assert identity["email"] == "unknown@example.com"
        assert identity["user"] is None
        assert identity["memberships"] == []


def test_get_current_identity_member_success() -> None:
    fake_user = {"user_id": uuid.uuid4(), "email": "member@example.com", "name": "Member"}
    fake_memberships = [{"workspace_id": uuid.uuid4(), "role": "owner"}]
    with (
        patch("engine.auth.get_authenticated_email", return_value="member@example.com"),
        patch("engine.services.get_user_by_email", return_value=fake_user),
        patch("engine.services.list_user_workspaces", return_value=fake_memberships),
    ):
        identity = get_current_identity()
        assert identity is not None
        assert identity["email"] == "member@example.com"
        assert identity["user"] == fake_user
        assert identity["memberships"] == fake_memberships


def test_render_login_screen() -> None:
    with (
        patch("streamlit.title") as mock_title,
        patch("streamlit.write") as mock_write,
        patch("streamlit.button", return_value=True) as mock_button,
        patch("streamlit.login") as mock_login,
    ):
        render_login_screen()
        mock_title.assert_called_once()
        mock_write.assert_called_once()
        mock_button.assert_called_once_with(_("Sign in with Google"))
        mock_login.assert_called_once_with("google")

    # Not clicked
    with (
        patch("streamlit.title"),
        patch("streamlit.write"),
        patch("streamlit.button", return_value=False),
        patch("streamlit.login") as mock_login,
    ):
        render_login_screen()
        mock_login.assert_not_called()


def test_render_access_denied() -> None:
    with (
        patch("streamlit.error") as mock_error,
        patch("streamlit.write") as mock_write,
        patch("streamlit.button", return_value=True) as mock_button,
        patch("streamlit.logout") as mock_logout,
    ):
        render_access_denied("denied@example.com")
        mock_error.assert_called_once()
        assert "denied@example.com" in mock_error.call_args[0][0]
        mock_write.assert_called_once()
        mock_button.assert_called_once_with(_("Sign out"))
        mock_logout.assert_called_once()

    # Not clicked
    with (
        patch("streamlit.error"),
        patch("streamlit.write"),
        patch("streamlit.button", return_value=False),
        patch("streamlit.logout") as mock_logout,
    ):
        render_access_denied("denied@example.com")
        mock_logout.assert_not_called()


def test_render_user_header() -> None:
    with (
        patch("streamlit.sidebar.markdown") as mock_md,
        patch("streamlit.sidebar.button", return_value=True) as mock_btn,
        patch("streamlit.logout") as mock_logout,
    ):
        render_user_header(
            user={"email": "alice@example.com"},
            workspace={"name": "Acme", "slug": "acme"},
        )
        assert mock_md.call_count == 2
        mock_btn.assert_called_once_with(_("Sign out"))
        mock_logout.assert_called_once()

    # Not clicked
    with (
        patch("streamlit.sidebar.markdown"),
        patch("streamlit.sidebar.button", return_value=False),
        patch("streamlit.logout") as mock_logout,
    ):
        render_user_header(
            user={"email": "alice@example.com"},
            workspace={"name": "Acme", "slug": "acme"},
        )
        mock_logout.assert_not_called()


def test_auth_exception_hierarchy() -> None:
    assert issubclass(FakeIdentityForbiddenError, AuthError)
    assert issubclass(AccessDeniedError, AuthError)
    assert issubclass(AuthConfigurationError, AuthError)


def test_authlib_installed_for_streamlit_oidc() -> None:
    """Ensure streamlit[auth] (Authlib>=1.3.2) is installed so st.login works."""
    assert is_authlib_installed() is True
    from authlib.integrations import starlette_client

    assert hasattr(starlette_client, "OAuth")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {
                "AUTH_REDIRECT_URI": "https://custom.example.com/oauth2callback",
                "APP_ENV": "production",
                "DOMAIN": "ignored.example.com",
            },
            "https://custom.example.com/oauth2callback",
        ),
        (
            {"APP_ENV": "staging", "DOMAIN": "staging.example.com", "CADDY_HTTPS_PORT": "443"},
            "https://staging.example.com/oauth2callback",
        ),
        (
            {"APP_ENV": "production", "DOMAIN": "localhost", "CADDY_HTTPS_PORT": "8443"},
            "https://localhost:8443/oauth2callback",
        ),
        (
            {"APP_ENV": "prod", "DOMAIN": "app.example.com"},
            "https://app.example.com/oauth2callback",
        ),
        (
            {"APP_ENV": "development"},
            "http://localhost:8501/oauth2callback",
        ),
        (
            {"APP_ENV": "development", "DOMAIN": "127.0.0.1", "STREAMLIT_SERVER_PORT": "8502"},
            "http://127.0.0.1:8502/oauth2callback",
        ),
    ],
)
def test_derive_redirect_uri(env: dict[str, str], expected: str) -> None:
    assert derive_redirect_uri(env) == expected


def test_build_and_render_streamlit_auth_secrets() -> None:
    assert build_streamlit_auth_secrets({}) is None

    with pytest.raises(AuthConfigurationError, match="AUTH_COOKIE_SECRET"):
        build_streamlit_auth_secrets({"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret"})

    with pytest.raises(AuthConfigurationError, match="control characters"):
        build_streamlit_auth_secrets(
            {
                "AUTH_COOKIE_SECRET": "cookie\nsecret",
                "GOOGLE_CLIENT_ID": "cid",
                "GOOGLE_CLIENT_SECRET": "csecret",
            }
        )

    with pytest.raises(AuthConfigurationError, match="control characters"):
        derive_redirect_uri({"AUTH_REDIRECT_URI": "https://x.com/\noauth2callback"})

    built = build_streamlit_auth_secrets(
        {
            "APP_ENV": "staging",
            "DOMAIN": "staging.example.com",
            "AUTH_COOKIE_SECRET": 'cook"ie\\secret$1',
            "GOOGLE_CLIENT_ID": "client-123.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "sec\"ret\\with'special$chars",
        }
    )
    assert built is not None
    toml_text = render_streamlit_secrets_toml(built)
    parsed = tomllib.loads(toml_text)
    assert parsed == {
        "auth": {
            "redirect_uri": "https://staging.example.com/oauth2callback",
            "cookie_secret": 'cook"ie\\secret$1',
            "google": {
                "client_id": "client-123.apps.googleusercontent.com",
                "client_secret": "sec\"ret\\with'special$chars",
                "server_metadata_url": DEFAULT_GOOGLE_SERVER_METADATA_URL,
            },
        }
    }


def test_sync_streamlit_secrets_file_and_validate_credentials(tmp_path: Path) -> None:
    target = tmp_path / "macy" / "secrets.toml"
    orig_files = list(st_config.get_option("secrets.files"))

    try:
        # Unconfigured env cleanly skips when file does not exist, and removes stale files
        assert sync_streamlit_secrets_file(target, env={}) is False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stale = true\n", encoding="utf-8")
        assert sync_streamlit_secrets_file(target, env={}) is False
        assert not target.exists()

        # Configured env writes valid TOML recognized by Streamlit OIDC validator
        env = {
            "APP_ENV": "production",
            "DOMAIN": "app.example.com",
            "AUTH_COOKIE_SECRET": "a" * 64,
            "GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "csecret",
        }
        assert sync_streamlit_secrets_file(target, env=env) is True
        assert target.is_file()
        if os.name == "posix":
            assert (target.stat().st_mode & 0o777) == 0o600

        st_config.set_option("secrets.files", [str(target)])
        secrets_singleton._reset()
        validate_auth_credentials("google")
    finally:
        secrets_singleton._reset()
        st_config.set_option("secrets.files", orig_files)


def test_ensure_runtime_oidc_secrets_programmatic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig_files = list(st_config.get_option("secrets.files"))
    missing_file = tmp_path / "does-not-exist.toml"
    try:
        st_config.set_option("secrets.files", [str(missing_file)])
        secrets_singleton._reset()
        monkeypatch.delenv("AUTH_COOKIE_SECRET", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        assert ensure_runtime_oidc_secrets() is False

        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("AUTH_COOKIE_SECRET", "b" * 64)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid-runtime")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret-runtime")

        assert ensure_runtime_oidc_secrets() is True
        validate_auth_credentials("google")

        # Already populated -> returns False
        assert ensure_runtime_oidc_secrets() is False
    finally:
        secrets_singleton._reset()
        monkeypatch.setattr(secrets_singleton, "_programmatic_secrets", None)
        st_config.set_option("secrets.files", orig_files)


def test_auth_cli_main_with_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "APP_ENV",
        "ENV",
        "DOMAIN",
        "CADDY_HTTPS_PORT",
        "AUTH_REDIRECT_URI",
        "AUTH_COOKIE_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_SERVER_METADATA_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "IGNORED_LINE_WITHOUT_EQUALS\n"
        "export APP_ENV=staging\n"
        "DOMAIN='staging.example.com'\n"
        'AUTH_COOKIE_SECRET="cookie-from-file"\n'
        "GOOGLE_CLIENT_ID=cid-from-file\n"
        "GOOGLE_CLIENT_SECRET=csecret-from-file\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "secrets.toml"
    monkeypatch.delenv("MACY_STREAMLIT_SECRETS_PATH", raising=False)
    assert default_secrets_output_path().parts[-2:] == ("macy", "secrets.toml")
    monkeypatch.setenv("MACY_STREAMLIT_SECRETS_PATH", str(out_file))
    assert default_secrets_output_path() == out_file

    # CLI without --env-file reads process environment cleanly
    assert auth_cli_main(["--output", str(out_file)]) == 0
    assert not out_file.exists()

    rc = auth_cli_main(["--env-file", str(env_file), "--output", str(out_file)])
    assert rc == 0
    parsed = tomllib.loads(out_file.read_text(encoding="utf-8"))
    assert parsed["auth"]["redirect_uri"] == "https://staging.example.com/oauth2callback"
    assert parsed["auth"]["google"]["client_id"] == "cid-from-file"

    # Partial config fails with non-zero exit code
    bad_env = tmp_path / "bad.env"
    bad_env.write_text("GOOGLE_CLIENT_ID=only-id\n", encoding="utf-8")
    assert auth_cli_main(["--env-file", str(bad_env), "--output", str(out_file)]) == 1
