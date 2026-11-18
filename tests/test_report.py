"""Tests for Weekly Report compilation, capping, and event emission (F09 AC3-AC5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import db, services


def _seed_report_environment(
    conn: psycopg.Connection[Any],
    *,
    articles_per_week: int = 1,
    opportunity_count: int = 5,
    unverified_indices: tuple[int, ...] = (1,),
) -> tuple[uuid.UUID, uuid.UUID, list[dict[str, Any]]]:
    """Helper creating workspace, campaign with custom volume, run, documents, and opportunities."""
    ws_id = db.create_workspace(
        conn,
        name="Report Test Studio",
        slug=f"ws-rep-{uuid.uuid4().hex[:8]}",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        conn, email=f"rep-user-{uuid.uuid4().hex[:8]}@example.com", name="Report Lead"
    )
    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name="Weekly Digest Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=articles_per_week,
        schedule_cron="0 10 * * 1",
        monthly_cap_usd=Decimal("200.00"),
        run_cap_usd=Decimal("30.00"),
        config={},
        config_schema_version=1,
        created_by=user["user_id"],
    )
    now = datetime.now(UTC)
    run_kwargs: dict[str, Any] = {
        "workspace_id": ws_id,
        "campaign_id": camp_id,
        "trigger": "scheduled",
        "window_start": now - timedelta(days=14),
        "window_end": now,
        "run_cap_usd": Decimal("45.00"),
        "code_version": "engine-v2",
        "pack_version": "pack-v2",
        "created_by": user["user_id"],
    }
    run_id = db.create_run(conn, **run_kwargs)

    created_opps: list[dict[str, Any]] = []
    stages = ["tofu", "mofu", "bofu"]

    for i in range(opportunity_count):
        is_unverified = i in unverified_indices
        date_status = "unverified" if is_unverified else "verified"
        doc = db.upsert_document(
            conn,
            workspace_id=ws_id,
            url=f"https://optical-news.example.com/story-{i}-{uuid.uuid4().hex[:6]}",
            canonical_url=f"https://optical-news.example.com/story-{i}-{uuid.uuid4().hex[:6]}",
            title=f"Industry Breakthrough Story #{i + 1}",
            date_status=date_status,
            published_at=now - timedelta(days=i + 1),
            fetch_status="ok",
            content_hash=f"hash-{i}".encode(),
        )
        rec = db.upsert_research_record(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            document_id=doc["id"],
            status="verified",
            schema_version=1,
            payload={
                "headline": f"Article Headline #{i + 1}",
                "summary": f"Comprehensive summary for article #{i + 1}.",
                "facts": [f"Fact A{i}", f"Fact B{i}"],
            },
        )
        score_val = 95.0 - (i * 5.0)
        opp = db.upsert_opportunity(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_id=rec["id"],
            scores={"novelty": {"score": 8.0, "evidence": "High"}},
            total=score_val,
            rank=i + 1,
            funnel_stage=stages[i % len(stages)],
            confidence="high" if score_val > 70 else "medium",
            angle=f"Angle perspective #{i + 1}",
        )
        created_opps.append(opp)

    return ws_id, run_id, created_opps


def test_report_compilation_top_10_and_volume_capping(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F09 AC3: Report lists top 10; top N matching max(3, articles/week) marked recommended."""
    # Case A: articles_per_week = 1 -> recommended cap is max(3, 1) = 3
    ws_id, run_id, _ = _seed_report_environment(
        db_conn, articles_per_week=1, opportunity_count=12, unverified_indices=(1, 4)
    )

    rep = services.report(db_conn, workspace_id=ws_id, run_id=run_id)

    assert rep["run_id"] == run_id
    assert rep["workspace_id"] == ws_id
    assert rep["articles_per_week"] == 1
    assert rep["recommended_count"] == 3
    assert rep["total_count"] == 10
    assert len(rep["items"]) == 10

    # First 3 items are recommended, remaining 7 are not
    for idx, item in enumerate(rep["items"]):
        assert item["rank"] == idx + 1
        assert item["already_covered"] is False
        assert "scores" in item
        assert item["original_url"].startswith("https://optical-news.example.com/")
        if idx < 3:
            assert item["is_recommended"] is True
        else:
            assert item["is_recommended"] is False


def test_report_higher_articles_per_week_recommends_n(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F09 AC3: When articles_per_week > 3, recommended count matches articles_per_week."""
    ws_id, run_id, _ = _seed_report_environment(db_conn, articles_per_week=5, opportunity_count=8)

    rep = services.report(db_conn, workspace_id=ws_id, run_id=run_id)
    assert rep["recommended_count"] == 5
    assert rep["total_count"] == 8
    recs = [it for it in rep["items"] if it["is_recommended"]]
    assert len(recs) == 5


def test_report_date_integrity_unverified_never_verified(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F09 AC4: WHEN a record's date is unverified, THEN it is never labelled verified."""
    ws_id, run_id, _ = _seed_report_environment(
        db_conn, articles_per_week=2, opportunity_count=4, unverified_indices=(0, 2)
    )

    rep = services.report(db_conn, workspace_id=ws_id, run_id=run_id)
    items = rep["items"]

    assert items[0]["date_status"] == "unverified"
    assert items[0]["is_verified_date"] is False

    assert items[1]["date_status"] == "verified"
    assert items[1]["is_verified_date"] is True

    assert items[2]["date_status"] == "unverified"
    assert items[2]["is_verified_date"] is False

    assert items[3]["date_status"] == "verified"
    assert items[3]["is_verified_date"] is True


def test_report_emits_awaiting_approval_event_idempotently(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F09 AC5: WHEN the report is ready, THEN topics.awaiting_approval event is emitted."""
    ws_id, run_id, _ = _seed_report_environment(db_conn, articles_per_week=2, opportunity_count=4)

    # Before report, event does not exist
    initial_events = db.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topics.awaiting_approval",
        entity_id=run_id,
    )
    assert len(initial_events) == 0

    # First call generates report and emits event
    rep1 = services.report(db_conn, workspace_id=ws_id, run_id=run_id)
    assert rep1["total_count"] == 4

    events = db.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topics.awaiting_approval",
        entity_id=run_id,
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "topics.awaiting_approval"
    assert ev["entity_type"] == "run"
    assert ev["entity_id"] == run_id
    payload = ev["payload"]
    assert payload["run_id"] == str(run_id)
    assert payload["recommended_count"] == 3
    assert payload["total_opportunities"] == 4
    assert len(payload["top_opportunity_ids"]) == 3

    # Second call is idempotent: event count remains 1
    rep2 = services.report(db_conn, workspace_id=ws_id, run_id=run_id)
    assert rep2["total_count"] == 4

    events_after = db.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topics.awaiting_approval",
        entity_id=run_id,
    )
    assert len(events_after) == 1


def test_report_managed_connection_and_missing_run(
    db_conn: psycopg.Connection[Any],
) -> None:
    """services.report works with conn=None and raises ValueError for missing run."""
    ws_id, run_id, _ = _seed_report_environment(db_conn, opportunity_count=2)

    # Test managed connection (conn=None)
    rep = services.report(None, workspace_id=ws_id, run_id=run_id)
    assert rep["total_count"] == 2

    # Test missing run
    missing_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        services.report(db_conn, workspace_id=ws_id, run_id=missing_id)


def test_report_empty_run_does_not_emit_event(
    db_conn: psycopg.Connection[Any],
) -> None:
    """When a run has 0 opportunities, report returns empty items and emits no event."""
    ws_id = db.create_workspace(
        db_conn,
        name="Empty WS",
        slug=f"empty-{uuid.uuid4().hex[:8]}",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        db_conn, email=f"user-{uuid.uuid4().hex[:8]}@example.com", name="U"
    )
    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Camp",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 0 * * *",
        monthly_cap_usd=Decimal("100"),
        run_cap_usd=Decimal("10"),
        config={},
        config_schema_version=1,
        created_by=user["user_id"],
    )
    now = datetime.now(UTC)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=1),
        window_end=now,
        run_cap_usd=Decimal("10"),
        code_version="v1",
        pack_version="p1",
        created_by=user["user_id"],
    )

    rep = services.report(db_conn, workspace_id=ws_id, run_id=run_id)
    assert rep["total_count"] == 0
    assert rep["items"] == []

    events = db.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topics.awaiting_approval",
        entity_id=run_id,
    )
    assert len(events) == 0
