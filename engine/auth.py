"""Authentication and session identity management (F01, ADR-013, ADR-014).

Handles Google OIDC authentication via Streamlit, workspace membership gating,
and fake identity handling for test environments (strictly refused in production).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import streamlit as st

from engine import passwords, services
from engine.i18n import _
from engine.passwords import (
    AuthError,
    RateLimitExceededError,
    hash_password,
    reset_local_login_rate_limits,
    verify_password,
)

if TYPE_CHECKING:
    import psycopg

DEFAULT_GOOGLE_SERVER_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"

__all__ = [
    "DEFAULT_GOOGLE_SERVER_METADATA_URL",
    "AccessDeniedError",
    "AuthConfigurationError",
    "AuthError",
    "FakeIdentityForbiddenError",
    "RateLimitExceededError",
    "build_streamlit_auth_secrets",
    "default_secrets_output_path",
    "derive_redirect_uri",
    "ensure_runtime_oidc_secrets",
    "get_authenticated_email",
    "get_current_identity",
    "hash_password",
    "is_production_environment",
    "main",
    "render_access_denied",
    "render_login_screen",
    "render_streamlit_secrets_toml",
    "render_user_header",
    "reset_local_login_rate_limits",
    "resolve_auth_mode",
    "sync_streamlit_secrets_file",
    "verify_password",
]


class FakeIdentityForbiddenError(AuthError):
    """Raised when a fake test identity is used in a production environment (F01 AC6)."""


class AccessDeniedError(AuthError):
    """Raised when an authenticated user holds no membership in any workspace (F01 AC2)."""


class AuthConfigurationError(AuthError):
    """Raised when environment OIDC settings cannot produce valid Streamlit secrets."""


def resolve_auth_mode(env: Mapping[str, str] | None = None) -> Literal["local", "oidc"]:
    """Resolve active UI auth mode from AUTH_MODE, POCKET_MODE, and GOOGLE_CLIENT_ID."""
    source = os.environ if env is None else env
    raw_mode = str(source.get("AUTH_MODE", "")).strip().lower()
    if raw_mode == "local":
        return "local"
    if raw_mode == "oidc":
        return "oidc"
    is_pocket = str(source.get("POCKET_MODE", "")).strip().lower() in {"1", "true", "yes"}
    if raw_mode == "auto" or is_pocket:
        has_google = bool(str(source.get("GOOGLE_CLIENT_ID", "")).strip())
        return "oidc" if has_google else "local"
    return "oidc"


def is_production_environment() -> bool:
    """Check whether the active environment is production."""
    env = os.environ.get("APP_ENV", os.environ.get("ENV", "development")).lower()
    return env in ("production", "prod")


def _get_local_session_email() -> str | None:
    if not hasattr(st, "session_state"):
        return None
    local_email = getattr(st.session_state, "local_auth_email", None)
    if not isinstance(local_email, str) and isinstance(st.session_state, Mapping):
        local_email = st.session_state.get("local_auth_email")
    if isinstance(local_email, str) and local_email.strip():
        return local_email.strip().lower()
    return None


def get_authenticated_email() -> str | None:
    """Resolve the currently authenticated email address.

    Prioritizes test identity in non-production environments; otherwise reads
    from local password session or Streamlit native OIDC user state.
    """
    test_email = os.environ.get("MACY_TEST_USER_EMAIL")
    if not test_email and hasattr(st, "session_state"):
        test_email = getattr(st.session_state, "test_user_email", None)

    if test_email:
        if is_production_environment():
            msg = "Fake identity test login is forbidden in production (F01 AC6)"
            raise FakeIdentityForbiddenError(msg)
        return test_email.strip().lower()

    local_email = _get_local_session_email()
    if local_email:
        return local_email

    st_user = getattr(st, "user", None)
    if st_user and getattr(st_user, "is_logged_in", False):
        email = getattr(st_user, "email", None)
        if email:
            return str(email).strip().lower()

    return None


def get_current_identity(
    conn: psycopg.Connection[Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve current user identity and associated workspace memberships."""
    email = get_authenticated_email()
    if not email:
        return None

    user = services.get_user_by_email(conn, email=email)
    if user is None:
        return {"email": email, "user": None, "memberships": []}

    memberships = services.list_user_workspaces(conn, user_id=user["user_id"])
    return {
        "email": email,
        "user": user,
        "memberships": memberships,
    }


def default_secrets_output_path() -> Path:
    """Return the runtime path where generated Streamlit secrets are stored."""
    override = os.environ.get("MACY_STREAMLIT_SECRETS_PATH", "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "macy" / "secrets.toml"


def _validate_no_control_chars(name: str, value: str) -> str:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        msg = f"{name} must not contain control characters"
        raise AuthConfigurationError(msg)
    return value


def derive_redirect_uri(env: Mapping[str, str]) -> str:
    """Resolve the Google OAuth callback URI from env vars."""
    explicit = env.get("AUTH_REDIRECT_URI", "").strip()
    if explicit:
        return _validate_no_control_chars("AUTH_REDIRECT_URI", explicit)

    app_env = (env.get("APP_ENV") or env.get("ENV") or "development").strip().lower()
    domain = _validate_no_control_chars("DOMAIN", (env.get("DOMAIN") or "localhost").strip())
    host = domain or "localhost"

    if app_env in ("staging", "production", "prod"):
        https_port = env.get("CADDY_HTTPS_PORT", "").strip()
        if https_port and https_port != "443":
            return f"https://{host}:{https_port}/oauth2callback"
        return f"https://{host}/oauth2callback"

    server_port = env.get("STREAMLIT_SERVER_PORT", "8501").strip() or "8501"
    return f"http://{host}:{server_port}/oauth2callback"


def build_streamlit_auth_secrets(env: Mapping[str, str]) -> dict[str, Any] | None:
    """Build Streamlit's ``[auth]`` secrets structure from environment variables.

    Returns ``None`` when none of ``AUTH_COOKIE_SECRET``, ``GOOGLE_CLIENT_ID``, or
    ``GOOGLE_CLIENT_SECRET`` are set so local/offline dev mode can start without OIDC.
    Raises ``AuthConfigurationError`` if configuration is partial or malformed.
    """
    required = {
        "AUTH_COOKIE_SECRET": env.get("AUTH_COOKIE_SECRET", "").strip(),
        "GOOGLE_CLIENT_ID": env.get("GOOGLE_CLIENT_ID", "").strip(),
        "GOOGLE_CLIENT_SECRET": env.get("GOOGLE_CLIENT_SECRET", "").strip(),
    }
    present = {k for k, v in required.items() if v}
    if not present:
        return None
    missing = [k for k, v in required.items() if not v]
    if missing:
        msg = f"Incomplete Google OIDC configuration in environment; missing: {', '.join(missing)}"
        raise AuthConfigurationError(msg)

    cookie_secret = _validate_no_control_chars("AUTH_COOKIE_SECRET", required["AUTH_COOKIE_SECRET"])
    client_id = _validate_no_control_chars("GOOGLE_CLIENT_ID", required["GOOGLE_CLIENT_ID"])
    client_secret = _validate_no_control_chars(
        "GOOGLE_CLIENT_SECRET", required["GOOGLE_CLIENT_SECRET"]
    )
    metadata_url = _validate_no_control_chars(
        "GOOGLE_SERVER_METADATA_URL",
        env.get("GOOGLE_SERVER_METADATA_URL", "").strip() or DEFAULT_GOOGLE_SERVER_METADATA_URL,
    )
    return {
        "auth": {
            "redirect_uri": derive_redirect_uri(env),
            "cookie_secret": cookie_secret,
            "google": {
                "client_id": client_id,
                "client_secret": client_secret,
                "server_metadata_url": metadata_url,
            },
        }
    }


def render_streamlit_secrets_toml(secrets_data: Mapping[str, Any]) -> str:
    """Serialize built ``[auth]`` secrets mapping into valid TOML."""
    auth_cfg = secrets_data["auth"]
    google_cfg = auth_cfg["google"]
    metadata_toml = json.dumps(google_cfg["server_metadata_url"], ensure_ascii=False)
    lines = [
        "# Auto-generated from environment variables by engine.auth",
        "[auth]",
        f"redirect_uri = {json.dumps(auth_cfg['redirect_uri'], ensure_ascii=False)}",
        f"cookie_secret = {json.dumps(auth_cfg['cookie_secret'], ensure_ascii=False)}",
        "",
        "[auth.google]",
        f"client_id = {json.dumps(google_cfg['client_id'], ensure_ascii=False)}",
        f"client_secret = {json.dumps(google_cfg['client_secret'], ensure_ascii=False)}",
        f"server_metadata_url = {metadata_toml}",
        "",
    ]
    return "\n".join(lines)


def sync_streamlit_secrets_file(
    output_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Write ``secrets.toml`` from env vars atomically, or remove stale file if unset."""
    target = output_path if output_path is not None else default_secrets_output_path()
    source_env = os.environ if env is None else env
    secrets_data = build_streamlit_auth_secrets(source_env)
    if secrets_data is None:
        if target.exists():
            target.unlink()
        return False

    toml_text = render_streamlit_secrets_toml(secrets_data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=".secrets-", suffix=".tmp", dir=target.parent)
    tmp_path = Path(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(toml_text)
    tmp_path.chmod(0o600)
    os.replace(tmp_path, target)
    return True


def ensure_runtime_oidc_secrets(env: Mapping[str, str] | None = None) -> bool:
    """Populate Streamlit's runtime secrets store from env vars when file config lacks [auth]."""
    from streamlit.runtime.secrets import secrets_singleton

    if secrets_singleton.load_if_toml_exists() and secrets_singleton.get("auth") is not None:
        return False

    secrets_data = build_streamlit_auth_secrets(os.environ if env is None else env)
    if secrets_data is None:
        return False

    secrets_singleton.merge_programmatic_secrets(secrets_data)
    return True


def _parse_env_file(env_file: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        clean_key = key.strip().removeprefix("export ").strip()
        clean_val = value.strip()
        if len(clean_val) >= 2 and clean_val[0] == clean_val[-1] and clean_val[0] in ('"', "'"):
            clean_val = clean_val[1:-1]
        parsed[clean_key] = clean_val
    return parsed


def main(argv: list[str] | None = None) -> int:
    """CLI helper that renders Streamlit OIDC secrets from environment / .env before startup."""
    parser = argparse.ArgumentParser(
        prog="python -m engine.auth",
        description="Render Streamlit OIDC secrets.toml from environment variables.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Destination TOML path (defaults to $MACY_STREAMLIT_SECRETS_PATH or temp dir).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional .env file to load before overlaying process environment variables.",
    )
    args = parser.parse_args(argv)

    merged_env: dict[str, str] = {}
    if args.env_file is not None:
        merged_env.update(_parse_env_file(args.env_file))
    merged_env.update(os.environ)

    try:
        sync_streamlit_secrets_file(output_path=args.output, env=merged_env)
    except AuthConfigurationError as exc:
        print(f"auth configuration error: {exc}", file=sys.stderr)
        return 1
    return 0


def _perform_sign_out() -> None:
    if (
        hasattr(st, "session_state")
        and isinstance(st.session_state, dict)
        and st.session_state.pop("local_auth_email", None)
    ):
        st.rerun()
        return
    if hasattr(st, "session_state") and getattr(st.session_state, "local_auth_email", None):
        delattr(st.session_state, "local_auth_email")
        st.rerun()
        return
    st.logout()


def render_login_screen() -> None:
    """Render local password form or Google OIDC sign-in prompt when unauthenticated."""
    st.title(_("Macy Content Intelligence Engine"))
    if resolve_auth_mode() == "local":
        st.write(_("Sign in with your workspace email and password."))
        email_val = st.text_input(_("Email"), key="local_login_email")
        pw_val = st.text_input(_("Password"), type="password", key="local_login_password")
        if st.button(_("Sign in")):
            try:
                user = services.authenticate_local_user(email=email_val, password=pw_val)
            except passwords.AuthError as exc:
                st.error(str(exc))
                return
            st.session_state["local_auth_email"] = user["email"]
            st.rerun()
        return

    st.write(_("Please sign in with your authorized Google account to access your workspace."))
    if st.button(_("Sign in with Google")):
        ensure_runtime_oidc_secrets()
        st.login("google")


def render_access_denied(email: str) -> None:
    """Render access denied message when user is not a member of any workspace."""
    st.error(_("Access denied: '{email}' is not a member of any workspace.").format(email=email))
    st.write(_("Please contact your workspace owner for access."))
    if st.button(_("Sign out")):
        _perform_sign_out()


def render_user_header(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render sidebar user identity and workspace details."""
    user_label = _("User")
    ws_label = _("Workspace")
    st.sidebar.markdown(f"**{user_label}:** `{user['email']}`")
    st.sidebar.markdown(f"**{ws_label}:** {workspace['name']} (`{workspace['slug']}`)")
    if st.sidebar.button(_("Sign out")):
        _perform_sign_out()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
