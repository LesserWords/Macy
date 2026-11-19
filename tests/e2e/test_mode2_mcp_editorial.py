"""Mode 2 E2E Tests: MCP Server streamable-http and stdio seasonal MOFU editorial flows."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from engine import db, services
from engine.integrations.config import set_workspace_integration
from scripts.post_deploy_smoke import _init_mcp_session
from tests.e2e.editorial_helpers import (
    DEFAULT_LOCAL_TOKEN,
    ROOT_DIR,
    SEASONAL_PRIMARY_KEYWORD,
    _build_seasonal_mofu_markdown,
    _call_stdio_tool,
    _ephemeral_http_mcp_server,
    _mcp_http_tool,
    _post_mcp_json_resilient,
)
from tests.test_mcp_research_gate1 import _seed_fake_workspace_integrations


async def _run_stdio_compliance_session(
    params: StdioServerParameters,
    *,
    ws_id: str,
    body_md: str,
) -> dict[str, Any]:
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        ingest_res = await _call_stdio_tool(
            session,
            "macy_ingest_research_documents",
            {
                "workspace_id": ws_id,
                "topic": "Black Friday e fim de ano no varejo",
                "documents": [
                    {
                        "title": "Guia de estoque para Black Friday e fim de ano",
                        "url": "https://example.com/varejo/black-friday-estoque",
                        "source_name": "Retail Lab",
                        "summary": (
                            "Inventário rotativo e controle de prazos evitam rupturas "
                            "durante picos sazonais de vendas."
                        ),
                        "facts": ["Contagem rotativa reduz rupturas em semanas promocionais."],
                    }
                ],
            },
        )
        import_res = await _call_stdio_tool(
            session,
            "macy_import_draft",
            {
                "workspace_id": ws_id,
                "body_md": body_md,
                "format_slug": "blog",
                "topic": "Black Friday e fim de ano no varejo",
                "primary_keyword": SEASONAL_PRIMARY_KEYWORD,
                "deterministic_only": True,
            },
        )
        draft_id = str(import_res["draft_id"])
        await _call_stdio_tool(
            session,
            "macy_decide_draft",
            {
                "workspace_id": ws_id,
                "draft_id": draft_id,
                "decision": "approved",
                "note": "Aprovado no fluxo MCP stdio de conformidade externa",
            },
        )
        pub_res = await _call_stdio_tool(
            session,
            "macy_publish_draft",
            {"workspace_id": ws_id, "draft_id": draft_id},
        )
        return {"ingest": ingest_res, "import": import_res, "publish": pub_res}


def _seed_mcp_workspace_with_markdown_publisher(
    conn: psycopg.Connection[Any],
    *,
    slug: str,
    owner_email: str,
    export_dir: Path,
) -> dict[str, Any]:
    export_dir.mkdir(parents=True, exist_ok=True)
    ws_id = db.create_workspace(conn, slug=slug, name=f"MCP {slug}", industry_pack="general")
    owner_res = services.add_workspace_owner(conn, workspace_slug=slug, email=owner_email)
    user_id = uuid.UUID(str(owner_res["user"]["user_id"]))
    services.seed_brand(conn, workspace_slug=slug)
    _seed_fake_workspace_integrations(conn, workspace_id=ws_id, user_id=user_id)
    set_workspace_integration(
        conn,
        workspace_id=ws_id,
        port="publisher",
        plugin="markdown_export",
        settings={"export_dir": str(export_dir), "default_mode": "draft"},
        secrets=None,
        updated_by=user_id,
    )
    services.save_campaign(
        conn,
        workspace_id=ws_id,
        user_id=user_id,
        name="Campanha MCP PT-BR",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("120.00"),
        run_cap_usd=Decimal("20.00"),
    )
    return {"workspace_id": ws_id, "user_id": user_id, "slug": slug}


def test_e2e_mode2a_mcp_http_seasonal_mofu_pipeline(
    isolated_modes_e2e_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode 2A E2E: Streamable HTTP sourced editorial pipeline from research to publish."""
    enc_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", isolated_modes_e2e_db)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", enc_key)

    http_export_dir = tmp_path / "exports_http"
    actor_http = "mcp-http@example.com"

    with psycopg.connect(isolated_modes_e2e_db) as conn:
        ws_http = _seed_mcp_workspace_with_markdown_publisher(
            conn,
            slug="seasonal-http-ws",
            owner_email=actor_http,
            export_dir=http_export_dir,
        )
        conn.commit()

    with _ephemeral_http_mcp_server(
        db_url=isolated_modes_e2e_db,
        enc_key=enc_key,
        token=DEFAULT_LOCAL_TOKEN,
        actor_email=actor_http,
    ) as base_url:
        mcp_url = f"{base_url}/mcp"
        unauth_status, _, _ = _post_mcp_json_resilient(
            mcp_url, payload={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        assert unauth_status == 401
        hdrs = _init_mcp_session(mcp_url, token=DEFAULT_LOCAL_TOKEN, timeout=15.0, insecure=False)
        _post_mcp_json_resilient(
            mcp_url,
            payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=hdrs,
        )

        ws_id_http = str(ws_http["workspace_id"])
        listed = _mcp_http_tool(mcp_url, hdrs, req_id=2, name="macy_list_workspaces", arguments={})
        assert any(w["workspace_id"] == ws_id_http for w in listed["workspaces"])

        research = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=3,
            name="macy_research_topic",
            arguments={
                "workspace_id": ws_id_http,
                "topic": "Black Friday e fim de ano no varejo",
                "offline": True,
            },
        )
        run_id = str(research["run_id"])
        scored = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=4,
            name="macy_score_topics",
            arguments={"workspace_id": ws_id_http, "run_id": run_id, "offline": True},
        )
        opp_id = str(scored["topics"][0]["id"])
        _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=5,
            name="macy_decide_topic",
            arguments={
                "workspace_id": ws_id_http,
                "opportunity_id": opp_id,
                "decision": "approved",
            },
        )
        brief = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=6,
            name="macy_get_editorial_brief",
            arguments={
                "workspace_id": ws_id_http,
                "opportunity_id": opp_id,
                "format_slug": "blog",
                "funnel_stage": "MOFU",
                "offline": True,
            },
        )
        draft = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=7,
            name="macy_generate_draft",
            arguments={"workspace_id": ws_id_http, "brief_id": str(brief["id"]), "offline": True},
        )
        draft_id = str(draft["id"])
        claims_res = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=8,
            name="macy_verify_claims",
            arguments={"workspace_id": ws_id_http, "draft_id": draft_id, "offline": True},
        )
        assert claims_res["verified_count"] >= 1
        eval_res = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=9,
            name="macy_evaluate_draft",
            arguments={"workspace_id": ws_id_http, "draft_id": draft_id, "offline": True},
        )
        assert eval_res["verdict"] == "PASS"
        _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=10,
            name="macy_decide_draft",
            arguments={"workspace_id": ws_id_http, "draft_id": draft_id, "decision": "approved"},
        )
        pub_http = _mcp_http_tool(
            mcp_url,
            hdrs,
            req_id=11,
            name="macy_publish_draft",
            arguments={"workspace_id": ws_id_http, "draft_id": draft_id},
        )
        assert pub_http["status"] == "published"
        assert list(http_export_dir.rglob("*.md"))


def test_e2e_mode2b_mcp_stdio_external_compliance_flow(
    isolated_modes_e2e_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode 2B E2E: Stdio MCP external agent research ingestion and compliance import loop."""
    enc_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", isolated_modes_e2e_db)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", enc_key)

    stdio_export_dir = tmp_path / "exports_stdio"
    actor_stdio = "mcp-stdio@example.com"

    with psycopg.connect(isolated_modes_e2e_db) as conn:
        ws_stdio = _seed_mcp_workspace_with_markdown_publisher(
            conn,
            slug="seasonal-stdio-ws",
            owner_email=actor_stdio,
            export_dir=stdio_export_dir,
        )
        conn.commit()

    env_stdio = {k: v for k, v in os.environ.items() if k != "ENV"}
    env_stdio.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": isolated_modes_e2e_db,
            "APP_ENCRYPTION_KEY": enc_key,
            "MACY_USER_EMAIL": actor_stdio,
        }
    )
    stdio_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "engine.cli", "mcp", "--transport", "stdio"],
        cwd=str(ROOT_DIR),
        env=env_stdio,
    )
    out_stdio = asyncio.run(
        _run_stdio_compliance_session(
            stdio_params,
            ws_id=str(ws_stdio["workspace_id"]),
            body_md=_build_seasonal_mofu_markdown(),
        )
    )
    assert out_stdio["ingest"]["ok"] is True
    assert out_stdio["import"]["verdict"] == "PASS"
    assert out_stdio["import"]["draft_id"]
    assert out_stdio["publish"]["status"] == "published"
