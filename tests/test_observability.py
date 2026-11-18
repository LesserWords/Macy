"""Tests for Sentry error tracking, secret scrubbing, tags, and heartbeat monitoring (ADR-023)."""

import logging
from typing import Any, cast
from unittest.mock import patch

import pytest
from sentry_sdk.types import Event

from engine.observability import (
    _is_sensitive_key,
    _scrub_value,
    capture_exception,
    ensure_sentry_initialized,
    init_sentry,
    scrub_sensitive_data,
    send_heartbeat,
    set_observability_tags,
)
from engine.safe_http import SafeHTTPError, SafeResponse


def test_is_sensitive_key() -> None:
    # Sensitive keys
    assert _is_sensitive_key("Authorization")
    assert _is_sensitive_key("authorization")
    assert _is_sensitive_key("api_key")
    assert _is_sensitive_key("apiKey")
    assert _is_sensitive_key("x-api-key")
    assert _is_sensitive_key("access_token")
    assert _is_sensitive_key("refresh_token")
    assert _is_sensitive_key("auth_token")
    assert _is_sensitive_key("password")
    assert _is_sensitive_key("secret")
    assert _is_sensitive_key("client_secret")
    assert _is_sensitive_key("cookie")
    assert _is_sensitive_key("Set-Cookie")
    assert _is_sensitive_key("credential")
    assert _is_sensitive_key("private_key")

    # Non-sensitive keys (token metrics, normal fields)
    assert not _is_sensitive_key("prompt_tokens")
    assert not _is_sensitive_key("completion_tokens")
    assert not _is_sensitive_key("total_tokens")
    assert not _is_sensitive_key("tokens")
    assert not _is_sensitive_key("token_count")
    assert not _is_sensitive_key("username")
    assert not _is_sensitive_key("status")
    assert not _is_sensitive_key("workspace_id")
    assert not _is_sensitive_key("run_id")


def test_scrub_sensitive_data() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer dummy-token",
                "Content-Type": "application/json",
                "X-Api-Key": "dummy-test-key",  # gitleaks:allow
                "Cookie": "session_id=abcdef",
            },
            "data": {
                "password": "secret_password",
                "username": "testuser",
                "credentials": {"token": "secret_token"},
            },
        },
        "extra": {
            "client_secret": "sensitive-extra",
            "metrics": {
                "prompt_tokens": 500,
                "completion_tokens": 100,
                "total_tokens": 600,
            },
        },
        "breadcrumbs": [
            {
                "category": "http",
                "data": {
                    "headers": {
                        "Authorization": "Bearer breadcrumb-token",
                    },
                },
            },
        ],
    }

    scrubbed = scrub_sensitive_data(cast(Event, event))
    assert scrubbed is not None
    scrubbed_dict = cast(dict[str, Any], scrubbed)

    # Headers scrubbed
    headers = scrubbed_dict["request"]["headers"]
    assert headers["Authorization"] == "[REDACTED]"
    assert headers["X-Api-Key"] == "[REDACTED]"
    assert headers["Cookie"] == "[REDACTED]"
    assert headers["Content-Type"] == "application/json"

    # Data scrubbed
    data = scrubbed_dict["request"]["data"]
    assert data["password"] == "[REDACTED]"
    assert data["username"] == "testuser"
    assert data["credentials"]["token"] == "[REDACTED]"

    # Extras & metrics
    extra = scrubbed_dict["extra"]
    assert extra["client_secret"] == "[REDACTED]"
    assert extra["metrics"]["prompt_tokens"] == 500
    assert extra["metrics"]["completion_tokens"] == 100
    assert extra["metrics"]["total_tokens"] == 600

    # Breadcrumbs scrubbed
    bc = scrubbed_dict["breadcrumbs"][0]["data"]["headers"]
    assert bc["Authorization"] == "[REDACTED]"


def test_scrub_value_primitives() -> None:
    assert _scrub_value("plain string") == "plain string"
    assert _scrub_value(12345) == 12345
    assert _scrub_value(None) is None


def test_init_sentry_disabled_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry(None) is False
    assert init_sentry("") is False


def test_init_sentry_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    monkeypatch.setenv("APP_ENV", "production")

    with patch("sentry_sdk.init") as mock_init:
        result = init_sentry(release="1.0.0")
        assert result is True
        mock_init.assert_called_once()
        kwargs = mock_init.call_args[1]
        assert kwargs["dsn"] == "https://public@example.com/1"
        assert kwargs["environment"] == "production"
        assert kwargs["release"] == "1.0.0"
        assert kwargs["before_send"] == scrub_sensitive_data


def test_set_observability_tags() -> None:
    with patch("sentry_sdk.set_tag") as mock_set_tag:
        set_observability_tags(workspace_id="ws-999", run_id="run-123")
        assert mock_set_tag.call_count == 2
        mock_set_tag.assert_any_call("workspace_id", "ws-999")
        mock_set_tag.assert_any_call("run_id", "run-123")


def test_set_observability_tags_partial() -> None:
    with patch("sentry_sdk.set_tag") as mock_set_tag:
        set_observability_tags(workspace_id="ws-999", run_id=None)
        assert mock_set_tag.call_count == 1
        mock_set_tag.assert_called_once_with("workspace_id", "ws-999")

    with patch("sentry_sdk.set_tag") as mock_set_tag_2:
        set_observability_tags(workspace_id=None, run_id="run-456")
        assert mock_set_tag_2.call_count == 1
        mock_set_tag_2.assert_called_once_with("run_id", "run-456")


def test_send_heartbeat_noop_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    assert send_heartbeat(None) is False
    assert send_heartbeat("") is False


def test_send_heartbeat_success() -> None:
    mock_resp = SafeResponse(status_code=200, headers={}, content=b"OK")
    with patch("engine.observability.safe_request", return_value=mock_resp) as mock_req:
        result = send_heartbeat("https://hc-ping.com/abcdef")
        assert result is True
        mock_req.assert_called_once_with("GET", "https://hc-ping.com/abcdef", timeout=5.0)


def test_send_heartbeat_failure_status(caplog: pytest.LogCaptureFixture) -> None:
    mock_resp = SafeResponse(status_code=500, headers={}, content=b"Server error")
    with (
        patch("engine.observability.safe_request", return_value=mock_resp),
        caplog.at_level(logging.WARNING),
    ):
        result = send_heartbeat("https://hc-ping.com/abcdef")
        assert result is False
        assert "Heartbeat ping failed with status 500" in caplog.text


def test_send_heartbeat_exception_caught(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("engine.observability.safe_request", side_effect=SafeHTTPError("SSRF error")),
        caplog.at_level(logging.WARNING),
    ):
        result = send_heartbeat("https://hc-ping.com/abcdef")
        assert result is False
        assert "Heartbeat ping encountered error" in caplog.text


def test_scrub_inline_secret_strings() -> None:
    dsn_str = "Connecting to postgres://owner:supersecretpw@db:5432/macy_prod"
    assert _scrub_value(dsn_str) == "Connecting to postgres://owner:[REDACTED]@db:5432/macy_prod"

    bearer_str = "Header Bearer fake-jwt-token-value"
    assert _scrub_value(bearer_str) == "Header Bearer [REDACTED]"

    query_str = "https://api.example.com/search?q=news&api_key=abc123xyz&token=tok999"
    assert _scrub_value(query_str) == (
        "https://api.example.com/search?q=news&api_key=[REDACTED]&token=[REDACTED]"
    )

    prefixed_sk = "bad call " + "sk-" + "example12345678"
    assert _scrub_value(prefixed_sk) == "bad call [REDACTED]"

    prefixed_wh = "bad sig " + "whsec_" + "example12345678"
    assert _scrub_value(prefixed_wh) == "bad sig [REDACTED]"


def test_ensure_sentry_initialized_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    with (
        patch("sentry_sdk.is_initialized", return_value=True),
        patch("sentry_sdk.init") as mock_init,
    ):
        assert ensure_sentry_initialized() is True
        mock_init.assert_not_called()

    with (
        patch("sentry_sdk.is_initialized", return_value=False),
        patch("sentry_sdk.init") as mock_init,
    ):
        assert ensure_sentry_initialized() is True
        mock_init.assert_called_once()


def test_capture_exception_tags_workspace_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    with patch("sentry_sdk.is_initialized", return_value=False):
        assert capture_exception(RuntimeError("no dsn"), workspace_id="ws-1") is None

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    with (
        patch("sentry_sdk.is_initialized", return_value=True),
        patch("sentry_sdk.new_scope") as mock_scope_cm,
        patch("sentry_sdk.capture_exception", return_value="evt-123") as mock_cap,
    ):
        scope = mock_scope_cm.return_value.__enter__.return_value
        err = RuntimeError("boom")
        event_id = capture_exception(
            err,
            workspace_id="ws-1",
            run_id="run-2",
            extra={"stage": "research"},
        )
        assert event_id == "evt-123"
        scope.set_tag.assert_any_call("workspace_id", "ws-1")
        scope.set_tag.assert_any_call("run_id", "run-2")
        scope.set_extra.assert_called_once_with("stage", "research")
        mock_cap.assert_called_once_with(err)

        event_id_plain = capture_exception(err)
        assert event_id_plain == "evt-123"


def test_send_heartbeat_failure_suffix() -> None:
    mock_resp = SafeResponse(status_code=200, headers={}, content=b"OK")
    with patch("engine.observability.safe_request", return_value=mock_resp) as mock_req:
        assert send_heartbeat("https://hc-ping.com/abcdef/", failed=True) is True
        mock_req.assert_called_once_with("GET", "https://hc-ping.com/abcdef/fail", timeout=5.0)
        assert send_heartbeat("https://hc-ping.com/abcdef/fail", failed=True) is True
        mock_req.assert_called_with("GET", "https://hc-ping.com/abcdef/fail", timeout=5.0)


def test_app_main_initializes_sentry_and_captures_unhandled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import streamlit as st

    import app as ui_app
    from engine import auth, services

    identity = {
        "email": "owner@example.com",
        "user": {"user_id": "user-1", "email": "owner@example.com", "ui_locale": "en-US"},
        "memberships": [{"workspace_id": "ws-sentry-ui"}],
    }
    monkeypatch.setattr(auth, "get_current_identity", lambda: identity)
    monkeypatch.setattr(
        services,
        "get_workspace_for_member",
        lambda **_: {"workspace_id": "ws-sentry-ui", "name": "Test", "slug": "test"},
    )
    monkeypatch.setattr(ui_app, "_render_sidebar", lambda *_: "dashboard")
    monkeypatch.setattr(st, "title", lambda *_, **__: None)
    monkeypatch.setattr(st, "write", lambda *_, **__: None)

    def _boom(*_: object) -> None:
        raise RuntimeError("Unhandled UI crash")

    monkeypatch.setattr(ui_app, "_render_selected_page", _boom)

    with (
        patch("engine.observability.ensure_sentry_initialized") as mock_ensure,
        patch("engine.observability.set_observability_tags") as mock_tags,
        patch("engine.observability.capture_exception") as mock_cap,
    ):
        with pytest.raises(RuntimeError, match="Unhandled UI crash"):
            ui_app.main()
        mock_ensure.assert_called_once()
        mock_tags.assert_called_once_with(workspace_id="ws-sentry-ui")
        assert mock_cap.called
        assert mock_cap.call_args.kwargs["workspace_id"] == "ws-sentry-ui"


def test_mcp_tool_tag_and_token_scrub() -> None:
    from engine import observability

    with patch("sentry_sdk.set_tag") as mock_set_tag:
        set_observability_tags(
            workspace_id="ws-mcp-1",
            run_id="run-mcp-1",
            mcp_tool="macy_list_workspaces",
        )
        mock_set_tag.assert_any_call("workspace_id", "ws-mcp-1")
        mock_set_tag.assert_any_call("run_id", "run-mcp-1")
        mock_set_tag.assert_any_call("mcp_tool", "macy_list_workspaces")

    with (
        patch("engine.observability.ensure_sentry_initialized", return_value=True),
        patch("sentry_sdk.new_scope") as mock_scope_cm,
        patch("sentry_sdk.capture_exception", return_value="evt-mcp") as mock_cap,
    ):
        scope = mock_scope_cm.return_value.__enter__.return_value
        err = RuntimeError("MCP failed Authorization: Bearer super-secret-mcp-token")
        event_id = capture_exception(
            err,
            workspace_id="ws-mcp-1",
            mcp_tool="macy_decide_topic",
        )
        assert event_id == "evt-mcp"
        scope.set_tag.assert_any_call("mcp_tool", "macy_decide_topic")
        mock_cap.assert_called_once_with(err)

    payload = {
        "tags": {"mcp_tool": "macy_get_workspace_status"},
        "extra": {
            "MCP_AUTH_TOKEN": "top-secret-mcp-token",
            "headers": {"Authorization": "Bearer top-secret-bearer"},
        },
        "exception": {
            "values": [
                {
                    "value": (
                        "Connection postgres://app:pw@db/macy failed with Bearer inline-jwt-value"
                    )
                }
            ]
        },
    }
    scrubbed = cast(dict[str, Any], scrub_sensitive_data(cast(Event, payload)))
    assert scrubbed["extra"]["MCP_AUTH_TOKEN"] == "[REDACTED]"
    assert scrubbed["extra"]["headers"]["Authorization"] == "[REDACTED]"
    err_val = scrubbed["exception"]["values"][0]["value"]
    assert "pw" not in err_val
    assert "inline-jwt-value" not in err_val
    assert "Bearer [REDACTED]" in err_val

    assert observability.scrub_text("Header Bearer live-token-123") == "Header Bearer [REDACTED]"
