"""Mode 4 E2E Test: Zero-Database `--mock` MCP sandbox seasonal MOFU editorial lifecycle."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.e2e.editorial_helpers import (
    ROOT_DIR,
    _call_stdio_tool,
)


async def _run_mock_mcp_journey(params: StdioServerParameters) -> dict[str, Any]:
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        ws_list = await _call_stdio_tool(session, "macy_list_workspaces", {})
        ws_id = str(ws_list["workspaces"][0]["workspace_id"])
        res_topic = await _call_stdio_tool(
            session,
            "macy_research_topic",
            {"workspace_id": ws_id, "topic": "Black Friday e fim de ano", "offline": True},
        )
        scored = await _call_stdio_tool(
            session,
            "macy_score_topics",
            {"workspace_id": ws_id, "run_id": res_topic["run_id"], "offline": True},
        )
        opp_id = str(scored["topics"][0]["id"])
        await _call_stdio_tool(
            session,
            "macy_decide_topic",
            {"workspace_id": ws_id, "opportunity_id": opp_id, "decision": "approved"},
        )
        brief = await _call_stdio_tool(
            session,
            "macy_get_editorial_brief",
            {
                "workspace_id": ws_id,
                "opportunity_id": opp_id,
                "format_slug": "blog",
                "funnel_stage": "MOFU",
                "offline": True,
            },
        )
        draft = await _call_stdio_tool(
            session,
            "macy_generate_draft",
            {"workspace_id": ws_id, "brief_id": str(brief["id"]), "offline": True},
        )
        draft_id = str(draft["id"])
        claims_res = await _call_stdio_tool(
            session,
            "macy_verify_claims",
            {"workspace_id": ws_id, "draft_id": draft_id, "offline": True},
        )
        eval_res = await _call_stdio_tool(
            session,
            "macy_evaluate_draft",
            {"workspace_id": ws_id, "draft_id": draft_id, "offline": True},
        )
        await _call_stdio_tool(
            session,
            "macy_decide_draft",
            {"workspace_id": ws_id, "draft_id": draft_id, "decision": "approved"},
        )
        pub_res = await _call_stdio_tool(
            session,
            "macy_publish_draft",
            {"workspace_id": ws_id, "draft_id": draft_id},
        )
        return {
            "claims": claims_res,
            "evaluate": eval_res,
            "publish": pub_res,
        }


def test_e2e_mode4_mcp_mock_seasonal_mofu_flow() -> None:
    """Mode 4 E2E: Zero-DB MCP stdio --mock full seasonal workflow through publish."""
    env_mock = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "ENV"}}
    env_mock.update({"APP_ENV": "development", "MACY_USER_EMAIL": "owner@example.com"})
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "engine.cli", "mcp", "--transport", "stdio", "--mock"],
        cwd=str(ROOT_DIR),
        env=env_mock,
    )
    out = asyncio.run(_run_mock_mcp_journey(params))
    assert out["claims"]["ok"] is True
    assert out["evaluate"]["verdict"] == "PASS"
    assert out["publish"]["status"] == "published"
