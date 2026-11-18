import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import db

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgres://app:app_secret@localhost:5432/macy?sslmode=disable"
)


def test_get_connection_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    conn = db.get_connection()
    assert conn is not None
    conn.close()


def test_workspace_crud(db_conn: psycopg.Connection[Any]) -> None:
    slug = f"ws-crud-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="CRUD Workspace",
        industry_pack="general",
        timezone="America/Sao_Paulo",
    )
    assert ws_id is not None

    ws = db.get_workspace(db_conn, workspace_id=ws_id)
    assert ws is not None
    assert ws["workspace_id"] == ws_id
    assert ws["slug"] == slug
    assert ws["name"] == "CRUD Workspace"
    assert ws["industry_pack"] == "general"

    # Non-existent workspace lookup returns None
    missing_ws = db.get_workspace(db_conn, workspace_id=uuid.uuid4())
    assert missing_ws is None


def test_runs_filtering_and_status_transitions(db_conn: psycopg.Connection) -> None:
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%s, 'Runs Tester', 'pt-BR')
            RETURNING id
            """,
            (f"runner-{uuid.uuid4().hex[:8]}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]

    now = datetime.now(UTC)
    ws_id = db.create_workspace(
        db_conn,
        slug=f"runs-{uuid.uuid4().hex[:8]}",
        name="Runs Workspace",
        industry_pack="general",
    )

    campaign_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Run Transitions Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=5,
        schedule_cron="0 12 * * *",
        monthly_cap_usd=Decimal("300.00"),
        run_cap_usd=Decimal("30.00"),
        config={"theme": "frames"},
        config_schema_version=1,
        created_by=user_id,
    )

    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=campaign_id,
        trigger="manual",
        window_start=now,
        window_end=now + timedelta(days=1),
        run_cap_usd=Decimal("30.00"),
        code_version="v1.0.0",
        pack_version="general-v1",
        created_by=user_id,
    )

    # Filter runs by campaign_id
    runs_for_campaign = db.list_runs(db_conn, workspace_id=ws_id, campaign_id=campaign_id)
    assert len(runs_for_campaign) == 1
    assert runs_for_campaign[0]["id"] == run_id

    # Filter runs for other campaign returns empty list
    other_campaign_id = uuid.uuid4()
    assert db.list_runs(db_conn, workspace_id=ws_id, campaign_id=other_campaign_id) == []

    # Successful state transition: queued -> running
    updated = db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="queued",
        new_status="running",
    )
    assert updated is True

    # Failed state transition: expected_status 'queued' fails because status is now 'running'
    stale_update = db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="queued",
        new_status="completed",
    )
    assert stale_update is False

    # Transition to completed
    completed = db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="running",
        new_status="completed",
    )
    assert completed is True


def test_usage_ledger_filtering(db_conn: psycopg.Connection) -> None:
    ws_id = db.create_workspace(
        db_conn,
        slug=f"usage-{uuid.uuid4().hex[:8]}",
        name="Usage Workspace",
        industry_pack="general",
    )

    u1_id = db.record_usage(
        db_conn,
        workspace_id=ws_id,
        stage="research",
        kind="api",
        vendor="tavily",
        cost_usd=Decimal("0.010000"),
        generation_id="gen-123",
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
    )

    # General list
    usage_list = db.list_usage(db_conn, workspace_id=ws_id)
    assert len(usage_list) >= 1
    assert any(u["id"] == u1_id for u in usage_list)

    # Run-specific list returns empty for random run_id
    run_filtered = db.list_usage(db_conn, workspace_id=ws_id, run_id=uuid.uuid4())
    assert run_filtered == []


def test_events_with_actor(db_conn: psycopg.Connection) -> None:
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%s, 'Actor User', 'pt-BR')
            RETURNING id
            """,
            (f"actor-{uuid.uuid4().hex[:8]}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]

    ws_id = db.create_workspace(
        db_conn,
        slug=f"event-{uuid.uuid4().hex[:8]}",
        name="Event Workspace",
        industry_pack="general",
    )

    event_id = db.insert_event(
        db_conn,
        workspace_id=ws_id,
        event_type="workspace.updated",
        entity_type="workspace",
        entity_id=ws_id,
        payload={"action": "test"},
        actor_user_id=user_id,
    )
    assert event_id is not None

    events = db.list_events(db_conn, workspace_id=ws_id, limit=10)
    assert len(events) >= 1
    assert events[0]["id"] == event_id
    assert events[0]["actor_user_id"] == user_id


def test_insert_failure_raises_runtime_error() -> None:
    from unittest.mock import MagicMock

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = None

    with pytest.raises(RuntimeError, match="Failed to insert workspace"):
        db.create_workspace(mock_conn, slug="slug", name="Name", industry_pack="pack")

    with pytest.raises(RuntimeError, match="Failed to insert campaign"):
        db.create_campaign(
            mock_conn,
            workspace_id=uuid.uuid4(),
            name="Name",
            locale="pt-BR",
            market="BR",
            articles_per_week=1,
            schedule_cron="* * * * *",
            monthly_cap_usd=Decimal("10"),
            run_cap_usd=Decimal("1"),
            config={},
            config_schema_version=1,
            created_by=uuid.uuid4(),
        )

    with pytest.raises(RuntimeError, match="Failed to insert run"):
        db.create_run(
            mock_conn,
            workspace_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            trigger="manual",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            run_cap_usd=Decimal("1"),
            code_version="v1",
            pack_version="v1",
        )

    with pytest.raises(RuntimeError, match="Failed to insert event"):
        db.insert_event(
            mock_conn,
            workspace_id=uuid.uuid4(),
            event_type="test",
            entity_type="test",
            entity_id=uuid.uuid4(),
            payload={},
        )

    with pytest.raises(RuntimeError, match="Failed to insert usage ledger entry"):
        db.record_usage(
            mock_conn,
            workspace_id=uuid.uuid4(),
            stage="test",
            kind="llm",
            vendor="test",
            cost_usd=Decimal("0"),
        )

    with pytest.raises(RuntimeError, match="Failed to create user"):
        db.create_or_get_user(mock_conn, email="test@example.com")

    with pytest.raises(RuntimeError, match="Failed to create membership"):
        db.create_membership(mock_conn, workspace_id=uuid.uuid4(), user_id=uuid.uuid4())

    with pytest.raises(RuntimeError, match="Failed to upsert document"):
        db.upsert_document(
            mock_conn,
            workspace_id=uuid.uuid4(),
            url="https://ex.com/a",
            canonical_url="https://ex.com/a",
            title="Title",
            date_status="verified",
            fetch_status="ok",
        )

    with pytest.raises(RuntimeError, match="Failed to upsert research record"):
        db.upsert_research_record(
            mock_conn,
            workspace_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status="extracted",
            schema_version=1,
            payload={},
        )

    with pytest.raises(RuntimeError, match="Failed to upsert opportunity"):
        db.upsert_opportunity(
            mock_conn,
            workspace_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            record_id=uuid.uuid4(),
            scores={},
            total=80.0,
            angle="Angle",
            funnel_stage="mofu",
            confidence="high",
            rank=1,
        )


def _create_test_run_fixture(
    db_conn: psycopg.Connection[Any], prefix: str
) -> tuple[uuid.UUID, uuid.UUID]:
    user = db.create_or_get_user(
        db_conn, email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com", name=f"{prefix} Tester"
    )
    ws_id = db.create_workspace(
        db_conn,
        name=f"{prefix} Workspace",
        slug=f"{prefix}-ws-{uuid.uuid4().hex[:8]}",
        industry_pack="general",
    )
    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name=f"{prefix} Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("5.00"),
        config={"pillars": []},
        config_schema_version=1,
        created_by=user["user_id"],
    )
    w_start = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    w_end = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=w_start,
        window_end=w_end,
        run_cap_usd=Decimal("75.00"),
        code_version="fixture-v1",
        pack_version="fixture-p1",
        created_by=user["user_id"],
    )
    return ws_id, run_id


def test_documents_and_research_records_crud(db_conn: psycopg.Connection[Any]) -> None:
    ws_id, run_id = _create_test_run_fixture(db_conn, prefix="doc-crud")
    now = datetime.now(UTC)

    canonical = f"https://opticanet.com.br/news/{uuid.uuid4().hex[:8]}"
    doc = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url=canonical + "?utm_source=rss",
        canonical_url=canonical,
        title="Initial Title",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"hash1",
        lang="pt",
        published_at=now,
        http_status=200,
        extracted_text="Texto inicial",
        meta={"author": "Maria"},
    )
    assert doc["canonical_url"] == canonical
    assert doc["lang"] == "pt"
    assert doc["meta"] == {"author": "Maria"}

    # Upsert on same canonical_url reuses row id and updates fields
    doc_updated = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url=canonical,
        canonical_url=canonical,
        title="Updated Title",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"hash2",
        lang="pt",
        published_at=now,
        http_status=200,
        extracted_text="Texto atualizado",
    )
    assert doc_updated["id"] == doc["id"]
    assert doc_updated["title"] == "Updated Title"

    fetched_by_url = db.get_document_by_canonical_url(
        db_conn, workspace_id=ws_id, canonical_url=canonical
    )
    assert fetched_by_url is not None
    assert fetched_by_url["id"] == doc["id"]

    fetched_by_id = db.get_document(db_conn, workspace_id=ws_id, document_id=doc["id"])
    assert fetched_by_id is not None
    assert fetched_by_id["title"] == "Updated Title"

    rr = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="new",
        schema_version=1,
        payload={"headline": "Updated Title"},
    )
    assert rr["status"] == "new"

    rr_extracted = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": "Updated Title", "facts": ["F1"]},
    )
    assert rr_extracted["id"] == rr["id"]
    assert rr_extracted["status"] == "extracted"

    all_rr = db.list_research_records(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(all_rr) == 1
    assert all_rr[0]["id"] == rr["id"]

    extracted_rr = db.list_research_records(
        db_conn, workspace_id=ws_id, run_id=run_id, status="extracted"
    )
    assert len(extracted_rr) == 1

    all_ws_rr = db.list_research_records(db_conn, workspace_id=ws_id)
    assert any(r["id"] == rr["id"] for r in all_ws_rr)


def test_opportunities_crud(db_conn: psycopg.Connection[Any]) -> None:
    """Test opportunity lifecycle and query functions (DATA-MODEL §8)."""
    ws_id, run_id = _create_test_run_fixture(db_conn, prefix="opp-crud")
    now = datetime.now(UTC)
    doc = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url=f"https://opticanet.com.br/opp/{uuid.uuid4().hex[:8]}",
        canonical_url=f"https://opticanet.com.br/opp/{uuid.uuid4().hex[:8]}",
        title="Sample Opp Document",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"hash_opp",
        lang="pt",
        published_at=now,
        http_status=200,
        extracted_text="Opp content",
    )
    rr = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Sample Opp Document"},
    )

    scores_payload = {
        "novelty": {"score": 8.0, "evidence": "New guidance", "is_estimate": False},
        "audience_fit": {"score": 9.0, "evidence": "Key for owners", "is_estimate": False},
        "brand_fit": {"score": 8.5, "evidence": "Aligns with voice", "is_estimate": False},
        "competitor_gap": {"score": 7.0, "evidence": "Underserved", "is_estimate": False},
        "trend": {"score": 6.0, "evidence": "Growing query", "is_estimate": True},
        "urgency": {"score": 5.0, "evidence": "Standard cadence", "is_estimate": False},
    }
    opp = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores=scores_payload,
        total=Decimal("78.00"),
        funnel_stage="mofu",
        confidence="high",
        angle="Clinical to commercial adoption",
        status="scored",
    )
    assert opp["run_id"] == run_id
    assert opp["record_id"] == rr["id"]
    assert opp["total"] == Decimal("78.00")
    assert opp["funnel_stage"] == "mofu"
    assert opp["confidence"] == "high"
    assert opp["status"] == "scored"
    assert opp["rank"] is None

    opp_updated = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores=scores_payload,
        total=Decimal("82.50"),
        funnel_stage="tofu",
        confidence="medium",
        angle="Updated angle for retail",
        status="scored",
    )
    assert opp_updated["id"] == opp["id"]
    assert opp_updated["total"] == Decimal("82.50")
    assert opp_updated["funnel_stage"] == "tofu"
    assert opp_updated["confidence"] == "medium"

    fetched_opp = db.get_opportunity(db_conn, workspace_id=ws_id, opportunity_id=opp["id"])
    assert fetched_opp is not None
    assert fetched_opp["id"] == opp["id"]
    assert fetched_opp["total"] == Decimal("82.50")

    ranked = db.update_opportunity_rank(
        db_conn, workspace_id=ws_id, opportunity_id=opp["id"], rank=1
    )
    assert ranked is not None
    assert ranked["rank"] == 1

    opps_run = db.list_opportunities(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(opps_run) == 1
    assert opps_run[0]["id"] == opp["id"]

    opps_status = db.list_opportunities(db_conn, workspace_id=ws_id, run_id=run_id, status="scored")
    assert len(opps_status) == 1

    opps_filtered_empty = db.list_opportunities(
        db_conn, workspace_id=ws_id, run_id=run_id, status="approved"
    )
    assert len(opps_filtered_empty) == 0

    opps_ws = db.list_opportunities(db_conn, workspace_id=ws_id)
    assert any(o["id"] == opp["id"] for o in opps_ws)

    found_ws = db.get_opportunity_workspace_id(db_conn, opportunity_id=opp["id"])
    assert found_ws == ws_id

    report_items = db.get_opportunity_report_items(
        db_conn, workspace_id=ws_id, run_id=run_id, limit=5
    )
    assert len(report_items) == 1
    assert report_items[0]["id"] == opp["id"]
    assert report_items[0]["original_url"] == doc["url"]
    assert report_items[0]["date_status"] == "verified"
    assert report_items[0]["record_payload"]["headline"] == "Sample Opp Document"

    other_ws = db.get_opportunity_report_items(db_conn, workspace_id=uuid.uuid4(), run_id=run_id)
    assert len(other_ws) == 0


def test_opportunity_decision_and_expiry_db(db_conn: psycopg.Connection[Any]) -> None:
    """Test decide_opportunity, count_approved_opportunities, and expire_previous_opportunities."""
    ws_id, run_id = _create_test_run_fixture(db_conn, prefix="opp-decide")
    doc = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url=f"https://opticanet.com.br/decide/{uuid.uuid4().hex[:8]}",
        canonical_url=f"https://opticanet.com.br/decide/{uuid.uuid4().hex[:8]}",
        title="Decide Doc",
        date_status="verified",
        fetch_status="ok",
    )
    rr = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Decide Doc"},
    )
    opp = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores={"novelty": {"score": 8.0, "evidence": "New"}},
        total=Decimal("78.00"),
        funnel_stage="mofu",
        confidence="high",
        angle="Angle 1",
        status="scored",
    )

    u_row = db.create_or_get_user(db_conn, email="approver-db@example.com")
    now_dt = datetime.now(UTC)
    decided = db.decide_opportunity(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp["id"],
        decision="approved",
        user_id=u_row["user_id"],
        decided_at=now_dt,
        note="Approved DB test",
    )
    assert decided is not None
    assert decided["status"] == "approved"
    assert decided["decided_by"] == u_row["user_id"]
    assert decided["decision_note"] == "Approved DB test"

    # Guarded transition returns None when not in 'scored' status
    retry_decide = db.decide_opportunity(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp["id"],
        decision="rejected",
        user_id=u_row["user_id"],
        decided_at=now_dt,
    )
    assert retry_decide is None

    # Count approved opportunities
    cnt = db.count_approved_opportunities(db_conn, workspace_id=ws_id, run_id=run_id)
    assert cnt == 1
    cnt_empty = db.count_approved_opportunities(db_conn, workspace_id=ws_id, run_id=uuid.uuid4())
    assert cnt_empty == 0

    # Expire previous opportunities
    doc2 = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url=f"https://opticanet.com.br/opp2/{uuid.uuid4().hex[:8]}",
        canonical_url=f"https://opticanet.com.br/opp2/{uuid.uuid4().hex[:8]}",
        title="Doc 2",
        date_status="verified",
        fetch_status="ok",
    )
    rr2 = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc2["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Doc 2"},
    )
    opp2 = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr2["id"],
        scores={"novelty": {"score": 7.0, "evidence": "Trend"}},
        total=Decimal("75.00"),
        funnel_stage="mofu",
        confidence="medium",
        angle="Angle 2",
        status="scored",
    )
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    camp_id = run_row["campaign_id"]

    dummy_next_run_id = uuid.uuid4()
    expired_cnt = db.expire_previous_opportunities(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        current_run_id=dummy_next_run_id,
    )
    assert expired_cnt >= 1
    opp2_refetched = db.get_opportunity(db_conn, workspace_id=ws_id, opportunity_id=opp2["id"])
    assert opp2_refetched is not None
    assert opp2_refetched["status"] == "expired"
