"""Tests for M7-01 D4 Path B zero-Docker stateless CLI & MCP execution."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from engine import cli
from engine.mcp.server import create_mcp_server
from tests.test_mcp_audit import _mock_site_fetcher
from tests.test_mcp_research_gate1 import _call_mcp_tool


def _forbid_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Database connection MUST NOT be opened in stateless mode")

    monkeypatch.setattr("psycopg.connect", _fail)
    monkeypatch.setattr("engine.db.get_connection", _fail)


def test_stateless_cli_audit_and_crawl_without_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    """D4 Path B: macy audit [--crawl] --stateless runs with zero DB calls."""
    _forbid_db(monkeypatch)
    monkeypatch.setattr("engine.safe_http.safe_request", _mock_site_fetcher())

    assert cli.main(["audit", "https://example.com/", "--stateless", "--json"]) == 0
    single_out = json.loads(capsys.readouterr().out)
    assert single_out["url"] == "https://example.com/"
    assert single_out["audit_id"] is None
    assert single_out["overall_score"] >= 50.0

    assert (
        cli.main(
            [
                "audit",
                "https://example.com/",
                "--crawl",
                "--max-pages",
                "5",
                "--stateless",
                "--json",
            ]
        )
        == 0
    )
    crawl_out = json.loads(capsys.readouterr().out)
    assert crawl_out["pages_crawled"] == 2
    assert crawl_out["site_audit_id"] is None


def test_stateless_cli_import_draft_without_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    """D4 Path B: macy import-draft --stateless runs compliance + JSON-LD cleanly."""
    _forbid_db(monkeypatch)
    md_file = tmp_path / "draft.md"
    body_words = " ".join(["lentes", "oticas", "gestao", "atendimento", "qualidade"] * 90)
    md_file.write_text(
        "---\ntitle: Guia completo de lentes para oticas\n"
        "meta_description: Aprenda como escolher lentes e gerenciar sua otica hoje.\n"
        "slug: guia-lentes-oticas\n---\n\n"
        "# Guia completo de lentes para oticas\n\n"
        "lentes de qualidade melhoram o resultado da sua loja.\n\n"
        f"## Como escolher lentes\n\n{body_words}\n",
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "import-draft",
                "--file",
                str(md_file),
                "--format",
                "blog",
                "--primary-keyword",
                "lentes",
                "--stateless",
                "--json",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["stateless"] is True
    assert out["verdict"] in {"PASS", "REVISION", "REJECT"}
    assert out["schema_json_ld"]["@type"] == "Article"
    assert isinstance(out["findings"], list)

    # Human summary output (no --json) and missing input guard
    assert cli.main(["import-draft", "--file", str(md_file), "--stateless"]) == 0
    assert "stateless" in capsys.readouterr().out

    from engine import services

    with pytest.raises(ValueError, match="Either file_path or markdown_text is required"):
        services.check_draft_stateless()


def test_stateless_mcp_server_tools_without_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    """D4 Path B: macy mcp --stateless serves audit & compliance tools with 0 DB calls."""
    _forbid_db(monkeypatch)
    monkeypatch.setattr("engine.safe_http.safe_request", _mock_site_fetcher())

    assert cli.main(["mcp", "--stateless", "--check"]) == 0
    err_out = capsys.readouterr().err
    assert "mode=stateless" in err_out

    bundle = create_mcp_server(stateless=True, transport="stdio")
    tool_names = {t.name for t in bundle.mcp._tool_manager.list_tools()}
    assert tool_names == {
        "macy_audit_url",
        "macy_crawl_site",
        "macy_import_draft",
        "macy_adapt_draft",
        "macy_evaluate_draft",
    }

    ws_id = str(uuid.uuid4())
    audit_res = _call_mcp_tool(
        bundle,
        "macy_audit_url",
        {"workspace_id": ws_id, "url": "https://example.com/"},
    )
    assert audit_res["ok"] is True
    assert audit_res["audit_id"] is None

    crawl_res = _call_mcp_tool(
        bundle,
        "macy_crawl_site",
        {"workspace_id": ws_id, "start_url": "https://example.com/", "max_pages": 5},
    )
    assert crawl_res["ok"] is True
    assert crawl_res["pages_crawled"] == 2

    md_text = (
        "---\ntitle: Guia de SEO tecnico\n"
        "meta_description: Um guia pratico de SEO tecnico e GEO para blogs e sites modernos.\n"
        "slug: guia-seo-tecnico\n---\n\n"
        "# Guia de SEO tecnico\n\n"
        "SEO tecnico melhora indexacao. " + ("conteudo relevante " * 120)
    )
    import_res = _call_mcp_tool(
        bundle,
        "macy_import_draft",
        {
            "workspace_id": ws_id,
            "body_md": md_text,
            "format_slug": "blog-short",
            "primary_keyword": "SEO",
        },
    )
    assert import_res["ok"] is True
    assert import_res["stateless"] is True
    assert import_res["schema_json_ld"]["@type"] == "Article"

    eval_res = _call_mcp_tool(
        bundle,
        "macy_evaluate_draft",
        {
            "workspace_id": ws_id,
            "draft_id": "stateless",
            "body_md": md_text,
            "format_slug": "blog-short",
            "primary_keyword": "SEO",
        },
    )
    assert eval_res["ok"] is True
    assert eval_res["stateless"] is True

    missing_body_res = _call_mcp_tool(
        bundle,
        "macy_evaluate_draft",
        {"workspace_id": ws_id, "draft_id": "stateless"},
    )
    assert missing_body_res["error"] == "INVALID_STATE"
