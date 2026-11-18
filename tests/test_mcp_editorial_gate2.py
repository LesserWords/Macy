"""Tests for M5-05: MCP briefing, drafting, runtime claims, evaluation, Gate 2 & resources."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from mcp.shared.exceptions import MCPError as SDKMCPError
from starlette.testclient import TestClient

from engine import db, services
from engine.mcp.errors import MCPErrorCode
from engine.mcp.mock_store import DEMO_WS_ID, LUMINA_WS_ID, MockStore
from engine.mcp.server import MacyMCPBundle, create_mcp_server
from tests.test_mcp_auth import _seed_workspace_fixture
from tests.test_mcp_research_gate1 import (
    _add_ws_user,
    _call_mcp_tool,
    _parse_sse_jsonrpc,
    _seed_fake_workspace_integrations,
)


def _seed_full_brand_and_doc(
    conn: psycopg.Connection[Any],
    *,
    ws: dict[str, Any],
    tag: str,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    ws_id: uuid.UUID = ws["workspace_id"]
    owner_id: uuid.UUID = ws["owner_id"]
    db.save_brand_profile(
        conn,
        workspace_id=ws_id,
        schema_version=1,
        payload={
            "voice": "Consultivo, tecnico e baseado em evidencias para gestores de oticas.",
            "positioning": "Plataforma especializada em gestao de oticas independentes e redes.",
            "capabilities": ["Controle de estoque por grau", "CRM pos-venda optico"],
            "prohibited_claims": ["Promessa de lucro garantido"],
            "prohibited_phrases": [r"\blucro garantido\b"],
            "cta_rules": ["TOFU educativo", "MOFU diagnostico", "BOFU demonstracao guiada"],
        },
        created_by=owner_id,
    )
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=ws["campaign_id"],
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("15.00"),
        code_version="v1",
        pack_version="p1",
        created_by=owner_id,
    )
    doc = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=None,
        url=f"https://evidence-{tag}.example.com/2026/11/01/estoque",
        canonical_url=f"https://evidence-{tag}.example.com/2026/11/01/estoque",
        title="Gestao de Estoque Optico em 2026",
        published_at=now - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
        extracted_text=(
            "Redes regionais de oticas reduziram a ruptura de lentes multifocais em 18% "
            "no terceiro trimestre de 2026 ao padronizar a reposicao semanal."
        ),
        meta={
            "source_name": "Abióptica",
            "source_role": "data",
            "summary": "Reposicao semanal reduziu ruptura de multifocais em 18% em 2026.",
            "facts": ["Redes regionais reduziram a ruptura de lentes multifocais em 18% em 2026."],
        },
    )
    rr = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": "Gestao de Estoque Optico em 2026",
            "summary": "Reposicao semanal reduziu ruptura em 18% em 2026.",
            "facts": ["Redes regionais reduziram a ruptura de lentes multifocais em 18% em 2026."],
        },
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores={"novelty": {"score": 8.5, "evidence": "Abióptica", "is_estimate": False}},
        total=Decimal("86.00"),
        funnel_stage="mofu",
        confidence="high",
        angle="Reposicao semanal orientada por giro no varejo optico",
        rank=1,
        status="approved",
        decided_by=owner_id,
        decided_at=now,
    )
    return {"run_id": run_id, "document": doc, "opportunity": opp}


def test_service_verify_claims_persisted_draft_without_evals(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """services.verify_claims verifies persisted draft_claims and documents without evals."""
    ws = _seed_workspace_fixture(
        db_conn,
        tag="claims-draft",
        owner_email=f"claims-{uuid.uuid4().hex[:6]}@example.com",
        with_campaign=True,
    )
    monkeypatch.setitem(sys.modules, "evals", None)
    monkeypatch.setitem(sys.modules, "httpx2", None)
    seeded = _seed_full_brand_and_doc(db_conn, ws=ws, tag="cd1")
    brief = services.generate_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )

    verified = services.verify_claims(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=ws["owner_id"],
        draft_id=draft["id"],
        offline=True,
    )
    assert verified["draft_id"] == draft["id"]
    assert verified["verified_count"] >= 1
    assert verified["supported_count"] >= 1
    assert verified["results"][0]["verdict"] == "supported"

    mcp_brief = services.get_or_create_mcp_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    assert mcp_brief["id"] == brief["id"]
    mcp_draft = services.generate_mcp_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    assert services.get_draft_workspace_id(db_conn, draft_id=mcp_draft["id"]) == ws["workspace_id"]
    mcp_eval = services.evaluate_mcp_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        draft_id=mcp_draft["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    assert mcp_eval["verdict"] in {"PASS", "REVISION", "FAIL"}
    weekly_rpt = services.get_weekly_report_resource(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=ws["owner_id"],
    )
    assert "awaiting_drafts_count" in weekly_rpt

    payload_only_draft = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        prompt_version="v1",
        schema_version=1,
        payload={"claims": [{"text": "Multifocal stockout dropped 18%."}]},
        claims=[],
    )
    payload_verified = services.verify_claims(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=ws["owner_id"],
        draft_id=payload_only_draft["id"],
        offline=True,
    )
    assert payload_verified["verified_count"] == 1

    brief_v2 = services.get_or_create_mcp_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        force_regenerate=True,
        offline=True,
    )
    empty_draft = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief_v2["id"],
        prompt_version="v1",
        schema_version=1,
        payload={},
        claims=[],
    )
    with pytest.raises(ValueError, match="no factual claims"):
        services.verify_claims(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=ws["owner_id"],
            draft_id=empty_draft["id"],
            offline=True,
        )
    with pytest.raises(ValueError, match="required"):
        services.verify_claims(db_conn, workspace_id=ws["workspace_id"], user_id=ws["owner_id"])
    with pytest.raises(ValueError, match="not found"):
        services.verify_claims(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=ws["owner_id"],
            draft_id=uuid.uuid4(),
        )
    with pytest.raises(ValueError, match="not found"):
        services.generate_mcp_draft(
            db_conn,
            workspace_id=ws["workspace_id"],
            brief_id=uuid.uuid4(),
            user_id=ws["owner_id"],
            offline=True,
        )
    with pytest.raises(ValueError, match="not found"):
        services.evaluate_mcp_draft(
            db_conn,
            workspace_id=ws["workspace_id"],
            draft_id=uuid.uuid4(),
            user_id=ws["owner_id"],
            offline=True,
        )


def _seed_keyword_integration(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    db.upsert_workspace_integration(
        conn,
        workspace_id=workspace_id,
        port="keyword_provider",
        plugin="fake",
        settings={},
        secrets=None,
        updated_by=user_id,
    )


def _seed_publisher_integration(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    db.upsert_workspace_integration(
        conn,
        workspace_id=workspace_id,
        port="publisher",
        plugin="fake",
        settings={"platform_name": "fake-cms"},
        secrets=None,
        updated_by=user_id,
    )


def _init_http_session(client: TestClient, hdrs: dict[str, str]) -> None:
    init_resp = client.post(
        "/mcp",
        headers=hdrs,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "g2-http-client", "version": "1.0"},
            },
        },
    )
    assert init_resp.status_code == 200
    hdrs["mcp-session-id"] = init_resp.headers["mcp-session-id"]
    client.post(
        "/mcp",
        headers=hdrs,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )


def _post_jsonrpc(
    client: TestClient,
    hdrs: dict[str, str],
    *,
    req_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    resp = client.post(
        "/mcp",
        headers=hdrs,
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    assert resp.status_code == 200
    return _parse_sse_jsonrpc(resp.text)


def _assert_mock_editorial_and_claims(mock_bundle: MacyMCPBundle) -> None:
    mock_opp_id = "01900000-0000-7000-8000-000000000605"
    mock_brief = _call_mcp_tool(
        mock_bundle,
        "macy_get_editorial_brief",
        {
            "workspace_id": str(DEMO_WS_ID),
            "opportunity_id": mock_opp_id,
            "actor_email": "owner@example.com",
        },
    )
    assert mock_brief["ok"] is True
    assert mock_brief["primary_keyword"]
    assert mock_brief["outline"]
    assert mock_brief["cited_documents"]

    mock_draft = _call_mcp_tool(
        mock_bundle,
        "macy_generate_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "brief_id": mock_brief["id"],
            "actor_email": "owner@example.com",
        },
    )
    assert mock_draft["ok"] is True
    assert mock_draft["claims"]
    assert any(c.get("document_id") for c in mock_draft["claims"])

    mock_verified = _call_mcp_tool(
        mock_bundle,
        "macy_verify_claims",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": mock_draft["id"],
            "actor_email": "owner@example.com",
        },
    )
    assert mock_verified["ok"] is True
    assert mock_verified["verified_count"] >= 1
    assert mock_verified["supported_count"] >= 1
    assert mock_verified["results"][0]["verdict"] == "supported"

    missing_claims_err = _call_mcp_tool(
        mock_bundle,
        "macy_verify_claims",
        {
            "workspace_id": str(DEMO_WS_ID),
            "actor_email": "owner@example.com",
        },
    )
    assert missing_claims_err["error"] == MCPErrorCode.INVALID_STATE.value

    mock_regen = _call_mcp_tool(
        mock_bundle,
        "macy_get_editorial_brief",
        {
            "workspace_id": str(DEMO_WS_ID),
            "opportunity_id": mock_opp_id,
            "force_regenerate": True,
            "actor_email": "owner@example.com",
        },
    )
    assert mock_regen["ok"] is True
    assert mock_regen["created"] is True
    assert mock_regen["id"] != mock_brief["id"]

    mock_direct = _call_mcp_tool(
        mock_bundle,
        "macy_verify_claims",
        {
            "workspace_id": str(DEMO_WS_ID),
            "claims": ["Multifocal stockout dropped 18%."],
            "passages": ["Multifocal stockout dropped 18% across regional optical chains."],
            "actor_email": "owner@example.com",
        },
    )
    assert mock_direct["ok"] is True
    assert mock_direct["supported_count"] == 1

    for tool_name, payload_args in (
        ("macy_get_editorial_brief", {"opportunity_id": str(uuid.uuid4())}),
        ("macy_generate_draft", {"brief_id": str(uuid.uuid4())}),
        ("macy_evaluate_draft", {"draft_id": str(uuid.uuid4())}),
    ):
        err = _call_mcp_tool(
            mock_bundle,
            tool_name,
            {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com", **payload_args},
        )
        assert err["error"] == MCPErrorCode.NOT_FOUND.value


def test_brief_draft_and_runtime_claim_verification_without_evals(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brief, FK-backed draft_claims, and runtime claim verification run with evals blocked."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setitem(sys.modules, "evals", None)
    monkeypatch.setitem(sys.modules, "httpx2", None)

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    _assert_mock_editorial_and_claims(mock_bundle)

    owner_email = f"mcp-ed-{uuid.uuid4().hex[:6]}@example.com"
    outsider_email = f"mcp-ed-out-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="ed1", owner_email=owner_email, with_campaign=True)
    _add_ws_user(db_conn, workspace_id=None, email=outsider_email)
    _seed_fake_workspace_integrations(
        db_conn, workspace_id=ws["workspace_id"], user_id=ws["owner_id"]
    )
    _seed_keyword_integration(db_conn, workspace_id=ws["workspace_id"], user_id=ws["owner_id"])
    seeded = _seed_full_brand_and_doc(db_conn, ws=ws, tag=uuid.uuid4().hex[:6])

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    brief_res = _call_mcp_tool(
        live_bundle,
        "macy_get_editorial_brief",
        {
            "workspace_id": str(ws["workspace_id"]),
            "opportunity_id": str(seeded["opportunity"]["id"]),
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert brief_res["ok"] is True
    assert brief_res["created"] is True
    cited_ids = {d["id"] for d in brief_res["cited_documents"]}
    assert cited_ids
    for cited_id in cited_ids:
        assert (
            db.get_document(
                db_conn, workspace_id=ws["workspace_id"], document_id=uuid.UUID(cited_id)
            )
            is not None
        )

    cached_brief = _call_mcp_tool(
        live_bundle,
        "macy_get_editorial_brief",
        {
            "workspace_id": str(ws["workspace_id"]),
            "opportunity_id": str(seeded["opportunity"]["id"]),
            "actor_email": owner_email,
        },
    )
    assert cached_brief["created"] is False
    assert cached_brief["id"] == brief_res["id"]

    draft_res = _call_mcp_tool(
        live_bundle,
        "macy_generate_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "brief_id": brief_res["id"],
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert draft_res["ok"] is True
    assert draft_res["payload"]["body_md"]
    persisted_claims = db.list_draft_claims(
        db_conn, workspace_id=ws["workspace_id"], draft_id=uuid.UUID(draft_res["id"])
    )
    assert persisted_claims
    assert any(
        c["document_id"] is not None and str(c["document_id"]) in cited_ids
        for c in persisted_claims
    )

    claims_res = _call_mcp_tool(
        live_bundle,
        "macy_verify_claims",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": draft_res["id"],
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert claims_res["ok"] is True
    assert claims_res["supported_count"] >= 1
    assert any(r["verdict"] == "supported" and r["evidence_quote"] for r in claims_res["results"])

    direct_claims = _call_mcp_tool(
        live_bundle,
        "macy_verify_claims",
        {
            "workspace_id": str(ws["workspace_id"]),
            "claims": ["Multifocal stockout dropped 18%.", "Unproven 99% jump occurred."],
            "passages": ["Regional optical chains cut multifocal stockout by 18% in 2026."],
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert direct_claims["ok"] is True
    assert [r["verdict"] for r in direct_claims["results"]] == ["supported", "unverifiable"]

    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_get_editorial_brief",
            {
                "workspace_id": str(ws["workspace_id"]),
                "opportunity_id": str(uuid.uuid4()),
                "actor_email": owner_email,
            },
        )["error"]
        == MCPErrorCode.NOT_FOUND.value
    )
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_generate_draft",
            {
                "workspace_id": str(ws["workspace_id"]),
                "brief_id": str(brief_res["id"]),
                "actor_email": outsider_email,
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )
    monkeypatch.setenv("APP_ENV", "production")
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_verify_claims",
            {
                "workspace_id": str(ws["workspace_id"]),
                "draft_id": draft_res["id"],
                "offline": True,
                "actor_email": owner_email,
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )


def test_evaluate_and_list_drafts_awaiting_approval(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_evaluate_draft runs deterministic+rubric checks and lists pass queue."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_brief = _call_mcp_tool(
        mock_bundle,
        "macy_get_editorial_brief",
        {
            "workspace_id": str(DEMO_WS_ID),
            "opportunity_id": "01900000-0000-7000-8000-000000000605",
            "actor_email": "owner@example.com",
        },
    )
    mock_draft = _call_mcp_tool(
        mock_bundle,
        "macy_generate_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "brief_id": mock_brief["id"],
            "actor_email": "owner@example.com",
        },
    )
    eval_mock = _call_mcp_tool(
        mock_bundle,
        "macy_evaluate_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": mock_draft["id"],
            "actor_email": "owner@example.com",
        },
    )
    assert eval_mock["ok"] is True
    assert eval_mock["verdict"] == "PASS"
    assert eval_mock["status"] == "pass"

    queue_mock = _call_mcp_tool(
        mock_bundle,
        "macy_list_drafts_awaiting_approval",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    assert queue_mock["ok"] is True
    assert all(d["status"] == "pass" for d in queue_mock["drafts"])
    assert any(d["id"] == mock_draft["id"] for d in queue_mock["drafts"])

    assert isinstance(mock_bundle.store, MockStore)
    ws_record = mock_bundle.store._workspaces[DEMO_WS_ID]
    ws_record["monthly_spend_usd"] = Decimal("120.000000")
    cap_err = _call_mcp_tool(
        mock_bundle,
        "macy_generate_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "brief_id": mock_brief["id"],
            "actor_email": "owner@example.com",
        },
    )
    assert cap_err["error"] == MCPErrorCode.BUDGET_CAP_EXCEEDED.value

    # Part B: Real Postgres evaluation + revision 2 surfaced to owner + run spend cap breach
    owner_email = f"mcp-eval-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="ev1", owner_email=owner_email, with_campaign=True)
    _seed_fake_workspace_integrations(
        db_conn, workspace_id=ws["workspace_id"], user_id=ws["owner_id"]
    )
    _seed_keyword_integration(db_conn, workspace_id=ws["workspace_id"], user_id=ws["owner_id"])
    seeded = _seed_full_brand_and_doc(db_conn, ws=ws, tag=uuid.uuid4().hex[:6])

    brief = services.generate_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft_v0 = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    live_bundle = create_mcp_server(mock=False, transport="stdio")
    eval_live = _call_mcp_tool(
        live_bundle,
        "macy_evaluate_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": str(draft_v0["id"]),
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert eval_live["ok"] is True
    assert eval_live["verdict"] == "PASS"
    assert eval_live["status"] == "pass"
    assert float(eval_live["score"]) >= 70.0

    # Also test revision=2 surfaced_after_max_revisions in Gate 2 queue
    rev2_payload = {
        **draft_v0["payload"],
        "title": "Revisao Max: lucro garantido no varejo optico",
        "body_md": (
            f"# Revisao Max: lucro garantido\n\n{draft_v0['payload']['body_md']}\n\nlucro garantido"
        ),
    }
    draft_rev2 = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        prompt_version="writer:v1",
        schema_version=1,
        payload=rev2_payload,
        claims=draft_v0["payload"].get("claims") or [],
        status="draft",
        revision=2,
    )
    eval_rev2 = _call_mcp_tool(
        live_bundle,
        "macy_evaluate_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": str(draft_rev2["id"]),
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert eval_rev2["ok"] is True
    assert eval_rev2["status"] == "pass"
    assert eval_rev2["surfaced_to_owner"] is True

    queue_live = _call_mcp_tool(
        live_bundle,
        "macy_list_drafts_awaiting_approval",
        {"workspace_id": str(ws["workspace_id"]), "actor_email": owner_email},
    )
    by_id = {d["id"]: d for d in queue_live["drafts"]}
    assert str(draft_v0["id"]) in by_id
    assert by_id[str(draft_rev2["id"])]["surfaced_after_max_revisions"] is True

    # Run spend cap breach aborts macy_evaluate_draft cleanly with BUDGET_CAP_EXCEEDED
    db.record_usage(
        db_conn,
        workspace_id=ws["workspace_id"],
        run_id=seeded["run_id"],
        stage="write",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("16.000000"),
        model="fake",
    )
    draft_capped = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        prompt_version="writer:v1",
        schema_version=1,
        payload=draft_v0["payload"],
        claims=[],
        status="draft",
        revision=1,
    )
    cap_eval = _call_mcp_tool(
        live_bundle,
        "macy_evaluate_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": str(draft_capped["id"]),
            "offline": True,
            "actor_email": owner_email,
        },
    )
    assert cap_eval["error"] == MCPErrorCode.BUDGET_CAP_EXCEEDED.value


def _assert_gate2_common_guards(  # noqa: PLR0913
    *,
    bundle: MacyMCPBundle,
    ws_id: str,
    draft_id: str,
    creator_email: str,
    coowner_email: str,
    member_email: str,
) -> None:
    assert (
        _call_mcp_tool(
            bundle,
            "macy_decide_draft",
            {
                "workspace_id": ws_id,
                "draft_id": draft_id,
                "decision": "approved",
                "actor_email": coowner_email,
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )
    assert (
        _call_mcp_tool(
            bundle,
            "macy_decide_draft",
            {
                "workspace_id": ws_id,
                "draft_id": draft_id,
                "decision": "approved",
                "actor_email": member_email,
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )
    for bad_val in ("accepted", "rejected", "maybe"):
        assert (
            _call_mcp_tool(
                bundle,
                "macy_decide_draft",
                {
                    "workspace_id": ws_id,
                    "draft_id": draft_id,
                    "decision": bad_val,
                    "actor_email": creator_email,
                },
            )["error"]
            == MCPErrorCode.INVALID_STATE.value
        )


def test_decide_draft_gate2_normalized_values_and_creator_check(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 2 accepts approved/declined only, enforces creator ownership, and emits audit events."""
    monkeypatch.setenv("APP_ENV", "development")

    # Part A: MockStore parity (approve & decline)
    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_queue = _call_mcp_tool(
        mock_bundle,
        "macy_list_drafts_awaiting_approval",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    mock_pass_id = mock_queue["drafts"][0]["id"]
    _assert_gate2_common_guards(
        bundle=mock_bundle,
        ws_id=str(DEMO_WS_ID),
        draft_id=mock_pass_id,
        creator_email="owner@example.com",
        coowner_email="coowner@example.com",
        member_email="editor@example.com",
    )
    mock_dec = _call_mcp_tool(
        mock_bundle,
        "macy_decide_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": mock_pass_id,
            "decision": "declined",
            "note": "Needs extra store quotes",
            "actor_email": "owner@example.com",
        },
    )
    assert mock_dec["ok"] is True
    assert mock_dec["status"] == "declined"
    assert (
        _call_mcp_tool(
            mock_bundle,
            "macy_decide_draft",
            {
                "workspace_id": str(DEMO_WS_ID),
                "draft_id": mock_pass_id,
                "decision": "approved",
                "actor_email": "owner@example.com",
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )

    # Part B: Real Postgres + Streamable HTTP JSON-RPC
    creator_email = f"mcp-g2-cr-{uuid.uuid4().hex[:6]}@example.com"
    coowner_email = f"mcp-g2-co-{uuid.uuid4().hex[:6]}@example.com"
    outsider_email = f"mcp-g2-out-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="g2", owner_email=creator_email, with_campaign=True)
    _add_ws_user(db_conn, workspace_id=ws["workspace_id"], email=coowner_email)
    _add_ws_user(db_conn, workspace_id=None, email=outsider_email)
    seeded = _seed_full_brand_and_doc(db_conn, ws=ws, tag=uuid.uuid4().hex[:6])

    brief = services.generate_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft_decline = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft_approve = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    for d in (draft_decline, draft_approve):
        db.transition_draft_status(
            db_conn,
            workspace_id=ws["workspace_id"],
            draft_id=d["id"],
            expected_statuses=("draft",),
            new_status="pass",
        )

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    _assert_gate2_common_guards(
        bundle=live_bundle,
        ws_id=str(ws["workspace_id"]),
        draft_id=str(draft_decline["id"]),
        creator_email=creator_email,
        coowner_email=coowner_email,
        member_email=outsider_email,
    )
    dec_declined = _call_mcp_tool(
        live_bundle,
        "macy_decide_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": str(draft_decline["id"]),
            "decision": "declined",
            "note": "Adjust angle",
            "actor_email": creator_email,
        },
    )
    assert dec_declined["ok"] is True
    assert dec_declined["status"] == "declined"

    dec_approved = _call_mcp_tool(
        live_bundle,
        "macy_decide_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": str(draft_approve["id"]),
            "decision": "approved",
            "note": "Ready to ship",
            "actor_email": creator_email,
        },
    )
    assert dec_approved["ok"] is True
    assert dec_approved["status"] == "approved"

    events_rows = db.list_events(
        db_conn,
        workspace_id=ws["workspace_id"],
        event_type="draft.decided",
        entity_id=draft_approve["id"],
    )
    assert len(events_rows) == 1
    assert events_rows[0]["actor_user_id"] == ws["owner_id"]
    assert events_rows[0]["payload"]["decision"] == "approved"

    # Re-deciding an already decided draft returns INVALID_STATE
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_decide_draft",
            {
                "workspace_id": str(ws["workspace_id"]),
                "draft_id": str(draft_approve["id"]),
                "decision": "declined",
                "actor_email": creator_email,
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )

    # HTTP JSON-RPC tools/list exposes all 13 tools and rejects spoofed actor_email
    http_env: Mapping[str, str] = {
        "APP_ENV": "development",
        "MCP_AUTH_TOKEN": "g2-http-secret-token-value",
        "MCP_ACTOR_EMAIL": creator_email,
    }
    http_bundle = create_mcp_server(mock=False, transport="streamable-http", env=http_env)
    app = http_bundle.create_http_app(host="127.0.0.1")
    hdrs: dict[str, str] = {
        "Authorization": "Bearer g2-http-secret-token-value",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        _init_http_session(client, hdrs)
        tools_res = _post_jsonrpc(client, hdrs, req_id=2, method="tools/list", params={})
        names = {t["name"] for t in tools_res["result"]["tools"]}
        assert {
            "macy_ingest_research_documents",
            "macy_get_editorial_brief",
            "macy_generate_draft",
            "macy_import_draft",
            "macy_verify_claims",
            "macy_evaluate_draft",
            "macy_list_drafts_awaiting_approval",
            "macy_decide_draft",
            "macy_publish_draft",
            "macy_audit_url",
            "macy_crawl_site",
        }.issubset(names)
        assert len(names) == 17


def test_publish_draft_creator_gate_and_staging_draft_mode(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_publish_draft enforces creator gate, maps missing publisher, and forces draft mode."""
    monkeypatch.setenv("APP_ENV", "development")

    # Part A: MockStore publish & staging mode="draft"
    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_queue = _call_mcp_tool(
        mock_bundle,
        "macy_list_drafts_awaiting_approval",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    mock_draft_id = mock_queue["drafts"][0]["id"]
    assert (
        _call_mcp_tool(
            mock_bundle,
            "macy_publish_draft",
            {
                "workspace_id": str(DEMO_WS_ID),
                "draft_id": mock_draft_id,
                "actor_email": "owner@example.com",
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )
    mock_approved = _call_mcp_tool(
        mock_bundle,
        "macy_decide_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": mock_draft_id,
            "decision": "approved",
            "actor_email": "owner@example.com",
        },
    )
    assert mock_approved["ok"] is True
    assert (
        _call_mcp_tool(
            mock_bundle,
            "macy_publish_draft",
            {
                "workspace_id": str(DEMO_WS_ID),
                "draft_id": mock_draft_id,
                "mode": "live",
                "actor_email": "coowner@example.com",
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )
    assert isinstance(mock_bundle.store, MockStore)
    orig_ports = list(mock_bundle.store._workspaces[DEMO_WS_ID]["configured_ports"])
    mock_bundle.store._workspaces[DEMO_WS_ID]["configured_ports"] = [
        p for p in orig_ports if p != "publisher"
    ]
    assert (
        _call_mcp_tool(
            mock_bundle,
            "macy_publish_draft",
            {
                "workspace_id": str(DEMO_WS_ID),
                "draft_id": mock_draft_id,
                "actor_email": "owner@example.com",
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )
    mock_bundle.store._workspaces[DEMO_WS_ID]["configured_ports"] = orig_ports
    monkeypatch.setenv("APP_ENV", "staging")
    mock_pub = _call_mcp_tool(
        mock_bundle,
        "macy_publish_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": mock_draft_id,
            "mode": "live",
            "actor_email": "owner@example.com",
        },
    )
    assert mock_pub["ok"] is True
    assert mock_pub["mode"] == "draft"

    # Part B: Real Postgres
    monkeypatch.setenv("APP_ENV", "development")
    creator_email = f"mcp-pub-cr-{uuid.uuid4().hex[:6]}@example.com"
    coowner_email = f"mcp-pub-co-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="pub", owner_email=creator_email, with_campaign=True)
    _add_ws_user(db_conn, workspace_id=ws["workspace_id"], email=coowner_email)
    seeded = _seed_full_brand_and_doc(db_conn, ws=ws, tag=uuid.uuid4().hex[:6])

    brief = services.generate_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft_unapproved = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft_ready = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    db.transition_draft_status(
        db_conn,
        workspace_id=ws["workspace_id"],
        draft_id=draft_ready["id"],
        expected_statuses=("draft",),
        new_status="pass",
    )
    services.decide_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        draft_id=draft_ready["id"],
        decision="approved",
        user_id=ws["owner_id"],
    )

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_publish_draft",
            {
                "workspace_id": str(ws["workspace_id"]),
                "draft_id": str(draft_unapproved["id"]),
                "actor_email": creator_email,
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_publish_draft",
            {
                "workspace_id": str(ws["workspace_id"]),
                "draft_id": str(draft_ready["id"]),
                "actor_email": creator_email,
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )

    _seed_publisher_integration(db_conn, workspace_id=ws["workspace_id"], user_id=ws["owner_id"])
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_publish_draft",
            {
                "workspace_id": str(ws["workspace_id"]),
                "draft_id": str(draft_ready["id"]),
                "mode": "live",
                "actor_email": coowner_email,
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )

    monkeypatch.setenv("APP_ENV", "staging")
    pub_res = _call_mcp_tool(
        live_bundle,
        "macy_publish_draft",
        {
            "workspace_id": str(ws["workspace_id"]),
            "draft_id": str(draft_ready["id"]),
            "mode": "live",
            "actor_email": creator_email,
        },
    )
    assert pub_res["ok"] is True
    assert pub_res["mode"] == "draft"
    assert pub_res["status"] == "published"
    assert pub_res["response"]["is_live"] is False


def _assert_http_owner_resources_and_prompt(
    *,
    owner_email: str,
    ws_id_str: str,
    draft_id_str: str,
    run_id_str: str,
) -> None:
    http_env_owner: Mapping[str, str] = {
        "APP_ENV": "development",
        "MCP_AUTH_TOKEN": "rsc-http-token-owner",
        "MCP_ACTOR_EMAIL": owner_email,
    }
    owner_http = create_mcp_server(mock=False, transport="streamable-http", env=http_env_owner)
    owner_hdrs = {
        "Authorization": "Bearer rsc-http-token-owner",
        "Accept": "application/json, text/event-stream",
    }
    app = owner_http.create_http_app(host="127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        _init_http_session(client, owner_hdrs)
        brand_rpc = _post_jsonrpc(
            client,
            owner_hdrs,
            req_id=10,
            method="resources/read",
            params={"uri": f"macy://workspaces/{ws_id_str}/brand"},
        )
        brand_json = json.loads(brand_rpc["result"]["contents"][0]["text"])
        assert "Consultivo" in brand_json["brand_profile"]["payload"]["voice"]

        report_rpc = _post_jsonrpc(
            client,
            owner_hdrs,
            req_id=11,
            method="resources/read",
            params={"uri": f"macy://workspaces/{ws_id_str}/weekly-report"},
        )
        report_json = json.loads(report_rpc["result"]["contents"][0]["text"])
        assert report_json["run_id"] == run_id_str

        claims_rpc = _post_jsonrpc(
            client,
            owner_hdrs,
            req_id=12,
            method="resources/read",
            params={"uri": f"macy://drafts/{draft_id_str}/claims"},
        )
        claims_json = json.loads(claims_rpc["result"]["contents"][0]["text"])
        assert claims_json["draft"]["id"] == draft_id_str
        assert claims_json["claims"]

        prompt_rpc = _post_jsonrpc(
            client,
            owner_hdrs,
            req_id=13,
            method="prompts/get",
            params={
                "name": "macy_editorial_review",
                "arguments": {"workspace_id": ws_id_str, "draft_id": draft_id_str},
            },
        )
        msg_text = prompt_rpc["result"]["messages"][0]["content"]["text"]
        assert draft_id_str in msg_text
        assert "macy_decide_draft" in msg_text

        missing_draft_rpc = _post_jsonrpc(
            client,
            owner_hdrs,
            req_id=14,
            method="resources/read",
            params={"uri": f"macy://drafts/{uuid.uuid4()}/claims"},
        )
        assert "NOT_FOUND" in missing_draft_rpc["error"]["message"]

        missing_prompt_rpc = _post_jsonrpc(
            client,
            owner_hdrs,
            req_id=15,
            method="prompts/get",
            params={
                "name": "macy_editorial_review",
                "arguments": {"workspace_id": ws_id_str, "draft_id": str(uuid.uuid4())},
            },
        )
        assert "NOT_FOUND" in missing_prompt_rpc["error"]["message"]


def _assert_http_intruder_denied(
    *,
    intruder_email: str,
    ws_id_str: str,
    draft_id_str: str,
) -> None:
    http_env_intruder: Mapping[str, str] = {
        "APP_ENV": "development",
        "MCP_AUTH_TOKEN": "rsc-http-token-intruder",
        "MCP_ACTOR_EMAIL": intruder_email,
    }
    intruder_http = create_mcp_server(
        mock=False, transport="streamable-http", env=http_env_intruder
    )
    intruder_hdrs = {
        "Authorization": "Bearer rsc-http-token-intruder",
        "Accept": "application/json, text/event-stream",
    }
    app = intruder_http.create_http_app(host="127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        _init_http_session(client, intruder_hdrs)
        for idx, uri in enumerate(
            (
                f"macy://workspaces/{ws_id_str}/brand",
                f"macy://workspaces/{ws_id_str}/weekly-report",
                f"macy://drafts/{draft_id_str}/claims",
            ),
            start=20,
        ):
            denied = _post_jsonrpc(
                client, intruder_hdrs, req_id=idx, method="resources/read", params={"uri": uri}
            )
            assert "PERMISSION_DENIED" in denied["error"]["message"]

        denied_prompt = _post_jsonrpc(
            client,
            intruder_hdrs,
            req_id=25,
            method="prompts/get",
            params={
                "name": "macy_editorial_review",
                "arguments": {"workspace_id": ws_id_str, "draft_id": draft_id_str},
            },
        )
        assert "PERMISSION_DENIED" in denied_prompt["error"]["message"]


def _read_json_resource(bundle: MacyMCPBundle, uri: str) -> dict[str, Any]:
    res = asyncio.run(bundle.mcp.read_resource(uri))
    assert isinstance(res, list)
    parsed: dict[str, Any] = json.loads(res[0].content)
    return parsed


def test_mcp_resources_and_prompt_tenant_scoping(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP resources and prompt return tenant-scoped data and fail closed across tenants."""
    monkeypatch.setenv("APP_ENV", "development")

    owner_email = f"mcp-res-own-{uuid.uuid4().hex[:6]}@example.com"
    intruder_email = f"mcp-res-int-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="rsc", owner_email=owner_email, with_campaign=True)
    _add_ws_user(db_conn, workspace_id=None, email=intruder_email)
    seeded = _seed_full_brand_and_doc(db_conn, ws=ws, tag=uuid.uuid4().hex[:6])

    brief = services.generate_brief(
        db_conn,
        workspace_id=ws["workspace_id"],
        opportunity_id=seeded["opportunity"]["id"],
        user_id=ws["owner_id"],
        offline=True,
    )
    draft = services.write_draft(
        db_conn,
        workspace_id=ws["workspace_id"],
        brief_id=brief["id"],
        user_id=ws["owner_id"],
        offline=True,
    )

    ws_id_str = str(ws["workspace_id"])
    draft_id_str = str(draft["id"])

    monkeypatch.setenv("MACY_USER_EMAIL", "owner@example.com")
    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_brand = _read_json_resource(mock_bundle, f"macy://workspaces/{DEMO_WS_ID}/brand")
    assert mock_brand["brand_profile"]["payload"]["voice"]
    mock_report = _read_json_resource(mock_bundle, f"macy://workspaces/{DEMO_WS_ID}/weekly-report")
    assert mock_report["awaiting_drafts_count"] >= 1
    mock_claims = _read_json_resource(
        mock_bundle, "macy://drafts/01900000-0000-7000-8000-000000000801/claims"
    )
    assert mock_claims["claims"]
    assert asyncio.run(
        mock_bundle.mcp.get_prompt(
            "macy_editorial_review",
            {
                "workspace_id": str(DEMO_WS_ID),
                "draft_id": "01900000-0000-7000-8000-000000000801",
            },
        )
    )
    with pytest.raises(SDKMCPError, match="PERMISSION_DENIED"):
        asyncio.run(mock_bundle.mcp.read_resource(f"macy://workspaces/{LUMINA_WS_ID}/brand"))

    monkeypatch.delenv("MACY_USER_EMAIL", raising=False)
    live_fallback = create_mcp_server(mock=False, transport="stdio", allow_owner_fallback=True)
    with pytest.raises(SDKMCPError, match="AUTH_REQUIRED"):
        asyncio.run(live_fallback.mcp.read_resource(f"macy://drafts/{draft_id_str}/claims"))

    _assert_http_owner_resources_and_prompt(
        owner_email=owner_email,
        ws_id_str=ws_id_str,
        draft_id_str=draft_id_str,
        run_id_str=str(seeded["run_id"]),
    )
    _assert_http_intruder_denied(
        intruder_email=intruder_email,
        ws_id_str=ws_id_str,
        draft_id_str=draft_id_str,
    )


def test_macy_import_draft_and_deterministic_only_agent_loop(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify M5-08: Agent-Driven Mode compliance loop via ingest -> import_draft -> decide."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    short_md = (
        "---\n"
        "title: Controle de Estoque em Oticas\n"
        "slug: controle-de-estoque-em-oticas\n"
        "meta_description: Guia pratico de controle de estoque para reduzir perdas e aumentar "
        "o giro de armacoes na sua otica.\n"
        "---\n\n"
        "# Controle de Estoque em Oticas\n\n" + " ".join(["otica"] * 80)
    )
    rev0_mock = _call_mcp_tool(
        mock_bundle,
        "macy_import_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "body_md": short_md,
            "format_slug": "blog-short",
            "primary_keyword": "controle de estoque",
            "deterministic_only": True,
            "actor_email": "owner@example.com",
        },
    )
    assert rev0_mock["revision"] == 0
    assert rev0_mock["verdict"] == "REVISION"
    assert any(f["criterion"] == "word_count" for f in rev0_mock["findings"])

    valid_md = (
        "---\n"
        "title: Controle de Estoque em Oticas\n"
        "slug: controle-de-estoque-em-oticas\n"
        "meta_description: Guia pratico de controle de estoque para reduzir perdas e aumentar "
        "o giro de armacoes na sua otica.\n"
        "---\n\n"
        "# Controle de Estoque em Oticas\n\n" + " ".join(["otica"] * 450)
    )
    rev1_mock = _call_mcp_tool(
        mock_bundle,
        "macy_import_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "brief_id": str(rev0_mock["brief_id"]),
            "body_md": valid_md,
            "format_slug": "blog-short",
            "deterministic_only": True,
            "actor_email": "owner@example.com",
        },
    )
    assert rev1_mock["revision"] == 1
    assert rev1_mock["verdict"] == "PASS"

    eval_mock = _call_mcp_tool(
        mock_bundle,
        "macy_evaluate_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": str(rev1_mock["draft_id"]),
            "deterministic_only": True,
            "actor_email": "owner@example.com",
        },
    )
    assert eval_mock["verdict"] == "PASS"

    dec_mock = _call_mcp_tool(
        mock_bundle,
        "macy_decide_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "draft_id": str(rev1_mock["draft_id"]),
            "decision": "approved",
            "actor_email": "owner@example.com",
        },
    )
    assert dec_mock["status"] == "approved"

    # file_path must be rejected on streamable-http transport
    http_bundle = create_mcp_server(mock=True, transport="streamable-http")
    http_err = _call_mcp_tool(
        http_bundle,
        "macy_import_draft",
        {
            "workspace_id": str(DEMO_WS_ID),
            "file_path": str(tmp_path / "article.md"),
            "format_slug": "blog-short",
            "actor_email": "owner@example.com",
        },
    )
    assert "error" in http_err
    assert "streamable-http" in http_err["message"].lower()

    # Postgres Agent-Driven Mode flow
    owner_email = f"mcp-ma-{uuid.uuid4().hex[:6]}@example.com"
    ws_seed = _seed_workspace_fixture(
        db_conn, tag="modo-a", owner_email=owner_email, with_campaign=True
    )
    ws_id = ws_seed["workspace_id"]
    services.save_brand(
        db_conn,
        workspace_id=ws_id,
        user_id=ws_seed["owner_id"],
        profile={"voice": "Consultivo", "positioning": "ERP para oticas"},
    )
    db_bundle = create_mcp_server(mock=False, transport="stdio")

    ing = _call_mcp_tool(
        db_bundle,
        "macy_ingest_research_documents",
        {
            "workspace_id": str(ws_id),
            "topic": "Controle de Estoque em Oticas",
            "documents": [
                {
                    "title": "Estudo Giro 2026",
                    "url": "https://example.org/estudo-giro-2026",
                    "summary": "Giro medio subiu 18%.",
                    "facts": ["Giro medio subiu 18%."],
                }
            ],
            "actor_email": owner_email,
        },
    )
    assert ing["ok"] is True

    rev0_db = _call_mcp_tool(
        db_bundle,
        "macy_import_draft",
        {
            "workspace_id": str(ws_id),
            "body_md": short_md,
            "format_slug": "blog-short",
            "primary_keyword": "controle de estoque",
            "deterministic_only": True,
            "actor_email": owner_email,
        },
    )
    assert rev0_db["revision"] == 0
    assert rev0_db["verdict"] == "REVISION"

    rev1_db = _call_mcp_tool(
        db_bundle,
        "macy_import_draft",
        {
            "workspace_id": str(ws_id),
            "brief_id": str(rev0_db["brief_id"]),
            "body_md": valid_md,
            "format_slug": "blog-short",
            "deterministic_only": True,
            "actor_email": owner_email,
        },
    )
    assert rev1_db["revision"] == 1
    assert rev1_db["verdict"] == "PASS"

    dec_db = _call_mcp_tool(
        db_bundle,
        "macy_decide_draft",
        {
            "workspace_id": str(ws_id),
            "draft_id": str(rev1_db["draft_id"]),
            "decision": "approved",
            "actor_email": owner_email,
        },
    )
    assert dec_db["status"] == "approved"


def test_compliance_agent_runner_cli_and_prompt() -> None:
    """Verify M5-08: compliance_agent builds prompt and parses args without engine/ deps."""
    from scripts import compliance_agent

    parser = compliance_agent.build_parser()
    args = parser.parse_args(
        [
            "--workspace-id",
            str(DEMO_WS_ID),
            "--topic",
            "Gestao de estoque",
            "--format",
            "blog-short",
        ]
    )
    assert args.format == "blog-short"
    prompt = compliance_agent.build_compliance_prompt(
        workspace_id=args.workspace_id,
        topic=args.topic,
        format_slug=args.format,
    )
    assert "macy_ingest_research_documents" in prompt
    assert "macy_import_draft" in prompt
    assert "blog-short" in prompt
