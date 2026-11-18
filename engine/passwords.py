"""PBKDF2-HMAC-SHA256 password hashing and brute-force rate limiting (ADR-030)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

PBKDF2_ITERATIONS = 600_000
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900.0
_FAILED_ATTEMPTS: dict[str, list[float]] = {}


class AuthError(Exception):
    """Base exception for authentication errors."""


class RateLimitExceededError(AuthError):
    """Raised when local login failures exceed the rate limit window."""


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a 16-byte random salt."""
    if not password or not password.strip():
        msg = "Password must not be empty"
        raise ValueError(msg)
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Verify a plaintext password against a stored PBKDF2-HMAC-SHA256 hash."""
    if not stored_hash or not password:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2].encode("ascii"), validate=True)
        expected = base64.b64decode(parts[3].encode("ascii"), validate=True)
    except Exception:  # noqa: BLE001
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(candidate, expected)


def _prune_attempts(email_key: str, *, now: float) -> list[float]:
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    recent = [ts for ts in _FAILED_ATTEMPTS.get(email_key, []) if ts >= cutoff]
    if recent:
        _FAILED_ATTEMPTS[email_key] = recent
    else:
        _FAILED_ATTEMPTS.pop(email_key, None)
    return recent


def check_local_rate_limit(email: str, *, now: float | None = None) -> None:
    """Raise RateLimitExceededError when failed logins reach threshold within window."""
    key = email.strip().lower()
    ts_now = time.monotonic() if now is None else now
    recent = _prune_attempts(key, now=ts_now)
    if len(recent) >= MAX_FAILED_ATTEMPTS:
        msg = "Too many failed login attempts; please wait before retrying"
        raise RateLimitExceededError(msg)


def record_local_login_failure(email: str, *, now: float | None = None) -> None:
    """Record a failed local login timestamp for rate limiting."""
    key = email.strip().lower()
    ts_now = time.monotonic() if now is None else now
    recent = _prune_attempts(key, now=ts_now)
    recent.append(ts_now)
    _FAILED_ATTEMPTS[key] = recent


def clear_local_login_failures(email: str) -> None:
    """Clear tracked login failures after successful authentication."""
    _FAILED_ATTEMPTS.pop(email.strip().lower(), None)


def reset_local_login_rate_limits() -> None:
    """Reset all tracked login failures (for tests)."""
    _FAILED_ATTEMPTS.clear()
