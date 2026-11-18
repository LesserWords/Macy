"""Tests for M4-06 data retention purge (F20 AC7) and LGPD user anonymisation (F20 AC8)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import cli, db, services

OWNER_DATABASE_URL = os.getenv(
    "OWNER_DATABASE_URL", "postgres://owner:secret@localhost:5432/macy?sslmode=disable"
)
RETENTION_DATABASE_URL = os.getenv(
    "RETENTION_DATABASE_URL",
    "postgres://retention:retention_secret@localhost:5432/macy?sslmode=disable",
)


@pytest.fixture
def owner_conn() -> Any:
    conn = db.get_connection(OWNER_DATABASE_URL)
    yield conn
    conn.close()


@pytest.fixture
def retention_conn() -> Any:
    conn = db.get_connection(RETENTION_DATABASE_URL)
    yield conn
    conn.close()


def _seed_retention_fixture(
    owner_conn: psycopg.Connection[Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    user = db.create_or_get_user(
        owner_conn,
        email=f"ret-{tag}@example.com",
        name="Retention Tester",
    )
    user_id = uuid.UUID(str(user["user_id"]))
    ws_id = db.create_workspace(
        owner_conn,
        slug=f"ret-ws-{tag}",
        name="Retention Workspace",
        industry_pack="general",
    )
    db.create_membership(owner_conn, workspace_id=ws_id, user_id=user_id, role="owner")

    sub = db.create_webhook_subscription(
        owner_conn,
        workspace_id=ws_id,
        url="https://hooks.example.com/retention",
        event_types=["run.completed"],
        secret=b"encrypted_secret",
        created_by=user_id,
        enabled=True,
    )
    sub_id = uuid.UUID(str(sub["id"]))

    # 1. llm_payloads: old (95 days) vs fresh (30 days)
    u_old = db.record_usage(
        owner_conn,
        workspace_id=ws_id,
        stage="write",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("0.120000"),
        model="gpt-4o-mini",
    )
    u_fresh = db.record_usage(
        owner_conn,
        workspace_id=ws_id,
        stage="write",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("0.080000"),
        model="gpt-4o-mini",
    )
    db.record_llm_payload(
        owner_conn,
        workspace_id=ws_id,
        usage_id=u_old,
        request={"prompt": "old"},
        response={"text": "old"},
    )
    db.record_llm_payload(
        owner_conn,
        workspace_id=ws_id,
        usage_id=u_fresh,
        request={"prompt": "fresh"},
        response={"text": "fresh"},
    )

    # 2. documents: old (400 days) vs fresh (100 days)
    doc_old = db.upsert_document(
        owner_conn,
        workspace_id=ws_id,
        url=f"https://example.com/old-{tag}",
        canonical_url=f"https://example.com/old-{tag}",
        title="Old Document Title",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"hash-old",
        published_at=now - timedelta(days=400),
        extracted_text="Sensitive third-party article body over 365 days old",
    )
    doc_fresh = db.upsert_document(
        owner_conn,
        workspace_id=ws_id,
        url=f"https://example.com/fresh-{tag}",
        canonical_url=f"https://example.com/fresh-{tag}",
        title="Fresh Document Title",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"hash-fresh",
        published_at=now - timedelta(days=100),
        extracted_text="Recent article body within 365 days",
    )

    # 3. events & webhook_deliveries
    ev_very_old = db.insert_event(
        owner_conn,
        workspace_id=ws_id,
        event_type="run.completed",
        entity_type="workspace",
        entity_id=ws_id,
        payload={"age": "25_months"},
        actor_user_id=user_id,
    )
    ev_deliv_old = db.insert_event(
        owner_conn,
        workspace_id=ws_id,
        event_type="run.completed",
        entity_type="workspace",
        entity_id=ws_id,
        payload={"age": "45_days_delivered"},
        actor_user_id=user_id,
    )
    ev_dead_old = db.insert_event(
        owner_conn,
        workspace_id=ws_id,
        event_type="run.completed",
        entity_type="workspace",
        entity_id=ws_id,
        payload={"age": "45_days_dead"},
        actor_user_id=user_id,
    )
    ev_pending_old = db.insert_event(
        owner_conn,
        workspace_id=ws_id,
        event_type="run.completed",
        entity_type="workspace",
        entity_id=ws_id,
        payload={"age": "45_days_retrying"},
        actor_user_id=user_id,
    )
    ev_fresh = db.insert_event(
        owner_conn,
        workspace_id=ws_id,
        event_type="run.completed",
        entity_type="workspace",
        entity_id=ws_id,
        payload={"age": "10_days_delivered"},
        actor_user_id=user_id,
    )

    with owner_conn.cursor() as cur:
        cur.execute(
            "UPDATE usage_ledger SET created_at = %s WHERE id = %s",
            (now - timedelta(days=95), u_old),
        )
        cur.execute(
            "UPDATE usage_ledger SET created_at = %s WHERE id = %s",
            (now - timedelta(days=30), u_fresh),
        )
        cur.execute(
            "UPDATE documents SET fetched_at = %s WHERE id = %s",
            (now - timedelta(days=400), doc_old["id"]),
        )
        cur.execute(
            "UPDATE documents SET fetched_at = %s WHERE id = %s",
            (now - timedelta(days=100), doc_fresh["id"]),
        )
        cur.execute(
            "UPDATE events SET occurred_at = %s WHERE id = %s",
            (now - timedelta(days=760), ev_very_old),
        )
        cur.execute(
            "UPDATE events SET occurred_at = %s WHERE id IN (%s, %s, %s)",
            (now - timedelta(days=45), ev_deliv_old, ev_dead_old, ev_pending_old),
        )
        cur.execute(
            "UPDATE events SET occurred_at = %s WHERE id = %s",
            (now - timedelta(days=10), ev_fresh),
        )

        # Insert webhook deliveries
        for ev_id, st_val, ts_val in [
            (ev_very_old, "delivered", now - timedelta(days=760)),
            (ev_deliv_old, "delivered", now - timedelta(days=45)),
            (ev_dead_old, "dead", now - timedelta(days=45)),
            (ev_pending_old, "retrying", now - timedelta(days=45)),
            (ev_fresh, "delivered", now - timedelta(days=10)),
        ]:
            cur.execute(
                """
                INSERT INTO webhook_deliveries (
                    workspace_id, subscription_id, event_id, status,
                    attempts, next_attempt_at, delivered_at
                )
                VALUES (%s, %s, %s, %s, 1, %s, %s)
                """,
                (
                    ws_id,
                    sub_id,
                    ev_id,
                    st_val,
                    ts_val,
                    ts_val if st_val == "delivered" else None,
                ),
            )

    return {
        "ws_id": ws_id,
        "sub_id": sub_id,
        "u_old": u_old,
        "u_fresh": u_fresh,
        "doc_old_id": uuid.UUID(str(doc_old["id"])),
        "doc_fresh_id": uuid.UUID(str(doc_fresh["id"])),
        "ev_very_old": ev_very_old,
        "ev_deliv_old": ev_deliv_old,
        "ev_dead_old": ev_dead_old,
        "ev_pending_old": ev_pending_old,
        "ev_fresh": ev_fresh,
    }


def test_purge_expired_data_enforces_adr024_windows_under_retention_role(
    owner_conn: psycopg.Connection[Any],
    retention_conn: psycopg.Connection[Any],
) -> None:
    """Verify F20 AC7 against seeded data with frozen dates under the retention role."""
    frozen_now = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
    seeded = _seed_retention_fixture(owner_conn, now=frozen_now)

    counts = services.purge_expired_data(retention_conn, now=frozen_now)
    assert counts["llm_payloads_deleted"] >= 1
    assert counts["documents_text_purged"] >= 1
    assert counts["webhook_deliveries_deleted"] >= 2
    assert counts["events_deleted"] >= 1

    ws_id = seeded["ws_id"]
    # 1. llm_payloads > 90d deleted, <= 90d kept; both usage_ledger rows kept
    assert db.get_llm_payload(owner_conn, workspace_id=ws_id, usage_id=seeded["u_old"]) is None
    assert (
        db.get_llm_payload(owner_conn, workspace_id=ws_id, usage_id=seeded["u_fresh"]) is not None
    )
    usage_ids = {u["id"] for u in db.list_usage(owner_conn, workspace_id=ws_id)}
    assert seeded["u_old"] in usage_ids
    assert seeded["u_fresh"] in usage_ids

    # 2. documents.extracted_text > 365d nulled + text_purged_at stamped; title/url/hash/dates kept
    doc_old_after = db.get_document(
        owner_conn, workspace_id=ws_id, document_id=seeded["doc_old_id"]
    )
    assert doc_old_after is not None
    assert doc_old_after["extracted_text"] is None
    assert doc_old_after["text_purged_at"] == frozen_now
    assert doc_old_after["title"] == "Old Document Title"
    assert bytes(doc_old_after["content_hash"]) == b"hash-old"
    assert doc_old_after["published_at"] == frozen_now - timedelta(days=400)

    doc_fresh_after = db.get_document(
        owner_conn, workspace_id=ws_id, document_id=seeded["doc_fresh_id"]
    )
    assert doc_fresh_after is not None
    assert doc_fresh_after["extracted_text"] == "Recent article body within 365 days"
    assert doc_fresh_after["text_purged_at"] is None

    # 3. webhook_deliveries: delivered/dead > 30d deleted; retrying > 30d and fresh delivered kept
    remaining_deliveries = {
        d["event_id"]
        for d in db.list_webhook_deliveries(
            owner_conn, workspace_id=ws_id, subscription_id=seeded["sub_id"]
        )
    }
    assert seeded["ev_deliv_old"] not in remaining_deliveries
    assert seeded["ev_dead_old"] not in remaining_deliveries
    assert seeded["ev_pending_old"] in remaining_deliveries
    assert seeded["ev_fresh"] in remaining_deliveries

    # 4. events > 24 months deleted; newer events kept
    remaining_events = {e["id"] for e in db.list_events(owner_conn, workspace_id=ws_id, limit=50)}
    assert seeded["ev_very_old"] not in remaining_events
    assert seeded["ev_deliv_old"] in remaining_events
    assert seeded["ev_dead_old"] in remaining_events
    assert seeded["ev_pending_old"] in remaining_events
    assert seeded["ev_fresh"] in remaining_events


def test_purge_refuses_non_retention_role(db_conn: psycopg.Connection[Any]) -> None:
    """F20 AC7: purge must only execute under the 'retention' database role."""
    frozen_now = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(PermissionError, match="retention"):
        services.purge_expired_data(db_conn, now=frozen_now)


def test_anonymise_user_scrubs_pii_and_keeps_opaque_event_id(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F20 AC8: email and name anonymised, memberships deleted, events keep opaque id."""
    tag = uuid.uuid4().hex[:8]
    raw_email = f"lgpd-{tag}@example.com"
    user = db.create_or_get_user(db_conn, email=raw_email, name="Maria Oliveira")
    user_id = uuid.UUID(str(user["user_id"]))

    ws_id = db.create_workspace(
        db_conn,
        slug=f"lgpd-ws-{tag}",
        name="LGPD Workspace",
        industry_pack="general",
    )
    db.create_membership(db_conn, workspace_id=ws_id, user_id=user_id, role="owner")
    ev_id = db.insert_event(
        db_conn,
        workspace_id=ws_id,
        event_type="membership.created",
        entity_type="membership",
        entity_id=user_id,
        payload={
            "user_id": str(user_id),
            "email": raw_email,
            "name": "Maria Oliveira",
            "role": "owner",
        },
        actor_user_id=user_id,
    )

    result = services.anonymise_user(db_conn, email=raw_email)
    assert result["user_id"] == user_id
    assert result["anonymised_email"] == f"deleted-{user_id}@anonymised.invalid"
    assert result["memberships_removed"] == 1
    assert result["events_scrubbed"] >= 1

    # Old email lookup returns None
    assert db.get_user_by_email(db_conn, email=raw_email) is None

    # Lookup by anonymised email shows name is None and email is opaque
    anon_user = db.get_user_by_email(db_conn, email=f"deleted-{user_id}@anonymised.invalid")
    assert anon_user is not None
    assert anon_user["user_id"] == user_id
    assert anon_user["name"] is None
    assert anon_user["email"] == f"deleted-{user_id}@anonymised.invalid"

    # Memberships are removed
    assert db.list_user_memberships(db_conn, user_id=user_id) == []

    # Events retain opaque actor_user_id and entity_id, but email/name are scrubbed from payload
    events_rows = db.list_events(db_conn, workspace_id=ws_id, entity_id=user_id)
    assert len(events_rows) == 1
    ev = events_rows[0]
    assert ev["id"] == ev_id
    assert ev["actor_user_id"] == user_id
    assert "email" not in ev["payload"]
    assert "name" not in ev["payload"]
    assert ev["payload"]["user_id"] == str(user_id)
    assert ev["payload"]["role"] == "owner"


def test_cli_purge_and_anonymise_user_integration(
    owner_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify CLI `purge` and `anonymise-user` end-to-end against the database."""
    frozen_now = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
    seeded = _seed_retention_fixture(owner_conn, now=frozen_now)
    ws_id = seeded["ws_id"]

    monkeypatch.setenv("RETENTION_DATABASE_URL", RETENTION_DATABASE_URL)
    code_purge = cli.main(["purge", "--now", "2026-11-09T12:00:00Z", "--workspace-id", str(ws_id)])
    assert code_purge == 0
    out_purge = capsys.readouterr().out
    assert "Purged expired data:" in out_purge

    # Naive timestamp in CLI also accepted and coerced to UTC
    code_naive = cli.main(["purge", "--now", "2026-11-09T12:00:00"])
    assert code_naive == 0

    # Invalid timestamp returns exit code 1
    code_bad_now = cli.main(["purge", "--now", "not-a-timestamp"])
    assert code_bad_now == 1
    assert "Error:" in capsys.readouterr().err

    tag = uuid.uuid4().hex[:8]
    email = f"cli-lgpd-{tag}@example.com"
    user = db.create_or_get_user(owner_conn, email=email, name="CLI User")
    code_anon = cli.main(["anonymise-user", email])
    assert code_anon == 0
    out_anon = capsys.readouterr().out
    assert str(user["user_id"]) in out_anon
    assert "Anonymised user" in out_anon

    # Non-existent user returns exit code 1
    code_missing = cli.main(["anonymise-user", "nobody-at-all@example.com"])
    assert code_missing == 1
    err_missing = capsys.readouterr().err
    assert "Error:" in err_missing


def test_retention_role_least_privilege_and_validation_edges(
    retention_conn: psycopg.Connection[Any],
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify retention role cannot delete usage_ledger or documents rows, and edge validations."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        retention_conn.execute("DELETE FROM usage_ledger")

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        retention_conn.execute("DELETE FROM documents")

    # Naive datetime in services.purge_expired_data is normalised to UTC
    naive_now = datetime(2020, 1, 1, 0, 0, 0)
    counts = services.purge_expired_data(retention_conn, now=naive_now)
    assert counts["events_deleted"] >= 0

    # Empty email in services.anonymise_user raises ValueError
    with pytest.raises(ValueError, match="must not be empty"):
        services.anonymise_user(db_conn, email="   ")
