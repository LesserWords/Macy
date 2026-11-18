"""Domain event emission and transactional outbox for webhooks (F06, ADR-017)."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

from engine import db


def emit(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    event_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Emit a domain event and atomically schedule webhook deliveries in caller's transaction.

    Ensures that matching enabled webhook subscriptions for this workspace have delivery
    records created in the same database transaction. If the transaction rolls back, neither
    the event nor its delivery rows exist (F06 AC1).
    """
    event_id = db.insert_event(
        conn,
        workspace_id=workspace_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        actor_user_id=actor_user_id,
    )
    db.insert_webhook_deliveries_for_event(
        conn,
        workspace_id=workspace_id,
        event_id=event_id,
        event_type=event_type,
    )
    return event_id
