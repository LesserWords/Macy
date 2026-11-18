"""Tests for scheduler, crontab generation, and CLI subcommands (F18 AC1, AC2, ADR-001)."""

from __future__ import annotations

import io
import sys
import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import cli, db, scheduler, services


def _setup_test_env(
    conn: psycopg.Connection[Any],
) -> tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]:
    slug = f"ws-sched-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        conn,
        slug=slug,
        name="Scheduler Test Workspace",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        conn,
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        name="Scheduler Owner",
    )
    user_id = user["user_id"]
    db.create_membership(
        conn,
        workspace_id=ws_id,
        user_id=user_id,
        role="owner",
    )
    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name="Scheduled Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 10 * * 2",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("5.00"),
        config={
            "scoring_weights": {
                "novelty": 30,
                "audience_fit": 20,
                "brand_fit": 20,
                "competitor_gap": 15,
                "trend": 10,
                "urgency": 5,
            }
        },
        config_schema_version=1,
        created_by=user_id,
    )
    db.create_source(
        conn,
        workspace_id=ws_id,
        name="Scheduler Feed",
        domain="scheduler.example.com",
        industry_pack="general",
        url="https://scheduler.example.com",
        fetch_mode="feed",
        feed_url="https://scheduler.example.com/feed.xml",
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["general"],
    )
    return slug, ws_id, user_id, camp_id


def test_scheduled_run_creation_snapshot_f18_ac1(
    db_conn: psycopg.Connection[Any],
) -> None:
    """WHEN schedule fires, THEN run is created with snapshot and versions (F18 AC1)."""
    slug, ws_id, user_id, camp_id = _setup_test_env(db_conn)

    run_id = scheduler.trigger_campaign_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="scheduled",
        window_days=7,
        code_version="0.1.0-test",
        pack_version="1.0.0-test",
    )
    assert run_id is not None

    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    assert run_row["trigger"] == "scheduled"
    assert run_row["status"] == "queued"
    assert run_row["code_version"] == "0.1.0-test"
    assert run_row["pack_version"] == "1.0.0-test"
    assert Decimal(str(run_row["run_cap_usd"])) == Decimal("5.00")
    assert run_row["window_end"] > run_row["window_start"]
    delta = run_row["window_end"] - run_row["window_start"]
    assert delta.days == 7


def test_generate_crontab_f18_ac1(db_conn: psycopg.Connection[Any]) -> None:
    """Crontab output includes CRON_TZ and content-engine run-week invocation."""
    slug, ws_id, user_id, camp_id = _setup_test_env(db_conn)

    crontab_txt = scheduler.generate_crontab(db_conn, workspace_id=ws_id)
    assert "Supercronic crontab" in crontab_txt
    assert "0 10 * * 2" in crontab_txt
    assert f"content-engine run-week --workspace {ws_id} --campaign {camp_id}" in crontab_txt
    assert "0 2 * * * content-engine purge" in crontab_txt


def test_trigger_due_scheduled_runs(db_conn: psycopg.Connection[Any]) -> None:
    """trigger_due_scheduled_runs queues runs for active campaigns."""
    slug, ws_id, user_id, camp_id = _setup_test_env(db_conn)

    triggered = scheduler.trigger_due_scheduled_runs(db_conn, workspace_id=ws_id)
    assert len(triggered) == 1

    # Second trigger skips because run is already queued (ActiveRunExistsError caught)
    triggered_again = scheduler.trigger_due_scheduled_runs(db_conn, workspace_id=ws_id)
    assert len(triggered_again) == 0


def test_cli_subcommands_offline(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test CLI subcommands: run-now, run-week, run-stage, and crontab."""
    slug, ws_id, user_id, camp_id = _setup_test_env(db_conn)

    # 1. CLI run-now
    ret_now = cli.main(["run-now", slug, "--campaign", str(camp_id), "--offline"])
    assert ret_now == 0

    runs = db.list_runs(db_conn, workspace_id=ws_id, campaign_id=camp_id)
    assert len(runs) >= 1
    completed_run = runs[0]

    # 2. CLI run-stage on completed run
    ret_stage = cli.main(["run-stage", str(completed_run["id"]), "--offline"])
    assert ret_stage == 0

    # 3. CLI crontab
    stdout_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_buf)
    ret_cron = cli.main(["crontab", "--workspace", slug])
    assert ret_cron == 0
    assert "content-engine run-week" in stdout_buf.getvalue()

    # 4. CLI run-week
    # Mark existing run completed so partial unique index allows a new run
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=completed_run["id"],
        expected_status=None,
        new_status="completed",
    )
    ret_week = cli.main(["run-week", slug, "--campaign", str(camp_id), "--offline"])
    assert ret_week == 0


def test_offline_journey_j3_run_now_to_records_stored(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Offline E2E Journey J3: Run now -> records stored -> run completed (F18 AC5)."""
    slug, ws_id, user_id, camp_id = _setup_test_env(db_conn)

    # Trigger Run now
    run_id = services.run_now(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        user_id=user_id,
    )
    assert run_id is not None

    # Execute run with offline fakes
    result = services.execute_run_pipeline(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        user_id=user_id,
    )
    assert result["status"] == "completed"
    assert result["finished_at"] is not None

    # Verify research records stored
    records = db.list_research_records(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(records) > 0
    assert any(r["status"] == "extracted" for r in records)

    # Verify run.completed event
    events = db.list_events(db_conn, workspace_id=ws_id)
    assert any(e["type"] == "run.completed" for e in events)
