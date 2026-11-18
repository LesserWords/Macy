"""Outbound webhook signing, backoff scheduling, and delivery worker (F06, ADR-017)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from standardwebhooks import Webhook

from engine import db, safe_http
from engine.integrations.config import _get_fernet_instance, _resolve_keys

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS: float = 60.0  # 1 minute
BACKOFF_FACTOR: float = 2.0
MAX_DELAY_SECONDS: float = 3600.0  # 1 hour
MAX_RETRY_WINDOW_SECONDS: float = 86400.0  # 24 hours


def calculate_next_backoff(  # noqa: PLR0913
    attempts: int,
    occurred_at: datetime,
    now: datetime | None = None,
    *,
    initial_delay: float = INITIAL_DELAY_SECONDS,
    factor: float = BACKOFF_FACTOR,
    max_delay: float = MAX_DELAY_SECONDS,
    max_window: float = MAX_RETRY_WINDOW_SECONDS,
) -> tuple[str, datetime | None]:
    """Calculate retry status and next timestamp using exponential backoff (F06 AC3).

    Returns:
        ('dead', None) if elapsed time or next attempt reaches/exceeds 24 hours (86400s).
        ('retrying', next_attempt_at) with exponential backoff capped at max_delay.
    """
    current_time = now if now is not None else datetime.now(UTC)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)

    elapsed = (current_time - occurred_at).total_seconds()
    if elapsed >= max_window:
        return "dead", None

    exponent = max(0, attempts - 1)
    delay = min(max_delay, initial_delay * (factor**exponent))
    next_attempt = current_time + timedelta(seconds=delay)

    if (next_attempt - occurred_at).total_seconds() > max_window:
        return "dead", None

    return "retrying", next_attempt


def sign_webhook_payload(
    secret: str | bytes,
    msg_id: str,
    timestamp: datetime,
    payload_json: str,
) -> str:
    """Generate Standard Webhooks signature header value (v1,<base64>) (F06 AC2)."""
    wh = Webhook(secret)
    return wh.sign(msg_id=msg_id, timestamp=timestamp, data=payload_json)


def build_webhook_headers(
    secret: str | bytes,
    event_id: uuid.UUID,
    timestamp: datetime,
    payload_json: str,
) -> dict[str, str]:
    """Build Standard Webhooks headers for outbound HTTP POST (F06 AC2)."""
    sig = sign_webhook_payload(secret, str(event_id), timestamp, payload_json)
    return {
        "Content-Type": "application/json",
        "webhook-id": str(event_id),
        "webhook-timestamp": str(int(timestamp.timestamp())),
        "webhook-signature": sig,
    }


def _decrypt_subscription_secret(
    encrypted_secret: bytes | memoryview,
    encryption_key: str | list[str] | None,
) -> str:
    keys = _resolve_keys(encryption_key)
    mf = _get_fernet_instance(keys)
    decrypted_bytes = mf.decrypt(bytes(encrypted_secret))
    return decrypted_bytes.decode("utf-8")


def _format_event_payload(delivery: dict[str, Any]) -> str:
    occurred = delivery["occurred_at"]
    occurred_str = occurred.isoformat() if hasattr(occurred, "isoformat") else str(occurred)
    body = {
        "id": str(delivery["event_id"]),
        "type": delivery["event_type"],
        "workspace_id": str(delivery["workspace_id"]),
        "occurred_at": occurred_str,
        "data": delivery.get("payload") or {},
    }
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def _record_delivery_failure(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    delivery: dict[str, Any],
    now_dt: datetime,
    attempts: int,
    status_code: int | None,
    error_msg: str,
) -> dict[str, Any]:
    occurred_at = delivery["occurred_at"]
    status, next_at = calculate_next_backoff(attempts, occurred_at, now=now_dt)
    db.update_webhook_delivery_attempt(
        conn,
        workspace_id=delivery["workspace_id"],
        subscription_id=delivery["subscription_id"],
        event_id=delivery["event_id"],
        status=status,
        attempts=attempts,
        next_attempt_at=next_at or now_dt,
        last_http_status=status_code,
        last_error=error_msg,
    )
    return {
        "event_id": delivery["event_id"],
        "subscription_id": delivery["subscription_id"],
        "status": status,
        "attempts": attempts,
        "http_status": status_code,
        "error": error_msg,
    }


def deliver_single_delivery(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    delivery: dict[str, Any],
    *,
    encryption_key: str | list[str] | None = None,
    timeout: float = 10.0,
    is_production: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute a single webhook delivery attempt with signing and backoff update."""
    now_dt = now or datetime.now(UTC)
    attempts = int(delivery.get("attempts", 0)) + 1

    if not delivery.get("enabled", True):
        db.update_webhook_delivery_attempt(
            conn,
            workspace_id=delivery["workspace_id"],
            subscription_id=delivery["subscription_id"],
            event_id=delivery["event_id"],
            status="dead",
            attempts=attempts,
            next_attempt_at=now_dt,
            last_error="Subscription disabled",
        )
        return {
            "event_id": delivery["event_id"],
            "status": "dead",
            "error": "Subscription disabled",
        }

    try:
        secret = _decrypt_subscription_secret(delivery["secret"], encryption_key)
        payload_json = _format_event_payload(delivery)
        headers = build_webhook_headers(secret, delivery["event_id"], now_dt, payload_json)
        resp = safe_http.safe_request(
            "POST",
            delivery["url"],
            headers=headers,
            data=payload_json.encode("utf-8"),
            timeout=timeout,
            is_production=is_production,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Webhook delivery %s/%s failed with network/SSRF error: %s",
            delivery["subscription_id"],
            delivery["event_id"],
            exc,
        )
        return _record_delivery_failure(conn, delivery, now_dt, attempts, None, str(exc))

    if 200 <= resp.status_code < 300:
        db.update_webhook_delivery_attempt(
            conn,
            workspace_id=delivery["workspace_id"],
            subscription_id=delivery["subscription_id"],
            event_id=delivery["event_id"],
            status="delivered",
            attempts=attempts,
            next_attempt_at=now_dt,
            last_http_status=resp.status_code,
            last_error=None,
            delivered_at=now_dt,
        )
        return {
            "event_id": delivery["event_id"],
            "subscription_id": delivery["subscription_id"],
            "status": "delivered",
            "attempts": attempts,
            "http_status": resp.status_code,
        }

    return _record_delivery_failure(
        conn,
        delivery,
        now_dt,
        attempts,
        resp.status_code,
        f"HTTP {resp.status_code}",
    )


def deliver_due(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID | None = None,
    batch_size: int = 10,
    encryption_key: str | list[str] | None = None,
    timeout: float = 10.0,
    is_production: bool | None = None,
) -> list[dict[str, Any]]:
    """Query due deliveries with row lock and dispatch them (F06 AC2, AC3)."""
    if conn is not None:
        deliveries = db.fetch_due_webhook_deliveries(
            conn, workspace_id=workspace_id, batch_size=batch_size
        )
        return [
            deliver_single_delivery(
                conn,
                d,
                encryption_key=encryption_key,
                timeout=timeout,
                is_production=is_production,
            )
            for d in deliveries
        ]

    with db.get_connection() as new_conn:
        deliveries = db.fetch_due_webhook_deliveries(
            new_conn, workspace_id=workspace_id, batch_size=batch_size
        )
        return [
            deliver_single_delivery(
                new_conn,
                d,
                encryption_key=encryption_key,
                timeout=timeout,
                is_production=is_production,
            )
            for d in deliveries
        ]
