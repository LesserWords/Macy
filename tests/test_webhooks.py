"""Unit, integration, and property tests for outbound webhooks (F06 AC1-AC7, ADR-017)."""

from __future__ import annotations

import base64
import json
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from hypothesis import given, settings
from hypothesis import strategies as st
from standardwebhooks import Webhook, WebhookVerificationError

from engine import db, events, safe_http, services, webhooks

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


# ---------------------------------------------------------------------------
# Section 1: Hypothesis Property Tests for Exponential Backoff Schedule
# ---------------------------------------------------------------------------


@given(
    attempts=st.integers(min_value=1, max_value=50),
    elapsed_seconds=st.floats(min_value=0.0, max_value=100000.0),
)
@settings(max_examples=100)
def test_hypothesis_backoff_schedule(attempts: int, elapsed_seconds: float) -> None:
    occurred_at = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
    now = occurred_at + timedelta(seconds=elapsed_seconds)

    status, next_at = webhooks.calculate_next_backoff(attempts, occurred_at, now=now)

    if elapsed_seconds >= webhooks.MAX_RETRY_WINDOW_SECONDS:
        assert status == "dead"
        assert next_at is None
    elif status == "dead":
        assert next_at is None
    else:
        assert status == "retrying"
        assert next_at is not None
        assert next_at > now
        # Must not exceed 24h window
        assert (next_at - occurred_at).total_seconds() <= webhooks.MAX_RETRY_WINDOW_SECONDS
        delay = (next_at - now).total_seconds()
        assert delay >= webhooks.INITIAL_DELAY_SECONDS
        assert delay <= webhooks.MAX_DELAY_SECONDS


def test_backoff_clamps_at_max_delay() -> None:
    occurred_at = datetime.now(UTC)
    now = occurred_at + timedelta(seconds=10)
    status, next_at = webhooks.calculate_next_backoff(10, occurred_at, now=now)
    assert status == "retrying"
    assert next_at is not None
    delay = (next_at - now).total_seconds()
    assert delay == webhooks.MAX_DELAY_SECONDS


# ---------------------------------------------------------------------------
# Section 2: Standard Webhooks Signing & Verification (F06 AC2)
# ---------------------------------------------------------------------------


def test_standard_webhooks_signature_roundtrip() -> None:
    token_bytes = secrets.token_bytes(24)
    secret_token = "whsec_" + base64.b64encode(token_bytes).decode("ascii")
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    payload_json = json.dumps({"test": "value", "id": str(event_id)})

    headers = webhooks.build_webhook_headers(secret_token, event_id, now, payload_json)
    assert headers["webhook-id"] == str(event_id)
    assert headers["webhook-signature"].startswith("v1,")
    assert headers["Content-Type"] == "application/json"

    wh = Webhook(secret_token)
    verified = wh.verify(payload_json, headers)
    assert verified == {"test": "value", "id": str(event_id)}

    # Tampered payload fails verification
    with pytest.raises(WebhookVerificationError):
        wh.verify('{"tampered": true}', headers)


# ---------------------------------------------------------------------------
# Section 3: Transactional Outbox Emission (F06 AC1)
# ---------------------------------------------------------------------------


def _seed_workspace_and_owner() -> tuple[uuid.UUID, uuid.UUID]:
    with db.get_connection() as conn:
        ws_id = db.create_workspace(
            conn,
            slug=f"ws-{uuid.uuid4().hex[:8]}",
            name="Webhooks Workspace",
            industry_pack="general",
        )
        u_row = db.create_or_get_user(conn, email=f"user-{uuid.uuid4().hex[:8]}@example.com")
        db.create_membership(conn, workspace_id=ws_id, user_id=u_row["user_id"], role="owner")
        return ws_id, u_row["user_id"]


def test_event_emit_transactional_rollback_ac1() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    sub = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/webhook",
        event_types=["topic.decided", "topics.awaiting_approval"],
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    assert sub["id"] is not None

    entity_id = uuid.uuid4()

    # Emit inside a transaction that rolls back by raising an error
    class CustomRollbackError(Exception):
        pass

    with db.get_connection() as conn:
        try:
            with conn.transaction():
                event_pk = events.emit(
                    conn,
                    workspace_id=ws_id,
                    event_type="topic.decided",
                    entity_type="opportunity",
                    entity_id=entity_id,
                    payload={"decision": "approved"},
                    actor_user_id=user_id,
                )
                # Within transaction, event and delivery exist
                cur_events = db.list_events(conn, workspace_id=ws_id, limit=10)
                assert any(e["id"] == event_pk for e in cur_events)
                cur_deliveries = db.list_webhook_deliveries(conn, workspace_id=ws_id, limit=10)
                assert len(cur_deliveries) == 1
                raise CustomRollbackError("rollback intentional")
        except CustomRollbackError:
            pass

    # After rollback, neither the event nor the delivery row exists in DB
    with db.get_connection() as conn:
        cur_events = db.list_events(conn, workspace_id=ws_id, limit=10)
        assert not any(e["entity_id"] == entity_id for e in cur_events)
        cur_deliveries = db.list_webhook_deliveries(conn, workspace_id=ws_id, limit=10)
        assert len(cur_deliveries) == 0


def test_event_emit_only_subscribes_matching_and_enabled_ac1() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    sub_match = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/match",
        event_types=["topic.decided"],
        enabled=True,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/other",
        event_types=["run.completed"],
        enabled=True,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/disabled",
        event_types=["topic.decided"],
        enabled=False,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )

    entity_id = uuid.uuid4()
    with db.get_connection() as conn, conn.transaction():
        events.emit(
            conn,
            workspace_id=ws_id,
            event_type="topic.decided",
            entity_type="opportunity",
            entity_id=entity_id,
            payload={"decision": "rejected"},
            actor_user_id=user_id,
        )

    deliveries = services.list_deliveries(workspace_id=ws_id, user_id=user_id)
    assert len(deliveries) == 1
    assert deliveries[0]["subscription_id"] == sub_match["id"]
    assert deliveries[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# Section 4: Subscription Security, SSRF & Encrypted Secret (F06 AC5, AC7)
# ---------------------------------------------------------------------------


def test_save_subscription_refuses_ssrf_and_private_ip_ac5() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    # Private IP
    with pytest.raises(safe_http.BlockedIPError):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=user_id,
            url="http://127.0.0.1:8080/hook",
            event_types=["topic.decided"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    # Cloud metadata
    with pytest.raises(safe_http.BlockedIPError):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=user_id,
            url="http://169.254.169.254/latest/meta-data",
            event_types=["topic.decided"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    # Insecure scheme in production
    with pytest.raises(safe_http.InsecureSchemeError):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=user_id,
            url="http://example.com/hook",
            event_types=["topic.decided"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=True,
        )

    # Empty url or empty event types
    with pytest.raises(ValueError, match="Webhook URL cannot be empty"):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=user_id,
            url="",
            event_types=["topic.decided"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    with pytest.raises(ValueError, match="at least one event type"):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=user_id,
            url="https://example.com/hook",
            event_types=[],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )


def test_save_subscription_secret_shown_once_and_encrypted_ac7() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    created = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/valid",
        event_types=["run.completed"],
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    secret_token = created["secret"]
    assert secret_token is not None
    assert secret_token.startswith("whsec_")

    # Verify secret is stored encrypted in database
    with db.get_connection() as conn:
        raw_row = db.get_webhook_subscription(
            conn, workspace_id=ws_id, subscription_id=created["id"]
        )
        assert raw_row is not None
        encrypted_bytes = raw_row["secret"]
        assert isinstance(encrypted_bytes, (bytes, memoryview))
        # Secret in database is encrypted Fernet ciphertext, not plaintext
        assert not bytes(encrypted_bytes).startswith(b"whsec_")

    # In subscription list, secret is not present
    subs = services.list_subscriptions(workspace_id=ws_id, user_id=user_id)
    assert len(subs) == 1
    assert "secret" not in subs[0]

    # Updating subscription does not leak or regenerate secret
    updated = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/updated",
        event_types=["run.completed", "run.failed"],
        subscription_id=created["id"],
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    assert updated["secret"] is None
    assert updated["url"] == "https://example.com/updated"

    # Delete subscription removes both subscription and deliveries
    deleted = services.delete_subscription(
        workspace_id=ws_id,
        subscription_id=created["id"],
        user_id=user_id,
    )
    assert deleted is True
    assert len(services.list_subscriptions(workspace_id=ws_id, user_id=user_id)) == 0


# ---------------------------------------------------------------------------
# Section 5: Delivery Worker, Local Receiver & Retries (F06 AC2, AC3, AC4)
# ---------------------------------------------------------------------------


class MockWebhookReceiver(HTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        secret: str,
        response_code: int = 200,
    ) -> None:
        self.secret = secret
        self.response_code = response_code
        self.received_requests: list[dict[str, Any]] = []
        super().__init__(server_address, MockWebhookHandler)


class MockWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        srv = cast("MockWebhookReceiver", self.server)
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length).decode("utf-8")
        headers_dict = {k.lower(): v for k, v in self.headers.items()}

        wh = Webhook(srv.secret)
        try:
            verified_data = wh.verify(body, headers_dict)
            srv.received_requests.append(
                {"headers": headers_dict, "body": body, "verified": verified_data}
            )
            self.send_response(srv.response_code)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        except Exception as exc:  # noqa: BLE001
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(exc).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _start_mock_receiver(response_code: int = 200) -> tuple[MockWebhookReceiver, str]:
    srv = MockWebhookReceiver(("127.0.0.1", 0), secret="", response_code=response_code)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/webhook"


def _subscribe_and_emit(
    ws_id: uuid.UUID,
    user_id: uuid.UUID,
    url: str,
    event_type: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], uuid.UUID]:
    sub = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url=url,
        event_types=[event_type],
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    ent_id = uuid.uuid4()
    with db.get_connection() as conn, conn.transaction():
        event_pk = events.emit(
            conn,
            workspace_id=ws_id,
            event_type=event_type,
            entity_type="opportunity",
            entity_id=ent_id,
            payload=payload,
            actor_user_id=user_id,
        )
    return sub, event_pk


def test_delivery_worker_success_ac2(monkeypatch: pytest.MonkeyPatch) -> None:
    # Allow localhost connection in test environment
    monkeypatch.setattr(safe_http, "is_ip_allowed", lambda ip: True)

    ws_id, user_id = _seed_workspace_and_owner()
    server, url = _start_mock_receiver(200)

    try:
        sub, event_pk = _subscribe_and_emit(
            ws_id,
            user_id,
            url,
            "topic.decided",
            {"topic": "Test AI", "decision": "approved"},
        )
        server.secret = sub["secret"]

        # Dispatch via deliver_webhooks
        results = services.deliver_webhooks(
            workspace_id=ws_id,
            batch_size=10,
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert len(results) >= 1
        delivery_res = next(r for r in results if r["event_id"] == event_pk)
        assert delivery_res["status"] == "delivered"
        assert delivery_res["http_status"] == 200

        # Assert local receiver received and verified the Standard Webhooks signature
        assert len(server.received_requests) == 1
        rec = server.received_requests[0]
        assert rec["verified"]["data"]["topic"] == "Test AI"
        assert rec["headers"]["webhook-id"] == str(event_pk)
    finally:
        server.shutdown()
        server.server_close()


def test_delivery_worker_retry_and_dead_ac3_and_resend_ac4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safe_http, "is_ip_allowed", lambda ip: True)

    ws_id, user_id = _seed_workspace_and_owner()
    server, url = _start_mock_receiver(500)

    try:
        sub, event_pk = _subscribe_and_emit(
            ws_id,
            user_id,
            url,
            "run.completed",
            {"status": "completed"},
        )
        server.secret = sub["secret"]

        # 1. Delivery failure results in retrying status with backoff
        results = services.deliver_webhooks(
            workspace_id=ws_id,
            batch_size=10,
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert len(results) >= 1
        res = next(r for r in results if r["event_id"] == event_pk)
        assert res["status"] == "retrying"
        assert res["attempts"] == 1

        # 2. When delivery attempt happens 25 hours after occurred_at, transitions to dead
        with db.get_connection() as conn:
            item = db.get_webhook_delivery(
                conn, workspace_id=ws_id, subscription_id=sub["id"], event_id=event_pk
            )
            assert item is not None
            future_now = item["occurred_at"] + timedelta(hours=25)
            result_dead = webhooks.deliver_single_delivery(
                conn,
                item,
                encryption_key=TEST_ENCRYPTION_KEY,
                is_production=False,
                now=future_now,
            )
            assert result_dead["status"] == "dead"

        # 3. Manual re-send on dead delivery resets to pending (AC4)
        resend_row = services.resend(
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=event_pk,
            user_id=user_id,
            execute_immediately=False,
        )
        assert resend_row["status"] == "pending"

        # 4. Immediate re-send executes immediately
        server.response_code = 200
        immediate_res = services.resend(
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=event_pk,
            user_id=user_id,
            execute_immediately=True,
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert immediate_res["status"] == "delivered"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Section 6: Pipeline Isolation (F06 AC6)
# ---------------------------------------------------------------------------


def test_pipeline_continues_unaffected_when_receiver_offline_ac6(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safe_http, "is_ip_allowed", lambda ip: True)

    ws_id, user_id = _seed_workspace_and_owner()

    # Target an offline port on localhost where no server is listening
    services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="http://127.0.0.1:59998/offline",
        event_types=["run.completed"],
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )

    with db.get_connection() as conn:
        camp_id = db.create_campaign(
            conn,
            workspace_id=ws_id,
            name="Webhooks Campaign",
            locale="pt-BR",
            market="BR",
            articles_per_week=1,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("150.00"),
            run_cap_usd=Decimal("25.00"),
            config={},
            config_schema_version=1,
            created_by=user_id,
        )
        run_id = db.create_run(
            conn,
            workspace_id=ws_id,
            campaign_id=camp_id,
            trigger="manual",
            window_start=datetime.now(UTC) - timedelta(days=7),
            window_end=datetime.now(UTC),
            run_cap_usd=Decimal("50.00"),
            code_version="test",
            pack_version="test",
            created_by=user_id,
        )

        # Emitting run.completed does not fail or crash
        event_pk = events.emit(
            conn,
            workspace_id=ws_id,
            event_type="run.completed",
            entity_type="run",
            entity_id=run_id,
            payload={"run_id": str(run_id), "status": "completed"},
            actor_user_id=user_id,
        )

    # Deliver due in background fails safely without raising exceptions to pipeline
    results = services.deliver_webhooks(
        workspace_id=ws_id,
        batch_size=5,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )
    assert len(results) >= 1
    assert any(r["event_id"] == event_pk for r in results)


# ---------------------------------------------------------------------------
# Section 6: Edge Cases, Error Handling, and 100% Branch Coverage
# ---------------------------------------------------------------------------


def test_db_create_webhook_subscription_error_branch() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = None

    with pytest.raises(RuntimeError, match="Failed to insert webhook subscription"):
        db.create_webhook_subscription(
            mock_conn,
            workspace_id=uuid.uuid4(),
            url="https://example.com/webhook",
            event_types=["run.completed"],
            secret=b"encrypted_secret",
            created_by=uuid.uuid4(),
        )


def test_webhooks_naive_datetimes_support() -> None:
    naive_occurred = datetime(2026, 11, 1, 12, 0, 0)
    naive_now = datetime(2026, 11, 1, 12, 5, 0)
    status, next_at = webhooks.calculate_next_backoff(1, naive_occurred, now=naive_now)
    assert status == "retrying"
    assert next_at is not None
    assert next_at.tzinfo == UTC


def test_webhooks_disabled_subscription_delivery() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    # 1. Create subscription enabled=True
    sub = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/endpoint",
        event_types=["run.completed"],
        enabled=True,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )

    with db.get_connection() as conn, conn.transaction():
        event_pk = events.emit(
            conn,
            workspace_id=ws_id,
            event_type="run.completed",
            entity_type="run",
            entity_id=uuid.uuid4(),
            payload={"test": "disabled"},
            actor_user_id=user_id,
        )

    # 2. Disable subscription after delivery is already queued
    services.save_subscription(
        workspace_id=ws_id,
        subscription_id=sub["id"],
        user_id=user_id,
        url="https://example.com/endpoint",
        event_types=["run.completed"],
        enabled=False,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )

    with db.get_connection() as conn:
        delivery = db.get_webhook_delivery(
            conn,
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=event_pk,
        )
        assert delivery is not None
        assert delivery["enabled"] is False

        # Deliver when enabled=False
        res = webhooks.deliver_single_delivery(
            conn,
            delivery,
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert res["status"] == "dead"
        assert res["error"] == "Subscription disabled"


def test_webhooks_deliver_due_with_explicit_conn() -> None:
    ws_id, user_id = _seed_workspace_and_owner()

    sub = services.save_subscription(
        workspace_id=ws_id,
        user_id=user_id,
        url="https://example.com/webhook",
        event_types=["run.completed"],
        enabled=True,
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )

    with db.get_connection() as conn:
        with conn.transaction():
            events.emit(
                conn,
                workspace_id=ws_id,
                event_type="run.completed",
                entity_type="run",
                entity_id=uuid.uuid4(),
                payload={"test": "conn"},
                actor_user_id=user_id,
            )

        # deliver_due with explicit conn
        results = webhooks.deliver_due(
            conn=conn,
            workspace_id=ws_id,
            batch_size=5,
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert len(results) >= 1
        assert any(r["subscription_id"] == sub["id"] for r in results)


def test_services_validation_and_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_id, owner_id = _seed_workspace_and_owner()

    # Seed a non-member user
    with db.get_connection() as conn:
        email = f"nonmember-{uuid.uuid4().hex[:8]}@example.com"
        u_non_member = db.create_or_get_user(conn, email=email)
        non_member_id = u_non_member["user_id"]

    # 1. Empty URL
    with pytest.raises(ValueError, match="Webhook URL cannot be empty"):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=owner_id,
            url="",
            event_types=["run.completed"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    # 2. Empty event_types
    with pytest.raises(ValueError, match="must include at least one event type"):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=owner_id,
            url="https://example.com/webhook",
            event_types=[],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    # 3. Non-member save_subscription raises PermissionError
    with pytest.raises(PermissionError, match="not a member"):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=non_member_id,
            url="https://example.com/webhook",
            event_types=["run.completed"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    sub = services.save_subscription(
        workspace_id=ws_id,
        user_id=owner_id,
        url="https://example.com/webhook",
        event_types=["run.completed"],
        encryption_key=TEST_ENCRYPTION_KEY,
        is_production=False,
    )

    # 4. Non-owner role permission checks (mocking non-owner role for defense-in-depth)
    monkeypatch.setattr(services, "require_member", lambda *_, **__: "viewer")

    with pytest.raises(PermissionError, match="must have owner role to manage webhooks"):
        services.save_subscription(
            workspace_id=ws_id,
            user_id=owner_id,
            url="https://example.com/webhook",
            event_types=["run.completed"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    with pytest.raises(PermissionError, match="must have owner role to delete webhooks"):
        services.delete_subscription(
            workspace_id=ws_id,
            subscription_id=sub["id"],
            user_id=owner_id,
        )

    with (
        db.get_connection() as conn,
        pytest.raises(PermissionError, match="must have owner role to delete webhooks"),
    ):
        services.delete_subscription(
            conn=conn,
            workspace_id=ws_id,
            subscription_id=sub["id"],
            user_id=owner_id,
        )

    fake_event = uuid.uuid4()
    with pytest.raises(PermissionError, match="must have owner role to re-send webhooks"):
        services.resend(
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=fake_event,
            user_id=owner_id,
        )

    monkeypatch.undo()

    # 5. Update non-existent subscription
    with pytest.raises(ValueError, match="not found"):
        services.save_subscription(
            workspace_id=ws_id,
            subscription_id=uuid.uuid4(),
            user_id=owner_id,
            url="https://example.com/new",
            event_types=["run.completed"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )

    # 6. Resend non-existent delivery
    with pytest.raises(ValueError, match="not found"):
        services.resend(
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=fake_event,
            user_id=owner_id,
        )


def test_services_with_explicit_conn_and_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_id, owner_id = _seed_workspace_and_owner()

    with db.get_connection() as conn:
        # 1. save_subscription with explicit conn
        sub = services.save_subscription(
            conn=conn,
            workspace_id=ws_id,
            user_id=owner_id,
            url="https://example.com/v1",
            event_types=["run.completed"],
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert sub["secret"] is not None

        # 2. update subscription with explicit conn (secret omitted on update)
        sub_updated = services.save_subscription(
            conn=conn,
            workspace_id=ws_id,
            subscription_id=sub["id"],
            user_id=owner_id,
            url="https://example.com/v2",
            event_types=["topic.decided"],
            enabled=True,
            encryption_key=TEST_ENCRYPTION_KEY,
            is_production=False,
        )
        assert sub_updated["url"] == "https://example.com/v2"
        assert sub_updated["secret"] is None

        # 3. list_subscriptions with explicit conn
        subs = services.list_subscriptions(conn=conn, workspace_id=ws_id, user_id=owner_id)
        assert len(subs) >= 1

        # 4. emit event and list_deliveries with explicit conn
        with conn.transaction():
            event_pk = events.emit(
                conn,
                workspace_id=ws_id,
                event_type="topic.decided",
                entity_type="topic",
                entity_id=uuid.uuid4(),
                payload={"topic": "Test"},
                actor_user_id=owner_id,
            )

        delivs = services.list_deliveries(conn=conn, workspace_id=ws_id, user_id=owner_id)
        assert any(d["event_id"] == event_pk for d in delivs)

        # 5. resend with execute_immediately=False and explicit conn
        resend_res = services.resend(
            conn=conn,
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=event_pk,
            user_id=owner_id,
            execute_immediately=False,
        )
        assert resend_res["status"] == "pending"

        # 6. resend with execute_immediately=True but get_webhook_delivery returning None
        monkeypatch.setattr(db, "get_webhook_delivery", lambda *args, **kwargs: None)
        resend_fallback = services.resend(
            conn=conn,
            workspace_id=ws_id,
            subscription_id=sub["id"],
            event_id=event_pk,
            user_id=owner_id,
            execute_immediately=True,
        )
        assert resend_fallback["status"] == "pending"

        # 7. delete_subscription with explicit conn
        deleted = services.delete_subscription(
            conn=conn,
            workspace_id=ws_id,
            subscription_id=sub["id"],
            user_id=owner_id,
        )
        assert deleted is True
