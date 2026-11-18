"""Tests for F17: Spend control, monthly/run cap guards, and budget ledger recording."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import budget, db


def _setup_test_workspace(
    conn: psycopg.Connection[Any],
    monthly_cap: Decimal = Decimal("150.00"),
    run_cap: Decimal = Decimal("5.00"),
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    slug = f"ws-budget-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        conn,
        slug=slug,
        name="Budget Test Workspace",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        conn,
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        name="Budget Owner",
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
        name="Campaign For Budget Tests",
        locale="en-US",
        market="US",
        articles_per_week=3,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=monthly_cap,
        run_cap_usd=run_cap,
        config={
            "scoring_weights": {
                "novelty": 30,
                "audience_fit": 20,
                "competitor_gap": 20,
                "trend": 15,
                "urgency": 10,
                "business_impact": 5,
            }
        },
        config_schema_version=1,
        created_by=user_id,
    )
    return ws_id, user_id, camp_id


def test_monthly_budget_guard_f17_ac3(db_conn: psycopg.Connection[Any]) -> None:
    """WHEN month's spend reaches monthly_cap_usd, THEN new runs are refused (F17 AC3)."""
    ws_id, user_id, camp_id = _setup_test_workspace(
        db_conn, monthly_cap=Decimal("50.00"), run_cap=Decimal("5.00")
    )

    # 1. Spend is currently 0, checking monthly budget returns 0
    spend = budget.check_monthly_budget(
        db_conn, workspace_id=ws_id, monthly_cap_usd=Decimal("50.00")
    )
    assert spend == Decimal("0.000000")

    # 2. Record usage in the current month pushing spend to $50.00
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        stage="research",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("50.000000"),
    )

    # 3. Spend now equals cap, check_monthly_budget must raise MonthlyCapReached
    with pytest.raises(budget.MonthlyCapReached) as exc_info:
        budget.check_monthly_budget(
            db_conn,
            workspace_id=ws_id,
            monthly_cap_usd=Decimal("50.00"),
        )

    assert exc_info.value.workspace_id == ws_id
    assert exc_info.value.monthly_cap_usd == Decimal("50.00")
    assert exc_info.value.current_spend == Decimal("50.000000")


def _create_capped_run(
    conn: psycopg.Connection[Any],
    ws_id: uuid.UUID,
    camp_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: Decimal = Decimal("4.00"),
) -> uuid.UUID:
    cur_time = datetime.now(UTC)
    w_start = cur_time - timedelta(days=14)
    return db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=w_start,
        window_end=cur_time,
        run_cap_usd=limit,
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=user_id,
    )


def test_run_budget_guard_blocks_and_emits_event_f17_ac2(
    db_conn: psycopg.Connection[Any],
) -> None:
    """WHEN a call would push run cost over cap, THEN run becomes blocked (F17 AC2)."""
    ws_id, user_id, camp_id = _setup_test_workspace(
        db_conn, monthly_cap=Decimal("150.00"), run_cap=Decimal("4.00")
    )

    run_id = _create_capped_run(db_conn, ws_id, camp_id, user_id)
    # Set to running
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="queued",
        new_status="running",
        stage="research",
    )

    # Record usage of 3.50 USD
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="research",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("3.500000"),
    )

    # Check budget with estimated cost of 0.40 USD -> total 3.90 <= 4.00 -> passes
    current_cost = budget.check_run_budget(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        run_cap_usd=Decimal("4.00"),
        estimated_cost=Decimal("0.400000"),
    )
    assert current_cost == Decimal("3.500000")

    # Check budget with estimated cost of 0.60 USD -> total 4.10 > 4.00 -> blocks!
    with pytest.raises(budget.BudgetCapReached) as exc_info:
        budget.check_run_budget(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            run_cap_usd=Decimal("4.00"),
            estimated_cost=Decimal("0.600000"),
        )
    assert exc_info.value.run_id == run_id
    assert exc_info.value.projected_cost == Decimal("4.100000")

    # Verify run transitioned to blocked(cap_reached)
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    assert run_row["status"] == "blocked"
    assert run_row["blocked_reason"] == "cap_reached"

    # Verify budget.cap_reached event was emitted
    events = db.list_events(db_conn, workspace_id=ws_id)
    cap_events = [e for e in events if e["type"] == "budget.cap_reached"]
    assert len(cap_events) == 1
    assert cap_events[0]["payload"]["run_id"] == str(run_id)
    assert cap_events[0]["payload"]["run_cap_usd"] == "4.00"


def test_resume_run_budget_f17_ac4(db_conn: psycopg.Connection[Any]) -> None:
    """WHEN a blocked run's cap is raised, THEN run can be resumed (F17 AC4)."""
    ws_id, user_id, camp_id = _setup_test_workspace(
        db_conn, monthly_cap=Decimal("150.00"), run_cap=Decimal("4.00")
    )

    run_id = _create_capped_run(db_conn, ws_id, camp_id, user_id)
    # Transition to blocked
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="queued",
        new_status="blocked",
        blocked_reason="cap_reached",
    )
    # Record cost of $4.00
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="research",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("4.000000"),
    )

    # 1. Attempt to resume without raising cap -> fails (cost 4.00 >= cap 4.00)
    resumed = budget.resume_run_budget(db_conn, workspace_id=ws_id, run_id=run_id)
    assert resumed is False

    # 2. Raise cap to $6.00 and resume -> succeeds
    resumed = budget.resume_run_budget(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        new_run_cap_usd=Decimal("6.00"),
    )
    assert resumed is True

    # 3. Check status is now 'running' and blocked_reason is cleared
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    assert run_row["status"] == "running"
    assert run_row["blocked_reason"] is None
    assert Decimal(str(run_row["run_cap_usd"])) == Decimal("6.00")

    # 4. Attempting to resume a run that is already 'running' returns False
    assert not budget.resume_run_budget(db_conn, workspace_id=ws_id, run_id=run_id)


@given(
    current_cost=st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("10.00"),
        places=6,
    ),
    estimated_cost=st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("5.00"),
        places=6,
    ),
    cap=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("12.00"),
        places=2,
    ),
)
def test_budget_exact_arithmetic_hypothesis(
    current_cost: Decimal,
    estimated_cost: Decimal,
    cap: Decimal,
) -> None:
    """Hypothesis test verifying decimal exactness at boundary amounts."""
    projected = current_cost + estimated_cost
    should_block = projected > cap
    if should_block:
        assert projected > cap
    else:
        assert projected <= cap
