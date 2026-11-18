"""Nightly live staging E2E checks for Streamable HTTP MCP endpoint (M5-06, ADR-018)."""

from __future__ import annotations

import json
import os
import urllib.request
from decimal import Decimal

import pytest

from scripts.post_deploy_smoke import (
    _init_mcp_session,
    _parse_mcp_rpc_body,
    _post_mcp_json,
    check_mcp_smoke,
)
from tests.test_live_e2e_j0_j4 import BudgetGuard

pytestmark = [pytest.mark.live_e2e]

DEFAULT_STAGING_URL = "http://localhost:8501"


@pytest.fixture
def live_mcp_target() -> str:
    """Resolve live staging URL for MCP checks, skipping locally unless forced."""
    raw_url = os.environ.get("STAGING_MCP_URL") or os.environ.get(
        "STAGING_APP_URL", DEFAULT_STAGING_URL
    )
    url = raw_url.strip().rstrip("/")
    if os.environ.get("REQUIRE_LIVE_SERVER") != "true":
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=3):  # noqa: S310
                pass
        except Exception as exc:
            pytest.skip(
                f"Live server at {url} is unreachable ({exc}). "
                "Set REQUIRE_LIVE_SERVER=true to require an active staging server."
            )
    return url


def test_live_e2e_mcp_fail_closed_and_readonly_session(live_mcp_target: str) -> None:
    """Verify live `/mcp` returns 401 unauthenticated and passes read-only MCP session checks."""
    mcp_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if os.environ.get("REQUIRE_LIVE_MCP") == "true" and not mcp_token:
        pytest.fail("REQUIRE_LIVE_MCP=true requires MCP_AUTH_TOKEN to be configured")

    smoke_res = check_mcp_smoke(live_mcp_target, mcp_token=mcp_token or None, timeout=15.0)
    assert smoke_res["unauth_401"] is True
    if not mcp_token:
        return

    assert smoke_res["auth_verified"] is True
    mcp_url = f"{live_mcp_target}/mcp"
    hdrs = _init_mcp_session(mcp_url, token=mcp_token, timeout=15.0, insecure=False)
    _post_mcp_json(
        mcp_url,
        payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=hdrs,
    )

    list_status, _hdrs, list_body = _post_mcp_json(
        mcp_url,
        payload={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "macy_list_workspaces", "arguments": {}},
        },
        headers=hdrs,
    )
    assert list_status == 200
    list_rpc = _parse_mcp_rpc_body(list_body)
    ws_payload = json.loads(list_rpc["result"]["content"][0]["text"])
    workspaces = ws_payload["workspaces"]
    if not workspaces:
        return

    ws_id = str(workspaces[0]["workspace_id"])
    status_code, _sh, status_body = _post_mcp_json(
        mcp_url,
        payload={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "macy_get_workspace_status", "arguments": {"workspace_id": ws_id}},
        },
        headers=hdrs,
    )
    assert status_code == 200
    status_data = json.loads(_parse_mcp_rpc_body(status_body)["result"]["content"][0]["text"])
    guard = BudgetGuard(default_cap_usd=Decimal("5.00"))
    spend = Decimal(str(status_data.get("monthly_spend_usd") or "0"))
    cap = Decimal(str(status_data.get("monthly_cap_usd") or "120.00"))
    guard.check_spend(spend, cap=cap)
