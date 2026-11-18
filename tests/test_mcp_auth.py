"""Tests for MCP service boundary helpers, fail-closed auth, and production guards (M5-03)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import db, services


def _seed_workspace_fixture(
    conn: psycopg.Connection[Any],
    *,
    tag: str,
    owner_email: str,
    with_campaign: bool = True,
) -> dict[str, Any]:
    slug = f"mcp-{tag}-{uuid.uuid4().hex[:6]}"
    ws = services.create_workspace(
        conn,
        slug=slug,
        name=f"MCP Test {tag}",
        industry_pack="general",
        timezone="America/Sao_Paulo",
    )
    ws_id = uuid.UUID(str(ws["workspace_id"]))
    owner_res = services.add_workspace_owner(
        conn,
        workspace_slug=slug,
        email=owner_email,
    )
    owner_id = uuid.UUID(str(owner_res["user"]["user_id"]))
    camp_id: uuid.UUID | None = None
    if with_campaign:
        camp = services.save_campaign(
            conn,
            workspace_id=ws_id,
            user_id=owner_id,
            locale="pt-BR",
            market="BR",
            articles_per_week=2,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("120.00"),
            run_cap_usd=Decimal("15.00"),
            name=f"Campaign {tag}",
        )
        camp_id = uuid.UUID(str(camp["id"]))
    return {
        "workspace_id": ws_id,
        "slug": slug,
        "owner_id": owner_id,
        "owner_email": owner_email,
        "campaign_id": camp_id,
    }


def _seed_gate_items(
    conn: psycopg.Connection[Any],
    *,
    ws_id: uuid.UUID,
    campaign_id: uuid.UUID,
    owner_id: uuid.UUID,
    tag: str,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    brand = db.save_brand_profile(
        conn,
        workspace_id=ws_id,
        schema_version=1,
        payload={"voice": f"Brand {tag}"},
        created_by=owner_id,
    )
    src_enabled = db.create_source(
        conn,
        workspace_id=ws_id,
        industry_pack="general",
        name=f"Active Src {tag}",
        domain=f"active-{tag}.example.com",
        url=f"https://active-{tag}.example.com",
        fetch_mode="feed",
        feed_url=f"https://active-{tag}.example.com/rss.xml",
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["optica"],
        enabled=True,
    )
    db.create_source(
        conn,
        workspace_id=ws_id,
        industry_pack="general",
        name=f"Disabled Src {tag}",
        domain=f"disabled-{tag}.example.com",
        url=f"https://disabled-{tag}.example.com",
        fetch_mode="feed",
        feed_url=f"https://disabled-{tag}.example.com/rss.xml",
        tier=2,
        role="news",
        locales=["pt-BR"],
        topics=["optica"],
        enabled=False,
    )
    db.upsert_workspace_integration(
        conn,
        workspace_id=ws_id,
        port="llm",
        plugin="fake",
        settings={},
        secrets=None,
        updated_by=owner_id,
    )
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=campaign_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("15.00"),
        code_version="v1",
        pack_version="general-v1",
        created_by=owner_id,
    )
    db.record_usage(
        conn,
        workspace_id=ws_id,
        stage="research",
        kind="llm",
        vendor="fake",
        cost_usd=Decimal("12.500000"),
        run_id=run_id,
        model="fake-llm",
    )

    doc_1 = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=src_enabled["id"],
        url=f"https://active-{tag}.example.com/1",
        canonical_url=f"https://active-{tag}.example.com/1",
        title="Scored Topic",
        date_status="verified",
        fetch_status="ok",
    )
    rr_1 = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_1["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Scored"},
    )
    db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr_1["id"],
        scores={"total": 84},
        total=Decimal("84.00"),
        angle="Scored awaiting Gate 1",
        funnel_stage="mofu",
        confidence="high",
        status="scored",
    )

    doc_2 = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=src_enabled["id"],
        url=f"https://active-{tag}.example.com/2",
        canonical_url=f"https://active-{tag}.example.com/2",
        title="Approved Topic",
        date_status="verified",
        fetch_status="ok",
    )
    rr_2 = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_2["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Approved"},
    )
    opp_approved = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr_2["id"],
        scores={"total": 90},
        total=Decimal("90.00"),
        angle="Already approved",
        funnel_stage="bofu",
        confidence="high",
        status="approved",
        decided_by=owner_id,
        decided_at=now,
    )
    brief = db.create_next_brief_version(
        conn,
        workspace_id=ws_id,
        opportunity_id=opp_approved["id"],
        brand_profile_id=brand["id"],
        prompt_version="brief:v1",
        keywords=[],
        schema_version=1,
        payload={"topic": "Gate 2 check"},
    )
    db.create_next_draft_revision(
        conn,
        workspace_id=ws_id,
        brief_id=brief["id"],
        prompt_version="writer:v1",
        schema_version=1,
        payload={"title": "Ready Draft"},
        claims=[],
        status="pass",
    )
    db.create_next_draft_revision(
        conn,
        workspace_id=ws_id,
        brief_id=brief["id"],
        prompt_version="writer:v1",
        schema_version=1,
        payload={"title": "Revision Draft"},
        claims=[],
        status="revision",
    )
    return {"run_id": run_id}


def test_services_list_accessible_workspaces_and_caps(db_conn: psycopg.Connection[Any]) -> None:
    alice_email = f"alice-{uuid.uuid4().hex[:6]}@example.com"
    bob_email = f"bob-{uuid.uuid4().hex[:6]}@example.com"
    ws_with_camp = _seed_workspace_fixture(
        db_conn, tag="a", owner_email=alice_email, with_campaign=True
    )
    ws_no_camp = _seed_workspace_fixture(
        db_conn, tag="nocamp", owner_email=alice_email, with_campaign=False
    )
    ws_other = _seed_workspace_fixture(db_conn, tag="b", owner_email=bob_email, with_campaign=True)

    rows = services.list_accessible_workspaces(db_conn, user_id=ws_with_camp["owner_id"])
    by_slug = {row["slug"]: row for row in rows}

    assert ws_with_camp["slug"] in by_slug
    assert ws_no_camp["slug"] in by_slug
    assert ws_other["slug"] not in by_slug

    camp_row = by_slug[ws_with_camp["slug"]]
    assert camp_row["role"] == "owner"
    assert camp_row["locale"] == "pt-BR"
    assert camp_row["market"] == "BR"
    assert camp_row["monthly_cap_usd"] == Decimal("120.00")
    assert camp_row["run_cap_usd"] == Decimal("15.00")
    assert camp_row["campaign_count"] == 1

    plain_row = by_slug[ws_no_camp["slug"]]
    assert plain_row["campaign_count"] == 0
    assert plain_row["monthly_cap_usd"] is None
    assert plain_row["locale"] is None

    loaded_user = services.get_user_by_id(db_conn, user_id=ws_with_camp["owner_id"])
    assert loaded_user is not None
    assert loaded_user["email"] == alice_email
    assert services.get_user_by_id(user_id=ws_with_camp["owner_id"]) is not None
    assert services.get_user_by_email(db_conn, email=alice_email) is not None

    # Default conn=None path and campaign-less status path
    rows_default = services.list_accessible_workspaces(user_id=ws_with_camp["owner_id"])
    assert any(r["slug"] == ws_with_camp["slug"] for r in rows_default)
    no_camp_status = services.get_workspace_status(
        workspace_id=ws_no_camp["workspace_id"],
        user_id=ws_with_camp["owner_id"],
    )
    assert no_camp_status["monthly_cap_usd"] is None
    assert no_camp_status["latest_run"] is None


def test_services_get_workspace_status_counts_and_member_guard(
    db_conn: psycopg.Connection[Any],
) -> None:
    alice_email = f"status-a-{uuid.uuid4().hex[:6]}@example.com"
    intruder_email = f"status-b-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="st", owner_email=alice_email, with_campaign=True)
    ws_other = _seed_workspace_fixture(
        db_conn, tag="intr", owner_email=intruder_email, with_campaign=False
    )
    seeded = _seed_gate_items(
        db_conn,
        ws_id=ws["workspace_id"],
        campaign_id=ws["campaign_id"],
        owner_id=ws["owner_id"],
        tag=uuid.uuid4().hex[:6],
    )

    status = services.get_workspace_status(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=ws["owner_id"],
    )
    assert status["slug"] == ws["slug"]
    assert status["monthly_spend_usd"] == Decimal("12.500000")
    assert status["monthly_cap_usd"] == Decimal("120.00")
    assert status["monthly_remaining_usd"] == Decimal("107.500000")
    assert status["gate1_awaiting_count"] == 1
    assert status["gate2_awaiting_count"] == 1
    assert status["active_sources_count"] >= 1
    assert status["configured_integrations_count"] == 1
    assert status["latest_run"]["id"] == seeded["run_id"]

    with pytest.raises(PermissionError, match="not a member"):
        services.get_workspace_status(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=ws_other["owner_id"],
        )


def test_services_get_workspace_owner_identity_env_guard(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_email = f"owner-id-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(db_conn, tag="own", owner_email=owner_email, with_campaign=False)

    monkeypatch.setenv("APP_ENV", "development")
    identity = services.get_workspace_owner_identity(db_conn, workspace_id=ws["workspace_id"])
    assert identity is not None
    assert identity["user_id"] == ws["owner_id"]
    assert identity["email"] == owner_email
    assert services.get_workspace_owner_identity(workspace_id=ws["workspace_id"]) is not None

    ownerless_slug = f"ownerless-{uuid.uuid4().hex[:6]}"
    ownerless_ws = services.create_workspace(
        db_conn,
        slug=ownerless_slug,
        name="Ownerless WS",
        industry_pack="general",
    )
    assert (
        services.get_workspace_owner_identity(db_conn, workspace_id=ownerless_ws["workspace_id"])
        is None
    )

    for restricted_env in ("staging", "production", "prod"):
        monkeypatch.setenv("APP_ENV", restricted_env)
        with pytest.raises(PermissionError, match="forbidden"):
            services.get_workspace_owner_identity(db_conn, workspace_id=ws["workspace_id"])


def test_mock_refused_in_staging_and_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.mcp.errors import MCPErrorCode, MockForbiddenError
    from engine.mcp.mock_store import MockStore, is_mock_mode_requested, validate_mock_policy

    for env_key in ("APP_ENV", "ENV"):
        for restricted in ("staging", "production", "prod"):
            monkeypatch.delenv("APP_ENV", raising=False)
            monkeypatch.delenv("ENV", raising=False)
            monkeypatch.delenv("MACY_MCP_STANDALONE", raising=False)
            monkeypatch.setenv(env_key, restricted)

            with pytest.raises(MockForbiddenError) as exc_info:
                validate_mock_policy(mock_flag=True)
            assert exc_info.value.code == MCPErrorCode.MOCK_FORBIDDEN

            monkeypatch.setenv("MACY_MCP_STANDALONE", "1")
            assert is_mock_mode_requested(mock_flag=False) is True
            with pytest.raises(MockForbiddenError):
                MockStore.create_default()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MACY_MCP_STANDALONE", "1")
    store = MockStore.create_default()
    assert store.default_workspace_slug == "acme-demo"


def test_http_bearer_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from engine.mcp.auth import verify_bearer_token
    from engine.mcp.server import create_mcp_server

    expected = "test-mcp-secret-token-value"
    assert verify_bearer_token(None, expected) is False
    assert verify_bearer_token("", expected) is False
    assert verify_bearer_token("Basic dXNlcjpwYXNz", expected) is False
    assert verify_bearer_token("Bearer ", expected) is False
    assert verify_bearer_token("Bearer wrong-token", expected) is False
    assert verify_bearer_token(f"Bearer {expected}", "") is False
    assert verify_bearer_token(f"Bearer {expected}", "CHANGE_ME") is False
    assert verify_bearer_token(f"Bearer {expected}", expected) is True

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MCP_AUTH_TOKEN", expected)
    monkeypatch.setenv("MCP_ACTOR_EMAIL", "owner@example.com")
    bundle = create_mcp_server(mock=True, transport="streamable-http")
    app = bundle.create_http_app(host="127.0.0.1")

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert (
            client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code
            == 401
        )
        assert bundle.auth_config.pinned_actor_email == "owner@example.com"
        assert (
            client.post(
                "/mcp",
                headers={"Authorization": "Bearer "},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/mcp",
                headers={"Authorization": "Bearer wrong-token"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            ).status_code
            == 401
        )
        resp_ok = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {expected}",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )
        assert resp_ok.status_code == 200


def test_http_startup_refuses_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.mcp.auth import validate_http_startup_config
    from engine.mcp.errors import MCPError, MCPErrorCode
    from engine.mcp.server import create_mcp_server

    for restricted in ("staging", "production", "prod"):
        with pytest.raises(MCPError) as missing_tok:
            validate_http_startup_config(
                {"APP_ENV": restricted, "MCP_ACTOR_EMAIL": "a@example.com"}
            )
        assert missing_tok.value.code == MCPErrorCode.AUTH_REQUIRED

        with pytest.raises(MCPError) as placeholder_tok:
            validate_http_startup_config(
                {
                    "APP_ENV": restricted,
                    "MCP_AUTH_TOKEN": "CHANGE_ME_TOKEN",
                    "MCP_ACTOR_EMAIL": "a@example.com",
                }
            )
        assert placeholder_tok.value.code == MCPErrorCode.AUTH_REQUIRED

        with pytest.raises(MCPError) as missing_actor:
            validate_http_startup_config(
                {"APP_ENV": restricted, "MCP_AUTH_TOKEN": "valid-runtime-secret-token"}
            )
        assert missing_actor.value.code == MCPErrorCode.AUTH_REQUIRED

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_ACTOR_EMAIL", raising=False)
    with pytest.raises(MCPError) as srv_err:
        create_mcp_server(mock=False, transport="streamable-http")
    assert srv_err.value.code == MCPErrorCode.AUTH_REQUIRED

    ok_cfg = validate_http_startup_config(
        {
            "APP_ENV": "production",
            "MCP_AUTH_TOKEN": "valid-runtime-secret-token",
            "MCP_ACTOR_EMAIL": "owner@example.com",
        }
    )
    assert ok_cfg.pinned_actor_email == "owner@example.com"


def test_http_pinned_actor_rejects_spoofed_tool_arg() -> None:
    from engine.mcp.auth import resolve_http_actor
    from engine.mcp.errors import MCPError, MCPErrorCode
    from engine.mcp.mock_store import MockStore

    store = MockStore.create_default({"APP_ENV": "development"})
    env = {
        "APP_ENV": "development",
        "MCP_AUTH_TOKEN": "test-mcp-secret-token-value",
        "MCP_ACTOR_EMAIL": "owner@example.com",
    }

    actor = resolve_http_actor(store, env=env)
    assert actor.email == "owner@example.com"
    assert actor.source == "pinned_http"

    # Matching arg is permitted; mismatched email or user_id is refused
    same_actor = resolve_http_actor(store, env=env, tool_actor_email="OWNER@example.com")
    assert same_actor.user_id == actor.user_id

    with pytest.raises(MCPError) as spoof_email:
        resolve_http_actor(store, env=env, tool_actor_email="intruder@lumina.com")
    assert spoof_email.value.code == MCPErrorCode.PERMISSION_DENIED

    with pytest.raises(MCPError) as spoof_uid:
        resolve_http_actor(store, env=env, tool_actor_user_id=str(uuid.uuid4()))
    assert spoof_uid.value.code == MCPErrorCode.PERMISSION_DENIED

    with pytest.raises(MCPError) as missing_http_actor:
        resolve_http_actor(store, env={"APP_ENV": "development"})
    assert missing_http_actor.value.code == MCPErrorCode.AUTH_REQUIRED

    with pytest.raises(MCPError) as unknown_http_actor:
        resolve_http_actor(
            store,
            env={"APP_ENV": "development", "MCP_ACTOR_EMAIL": "nobody@unknown.com"},
        )
    assert unknown_http_actor.value.code == MCPErrorCode.AUTH_REQUIRED


def test_stdio_three_tier_resolution_and_fake_id_block() -> None:
    from engine.auth import FakeIdentityForbiddenError
    from engine.mcp.auth import resolve_stdio_actor
    from engine.mcp.errors import MCPError, MCPErrorCode
    from engine.mcp.mock_store import MockStore

    store = MockStore.create_default({"APP_ENV": "development"})
    owner_user = store.get_user_by_email("owner@example.com")
    assert owner_user is not None
    ws_demo = store.get_workspace_id_by_slug("acme-demo")

    # Tier 1: explicit argument beats env
    a1 = resolve_stdio_actor(
        store,
        env={"APP_ENV": "development", "MACY_USER_EMAIL": "intruder@lumina.com"},
        actor_email="owner@example.com",
    )
    assert a1.email == "owner@example.com"
    assert a1.source == "arg_email"

    # Tier 2: MACY_USER_EMAIL
    a2 = resolve_stdio_actor(
        store,
        env={"APP_ENV": "development", "MACY_USER_EMAIL": "owner@example.com"},
    )
    assert a2.email == "owner@example.com"
    assert a2.source == "env_user_email"

    # Explicit actor_user_id and mismatched actor_email/actor_user_id edge paths
    a1_uid = resolve_stdio_actor(
        store,
        env={"APP_ENV": "development"},
        actor_user_id=owner_user["user_id"],
    )
    assert a1_uid.source == "arg_user_id"
    with pytest.raises(MCPError) as mismatch_args:
        resolve_stdio_actor(
            store,
            env={"APP_ENV": "development"},
            actor_email="owner@example.com",
            actor_user_id=str(uuid.uuid4()),
        )
    assert mismatch_args.value.code == MCPErrorCode.PERMISSION_DENIED

    # Tier 3: MACY_ACTOR_USER_ID
    a3 = resolve_stdio_actor(
        store,
        env={"APP_ENV": "development", "MACY_ACTOR_USER_ID": str(owner_user["user_id"])},
    )
    assert a3.email == "owner@example.com"
    assert a3.source == "env_actor_user_id"

    # Tier 4: MACY_TEST_USER_EMAIL allowed in dev, forbidden in staging/prod
    a4 = resolve_stdio_actor(
        store,
        env={"APP_ENV": "development", "MACY_TEST_USER_EMAIL": "owner@example.com"},
    )
    assert a4.email == "owner@example.com"
    assert a4.source == "env_test_email"

    for restricted in ("staging", "production", "prod"):
        with pytest.raises(FakeIdentityForbiddenError):
            resolve_stdio_actor(
                store,
                env={"APP_ENV": restricted, "MACY_TEST_USER_EMAIL": "owner@example.com"},
            )

    # Tier 5: workspace owner fallback only when explicitly enabled in non-prod
    with pytest.raises(MCPError) as denied_no_flag:
        resolve_stdio_actor(
            store,
            env={"APP_ENV": "development"},
            workspace_id=ws_demo,
            allow_owner_fallback=False,
        )
    assert denied_no_flag.value.code == MCPErrorCode.AUTH_REQUIRED

    a5 = resolve_stdio_actor(
        store,
        env={"APP_ENV": "development"},
        workspace_id=ws_demo,
        allow_owner_fallback=True,
    )
    assert a5.email == "owner@example.com"
    assert a5.source == "owner_fallback"

    for restricted in ("staging", "production", "prod"):
        with pytest.raises(MCPError) as denied_prod:
            resolve_stdio_actor(
                store,
                env={"APP_ENV": restricted},
                workspace_id=ws_demo,
                allow_owner_fallback=True,
            )
        assert denied_prod.value.code in (
            MCPErrorCode.AUTH_REQUIRED,
            MCPErrorCode.PERMISSION_DENIED,
        )

    with pytest.raises(MCPError) as ownerless_denied:
        resolve_stdio_actor(
            store,
            env={"APP_ENV": "development"},
            workspace_id=uuid.uuid4(),
            allow_owner_fallback=True,
        )
    assert ownerless_denied.value.code == MCPErrorCode.AUTH_REQUIRED


def test_non_member_permission_denied(db_conn: psycopg.Connection[Any]) -> None:
    from engine.mcp.auth import authorize_workspace_access, resolve_stdio_actor
    from engine.mcp.errors import MCPError, MCPErrorCode
    from engine.mcp.mock_store import MockStore
    from engine.mcp.server import ServiceStoreAdapter

    # Mock store cross-tenant refusal (owner@example.com cannot access lumina)
    store = MockStore.create_default({"APP_ENV": "development"})
    actor = resolve_stdio_actor(
        store, env={"APP_ENV": "development"}, actor_email="owner@example.com"
    )
    lumina_id = store.get_workspace_id_by_slug("lumina")
    with pytest.raises(MCPError) as mock_denied:
        authorize_workspace_access(store, actor=actor, workspace_id=lumina_id)
    assert mock_denied.value.code == MCPErrorCode.PERMISSION_DENIED

    # Real Postgres services adapter cross-tenant refusal
    alice_email = f"mcp-auth-a-{uuid.uuid4().hex[:6]}@example.com"
    bob_email = f"mcp-auth-b-{uuid.uuid4().hex[:6]}@example.com"
    ws_alice = _seed_workspace_fixture(
        db_conn, tag="ma", owner_email=alice_email, with_campaign=False
    )
    ws_bob = _seed_workspace_fixture(db_conn, tag="mb", owner_email=bob_email, with_campaign=False)

    svc_adapter = ServiceStoreAdapter()
    assert svc_adapter.check_connectivity()
    assert svc_adapter.get_user_by_id(ws_bob["owner_id"]) is not None
    assert svc_adapter.get_workspace_owner_identity(workspace_id=ws_bob["workspace_id"]) is not None
    assert any(
        w["workspace_id"] == ws_bob["workspace_id"]
        for w in svc_adapter.list_accessible_workspaces(user_id=ws_bob["owner_id"])
    )
    assert (
        svc_adapter.get_workspace_status(
            workspace_id=ws_bob["workspace_id"],
            user_id=ws_bob["owner_id"],
        )["slug"]
        == ws_bob["slug"]
    )
    assert store.check_connectivity() == "mock"
    with pytest.raises(KeyError, match="missing-ws"):
        store.get_workspace_id_by_slug("missing-ws")

    bob_actor = resolve_stdio_actor(
        svc_adapter,
        env={"APP_ENV": "development"},
        actor_email=bob_email,
    )
    with pytest.raises(MCPError) as db_denied:
        authorize_workspace_access(
            svc_adapter,
            actor=bob_actor,
            workspace_id=ws_alice["workspace_id"],
        )
    assert db_denied.value.code == MCPErrorCode.PERMISSION_DENIED
    assert (
        authorize_workspace_access(
            svc_adapter, actor=bob_actor, workspace_id=ws_bob["workspace_id"]
        )
        == "owner"
    )


def test_error_redaction_and_sentry_capture() -> None:
    from unittest.mock import patch

    from engine.auth import FakeIdentityForbiddenError
    from engine.mcp.errors import MCPError, MCPErrorCode, format_exception_error

    err = MCPError(
        MCPErrorCode.INTERNAL_ERROR,
        "Bad postgres://app:secretpw@db:5432/macy Bearer super-secret-jwt",
    )
    as_dict = err.to_dict()
    assert "secretpw" not in as_dict["message"]
    assert "super-secret-jwt" not in as_dict["message"]
    assert "Bearer [REDACTED]" in as_dict["message"]

    assert format_exception_error(err) == as_dict
    assert format_exception_error(FakeIdentityForbiddenError("fake id"))["error"] == (
        MCPErrorCode.AUTH_REQUIRED.value
    )
    assert format_exception_error(PermissionError("denied"))["error"] == (
        MCPErrorCode.PERMISSION_DENIED.value
    )
    for code in (
        MCPErrorCode.NOT_FOUND,
        MCPErrorCode.INVALID_STATE,
        MCPErrorCode.BUDGET_CAP_EXCEEDED,
    ):
        assert MCPError(code, "domain msg").to_dict()["error"] == code.value

    with patch("engine.observability.capture_exception") as mock_cap:
        res = format_exception_error(
            RuntimeError("Boom Bearer live-tok-999"),
            workspace_id="ws-err",
            run_id="run-err",
            mcp_tool="macy_list_workspaces",
        )
        assert res["error"] == MCPErrorCode.INTERNAL_ERROR.value
        assert "live-tok-999" not in res["message"]
        mock_cap.assert_called_once()
        assert mock_cap.call_args.kwargs["mcp_tool"] == "macy_list_workspaces"
