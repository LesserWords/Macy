"""Red-to-Green tests for M11-03: CLI, MCP, Admin UI, and cross-workspace E2E adaptation."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

import app as macy_app
from engine import capabilities, cli, config, db, services
from engine.contracts import BrandProfile, ContentDraft, DraftClaim
from engine.mcp.auth import AdaptDraftRequest
from engine.mcp.mock_store import DEMO_WS_ID
from engine.mcp.server import create_mcp_server
from tests.test_mcp_research_gate1 import _call_mcp_tool


def _sample_markdown_with_source() -> str:
    doc_id = str(uuid.uuid4())
    pad = " ".join(
        [
            "Gestão fiscal integrada com PDV e estoque reduz falhas no fechamento de caixa "
            "para operações comerciais e distribuidoras com emissão regular de documentos."
        ]
        * 46
    )
    draft = ContentDraft(
        title="Gestão fiscal e PDV integrado no varejo em expansão",
        meta_description=(
            "Como alinhar emissão fiscal, PDV e controle financeiro em operações de varejo "
            "sem retrabalho no fechamento."
        ),
        slug="gestao-fiscal-pdv-integrado-varejo-expansao",
        body_md=(
            "# Gestão fiscal e PDV integrado no varejo em expansão\n\n"
            "No Core ERP, a emissão de NF-e e NFC-e conversa com o controle financeiro.\n\n"
            f"{pad}"
        ),
        faq=[
            {
                "question": "Qual o ganho do PDV integrado ao financeiro?",
                "answer": "Elimina redigitação no fechamento diário e concilia vendas TEF e PIX.",
            },
            {
                "question": "Como validar CFOP e NCM antes da emissão?",
                "answer": "Regras fiscais centralizadas conferem cadastros automaticamente.",
            },
        ],
        cta={
            "headline": "Solicite demonstração do Core ERP",
            "body": "Conheça o fluxo completo de caixa e DRE do Core ERP.",
            "button_text": "Ver Core ERP",
            "intensity": "medium",
        },
        claims=[
            DraftClaim(
                text="Integrar caixa e fiscal reduz redigitação operacional.",
                kind="fact",
                source_id=doc_id,
            )
        ],
    )
    return services.render_draft_markdown(
        draft,
        source_url_by_doc_id={doc_id: "https://example.org/fiscal-guide"},
    )


def _write_vertical_fixture(tmp_path: Path) -> Path:
    brand = BrandProfile(
        brand_name="Core Specialty ERP",
        target_persona="Donos de lojas especializadas e laboratórios técnicos",
        canonical_domain="vertical.example.com",
        voice="Consultivo e objetivo para operações especializadas",
        positioning="Core Specialty ERP une PDV fiscal e Ordens de Serviço técnicas.",
        capabilities=["Frente de caixa PDV com TEF e PIX", "Ordens de Serviço integradas"],
        prohibited_claims=["Prometer ROI garantido"],
        prohibited_phrases=[r"\bCore ERP\b"],
        cta_rules=["MOFU: apresentar checklist técnico do Core Specialty ERP"],
    )
    fixture = tmp_path / "seed_vertical.yaml"
    fixture.write_text(
        yaml.safe_dump(brand.model_dump(mode="json"), allow_unicode=True),
        encoding="utf-8",
    )
    pack_dir = tmp_path / "packs" / "industries" / "specialty-retail"
    pack_dir.mkdir(parents=True)
    (pack_dir / "profile.yaml").write_text(
        "name: Specialty\nslug: specialty-retail\nmarket: BR\n"
        "locales:\n  - pt-BR\ndescription: Ops\n",
        encoding="utf-8",
    )
    return fixture


def test_capability_registry_exposes_adapt_draft_surface() -> None:
    """CoreComplianceCapability advertises adapt-draft CLI and macy_adapt_draft MCP."""
    core = capabilities.get_capability("core-compliance")
    assert "adapt-draft" in core.cli_commands
    assert "macy_adapt_draft" in core.mcp_tools


def test_cli_adapt_draft_stateless_and_db_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    """CLI macy adapt-draft works in --stateless (with --output) and DB workspace mode."""
    fixture = _write_vertical_fixture(tmp_path)
    monkeypatch.setattr(
        config,
        "get_packs_roots",
        lambda explicit_base=None: (
            [tmp_path / "packs"] if explicit_base is None else [Path(explicit_base)]
        ),
    )
    src_file = tmp_path / "source.md"
    src_file.write_text(_sample_markdown_with_source(), encoding="utf-8")
    out_file = tmp_path / "out" / "adapted.md"

    code_stateless = cli.main(
        [
            "adapt-draft",
            "--file",
            str(src_file),
            "--stateless",
            "--brand-fixture",
            str(fixture),
            "--industry-pack",
            "specialty-retail",
            "--offline",
            "--deterministic-only",
            "--output",
            str(out_file),
            "--json",
        ]
    )
    assert code_stateless == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stateless"] is True
    assert payload["verdict"] == "PASS"
    assert out_file.exists()
    assert "Core Specialty ERP" in out_file.read_text(encoding="utf-8")

    # Non-stateless DB workspace mode dispatches to services.adapt_draft_for_workspace
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    monkeypatch.setattr(cli, "_resolve_workspace_and_owner", lambda _ident: (ws_id, owner_id))
    called: dict[str, Any] = {}

    def _fake_adapt_ws(**kwargs: Any) -> dict[str, Any]:
        called.update(kwargs)
        return {
            "draft_id": str(uuid.uuid4()),
            "title": "Adapted DB Title",
            "slug": "adapted-db-title",
            "verdict": "PASS",
            "uniqueness": {"passed": True, "similarity": 0.12},
            "adapted_markdown": "# Adapted DB Title\n\nBody",
        }

    monkeypatch.setattr(services, "adapt_draft_for_workspace", _fake_adapt_ws)
    code_db = cli.main(
        [
            "adapt-draft",
            "--file",
            str(src_file),
            "--workspace",
            "target-ws",
            "--offline",
            "--deterministic-only",
        ]
    )
    assert code_db == 0
    assert "Adapted draft" in capsys.readouterr().out
    assert called["workspace_id"] == ws_id

    # Missing file/markdown returns non-zero cleanly
    code_err = cli.main(["adapt-draft", "--stateless"])
    assert code_err == 1
    assert "Error:" in capsys.readouterr().err


def test_mcp_adapt_draft_across_stateless_mock_and_service_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_adapt_draft works across stateless, --mock, streamable-http guard, and DB service."""
    monkeypatch.setenv("APP_ENV", "development")
    fixture = _write_vertical_fixture(tmp_path)
    monkeypatch.setattr(
        config,
        "get_packs_roots",
        lambda explicit_base=None: (
            [tmp_path / "packs"] if explicit_base is None else [Path(explicit_base)]
        ),
    )
    src_md = _sample_markdown_with_source()

    # 1) Stateless MCP mode
    stateless_bundle = create_mcp_server(stateless=True, transport="stdio")
    res_stateless = _call_mcp_tool(
        stateless_bundle,
        "macy_adapt_draft",
        {
            "workspace_id": str(uuid.uuid4()),
            "body_md": src_md,
            "brand_fixture": str(fixture),
            "industry_pack": "specialty-retail",
            "offline": True,
            "deterministic_only": True,
        },
    )
    assert res_stateless["ok"] is True
    assert res_stateless["verdict"] == "PASS"
    assert res_stateless["uniqueness"]["passed"] is True

    # 2) MockStore mode
    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    res_mock = _call_mcp_tool(
        mock_bundle,
        "macy_adapt_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "body_md": src_md,
            "offline": True,
            "deterministic_only": True,
            "actor_email": "owner@example.com",
        },
    )
    assert res_mock["ok"] is True
    assert res_mock["status"] == "pass"
    assert res_mock["uniqueness"]["passed"] is True

    # 3) streamable-http blocks file_path just like macy_import_draft
    http_bundle = create_mcp_server(
        mock=True,
        transport="streamable-http",
        env={
            "APP_ENV": "development",
            "MCP_AUTH_TOKEN": "t" * 32,
            "MCP_ACTOR_EMAIL": "owner@example.com",
        },
    )
    res_http_err = _call_mcp_tool(
        http_bundle,
        "macy_adapt_draft",
        {"workspace_id": str(DEMO_WS_ID), "file_path": str(tmp_path / "draft.md")},
    )
    assert "error" in res_http_err
    assert "streamable-http" in res_http_err["message"]

    # 4) ServiceStoreAdapter delegates to services.adapt_draft_for_workspace
    from engine.mcp.server import ServiceStoreAdapter

    called: dict[str, Any] = {}
    monkeypatch.setattr(
        services,
        "adapt_draft_for_workspace",
        lambda **kwargs: called.update(kwargs) or {"verdict": "PASS"},
    )
    adapter = ServiceStoreAdapter()
    req = AdaptDraftRequest(
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        body_md=src_md,
        offline=True,
        deterministic_only=True,
    )
    assert adapter.adapt_draft(req)["verdict"] == "PASS"
    assert called["workspace_id"] == req.workspace_id


def test_admin_ui_adapt_draft_to_workspace_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin UI helper exports review bundle by value and calls adapt_draft_for_workspace."""
    target_ws = uuid.uuid4()
    actor_id = uuid.uuid4()
    doc_id = str(uuid.uuid4())
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        services,
        "adapt_draft_for_workspace",
        lambda **kwargs: captured.update(kwargs) or {"draft_id": uuid.uuid4(), "verdict": "PASS"},
    )
    bundle = {
        "draft": {
            "id": uuid.uuid4(),
            "payload": {
                "title": "Post original ERP",
                "meta_description": (
                    "Descrição meta de exemplo com mais de sessenta caracteres para validação."
                ),
                "slug": "post-original-erp",
                "body_md": "# Post original ERP\n\nConteúdo sobre Core ERP.",
                "faq": [],
                "cta": {
                    "headline": "Teste Core ERP",
                    "body": "Veja na prática.",
                    "button_text": "Testar",
                    "intensity": "medium",
                },
                "claims": [
                    {"text": "Fato ancorado.", "kind": "fact", "source_id": doc_id},
                ],
            },
        },
        "claims": [
            {
                "text": "Fato ancorado.",
                "kind": "fact",
                "document_id": doc_id,
                "document_url": "https://example.org/benchmark",
            }
        ],
    }
    res = macy_app._adapt_draft_to_workspace(
        bundle,
        target_workspace_id=target_ws,
        user_id=actor_id,
        offline=True,
        deterministic_only=True,
    )
    assert res["verdict"] == "PASS"
    assert captured["workspace_id"] == target_ws
    assert captured["user_id"] == actor_id
    assert "https://example.org/benchmark" in str(captured["markdown_text"])


def test_m11_03_cross_workspace_adaptation_e2e_db(db_conn: psycopg.Connection[Any]) -> None:
    """E2E DB flow: draft adapted by value into sibling WS and approved at Gate 2."""
    core_slug = f"core-{uuid.uuid4().hex[:6]}"
    vert_slug = f"vert-{uuid.uuid4().hex[:6]}"
    ws_core = db.create_workspace(
        db_conn, slug=core_slug, name="Core Blog", industry_pack="general"
    )
    ws_vert = db.create_workspace(
        db_conn, slug=vert_slug, name="Vertical Blog", industry_pack="general"
    )
    owner_res = services.add_workspace_owner(
        db_conn, workspace_slug=vert_slug, email=f"e2e-{uuid.uuid4().hex[:6]}@example.com"
    )
    owner_id = uuid.UUID(str(owner_res["user"]["user_id"]))
    services.save_brand(
        db_conn,
        workspace_id=ws_vert,
        user_id=owner_id,
        profile=BrandProfile(
            brand_name="Core Specialty ERP",
            target_persona="Gestores de clínicas e lojas técnicas",
            canonical_domain="vertical.example.com",
            voice="Consultivo e preciso",
            positioning="Core Specialty ERP organiza O.S. e frente de caixa fiscal.",
            capabilities=["Controle de O.S.", "PDV fiscal NF-e"],
            prohibited_claims=["Garantir dobro de receita"],
            prohibited_phrases=[r"\bCore ERP\b"],
            cta_rules=["MOFU: demonstrar módulo técnico do Core Specialty ERP"],
        ),
    )
    services.save_campaign(
        db_conn,
        workspace_id=ws_vert,
        user_id=owner_id,
        name="Vertical Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 8 * * 1",
        monthly_cap_usd="50.00",
        run_cap_usd="10.00",
    )
    adapted = services.adapt_draft_for_workspace(
        db_conn,
        workspace_id=ws_vert,
        user_id=owner_id,
        markdown_text=_sample_markdown_with_source(),
        sources=[
            {
                "title": "Fiscal Benchmark",
                "url": "https://example.org/fiscal-guide",
                "summary": "Integrar caixa e fiscal reduz redigitação operacional.",
                "facts": ["Integrar caixa e fiscal reduz redigitação operacional."],
            }
        ],
        offline=True,
        deterministic_only=True,
    )
    assert adapted["verdict"] == "PASS"
    assert adapted["uniqueness"]["passed"] is True
    target_draft_id = uuid.UUID(str(adapted["draft_id"]))

    # Zero leakage into source workspace
    assert db.get_draft(db_conn, workspace_id=ws_core, draft_id=target_draft_id) is None

    decided = services.decide_draft(
        db_conn,
        workspace_id=ws_vert,
        draft_id=target_draft_id,
        decision="approved",
        user_id=owner_id,
    )
    assert decided["status"] == "approved"
