"""Spend control, monthly/run cap guards, and budget ledger recording (ADR-016, F17)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

from engine import db, events


class BudgetCapReached(Exception):
    """Raised when a proposed call would exceed the run spending limit."""

    def __init__(self, run_id: uuid.UUID, run_cap_usd: Decimal, projected_cost: Decimal) -> None:
        self.run_id = run_id
        self.run_cap_usd = run_cap_usd
        self.projected_cost = projected_cost
        super().__init__(
            f"Run {run_id} budget cap exceeded: "
            f"projected {projected_cost:.4f} USD > cap {run_cap_usd:.2f} USD"
        )


class MonthlyCapReached(Exception):
    """Raised when a workspace's current month spend has reached or exceeded its monthly cap."""

    def __init__(
        self,
        workspace_id: uuid.UUID,
        monthly_cap_usd: Decimal,
        current_spend: Decimal,
    ) -> None:
        self.workspace_id = workspace_id
        self.monthly_cap_usd = monthly_cap_usd
        self.current_spend = current_spend
        super().__init__(
            f"Workspace {workspace_id} monthly budget cap reached: "
            f"{current_spend:.2f} USD >= {monthly_cap_usd:.2f} USD"
        )


def check_monthly_budget(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    monthly_cap_usd: Decimal,
    reference_date: datetime | None = None,
) -> Decimal:
    """Verify that current monthly spend does not exceed the campaign monthly cap."""
    current_spend = db.get_monthly_spend(
        conn, workspace_id=workspace_id, reference_date=reference_date
    )
    if current_spend >= monthly_cap_usd:
        raise MonthlyCapReached(workspace_id, monthly_cap_usd, current_spend)
    return current_spend


def check_run_budget(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    run_cap_usd: Decimal,
    estimated_cost: Decimal = Decimal("0.000000"),
) -> Decimal:
    """Verify that the proposed call or stage transition does not exceed the per-run cap."""
    current_cost = db.get_run_total_cost(conn, workspace_id=workspace_id, run_id=run_id)
    projected = current_cost + estimated_cost
    if projected > run_cap_usd:
        # Move run to blocked(cap_reached) and emit budget.cap_reached event (F17 AC2)
        db.update_run_status(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            expected_status=None,
            new_status="blocked",
            blocked_reason="cap_reached",
            cost_usd=current_cost,
        )
        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type="budget.cap_reached",
            entity_type="run",
            entity_id=run_id,
            payload={
                "run_id": str(run_id),
                "run_cap_usd": str(run_cap_usd),
                "current_cost_usd": str(current_cost),
                "projected_cost_usd": str(projected),
            },
        )
        raise BudgetCapReached(run_id, run_cap_usd, projected)
    return current_cost


def resume_run_budget(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    new_run_cap_usd: Decimal | None = None,
) -> bool:
    """Resume a run blocked on cap_reached if the current cost is strictly within cap (F17 AC4)."""
    run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
    if not run_row or run_row["status"] != "blocked" or run_row["blocked_reason"] != "cap_reached":
        return False

    cap = new_run_cap_usd if new_run_cap_usd is not None else Decimal(str(run_row["run_cap_usd"]))
    current_cost = db.get_run_total_cost(conn, workspace_id=workspace_id, run_id=run_id)
    if current_cost >= cap:
        return False

    return db.update_run_status(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        expected_status="blocked",
        new_status="running",
        run_cap_usd=cap,
    )
