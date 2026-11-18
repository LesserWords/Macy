"""Tests for M5-04: MCP discovery, research, scoring, and Gate 1 approval tools."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from mcp.types import CallToolResult
from starlette.testclient import TestClient

from engine import db, services
from engine.contracts import ResearchRecord
from engine.integrations.fakes import FakeExtractor, FakeFeedSource, FakeLLM, FakeNewsSearch
from engine.mcp.errors import MCPErrorCode
from engine.mcp.mock_store import (
    DEMO_COOWNER_ID,
    DEMO_MEMBER_ID,
    DEMO_WS_ID,
    LUMINA_WS_ID,
)
from engine.mcp.server import MacyMCPBundle, create_mcp_server
from tests.test_mcp_auth import _seed_gate_items, _seed_workspace_fixture


def _call_mcp_tool(bundle: MacyMCPBundle, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(bundle.mcp.call_tool(name, args))
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.content
    text_block = result.content[0]
    text_val = getattr(text_block, "text", None)
    assert isinstance(text_val, str)
    parsed: dict[str, Any] = json.loads(text_val)
    return parsed


def _parse_sse_jsonrpc(raw_text: str) -> dict[str, Any]:
    for line in raw_text.splitlines():
        if line.startswith("data: "):
            parsed: dict[str, Any] = json.loads(line.removeprefix("data: "))
            return parsed
    parsed_plain: dict[str, Any] = json.loads(raw_text)
    return parsed_plain


def _seed_fake_workspace_integrations(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    for port in ("llm", "news_search", "feed_source", "extractor"):
        db.upsert_workspace_integration(
            conn,
            workspace_id=workspace_id,
            port=port,
            plugin="fake",
            settings={},
            secrets=None,
            updated_by=user_id,
        )


def _add_ws_user(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None,
    email: str,
) -> dict[str, Any]:
    user = db.create_or_get_user(conn, email=email, name="User")
    if workspace_id is not None:
        db.create_membership(
            conn,
            workspace_id=workspace_id,
            user_id=user["user_id"],
            role="owner",
        )
    return user


def _create_test_run(
    conn: psycopg.Connection[Any],
    ws: dict[str, Any],
) -> uuid.UUID:
    return db.create_run(
        conn,
        workspace_id=ws["workspace_id"],
        campaign_id=ws["campaign_id"],
        trigger="manual",
        window_start=datetime.now(UTC) - timedelta(days=7),
        window_end=datetime.now(UTC),
        run_cap_usd=Decimal("15.00"),
        code_version="v1",
        pack_version="p1",
        created_by=ws["owner_id"],
    )


def _seed_scored_opp(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    tag: str,
    total: Decimal,
    status: str = "scored",
    decided_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    doc = db.upsert_document(
        conn,
        workspace_id=workspace_id,
        source_id=None,
        url=f"https://optical-{tag}.example.com/story",
        canonical_url=f"https://optical-{tag}.example.com/story",
        title=f"Optical Story {tag}",
        published_at=datetime.now(UTC) - timedelta(days=1),
        date_status="verified",
        fetch_status="ok",
    )
    rr = db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={"headline": f"Optical Story {tag}", "summary": f"Summary {tag}"},
    )
    decided_at = datetime.now(UTC) if status in ("approved", "rejected") else None
    return db.upsert_opportunity(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        record_id=rr["id"],
        scores={"novelty": {"score": 8.0, "evidence": "Recent study", "is_estimate": False}},
        total=total,
        funnel_stage="mofu",
        confidence="high",
        angle=f"Angle for {tag}",
        rank=1,
        status=status,
        decided_by=decided_by,
        decided_at=decided_at,
    )


def test_macy_list_workspaces_scoped_to_actor(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_list_workspaces returns only memberships of the resolved actor in DB and mock mode."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_owner = _call_mcp_tool(
        mock_bundle,
        "macy_list_workspaces",
        {"actor_email": "owner@example.com"},
    )
    assert mock_owner["ok"] is True
    assert [w["slug"] for w in mock_owner["workspaces"]] == ["acme-demo"]
    demo_ws = mock_owner["workspaces"][0]
    assert demo_ws["locale"] == "pt-BR"
    assert demo_ws["monthly_cap_usd"] == "120.00"
    assert demo_ws["run_cap_usd"] == "15.00"

    mock_intruder = _call_mcp_tool(
        mock_bundle,
        "macy_list_workspaces",
        {"actor_email": "intruder@lumina.com"},
    )
    assert [w["slug"] for w in mock_intruder["workspaces"]] == ["lumina"]

    alice_email = f"mcp-list-a-{uuid.uuid4().hex[:6]}@example.com"
    bob_email = f"mcp-list-b-{uuid.uuid4().hex[:6]}@example.com"
    ws_a1 = _seed_workspace_fixture(db_conn, tag="la1", owner_email=alice_email, with_campaign=True)
    ws_a2 = _seed_workspace_fixture(db_conn, tag="la2", owner_email=alice_email, with_campaign=True)
    ws_b = _seed_workspace_fixture(db_conn, tag="lb1", owner_email=bob_email, with_campaign=True)

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    res = _call_mcp_tool(live_bundle, "macy_list_workspaces", {"actor_email": alice_email})
    assert res["ok"] is True
    returned_ids = {w["workspace_id"] for w in res["workspaces"]}
    assert str(ws_a1["workspace_id"]) in returned_ids
    assert str(ws_a2["workspace_id"]) in returned_ids
    assert str(ws_b["workspace_id"]) not in returned_ids
    sample = next(w for w in res["workspaces"] if w["workspace_id"] == str(ws_a1["workspace_id"]))
    assert sample["locale"] == "pt-BR"
    assert Decimal(sample["monthly_cap_usd"]) == Decimal("120.00")


def test_macy_get_workspace_status_metrics(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_get_workspace_status returns spend vs cap, sources, integrations, and gate counts."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_status = _call_mcp_tool(
        mock_bundle,
        "macy_get_workspace_status",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    assert mock_status["ok"] is True
    assert mock_status["slug"] == "acme-demo"
    assert Decimal(mock_status["monthly_spend_usd"]) == Decimal("12.500000")
    assert Decimal(mock_status["monthly_cap_usd"]) == Decimal("120.00")
    assert mock_status["gate1_awaiting_count"] >= 1
    assert mock_status["gate2_awaiting_count"] >= 1
    assert "llm" in mock_status["configured_ports"]

    denied_mock = _call_mcp_tool(
        mock_bundle,
        "macy_get_workspace_status",
        {"workspace_id": str(LUMINA_WS_ID), "actor_email": "owner@example.com"},
    )
    assert denied_mock["error"] == MCPErrorCode.PERMISSION_DENIED.value

    alice_email = f"mcp-stat-a-{uuid.uuid4().hex[:6]}@example.com"
    intruder_email = f"mcp-stat-b-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="st1", owner_email=alice_email, with_campaign=True)
    ws_other = _seed_workspace_fixture(
        db_conn, tag="st2", owner_email=intruder_email, with_campaign=False
    )
    _seed_gate_items(
        db_conn,
        ws_id=ws["workspace_id"],
        campaign_id=ws["campaign_id"],
        owner_id=ws["owner_id"],
        tag=uuid.uuid4().hex[:6],
    )

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    live_status = _call_mcp_tool(
        live_bundle,
        "macy_get_workspace_status",
        {"workspace_id": str(ws["workspace_id"]), "actor_email": alice_email},
    )
    assert live_status["ok"] is True
    assert Decimal(live_status["monthly_spend_usd"]) == Decimal("12.500000")
    assert live_status["gate1_awaiting_count"] == 1
    assert live_status["gate2_awaiting_count"] == 1
    assert live_status["active_sources_count"] >= 1
    assert live_status["configured_integrations_count"] == 1

    bad_uuid = _call_mcp_tool(
        live_bundle,
        "macy_get_workspace_status",
        {"workspace_id": "not-a-uuid", "actor_email": alice_email},
    )
    assert bad_uuid["error"] == MCPErrorCode.INVALID_STATE.value

    cross_tenant = _call_mcp_tool(
        live_bundle,
        "macy_get_workspace_status",
        {"workspace_id": str(ws["workspace_id"]), "actor_user_id": str(ws_other["owner_id"])},
    )
    assert cross_tenant["error"] == MCPErrorCode.PERMISSION_DENIED.value


def test_macy_research_topic_extracts_dates_and_urls(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_research_topic extracts canonical URLs, explicit/unverified dates, and text."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_res = _call_mcp_tool(
        mock_bundle,
        "macy_research_topic",
        {
            "workspace_id": str(DEMO_WS_ID),
            "topic": "gestao-estoque-oticas",
            "actor_email": "owner@example.com",
        },
    )
    assert mock_res["ok"] is True
    assert mock_res["document_count"] >= 2
    for doc in mock_res["documents"]:
        assert doc["canonical_url"].startswith("https://")
        assert doc["title"]
        assert doc["date_status"] in ("verified", "inferred", "unverified")
        assert doc["extracted_passage"]

    alice_email = f"mcp-res-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="res1", owner_email=alice_email, with_campaign=True)
    ws_id = ws["workspace_id"]
    uid = ws["owner_id"]
    _seed_fake_workspace_integrations(db_conn, workspace_id=ws_id, user_id=uid)

    class _MixedDateSearch(FakeNewsSearch):
        def search(
            self,
            query: str,
            locale: str,
            market: str,
            since: Any = None,
            *,
            domain: str | None = None,
            **kwargs: Any,
        ) -> list[ResearchRecord]:
            del query, locale, market, since, domain, kwargs
            return [
                ResearchRecord(
                    headline="Dated Optical Benchmark",
                    source="Sector Journal",
                    url="https://sector.example.com/2026/11/01/benchmark?utm_source=x",
                    published_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
                    summary="Dated summary",
                    verification_status="verified",
                ),
                ResearchRecord(
                    headline="Undated Industry Note",
                    source="Sector Blog",
                    url="https://sector.example.com/undated-note?fbclid=123",
                    published_at=None,
                    summary="Undated summary",
                    verification_status="unverified",
                ),
            ]

    res = services.run_mcp_research(
        db_conn,
        workspace_id=ws_id,
        user_id=uid,
        query="gestao-oticas",
        feed_adapter=FakeFeedSource(),
        news_search_adapter=_MixedDateSearch(),
        extractor_adapter=FakeExtractor(),
        llm_adapter=FakeLLM(),
    )
    assert res["run_id"] is not None
    assert len(res["documents"]) >= 2
    by_canonical = {d["canonical_url"]: d for d in res["documents"]}
    dated = by_canonical["https://sector.example.com/2026/11/01/benchmark"]
    assert dated["date_status"] == "verified"
    assert dated["published_at"] is not None
    assert dated["extracted_passage"]

    undated = by_canonical["https://sector.example.com/undated-note"]
    assert undated["date_status"] == "unverified"
    assert undated["published_at"] is None

    # Live MCP tool invocation reuses the active run cleanly (both with and without topic/run_id)
    live_bundle = create_mcp_server(mock=False, transport="stdio")
    reused_default = _call_mcp_tool(
        live_bundle,
        "macy_research_topic",
        {"workspace_id": str(ws_id), "actor_email": alice_email},
    )
    assert reused_default["ok"] is True
    assert reused_default["run_id"] == str(res["run_id"])

    tool_res = _call_mcp_tool(
        live_bundle,
        "macy_research_topic",
        {
            "workspace_id": str(ws_id),
            "query": "miopia-infantil",
            "run_id": str(res["run_id"]),
            "actor_email": alice_email,
        },
    )
    assert tool_res["ok"] is True
    assert tool_res["run_id"] == str(res["run_id"])
    assert tool_res["document_count"] >= 2

    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_research_topic",
            {
                "workspace_id": str(ws_id),
                "run_id": str(uuid.uuid4()),
                "actor_email": alice_email,
            },
        )["error"]
        == MCPErrorCode.NOT_FOUND.value
    )

    ws_nocamp = _seed_workspace_fixture(
        db_conn,
        tag="rnc",
        owner_email=f"mcp-rnc-{uuid.uuid4().hex[:6]}@example.com",
        with_campaign=False,
    )
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_research_topic",
            {
                "workspace_id": str(ws_nocamp["workspace_id"]),
                "actor_user_id": str(ws_nocamp["owner_id"]),
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )

    # Workspace with campaign, no integrations, and no prior runs fails closed in production
    ws_noint = _seed_workspace_fixture(
        db_conn,
        tag="rni",
        owner_email=f"mcp-rni-{uuid.uuid4().hex[:6]}@example.com",
        with_campaign=True,
    )
    assert (
        _call_mcp_tool(
            live_bundle,
            "macy_score_topics",
            {
                "workspace_id": str(ws_noint["workspace_id"]),
                "actor_user_id": str(ws_noint["owner_id"]),
            },
        )["error"]
        == MCPErrorCode.INVALID_STATE.value
    )

    # Production guard forbids offline fake execution AND missing integrations
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(PermissionError, match="forbidden"):
        services.run_mcp_research(
            db_conn, workspace_id=ws_id, user_id=uid, query="test", offline=True
        )
    prod_err = _call_mcp_tool(
        live_bundle,
        "macy_research_topic",
        {
            "workspace_id": str(ws_id),
            "topic": "test",
            "offline": True,
            "actor_email": alice_email,
        },
    )
    assert prod_err["error"] == MCPErrorCode.PERMISSION_DENIED.value

    missing_res = _call_mcp_tool(
        live_bundle,
        "macy_research_topic",
        {
            "workspace_id": str(ws_noint["workspace_id"]),
            "actor_user_id": str(ws_noint["owner_id"]),
        },
    )
    assert missing_res["error"] == MCPErrorCode.INVALID_STATE.value
    missing_score = _call_mcp_tool(
        live_bundle,
        "macy_score_topics",
        {
            "workspace_id": str(ws_noint["workspace_id"]),
            "actor_user_id": str(ws_noint["owner_id"]),
        },
    )
    assert missing_score["error"] == MCPErrorCode.INVALID_STATE.value


def test_macy_score_topics_returns_subscores(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_score_topics returns ranked topics with weighted criterion sub-scores and totals."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_research = _call_mcp_tool(
        mock_bundle,
        "macy_research_topic",
        {
            "workspace_id": str(DEMO_WS_ID),
            "topic": "crm-oticas",
            "actor_email": "owner@example.com",
        },
    )
    mock_scored = _call_mcp_tool(
        mock_bundle,
        "macy_score_topics",
        {
            "workspace_id": str(DEMO_WS_ID),
            "run_id": mock_research["run_id"],
            "actor_email": "owner@example.com",
        },
    )
    assert mock_scored["ok"] is True
    assert mock_scored["topics"]
    top_mock = mock_scored["topics"][0]
    assert top_mock["rank"] == 1
    assert top_mock["total"] > 0
    for key in ("novelty", "audience_fit", "brand_fit", "competitor_gap", "trend", "urgency"):
        assert key in top_mock["scores"]

    mock_default_scored = _call_mcp_tool(
        mock_bundle,
        "macy_score_topics",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    assert mock_default_scored["ok"] is True
    assert (
        _call_mcp_tool(
            mock_bundle,
            "macy_score_topics",
            {
                "workspace_id": str(DEMO_WS_ID),
                "run_id": str(uuid.uuid4()),
                "actor_email": "owner@example.com",
            },
        )["error"]
        == MCPErrorCode.NOT_FOUND.value
    )

    alice_email = f"mcp-score-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="scr1", owner_email=alice_email, with_campaign=True)
    ws_id = ws["workspace_id"]
    uid = ws["owner_id"]
    _seed_fake_workspace_integrations(db_conn, workspace_id=ws_id, user_id=uid)

    research_out = services.run_mcp_research(
        db_conn,
        workspace_id=ws_id,
        user_id=uid,
        query="lentes-multifocais",
        offline=True,
    )
    live_bundle = create_mcp_server(mock=False, transport="stdio")
    scored_res = _call_mcp_tool(
        live_bundle,
        "macy_score_topics",
        {"workspace_id": str(ws_id), "actor_email": alice_email},
    )
    assert scored_res["ok"] is True
    assert scored_res["run_id"] == str(research_out["run_id"])
    assert scored_res["topics"]
    top_live = scored_res["topics"][0]
    assert top_live["rank"] == 1
    assert top_live["status"] == "scored"
    assert top_live["total"] > 0
    assert "novelty" in top_live["scores"]
    assert isinstance(top_live["is_verified_date"], bool)

    # Run transitioned cleanly to completed; re-scoring completed run stays idempotent, while
    # researching an already completed run refuses with INVALID_STATE
    finished_run = db.get_run(db_conn, workspace_id=ws_id, run_id=research_out["run_id"])
    assert finished_run is not None
    assert finished_run["status"] == "completed"
    idempotent = _call_mcp_tool(
        live_bundle,
        "macy_score_topics",
        {
            "workspace_id": str(ws_id),
            "run_id": str(research_out["run_id"]),
            "actor_email": alice_email,
        },
    )
    assert idempotent["ok"] is True
    completed_err = _call_mcp_tool(
        live_bundle,
        "macy_research_topic",
        {
            "workspace_id": str(ws_id),
            "run_id": str(research_out["run_id"]),
            "actor_email": alice_email,
        },
    )
    assert completed_err["error"] == MCPErrorCode.INVALID_STATE.value

    # Missing run returns NOT_FOUND
    missing_run = _call_mcp_tool(
        live_bundle,
        "macy_score_topics",
        {"workspace_id": str(ws_id), "run_id": str(uuid.uuid4()), "actor_email": alice_email},
    )
    assert missing_run["error"] == MCPErrorCode.NOT_FOUND.value

    # Exceeding run budget cap maps to BUDGET_CAP_EXCEEDED
    blocked_run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=ws["campaign_id"],
        trigger="manual",
        window_start=datetime.now(UTC) - timedelta(days=7),
        window_end=datetime.now(UTC),
        run_cap_usd=Decimal("0.01"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=uid,
    )
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=blocked_run_id,
        stage="research",
        kind="llm",
        vendor="fake",
        cost_usd=Decimal("1.000000"),
        model="fake-llm",
    )
    cap_err = _call_mcp_tool(
        live_bundle,
        "macy_score_topics",
        {"workspace_id": str(ws_id), "run_id": str(blocked_run_id), "actor_email": alice_email},
    )
    assert cap_err["error"] == MCPErrorCode.BUDGET_CAP_EXCEEDED.value


def test_list_topics_awaiting_approval_queries_scored(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macy_list_topics_awaiting_approval strictly queries status='scored' in workspace."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_list = _call_mcp_tool(
        mock_bundle,
        "macy_list_topics_awaiting_approval",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    assert mock_list["ok"] is True
    assert mock_list["count"] >= 2
    assert all(t["status"] == "scored" for t in mock_list["topics"])
    mock_missing_run = _call_mcp_tool(
        mock_bundle,
        "macy_list_topics_awaiting_approval",
        {
            "workspace_id": str(DEMO_WS_ID),
            "run_id": str(uuid.uuid4()),
            "actor_email": "owner@example.com",
        },
    )
    assert mock_missing_run["error"] == MCPErrorCode.NOT_FOUND.value

    ws_empty_email = f"mcp-empty-{uuid.uuid4().hex[:6]}@example.com"
    ws_empty = _seed_workspace_fixture(
        db_conn, tag="qe", owner_email=ws_empty_email, with_campaign=True
    )

    alice_email = f"mcp-queue-a-{uuid.uuid4().hex[:6]}@example.com"
    bob_email = f"mcp-queue-b-{uuid.uuid4().hex[:6]}@example.com"
    ws_a = _seed_workspace_fixture(db_conn, tag="qa", owner_email=alice_email, with_campaign=True)
    ws_b = _seed_workspace_fixture(db_conn, tag="qb", owner_email=bob_email, with_campaign=True)

    run_a = _create_test_run(db_conn, ws_a)
    opp_scored = _seed_scored_opp(
        db_conn,
        workspace_id=ws_a["workspace_id"],
        run_id=run_a,
        tag=f"sc-{uuid.uuid4().hex[:4]}",
        total=Decimal("88.00"),
        status="scored",
    )
    opp_approved = _seed_scored_opp(
        db_conn,
        workspace_id=ws_a["workspace_id"],
        run_id=run_a,
        tag=f"ap-{uuid.uuid4().hex[:4]}",
        total=Decimal("92.00"),
        status="approved",
        decided_by=ws_a["owner_id"],
    )
    opp_rejected = _seed_scored_opp(
        db_conn,
        workspace_id=ws_a["workspace_id"],
        run_id=run_a,
        tag=f"rj-{uuid.uuid4().hex[:4]}",
        total=Decimal("70.00"),
        status="rejected",
        decided_by=ws_a["owner_id"],
    )
    opp_expired = _seed_scored_opp(
        db_conn,
        workspace_id=ws_a["workspace_id"],
        run_id=run_a,
        tag=f"ex-{uuid.uuid4().hex[:4]}",
        total=Decimal("60.00"),
        status="expired",
    )
    run_b = _create_test_run(db_conn, ws_b)
    opp_other_tenant = _seed_scored_opp(
        db_conn,
        workspace_id=ws_b["workspace_id"],
        run_id=run_b,
        tag=f"ob-{uuid.uuid4().hex[:4]}",
        total=Decimal("95.00"),
        status="scored",
    )

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    queue_res = _call_mcp_tool(
        live_bundle,
        "macy_list_topics_awaiting_approval",
        {"workspace_id": str(ws_a["workspace_id"]), "actor_email": alice_email},
    )
    assert queue_res["ok"] is True
    ids = {item["id"] for item in queue_res["topics"]}
    assert ids == {str(opp_scored["id"])}
    assert str(opp_approved["id"]) not in ids
    assert str(opp_rejected["id"]) not in ids
    assert str(opp_expired["id"]) not in ids
    assert str(opp_other_tenant["id"]) not in ids
    assert all(item["status"] == "scored" for item in queue_res["topics"])

    explicit_run = _call_mcp_tool(
        live_bundle,
        "macy_list_topics_awaiting_approval",
        {
            "workspace_id": str(ws_a["workspace_id"]),
            "run_id": str(run_a),
            "actor_email": alice_email,
        },
    )
    assert {item["id"] for item in explicit_run["topics"]} == {str(opp_scored["id"])}

    empty_queue = _call_mcp_tool(
        live_bundle,
        "macy_list_topics_awaiting_approval",
        {"workspace_id": str(ws_empty["workspace_id"]), "actor_email": ws_empty_email},
    )
    assert empty_queue["topics"] == []
    assert empty_queue["count"] == 0

    # Cross-tenant run_id explicitly requested fails closed
    cross_run = _call_mcp_tool(
        live_bundle,
        "macy_list_topics_awaiting_approval",
        {
            "workspace_id": str(ws_a["workspace_id"]),
            "run_id": str(run_b),
            "actor_email": alice_email,
        },
    )
    assert cross_run["error"] == MCPErrorCode.NOT_FOUND.value


def _assert_gate1_decisions_for_bundle(  # noqa: PLR0913
    *,
    bundle: MacyMCPBundle,
    ws_id: str,
    creator_email: str,
    coowner_email: str,
    unauthorized_email: str,
    scored_ids: list[str],
) -> None:
    assert len(scored_ids) >= 4
    # 1. Co-owner (owner role, NOT campaign creator) refused with PERMISSION_DENIED
    co_denied = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[0],
            "decision": "approved",
            "actor_email": coowner_email,
        },
    )
    assert co_denied["error"] == MCPErrorCode.PERMISSION_DENIED.value

    # 2. Non-owner / non-member actor refused with PERMISSION_DENIED
    mem_denied = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[0],
            "decision": "approved",
            "actor_email": unauthorized_email,
        },
    )
    assert mem_denied["error"] == MCPErrorCode.PERMISSION_DENIED.value

    # 3. Campaign creator approves 2 topics (articles_per_week=2) and rejects 1 topic
    ap1 = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[0],
            "decision": "approved",
            "note": "Strong B2B retail fit",
            "actor_email": creator_email,
        },
    )
    assert ap1["ok"] is True
    assert ap1["status"] == "approved"

    ap2 = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[1],
            "decision": "approved",
            "actor_email": creator_email,
        },
    )
    assert ap2["ok"] is True
    assert ap2["status"] == "approved"

    # 4. 3rd approval breaches articles_per_week=2 -> INVALID_STATE
    cap_err = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[2],
            "decision": "approved",
            "actor_email": creator_email,
        },
    )
    assert cap_err["error"] == MCPErrorCode.INVALID_STATE.value
    assert "Approval limit reached" in cap_err["message"]

    # 5. Creator can still reject another scored topic
    rej = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[3],
            "decision": "rejected",
            "note": "Too niche",
            "actor_email": creator_email,
        },
    )
    assert rej["ok"] is True
    assert rej["status"] == "rejected"

    # 6. Validation errors: invalid decision, missing opp, and already-decided opp
    bad_dec = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[2],
            "decision": "maybe",
            "actor_email": creator_email,
        },
    )
    assert bad_dec["error"] == MCPErrorCode.INVALID_STATE.value

    missing_opp = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": str(uuid.uuid4()),
            "decision": "rejected",
            "actor_email": creator_email,
        },
    )
    assert missing_opp["error"] == MCPErrorCode.NOT_FOUND.value

    redecide_err = _call_mcp_tool(
        bundle,
        "macy_decide_topic",
        {
            "workspace_id": ws_id,
            "opportunity_id": scored_ids[0],
            "decision": "rejected",
            "actor_email": creator_email,
        },
    )
    assert redecide_err["error"] == MCPErrorCode.INVALID_STATE.value


def test_decide_topic_gate1_creator_and_weekly_cap(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 1 enforces campaign creator authority, weekly cap, audit events, and HTTP JSON-RPC."""
    monkeypatch.setenv("APP_ENV", "development")

    # Part A: MockStore parity verification
    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_queue = _call_mcp_tool(
        mock_bundle,
        "macy_list_topics_awaiting_approval",
        {"workspace_id": str(DEMO_WS_ID), "actor_email": "owner@example.com"},
    )
    mock_ids = [t["id"] for t in mock_queue["topics"]]
    assert mock_bundle.store.get_user_by_id(DEMO_COOWNER_ID) is not None
    assert mock_bundle.store.get_user_by_id(DEMO_MEMBER_ID) is not None
    _assert_gate1_decisions_for_bundle(
        bundle=mock_bundle,
        ws_id=str(DEMO_WS_ID),
        creator_email="owner@example.com",
        coowner_email="coowner@example.com",
        unauthorized_email="editor@example.com",
        scored_ids=mock_ids,
    )

    # Part B: Real Postgres + HTTP Streamable JSON-RPC end-to-end verification
    creator_email = f"mcp-g1-creator-{uuid.uuid4().hex[:6]}@example.com"
    coowner_email = f"mcp-g1-co-{uuid.uuid4().hex[:6]}@example.com"
    outsider_email = f"mcp-g1-out-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="g1", owner_email=creator_email, with_campaign=True)
    _add_ws_user(db_conn, workspace_id=ws["workspace_id"], email=coowner_email)
    _add_ws_user(db_conn, workspace_id=None, email=outsider_email)

    run_id = _create_test_run(db_conn, ws)
    opps = [
        _seed_scored_opp(
            db_conn,
            workspace_id=ws["workspace_id"],
            run_id=run_id,
            tag=f"g1-{i}-{uuid.uuid4().hex[:4]}",
            total=Decimal(str(90 - i)),
        )
        for i in range(4)
    ]

    live_bundle = create_mcp_server(mock=False, transport="stdio")
    _assert_gate1_decisions_for_bundle(
        bundle=live_bundle,
        ws_id=str(ws["workspace_id"]),
        creator_email=creator_email,
        coowner_email=coowner_email,
        unauthorized_email=outsider_email,
        scored_ids=[str(o["id"]) for o in opps],
    )

    # Verify audit event persisted with actor_user_id = creator
    decided_events = db.list_events(
        db_conn,
        workspace_id=ws["workspace_id"],
        event_type="topic.decided",
        entity_id=opps[0]["id"],
    )
    assert len(decided_events) == 1
    assert decided_events[0]["actor_user_id"] == ws["owner_id"]
    assert decided_events[0]["payload"]["decision"] == "approved"

    # Streamable HTTP JSON-RPC tools/list and tools/call + spoofed actor refusal
    http_env: Mapping[str, str] = {
        "APP_ENV": "development",
        "MCP_AUTH_TOKEN": "g1-http-secret-token-value",
        "MCP_ACTOR_EMAIL": creator_email,
    }
    http_bundle = create_mcp_server(mock=False, transport="streamable-http", env=http_env)
    app = http_bundle.create_http_app(host="127.0.0.1")
    hdrs: dict[str, str] = {
        "Authorization": "Bearer g1-http-secret-token-value",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
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
                    "clientInfo": {"name": "g1-http-client", "version": "1.0"},
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
        list_resp = client.post(
            "/mcp",
            headers=hdrs,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tool_names = {t["name"] for t in _parse_sse_jsonrpc(list_resp.text)["result"]["tools"]}
        assert {
            "macy_list_workspaces",
            "macy_get_workspace_status",
            "macy_research_topic",
            "macy_score_topics",
            "macy_list_topics_awaiting_approval",
            "macy_decide_topic",
        }.issubset(tool_names)

        spoof_resp = client.post(
            "/mcp",
            headers=hdrs,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "macy_list_workspaces",
                    "arguments": {"actor_email": coowner_email},
                },
            },
        )
        spoof_text = _parse_sse_jsonrpc(spoof_resp.text)["result"]["content"][0]["text"]
        assert json.loads(spoof_text)["error"] == MCPErrorCode.PERMISSION_DENIED.value


def test_macy_ingest_research_documents_persists_validates_ssrf_and_scopes(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify M5-08: macy_ingest_research_documents works in mock/DB, blocks SSRF, and scopes."""
    monkeypatch.setenv("APP_ENV", "development")

    mock_bundle = create_mcp_server(mock=True, transport="stdio")
    mock_res = _call_mcp_tool(
        mock_bundle,
        "macy_ingest_research_documents",
        {
            "workspace_id": str(DEMO_WS_ID),
            "topic": "Gestao de estoque em oticas",
            "documents": [
                {
                    "title": "Estudo Varejo 2026",
                    "url": "https://example.org/estudo-varejo-2026",
                    "summary": "Resumo setorial.",
                    "facts": ["Giro subiu 15%."],
                }
            ],
            "actor_email": "owner@example.com",
        },
    )
    assert mock_res["ok"] is True
    assert len(mock_res["document_ids"]) == 1

    owner_email = f"mcp-ing-{uuid.uuid4().hex[:6]}@example.com"
    outsider_email = f"mcp-out-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="ing1", owner_email=owner_email, with_campaign=True)
    _seed_workspace_fixture(db_conn, tag="ing2", owner_email=outsider_email, with_campaign=False)
    ws_id = ws["workspace_id"]
    db_bundle = create_mcp_server(mock=False, transport="stdio")

    ok_res = _call_mcp_tool(
        db_bundle,
        "macy_ingest_research_documents",
        {
            "workspace_id": str(ws_id),
            "topic": "Gestao de estoque em oticas",
            "documents": [
                {
                    "title": "Benchmark Otico 2026",
                    "url": "https://example.org/benchmark-2026",
                    "summary": "Dados verificados do varejo optico.",
                    "facts": ["Ticket medio subiu 12%."],
                }
            ],
            "actor_email": owner_email,
        },
    )
    assert ok_res["ok"] is True
    assert len(ok_res["document_ids"]) == 1

    ssrf_res = _call_mcp_tool(
        db_bundle,
        "macy_ingest_research_documents",
        {
            "workspace_id": str(ws_id),
            "topic": "SSRF test",
            "documents": [
                {
                    "title": "Localhost leak",
                    "url": "http://127.0.0.1/admin",
                    "summary": "bad",
                }
            ],
            "actor_email": owner_email,
        },
    )
    assert "error" in ssrf_res

    denied_res = _call_mcp_tool(
        db_bundle,
        "macy_ingest_research_documents",
        {
            "workspace_id": str(ws_id),
            "topic": "Cross tenant",
            "documents": [{"title": "Doc", "url": "https://example.org/doc"}],
            "actor_email": outsider_email,
        },
    )
    assert denied_res["error"] == MCPErrorCode.PERMISSION_DENIED.value
