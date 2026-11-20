"""Mode 3 E2E Tests: Headless CLI (`macy`) DB-backed Path A and stateless Path B flows."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from engine import services
from tests.e2e.editorial_helpers import (
    ROOT_DIR,
    SEASONAL_PRIMARY_KEYWORD,
    _build_seasonal_mofu_markdown,
    _call_stdio_tool,
)


async def _run_stateless_stdio_mcp(
    params: StdioServerParameters,
    *,
    body_md: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        imp = await _call_stdio_tool(
            session,
            "macy_import_draft",
            {
                "workspace_id": "00000000-0000-0000-0000-000000000000",
                "body_md": body_md,
                "format_slug": "blog",
                "primary_keyword": SEASONAL_PRIMARY_KEYWORD,
            },
        )
        ev = await _call_stdio_tool(
            session,
            "macy_evaluate_draft",
            {
                "workspace_id": "00000000-0000-0000-0000-000000000000",
                "draft_id": "stateless",
                "body_md": body_md,
                "format_slug": "blog",
                "primary_keyword": SEASONAL_PRIMARY_KEYWORD,
            },
        )
        return imp, ev


def _run_cli(*args: str, env_map: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "engine.cli", *args],
        cwd=str(ROOT_DIR),
        env=env_map,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def test_e2e_mode3a_cli_db_backed_editorial_flow(
    isolated_modes_e2e_db: str,
    tmp_path: Path,
) -> None:
    """Mode 3A E2E: DB-backed CLI workflow (Path A) from workspace creation to draft import."""
    md_text = _build_seasonal_mofu_markdown()

    enc_key = Fernet.generate_key().decode("ascii")
    env_db = {k: v for k, v in os.environ.items() if k != "ENV"}
    env_db.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": isolated_modes_e2e_db,
            "APP_ENCRYPTION_KEY": enc_key,
        }
    )

    slug = "seasonal-cli-ws"
    _run_cli(
        "create-workspace",
        slug,
        "--name",
        "Seasonal CLI WS",
        "--pack",
        "general",
        env_map=env_db,
    )
    _run_cli("add-owner", slug, "cli-mode3@example.com", env_map=env_db)
    _run_cli("seed-brand", slug, env_map=env_db)
    _run_cli("seed-sources", "--pack", "general", env_map=env_db)

    with psycopg.connect(isolated_modes_e2e_db) as conn:
        ws = services.resolve_workspace(conn, workspace_identifier=slug)
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        user_id = uuid.UUID(str(ws["owner_user_id"]))
        services.save_campaign(
            conn,
            workspace_id=ws_id,
            user_id=user_id,
            name="Campanha CLI Sazonal",
            locale="pt-BR",
            market="BR",
            articles_per_week=1,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("120.00"),
            run_cap_usd=Decimal("20.00"),
        )
        conn.commit()

    preset_out = _run_cli(
        "integrations",
        "preset",
        "apply",
        "pocket_free",
        "--workspace-id",
        str(ws_id),
        "--user-id",
        str(user_id),
        "--json",
        env_map=env_db,
    )
    preset_rows = json.loads(preset_out.stdout)
    assert isinstance(preset_rows, list)
    assert {row["port"] for row in preset_rows} >= {
        "news_search",
        "keyword_provider",
        "feed_source",
        "extractor",
        "publisher",
    }

    _run_cli("run-week", slug, "--offline", "--window-days", "7", env_map=env_db)
    md_file = tmp_path / "seasonal_mofu_post.md"
    md_file.write_text(md_text, encoding="utf-8")

    import_out = _run_cli(
        "import-draft",
        "--workspace",
        slug,
        "--file",
        str(md_file),
        "--format",
        "blog",
        "--topic",
        "Black Friday e fim de ano no varejo",
        "--primary-keyword",
        SEASONAL_PRIMARY_KEYWORD,
        "--deterministic-only",
        "--json",
        env_map=env_db,
    )
    import_payload = json.loads(import_out.stdout)
    assert import_payload["verdict"] == "PASS"
    assert import_payload["draft_id"]


def test_e2e_mode3b_cli_and_mcp_stateless_path_b_flow(tmp_path: Path) -> None:
    """Mode 3B E2E: Zero-DB stateless CLI + MCP stdio (Path B)."""
    md_text = _build_seasonal_mofu_markdown()
    env_stateless = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "ENV"}}
    env_stateless["APP_ENV"] = "test"

    md_file = tmp_path / "seasonal_mofu_post.md"
    md_file.write_text(md_text, encoding="utf-8")

    stateless_cli_out = _run_cli(
        "import-draft",
        "--file",
        str(md_file),
        "--format",
        "blog",
        "--primary-keyword",
        SEASONAL_PRIMARY_KEYWORD,
        "--stateless",
        "--json",
        env_map=env_stateless,
    )
    stateless_cli_json = json.loads(stateless_cli_out.stdout)
    assert stateless_cli_json["stateless"] is True
    assert stateless_cli_json["verdict"] == "PASS"
    assert stateless_cli_json["schema_json_ld"]["@type"] == "Article"

    stateless_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "engine.cli", "mcp", "--transport", "stdio", "--stateless"],
        cwd=str(ROOT_DIR),
        env=env_stateless,
    )
    imp_mcp, eval_mcp = asyncio.run(_run_stateless_stdio_mcp(stateless_params, body_md=md_text))
    assert imp_mcp["ok"] is True
    assert imp_mcp["stateless"] is True
    assert imp_mcp["verdict"] == "PASS"
    assert eval_mcp["ok"] is True
    assert eval_mcp["stateless"] is True
