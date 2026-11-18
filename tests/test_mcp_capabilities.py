"""Tests for M7-01 D3 MCP capability filtering and CAPABILITY_DISABLED guards."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from engine import services
from engine.mcp.mock_store import DEMO_WS_ID, MockStore
from engine.mcp.server import create_mcp_server, list_tools_for_workspace
from tests.test_mcp_research_gate1 import _call_mcp_tool


def test_mcp_mock_store_capability_filtering_and_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: Disabling editorial-pipeline filters tools and returns CAPABILITY_DISABLED."""
    monkeypatch.setenv("APP_ENV", "development")

    # Filtered server creation omits editorial tools from tool registry
    scoped_bundle = create_mcp_server(
        mock=True,
        transport="stdio",
        enabled_capabilities=["core-compliance", "seo-audit"],
    )
    registered_names = {t.name for t in scoped_bundle.mcp._tool_manager.list_tools()}
    assert "macy_audit_url" in registered_names
    assert "macy_import_draft" in registered_names
    assert "macy_generate_draft" not in registered_names

    # Full bundle enforcing per-workspace switchboard
    bundle = create_mcp_server(mock=True, transport="stdio")
    assert isinstance(bundle.store, MockStore)
    bundle.store.set_enabled_capabilities(
        workspace_id=DEMO_WS_ID,
        enabled_capabilities=["core-compliance", "seo-audit"],
    )

    allowed = list_tools_for_workspace(bundle, workspace_id=DEMO_WS_ID)
    assert "macy_audit_url" in allowed
    assert "macy_generate_draft" not in allowed

    res = _call_mcp_tool(
        bundle,
        "macy_generate_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "brief_id": str(uuid.uuid4()),
            "actor_email": "owner@example.com",
        },
    )
    assert res["error"] == "CAPABILITY_DISABLED"


def test_mcp_db_store_capability_guard(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: ServiceStoreAdapter enforces workspace enabled_capabilities and CAPABILITY_DISABLED."""
    monkeypatch.setenv("APP_ENV", "development")
    slug = f"ws-mcpcap-{uuid.uuid4().hex[:8]}"
    email = f"mcpcap-{uuid.uuid4().hex[:6]}@example.com"
    ws = services.create_workspace(db_conn, slug=slug, name="MCP Cap WS")
    owner = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    services.set_enabled_capabilities(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=owner["user"]["user_id"],
        enabled_capabilities=["seo-audit"],
    )

    bundle = create_mcp_server(mock=False, transport="stdio")
    allowed = list_tools_for_workspace(bundle, workspace_id=ws["workspace_id"])
    assert "macy_audit_url" in allowed
    assert "macy_generate_draft" not in allowed

    res = _call_mcp_tool(
        bundle,
        "macy_generate_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "brief_id": str(uuid.uuid4()),
            "actor_email": email,
        },
    )
    assert res["error"] == "CAPABILITY_DISABLED"
