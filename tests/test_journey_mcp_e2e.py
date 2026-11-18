"""Offline & containerized Streamable HTTP MCP end-to-end journey test (M5-06)."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg
import pytest
import uvicorn
from cryptography.fernet import Fernet
from standardwebhooks import Webhook

from engine import db, safe_http, services
from engine.integrations.config import set_workspace_integration
from engine.mcp.server import create_mcp_server
from scripts.post_deploy_smoke import (
    _extract_mcp_tool_payload,
    _init_mcp_session,
    _parse_mcp_rpc_body,
    _post_mcp_json,
)
from tests.test_mcp_auth import _seed_workspace_fixture
from tests.test_mcp_research_gate1 import _seed_fake_workspace_integrations

WEBHOOK_SIGNING_SECRET = "whsec_" + base64.b64encode(b"e2e-webhook-test-secret!").decode("ascii")
DEFAULT_E2E_TOKEN = "e2e-test-mcp-bearer-token-32chars"
DEFAULT_E2E_ACTOR = "owner@example.test"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _local_webhook_receiver() -> Generator[tuple[str, list[dict[str, Any]]], None, None]:
    lock = threading.Lock()
    received: list[dict[str, Any]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            with lock:
                received.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": parsed,
                        "raw": raw,
                    }
                )
            draft = parsed.get("draft") if isinstance(parsed, dict) else {}
            slug = draft.get("slug", "e2e-post") if isinstance(draft, dict) else "e2e-post"
            did = parsed.get("draft_id", "e2e") if isinstance(parsed, dict) else "e2e"
            resp = json.dumps(
                {
                    "schema_version": 1,
                    "destination": "http_webhook",
                    "external_id": f"e2e-{did}",
                    "url": f"https://cms.example.com/posts/{slug}",
                    "status": "published",
                    "raw": {"mode": parsed.get("mode", "draft")},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/publish", received
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


@contextmanager
def _local_http_mcp_server(token: str, actor_email: str) -> Generator[str, None, None]:
    env_cfg = {
        "APP_ENV": "test",
        "MCP_AUTH_TOKEN": token,
        "MCP_ACTOR_EMAIL": actor_email,
    }
    bundle = create_mcp_server(mock=False, transport="streamable-http", env=env_cfg)
    app = bundle.create_http_app(host="127.0.0.1")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"{base_url}/healthz", timeout=1) as resp:  # noqa: S310
                    if getattr(resp, "status", 200) == 200:
                        break
            except Exception:
                time.sleep(0.1)
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _seed_e2e_workspace(
    conn: psycopg.Connection[Any],
    *,
    owner_email: str,
    webhook_target_url: str,
) -> dict[str, Any]:
    ws = _seed_workspace_fixture(
        conn,
        tag=f"e2e-{uuid.uuid4().hex[:6]}",
        owner_email=owner_email,
        with_campaign=True,
    )
    ws_id: uuid.UUID = ws["workspace_id"]
    owner_id: uuid.UUID = ws["owner_id"]
    services.seed_brand(conn, workspace_slug=ws["slug"])
    _seed_fake_workspace_integrations(conn, workspace_id=ws_id, user_id=owner_id)
    set_workspace_integration(
        conn,
        workspace_id=ws_id,
        port="keyword_provider",
        plugin="fake",
        settings={},
        secrets=None,
        updated_by=owner_id,
    )
    set_workspace_integration(
        conn,
        workspace_id=ws_id,
        port="publisher",
        plugin="http_webhook",
        settings={
            "endpoint_url": webhook_target_url,
            "timeout_seconds": 10.0,
            "default_mode": "draft",
        },
        secrets={"signing_secret": WEBHOOK_SIGNING_SECRET},
        updated_by=owner_id,
    )
    return ws


def _mcp_tool_call(
    mcp_url: str,
    session_headers: dict[str, str],
    *,
    req_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    status, _hdrs, body = _post_mcp_json(
        mcp_url,
        payload={
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=session_headers,
        timeout=30.0,
    )
    assert status == 200, f"{name} returned HTTP {status}: {body}"
    return _extract_mcp_tool_payload(_parse_mcp_rpc_body(body))


def _assert_health_and_fail_closed(base_url: str, bad_token: str) -> None:
    mcp_url = f"{base_url}/mcp"
    with urllib.request.urlopen(f"{base_url}/healthz", timeout=5) as health_resp:  # noqa: S310
        assert getattr(health_resp, "status", 200) == 200
        assert json.loads(health_resp.read().decode("utf-8"))["status"] == "ok"

    unauth_status, unauth_hdrs, _ = _post_mcp_json(
        mcp_url,
        payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert unauth_status == 401
    assert "bearer" in unauth_hdrs.get("www-authenticate", "").lower()

    bad_status, _, _ = _post_mcp_json(
        mcp_url,
        payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert bad_status == 401


def _exercise_http_mcp_journey(
    base_url: str,
    *,
    token: str,
    ws_slug: str,
    ws_id: str,
) -> tuple[str, str]:
    mcp_url = f"{base_url}/mcp"
    hdrs = _init_mcp_session(mcp_url, token=token, timeout=10.0, insecure=False)
    notif_status, _, _ = _post_mcp_json(
        mcp_url,
        payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=hdrs,
    )
    assert notif_status in (200, 202, 204)

    listed = _mcp_tool_call(mcp_url, hdrs, req_id=2, name="macy_list_workspaces", arguments={})
    assert listed["ok"] is True
    assert any(w["slug"] == ws_slug and w["workspace_id"] == ws_id for w in listed["workspaces"])

    status_res = _mcp_tool_call(
        mcp_url, hdrs, req_id=3, name="macy_get_workspace_status", arguments={"workspace_id": ws_id}
    )
    assert status_res["ok"] is True
    assert "publisher" in status_res["configured_ports"]

    research = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=4,
        name="macy_research_topic",
        arguments={"workspace_id": ws_id, "topic": "gestao-estoque-oticas"},
    )
    assert research["ok"] is True
    assert research["document_count"] >= 1
    run_id = str(research["run_id"])

    scored = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=5,
        name="macy_score_topics",
        arguments={"workspace_id": ws_id, "run_id": run_id},
    )
    assert scored["ok"] is True
    assert scored["topics"]

    gate1 = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=6,
        name="macy_list_topics_awaiting_approval",
        arguments={"workspace_id": ws_id, "run_id": run_id},
    )
    assert gate1["count"] >= 1
    opp_id = str(gate1["topics"][0]["id"])

    dec_t = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=7,
        name="macy_decide_topic",
        arguments={"workspace_id": ws_id, "opportunity_id": opp_id, "decision": "approved"},
    )
    assert dec_t["ok"] is True
    assert dec_t["status"] == "approved"

    brief = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=8,
        name="macy_get_editorial_brief",
        arguments={"workspace_id": ws_id, "opportunity_id": opp_id},
    )
    assert brief["ok"] is True

    draft = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=9,
        name="macy_generate_draft",
        arguments={"workspace_id": ws_id, "brief_id": str(brief["id"])},
    )
    assert draft["ok"] is True
    draft_id = str(draft["id"])

    claims = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=10,
        name="macy_verify_claims",
        arguments={"workspace_id": ws_id, "draft_id": draft_id},
    )
    assert claims["ok"] is True
    assert claims["verified_count"] >= 1

    evaluated = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=11,
        name="macy_evaluate_draft",
        arguments={"workspace_id": ws_id, "draft_id": draft_id},
    )
    assert evaluated["ok"] is True
    assert evaluated["status"] == "pass"

    gate2 = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=12,
        name="macy_list_drafts_awaiting_approval",
        arguments={"workspace_id": ws_id},
    )
    assert any(d["id"] == draft_id for d in gate2["drafts"])

    dec_d = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=13,
        name="macy_decide_draft",
        arguments={"workspace_id": ws_id, "draft_id": draft_id, "decision": "approved"},
    )
    assert dec_d["ok"] is True
    assert dec_d["status"] in {"approved", "published"}

    pub = _mcp_tool_call(
        mcp_url,
        hdrs,
        req_id=14,
        name="macy_publish_draft",
        arguments={
            "workspace_id": ws_id,
            "draft_id": draft_id,
            "mode": "draft",
            "destination": "http_webhook",
        },
    )
    assert pub["ok"] is True
    assert pub["status"] == "published"
    assert pub["destination"] == "http_webhook"
    return run_id, draft_id


def _assert_webhook_signature(entry: dict[str, Any], *, draft_id: str) -> None:
    headers = entry["headers"]
    assert headers.get("webhook-signature", "").startswith("v1,")
    assert entry["body"].get("draft_id") == draft_id
    raw_body = entry.get("raw")
    if isinstance(raw_body, str):
        verified = Webhook(WEBHOOK_SIGNING_SECRET).verify(raw_body, headers)
        assert verified["draft_id"] == draft_id


def test_journey_mcp_http_end_to_end(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise full Streamable HTTP MCP workflow against Postgres without evals."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SAFE_HTTP_TEST_ALLOW_HOSTS", "webhook-receiver")
    external_mcp_url = os.environ.get("MCP_E2E_URL", "").strip().rstrip("/")
    token_val = os.environ.get("MCP_AUTH_TOKEN", DEFAULT_E2E_TOKEN)
    bad_token = "invalid-bearer-token-for-e2e"
    verifier = safe_http.validate_safe_url

    if external_mcp_url:
        actor_email = os.environ.get("MCP_ACTOR_EMAIL", DEFAULT_E2E_ACTOR)
        if not os.environ.get("APP_ENCRYPTION_KEY"):
            pytest.fail("APP_ENCRYPTION_KEY must be set when running against MCP_E2E_URL")
        webhook_target = os.environ.get(
            "MCP_E2E_WEBHOOK_TARGET_URL", "http://webhook-receiver:9000/publish"
        )
        webhook_inspect = os.environ.get("WEBHOOK_RECEIVER_URL", "http://127.0.0.1:9000").rstrip(
            "/"
        )
        ws = _seed_e2e_workspace(
            db_conn,
            owner_email=actor_email,
            webhook_target_url=webhook_target,
        )
        ws_id = str(ws["workspace_id"])
        _assert_health_and_fail_closed(external_mcp_url, bad_token)
        _run_id, draft_id = _exercise_http_mcp_journey(
            external_mcp_url,
            token=token_val,
            ws_slug=ws["slug"],
            ws_id=ws_id,
        )
        with urllib.request.urlopen(f"{webhook_inspect}/requests", timeout=5) as req_resp:  # noqa: S310
            requests_data = json.loads(req_resp.read().decode("utf-8"))
        matched = [
            item
            for item in requests_data["requests"]
            if isinstance(item, dict) and item.get("body", {}).get("draft_id") == draft_id
        ]
        assert matched, f"Webhook receiver did not record draft {draft_id}"
        _assert_webhook_signature(matched[-1], draft_id=draft_id)
        return

    # Local/CI mode: real ephemeral HTTP uvicorn MCP server + real Postgres + blocked evals
    for mod_name in [k for k in sys.modules if k == "evals" or k.startswith("evals.")]:
        monkeypatch.delitem(sys.modules, mod_name)
    monkeypatch.setitem(sys.modules, "evals", None)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    owner_email = f"mcp-e2e-{uuid.uuid4().hex[:6]}@example.test"
    monkeypatch.setattr(safe_http, "resolve_and_validate_host", lambda _host: ["93.184.216.34"])
    assert verifier is safe_http.validate_safe_url

    with _local_webhook_receiver() as (webhook_target, received):
        ws = _seed_e2e_workspace(
            db_conn,
            owner_email=owner_email,
            webhook_target_url=webhook_target,
        )
        ws_id = str(ws["workspace_id"])
        with _local_http_mcp_server(token_val, owner_email) as base_url:
            _assert_health_and_fail_closed(base_url, bad_token)
            _run_id, draft_id = _exercise_http_mcp_journey(
                base_url,
                token=token_val,
                ws_slug=ws["slug"],
                ws_id=ws_id,
            )
        assert sys.modules.get("evals") is None
        assert not any(k.startswith("evals.") for k in sys.modules)
        assert len(received) >= 1
        _assert_webhook_signature(received[-1], draft_id=draft_id)

    pubs = db.list_publications(db_conn, workspace_id=ws["workspace_id"])
    assert any(
        str(p["draft_id"]) == draft_id
        and p["status"] == "published"
        and p["destination"] == "http_webhook"
        for p in pubs
    )
    latest_run = db.list_runs(db_conn, workspace_id=ws["workspace_id"])[0]
    assert Decimal(str(latest_run["run_cap_usd"])) == Decimal("15.00")
