"""Observability: Sentry SDK initialization, sensitive secret scrubbing, and heartbeat monitoring.

Implements requirements from ADR-023 and F20:
- Sentry SDK initialization with environment and release tags
- Strict `before_send` scrubber redacting API keys, passwords, Authorization headers, and tokens
- Workspace and run_id context tagging
- Run heartbeat ping helper with SSRF guards and no-op safety
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk
from sentry_sdk.types import Event, Hint

from engine import __version__
from engine.safe_http import safe_request

logger = logging.getLogger(__name__)

_DSN_USERINFO_RE = re.compile(r"((?:postgres(?:ql)?|https?|redis|amqps?)://[^:/\s@]+:)([^@\s]+)(@)")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/]+=*")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:api_key|apikey|token|access_token|secret|password|auth)=)([^&\s#]+)"
)
_SECRET_PREFIX_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|whsec_[A-Za-z0-9+/=_-]{6,}|BSA[A-Za-z0-9_-]{8,})"
)

NON_SENSITIVE_EXCLUSIONS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "tokens",
        "token_count",
        "tokens_used",
    }
)

SENSITIVE_PATTERNS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "password",
        "authorization",
        "cookie",
        "credential",
        "private_key",
    }
)


def _is_sensitive_key(key: str) -> bool:
    """Check whether a configuration or dictionary key is considered sensitive."""
    clean = key.lower().replace("-", "_")
    if clean in NON_SENSITIVE_EXCLUSIONS:
        return False
    if any(pat in clean for pat in SENSITIVE_PATTERNS):
        return True
    return "token" in clean


def _scrub_string(value: str) -> str:
    """Redact inline credential patterns inside exception messages, URLs, and strings."""
    scrubbed = _DSN_USERINFO_RE.sub(r"\1[REDACTED]\3", value)
    scrubbed = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", scrubbed)
    scrubbed = _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", scrubbed)
    return _SECRET_PREFIX_RE.sub("[REDACTED]", scrubbed)


def scrub_text(value: str) -> str:
    """Redact inline credentials and Bearer tokens from text messages."""
    return _scrub_string(value)


def _scrub_value(value: Any) -> Any:
    """Recursively redact values associated with sensitive keys and inline credentials."""
    if isinstance(value, dict):
        return {
            k: (
                _scrub_value(v)
                if isinstance(v, (dict, list))
                else ("[REDACTED]" if _is_sensitive_key(str(k)) else _scrub_value(v))
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def scrub_sensitive_data(
    event: Event,
    hint: Hint | None = None,
) -> Event | None:
    """Scrub sensitive credentials, tokens, and headers before sending to Sentry."""
    del hint
    return _scrub_value(event)  # type: ignore[no-any-return]


def init_sentry(
    dsn: str | None = None,
    environment: str | None = None,
    release: str | None = None,
) -> bool:
    """Initialize Sentry SDK with scrubbing hook and environment settings."""
    target_dsn = dsn if dsn is not None else os.environ.get("SENTRY_DSN")
    if not target_dsn:
        logger.info("Sentry DSN not configured; error tracking disabled")
        return False

    target_env = (
        environment
        if environment is not None
        else os.environ.get("APP_ENV", os.environ.get("ENV", "development"))
    )

    sentry_sdk.init(
        dsn=target_dsn,
        environment=target_env,
        release=release,
        before_send=scrub_sensitive_data,
    )
    logger.info("Sentry initialized (environment: %s)", target_env)
    return True


def ensure_sentry_initialized(
    dsn: str | None = None,
    environment: str | None = None,
    release: str | None = None,
) -> bool:
    """Initialize Sentry once if SENTRY_DSN is set and the SDK is not yet initialized."""
    if sentry_sdk.is_initialized():
        return True
    target_release = release if release is not None else os.environ.get("APP_TAG") or __version__
    return init_sentry(dsn=dsn, environment=environment, release=target_release)


def set_observability_tags(
    workspace_id: str | None = None,
    run_id: str | None = None,
    mcp_tool: str | None = None,
) -> None:
    """Tag active Sentry scope with workspace, run, and MCP tool identifiers."""
    if workspace_id:
        sentry_sdk.set_tag("workspace_id", workspace_id)
    if run_id:
        sentry_sdk.set_tag("run_id", run_id)
    if mcp_tool:
        sentry_sdk.set_tag("mcp_tool", mcp_tool)


def capture_exception(
    exc: BaseException,
    *,
    workspace_id: Any = None,
    run_id: Any = None,
    mcp_tool: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Capture an exception to Sentry in a scoped context with workspace_id/run_id/mcp_tool tags."""
    if not ensure_sentry_initialized():
        return None

    with sentry_sdk.new_scope() as scope:
        if workspace_id is not None:
            scope.set_tag("workspace_id", str(workspace_id))
        if run_id is not None:
            scope.set_tag("run_id", str(run_id))
        if mcp_tool is not None:
            scope.set_tag("mcp_tool", str(mcp_tool))
        if extra:
            for key, val in extra.items():
                scope.set_extra(key, val)
        return sentry_sdk.capture_exception(exc)


def send_test_sentry_event() -> str:
    """Capture and flush a forced test exception to verify Sentry delivery (F20 AC5)."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn and not sentry_sdk.is_initialized():
        msg = "SENTRY_DSN is not configured"
        raise ValueError(msg)

    ensure_sentry_initialized(dsn=dsn or None)
    try:
        raise RuntimeError("Macy Sentry verification test exception")
    except RuntimeError as exc:
        event_id = capture_exception(
            exc,
            extra={"test_event": True, "source": "cli.test_sentry"},
        )
    sentry_sdk.flush(timeout=5.0)
    return str(event_id or "captured")


def send_heartbeat(
    url: str | None = None,
    *,
    failed: bool = False,
    timeout: float = 5.0,
) -> bool:
    """Send a heartbeat ping (GET request) to monitor run completion or failure."""
    target_url = (url or os.environ.get("HEARTBEAT_URL") or "").strip()
    if not target_url:
        return False

    if failed:
        parsed = urlsplit(target_url)
        base_path = parsed.path.rstrip("/")
        if not base_path.endswith("/fail"):
            base_path = f"{base_path}/fail"
        target_url = urlunsplit(
            (parsed.scheme, parsed.netloc, base_path, parsed.query, parsed.fragment)
        )

    try:
        resp = safe_request("GET", target_url, timeout=timeout)
        if resp.status_code < 400:
            return True
        logger.warning("Heartbeat ping failed with status %d", resp.status_code)
        return False
    except Exception as exc:
        logger.warning("Heartbeat ping encountered error: %s", exc)
        return False
