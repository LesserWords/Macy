import ast
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgres://app:app_secret@localhost:5432/macy?sslmode=disable"
)


def extract_sql_queries_from_ast(source: str) -> list[tuple[str, str, int]]:
    """
    Parses Python source and returns tuples of (function_name, sql_string, line_number)
    for string constants that look like SQL statements (SELECT, INSERT, UPDATE, DELETE).
    """
    tree = ast.parse(source)
    sql_queries: list[tuple[str, str, int]] = []

    class SQLVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.current_function = "<module>"

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            prev = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = prev

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            prev = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = prev

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str):
                val = node.value.strip()
                first_word = val.split()[0].upper() if val.split() else ""
                if first_word in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                    sql_queries.append((self.current_function, val, node.lineno))
            self.generic_visit(node)

    SQLVisitor().visit(tree)
    return sql_queries


GLOBAL_EXEMPT_FUNCTIONS = {
    # Functions operating on the global 'users' table prior to workspace binding
    "get_user_by_email",
    "get_user_auth_by_email",
    "get_user_by_id",
    "create_or_get_user",
    "set_user_password_hash",
    "update_user_ui_locale",
    "anonymise_user_record",
    "get_current_db_role",
    # Functions operating on the global cross-workspace keyword cache (ADR-015, F11)
    "get_fresh_keywords_from_cache",
    "upsert_keyword_cache",
}


def check_tenant_scoping_ast(source: str) -> list[str]:
    """
    Checks that every SQL query string contains 'workspace_id'.
    Returns a list of violation messages.
    """
    queries = extract_sql_queries_from_ast(source)
    violations: list[str] = []
    for func_name, query, lineno in queries:
        if func_name in GLOBAL_EXEMPT_FUNCTIONS:
            continue
        if "workspace_id" not in query.lower():
            violations.append(
                f"Query in function '{func_name}' at line {lineno} lacks 'workspace_id': "
                f"{query[:50]}..."
            )
    return violations


def test_ast_check_detects_unscoped_planted_query() -> None:
    planted_code = """
def leak_campaigns(conn):
    cur = conn.cursor()
    cur.execute("SELECT id, name, market FROM campaigns WHERE status = 'active'")
    return cur.fetchall()

def leak_runs(conn, run_id):
    cur = conn.cursor()
    cur.execute("UPDATE runs SET status = 'failed' WHERE id = %(run_id)s")
"""
    violations = check_tenant_scoping_ast(planted_code)
    assert len(violations) == 2
    assert "leak_campaigns" in violations[0]
    assert "leak_runs" in violations[1]


def test_engine_db_ast_scoping() -> None:
    db_path = Path("engine/db.py")
    assert db_path.exists(), "engine/db.py must exist"
    source = db_path.read_text(encoding="utf-8")
    violations = check_tenant_scoping_ast(source)
    assert not violations, f"AST tenant-scoping violations found: {violations}"


def test_multi_tenant_data_isolation(db_conn: psycopg.Connection[Any]) -> None:
    from datetime import datetime, timedelta

    from engine import db

    # Setup a user
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%s, 'Tenant User', 'pt-BR')
            RETURNING id
            """,
            (f"tenant-{datetime.now(UTC).timestamp()}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]

    now = datetime.now(UTC)
    ws_1_id = db.create_workspace(
        db_conn,
        slug=f"ws1-{int(now.timestamp())}",
        name="Workspace 1",
        industry_pack="general",
    )
    ws_2_id = db.create_workspace(
        db_conn,
        slug=f"ws2-{int(now.timestamp())}",
        name="Workspace 2",
        industry_pack="general",
    )

    # Workspace 1 records
    c1_id = db.create_campaign(
        db_conn,
        workspace_id=ws_1_id,
        name="Campaign W1",
        locale="pt-BR",
        market="BR",
        articles_per_week=3,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("15.00"),
        config={"theme": "sunglasses"},
        config_schema_version=1,
        created_by=user_id,
    )
    r1_id = db.create_run(
        db_conn,
        workspace_id=ws_1_id,
        campaign_id=c1_id,
        trigger="scheduled",
        window_start=now,
        window_end=now + timedelta(days=7),
        run_cap_usd=Decimal("15.00"),
        code_version="v1.0.0",
        pack_version="general-v1",
        created_by=user_id,
    )
    e1_id = db.insert_event(
        db_conn,
        workspace_id=ws_1_id,
        event_type="campaign.created",
        entity_type="campaign",
        entity_id=c1_id,
        payload={"campaign_id": str(c1_id)},
    )
    u1_id = db.record_usage(
        db_conn,
        workspace_id=ws_1_id,
        stage="research",
        kind="llm",
        vendor="gemini",
        cost_usd=Decimal("0.045000"),
        run_id=r1_id,
        model="gemini-2.5-flash",
    )

    # Workspace 2 records
    c2_id = db.create_campaign(
        db_conn,
        workspace_id=ws_2_id,
        name="Campaign W2",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 10 * * 2",
        monthly_cap_usd=Decimal("200.00"),
        run_cap_usd=Decimal("20.00"),
        config={"theme": "contact-lenses"},
        config_schema_version=1,
        created_by=user_id,
    )
    r2_id = db.create_run(
        db_conn,
        workspace_id=ws_2_id,
        campaign_id=c2_id,
        trigger="manual",
        window_start=now,
        window_end=now + timedelta(days=7),
        run_cap_usd=Decimal("20.00"),
        code_version="v1.0.0",
        pack_version="general-v1",
        created_by=user_id,
    )
    e2_id = db.insert_event(
        db_conn,
        workspace_id=ws_2_id,
        event_type="campaign.created",
        entity_type="campaign",
        entity_id=c2_id,
        payload={"campaign_id": str(c2_id)},
    )
    u2_id = db.record_usage(
        db_conn,
        workspace_id=ws_2_id,
        stage="research",
        kind="llm",
        vendor="gemini",
        cost_usd=Decimal("0.060000"),
        run_id=r2_id,
        model="gemini-2.5-flash",
    )

    # Test isolation: Workspace 1 sees only W1 data
    w1_campaigns = db.list_campaigns(db_conn, workspace_id=ws_1_id)
    assert any(c["id"] == c1_id for c in w1_campaigns)
    assert not any(c["id"] == c2_id for c in w1_campaigns)

    w1_runs = db.list_runs(db_conn, workspace_id=ws_1_id)
    assert any(r["id"] == r1_id for r in w1_runs)
    assert not any(r["id"] == r2_id for r in w1_runs)

    w1_events = db.list_events(db_conn, workspace_id=ws_1_id)
    assert any(e["id"] == e1_id for e in w1_events)
    assert not any(e["id"] == e2_id for e in w1_events)

    w1_usage = db.list_usage(db_conn, workspace_id=ws_1_id)
    assert any(u["id"] == u1_id for u in w1_usage)
    assert not any(u["id"] == u2_id for u in w1_usage)


def _seed_tenant_brief_bundle(
    conn: psycopg.Connection[Any],
    *,
    ws_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    tag: str,
) -> dict[str, Any]:
    from engine import db

    brand = db.save_brand_profile(
        conn,
        workspace_id=ws_id,
        schema_version=1,
        payload={"voice": f"V-{tag}"},
        created_by=user_id,
    )
    src = db.create_source(
        conn,
        workspace_id=ws_id,
        industry_pack="general",
        name=f"Data Source {tag}",
        domain=f"{tag}.example.com",
        url=f"https://{tag}.example.com",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="data",
        locales=["pt-BR"],
        topics=["optica"],
        enabled=True,
    )
    doc = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=src["id"],
        url=f"https://{tag}.example.com/a",
        canonical_url=f"https://{tag}.example.com/a",
        title=f"Doc {tag}",
        date_status="verified",
        fetch_status="ok",
        meta={"source_role": "data"},
    )
    rr = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={"headline": f"H-{tag}"},
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores={},
        total=Decimal("80.00"),
        funnel_stage="mofu",
        confidence="high",
        angle=f"Angle {tag}",
        status="approved",
        decided_by=user_id,
        decided_at=datetime.now(UTC),
    )
    brief = db.create_next_brief_version(
        conn,
        workspace_id=ws_id,
        opportunity_id=opp["id"],
        brand_profile_id=brand["id"],
        prompt_version="brief.md:v1",
        keywords=[],
        schema_version=1,
        payload={"topic": f"Topic {tag}"},
    )
    return {"doc": doc, "opp": opp, "brief": brief}


def test_multi_tenant_brief_and_evidence_isolation(db_conn: psycopg.Connection[Any]) -> None:
    """Verify M3-02 brief and evidence queries strictly enforce workspace_id tenant isolation."""
    from datetime import datetime, timedelta

    from engine import db

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%s, 'Brief Tenant User', 'pt-BR')
            RETURNING id
            """,
            (f"brief-tenant-{uuid.uuid4().hex[:8]}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]

    now = datetime.now(UTC)
    ws_1_id = db.create_workspace(
        db_conn, slug=f"br-w1-{uuid.uuid4().hex[:6]}", name="BW1", industry_pack="general"
    )
    ws_2_id = db.create_workspace(
        db_conn, slug=f"br-w2-{uuid.uuid4().hex[:6]}", name="BW2", industry_pack="general"
    )
    c1 = db.create_campaign(
        db_conn,
        workspace_id=ws_1_id,
        name="C1",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    c2 = db.create_campaign(
        db_conn,
        workspace_id=ws_2_id,
        name="C2",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    r1 = db.create_run(
        db_conn,
        workspace_id=ws_1_id,
        campaign_id=c1,
        trigger="manual",
        window_start=now,
        window_end=now + timedelta(days=7),
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_id,
    )
    r2 = db.create_run(
        db_conn,
        workspace_id=ws_2_id,
        campaign_id=c2,
        trigger="manual",
        window_start=now,
        window_end=now + timedelta(days=7),
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_id,
    )

    b1 = _seed_tenant_brief_bundle(db_conn, ws_id=ws_1_id, run_id=r1, user_id=user_id, tag="w1")
    b2 = _seed_tenant_brief_bundle(db_conn, ws_id=ws_2_id, run_id=r2, user_id=user_id, tag="w2")

    assert db.get_brief(db_conn, workspace_id=ws_1_id, brief_id=b2["brief"]["id"]) is None
    assert (
        db.get_latest_brief_for_opportunity(
            db_conn, workspace_id=ws_1_id, opportunity_id=b2["opp"]["id"]
        )
        is None
    )
    assert (
        db.get_opportunity_context(db_conn, workspace_id=ws_1_id, opportunity_id=b2["opp"]["id"])
        is None
    )
    assert [b["id"] for b in db.list_briefs(db_conn, workspace_id=ws_1_id)] == [b1["brief"]["id"]]
    assert set(db.list_latest_briefs_for_run(db_conn, workspace_id=ws_1_id, run_id=r1).keys()) == {
        b1["opp"]["id"]
    }
    assert db.list_latest_briefs_for_run(db_conn, workspace_id=ws_1_id, run_id=r2) == {}
    assert [d["id"] for d in db.list_evidence_documents(db_conn, workspace_id=ws_1_id)] == [
        b1["doc"]["id"]
    ]
    assert db.get_brief(db_conn, workspace_id=ws_2_id, brief_id=b1["brief"]["id"]) is None
    assert [d["id"] for d in db.list_evidence_documents(db_conn, workspace_id=ws_2_id)] == [
        b2["doc"]["id"]
    ]

    # Verify M4-01 draft and draft_claims queries enforce workspace_id tenant isolation
    d1 = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws_1_id,
        brief_id=b1["brief"]["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload={"title": "Draft W1"},
        claims=[
            {
                "kind": "fact",
                "text": "Fact W1",
                "document_id": b1["doc"]["id"],
            }
        ],
    )
    d2 = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws_2_id,
        brief_id=b2["brief"]["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload={"title": "Draft W2"},
        claims=[
            {
                "kind": "fact",
                "text": "Fact W2",
                "document_id": b2["doc"]["id"],
            }
        ],
    )

    assert db.get_draft(db_conn, workspace_id=ws_1_id, draft_id=d2["id"]) is None
    assert (
        db.get_latest_draft_for_brief(db_conn, workspace_id=ws_1_id, brief_id=b2["brief"]["id"])
        is None
    )
    assert (
        db.get_brief_writing_context(db_conn, workspace_id=ws_1_id, brief_id=b2["brief"]["id"])
        is None
    )
    assert [d["id"] for d in db.list_drafts(db_conn, workspace_id=ws_1_id)] == [d1["id"]]
    assert db.list_draft_claims(db_conn, workspace_id=ws_1_id, draft_id=d2["id"]) == []
    assert len(db.list_draft_claims(db_conn, workspace_id=ws_1_id, draft_id=d1["id"])) == 1

    # Verify M4-03 quality_reports, draft transitions, and pool-return enforce workspace_id
    qr1 = db.upsert_quality_report(
        db_conn,
        workspace_id=ws_1_id,
        draft_id=d1["id"],
        verdict="pass",
        score=Decimal("88.50"),
        checks={"language_match": {"passed": True}},
        findings=[],
        evaluator_model="fake-eval",
        prompt_version="evaluate.md:v1",
    )
    assert qr1["draft_id"] == d1["id"]
    assert db.get_quality_report(db_conn, workspace_id=ws_1_id, draft_id=d2["id"]) is None
    assert db.get_quality_report(db_conn, workspace_id=ws_2_id, draft_id=d1["id"]) is None
    assert [r["draft_id"] for r in db.list_quality_reports(db_conn, workspace_id=ws_1_id)] == [
        d1["id"]
    ]
    assert db.get_draft_evaluation_context(db_conn, workspace_id=ws_1_id, draft_id=d2["id"]) is None
    assert (
        db.transition_draft_status(
            db_conn,
            workspace_id=ws_1_id,
            draft_id=d2["id"],
            expected_statuses=("draft",),
            new_status="evaluating",
        )
        is None
    )
    assert (
        db.return_opportunity_to_pool(
            db_conn, workspace_id=ws_1_id, opportunity_id=b2["opp"]["id"], note="cross-tenant"
        )
        is None
    )
    assert (
        db.get_max_draft_revision_for_opportunity(
            db_conn, workspace_id=ws_1_id, opportunity_id=b2["opp"]["id"]
        )
        is None
    )
    _assert_draft_review_isolation(
        db_conn,
        ws_1_id=ws_1_id,
        ws_2_id=ws_2_id,
        ids={"b1_opp": b1["opp"]["id"], "b2_opp": b2["opp"]["id"], "d1": d1["id"], "d2": d2["id"]},
        runs=(r1, r2),
        user_id=user_id,
    )


def _assert_draft_review_isolation(  # noqa: PLR0913
    db_conn: psycopg.Connection[Any],
    *,
    ws_1_id: uuid.UUID,
    ws_2_id: uuid.UUID,
    ids: dict[str, uuid.UUID],
    runs: tuple[uuid.UUID, uuid.UUID],
    user_id: uuid.UUID,
) -> None:
    from engine import db

    # Verify M4-04 draft review queue, latest drafts, revision history, breakdown, decide_draft
    r1, r2 = runs
    db.transition_draft_status(
        db_conn,
        workspace_id=ws_1_id,
        draft_id=ids["d1"],
        expected_statuses=("draft",),
        new_status="pass",
    )
    db.transition_draft_status(
        db_conn,
        workspace_id=ws_2_id,
        draft_id=ids["d2"],
        expected_statuses=("draft",),
        new_status="pass",
    )
    assert [r["id"] for r in db.list_drafts_for_review(db_conn, workspace_id=ws_1_id)] == [
        ids["d1"]
    ]
    assert set(db.list_latest_drafts_for_run(db_conn, workspace_id=ws_1_id, run_id=r1).keys()) == {
        ids["b1_opp"]
    }
    assert db.list_latest_drafts_for_run(db_conn, workspace_id=ws_1_id, run_id=r2) == {}
    assert [
        h["id"]
        for h in db.list_draft_revision_history_for_opportunity(
            db_conn, workspace_id=ws_1_id, opportunity_id=ids["b1_opp"]
        )
    ] == [ids["d1"]]
    assert (
        db.list_draft_revision_history_for_opportunity(
            db_conn, workspace_id=ws_1_id, opportunity_id=ids["b2_opp"]
        )
        == []
    )
    assert db.get_run_usage_breakdown(db_conn, workspace_id=ws_1_id, run_id=r2) == []
    assert (
        db.decide_draft(
            db_conn,
            workspace_id=ws_1_id,
            draft_id=ids["d2"],
            decision="approved",
            user_id=user_id,
            decided_at=datetime.now(UTC),
            note="cross-tenant attempt",
        )
        is None
    )

    # M4-05 draft publications isolation
    pub1 = db.upsert_draft_publication(
        db_conn,
        workspace_id=ws_1_id,
        draft_id=ids["d1"],
        destination="markdown_export",
        mode="draft",
        status="published",
        title="WS1 Draft Article",
        external_id=f"md-{ids['d1']}",
        url="https://ws1.example.com/draft",
        published_at=datetime.now(UTC),
        response={"mode": "draft", "is_live": False},
    )
    assert pub1["draft_id"] == ids["d1"]
    assert (
        db.get_publication_for_draft(
            db_conn,
            workspace_id=ws_2_id,
            draft_id=ids["d1"],
        )
        is None
    )
    assert (
        db.list_publications_for_draft(
            db_conn,
            workspace_id=ws_2_id,
            draft_id=ids["d1"],
        )
        == []
    )


def test_mcp_gate1_cross_tenant_refused(db_conn: psycopg.Connection[Any]) -> None:
    """MCP Gate 1 services and tools refuse cross-tenant workspaces, runs, and opportunities."""
    import asyncio
    import json
    from datetime import timedelta

    import pytest

    from engine import db, services
    from engine.mcp.errors import MCPErrorCode
    from engine.mcp.server import create_mcp_server

    now = datetime.now(UTC)
    user_a = db.create_or_get_user(
        db_conn, email=f"mcp-xt-a-{uuid.uuid4().hex[:6]}@example.com", name="Tenant A"
    )
    user_b = db.create_or_get_user(
        db_conn, email=f"mcp-xt-b-{uuid.uuid4().hex[:6]}@example.com", name="Tenant B"
    )
    ws_a = db.create_workspace(
        db_conn,
        slug=f"xt-a-{uuid.uuid4().hex[:6]}",
        name="XT A",
        industry_pack="general",
    )
    ws_b = db.create_workspace(
        db_conn,
        slug=f"xt-b-{uuid.uuid4().hex[:6]}",
        name="XT B",
        industry_pack="general",
    )
    db.create_membership(db_conn, workspace_id=ws_a, user_id=user_a["user_id"], role="owner")
    db.create_membership(db_conn, workspace_id=ws_b, user_id=user_b["user_id"], role="owner")
    camp_a = db.create_campaign(
        db_conn,
        workspace_id=ws_a,
        name="Camp A",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        config={},
        config_schema_version=1,
        created_by=user_a["user_id"],
    )
    camp_b = db.create_campaign(
        db_conn,
        workspace_id=ws_b,
        name="Camp B",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        config={},
        config_schema_version=1,
        created_by=user_b["user_id"],
    )
    run_b = db.create_run(
        db_conn,
        workspace_id=ws_b,
        campaign_id=camp_b,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_b["user_id"],
    )
    b_bundle = _seed_tenant_brief_bundle(
        db_conn, ws_id=ws_b, run_id=run_b, user_id=user_b["user_id"], tag=uuid.uuid4().hex[:4]
    )
    doc_b_scored = db.upsert_document(
        db_conn,
        workspace_id=ws_b,
        source_id=None,
        url=f"https://xt-b-{uuid.uuid4().hex[:4]}.example.com/scored",
        canonical_url=f"https://xt-b-{uuid.uuid4().hex[:4]}.example.com/scored",
        title="B Scored",
        published_at=now,
        date_status="verified",
        fetch_status="ok",
    )
    rr_b_scored = db.upsert_research_record(
        db_conn,
        workspace_id=ws_b,
        run_id=run_b,
        document_id=doc_b_scored["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "B Scored"},
    )
    opp_b_scored = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_b,
        run_id=run_b,
        record_id=rr_b_scored["id"],
        scores={},
        total=Decimal("85.00"),
        funnel_stage="mofu",
        confidence="high",
        angle="B angle",
        status="scored",
    )

    assert b_bundle["opp"]["status"] == "approved"
    with pytest.raises(PermissionError, match="not a member"):
        services.list_topics_for_approval(db_conn, workspace_id=ws_b, user_id=user_a["user_id"])
    with pytest.raises(PermissionError, match="not a member"):
        services.run_mcp_research(
            db_conn, workspace_id=ws_b, user_id=user_a["user_id"], query="test"
        )
    with pytest.raises(PermissionError, match="not a member"):
        services.score_mcp_topics(
            db_conn, workspace_id=ws_b, user_id=user_a["user_id"], run_id=run_b
        )

    assert camp_a is not None
    bundle = create_mcp_server(mock=False, transport="stdio")

    def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        res = asyncio.run(bundle.mcp.call_tool(name, args))
        parsed: dict[str, Any] = json.loads(res.content[0].text)  # type: ignore[union-attr]
        return parsed

    assert (
        _call(
            "macy_list_topics_awaiting_approval",
            {"workspace_id": str(ws_b), "actor_user_id": str(user_a["user_id"])},
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )
    assert (
        _call(
            "macy_decide_topic",
            {
                "workspace_id": str(ws_b),
                "opportunity_id": str(opp_b_scored["id"]),
                "decision": "approved",
                "actor_user_id": str(user_a["user_id"]),
            },
        )["error"]
        == MCPErrorCode.PERMISSION_DENIED.value
    )
    # Cross-tenant opportunity ID submitted under caller's own workspace_id -> NOT_FOUND
    assert (
        _call(
            "macy_decide_topic",
            {
                "workspace_id": str(ws_a),
                "opportunity_id": str(opp_b_scored["id"]),
                "decision": "approved",
                "actor_user_id": str(user_a["user_id"]),
            },
        )["error"]
        == MCPErrorCode.NOT_FOUND.value
    )
    # Scored opp in WS B remains untouched
    loaded = db.get_opportunity(db_conn, workspace_id=ws_b, opportunity_id=opp_b_scored["id"])
    assert loaded is not None
    assert loaded["status"] == "scored"
