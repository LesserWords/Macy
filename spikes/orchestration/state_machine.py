"""Option A: PostgreSQL State Machine Runner.

Uses:
- Guarded status transitions on rows
- FOR UPDATE SKIP LOCKED row claiming
- Partial unique index enforcing single active run per campaign
- Per-item failure isolation
- Transparent SQL resume logic
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row

from spikes.orchestration.common import (
    ActiveRunExistsError,
    PipelineItem,
    StepExecutionError,
    fake_research_step,
    fake_score_step,
)


def init_schema(conn: psycopg.Connection[Any]) -> None:
    """Initialize test tables and active run constraint in Postgres."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS spike_state_runs (
                run_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE UNIQUE INDEX IF NOT EXISTS spike_state_runs_one_active_idx
            ON spike_state_runs (campaign_id)
            WHERE status IN ('queued', 'running');

            CREATE TABLE IF NOT EXISTS spike_state_items (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES spike_state_runs(run_id) ON DELETE CASCADE,
                campaign_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                status TEXT NOT NULL,
                research_data JSONB,
                score_data JSONB,
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS spike_state_items_claim_idx
            ON spike_state_items (run_id, status);
            """
        )


def create_run(
    conn: psycopg.Connection[Any],
    campaign_id: str,
    items: list[PipelineItem],
) -> str:
    """Create a new run. Enforces single active run per campaign via DB partial unique index."""
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO spike_state_runs (run_id, campaign_id, status)
                VALUES (%s, %s, 'running');
                """,
                (run_id, campaign_id),
            )
            for it in items:
                cur.execute(
                    """
                    INSERT INTO spike_state_items (
                        id, run_id, campaign_id, item_id, raw_text, status
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending');
                    """,
                    (
                        f"item_{uuid.uuid4().hex[:12]}",
                        run_id,
                        campaign_id,
                        it.item_id,
                        it.raw_text,
                    ),
                )
    except psycopg.errors.UniqueViolation as err:
        raise ActiveRunExistsError(
            f"An active run already exists for campaign {campaign_id}"
        ) from err

    return run_id


def run_stage_research(
    conn: psycopg.Connection[Any],
    run_id: str,
    *,
    fail_items: set[str] | None = None,
) -> dict[str, int]:
    """Execute Stage 1 (Research) with FOR UPDATE SKIP LOCKED and failure isolation."""
    failures = fail_items or set()
    counts = {"success": 0, "failed": 0}

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, item_id, raw_text
            FROM spike_state_items
            WHERE run_id = %s AND status IN ('pending', 'failed')
            FOR UPDATE SKIP LOCKED;
            """,
            (run_id,),
        )
        claimed_items = cur.fetchall()

    for row in claimed_items:
        item_pk = row["id"]
        item_id = row["item_id"]
        raw_text = row["raw_text"]

        try:
            res = fake_research_step(
                item_id,
                raw_text,
                should_fail=(item_id in failures),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE spike_state_items
                    SET status = 'researched',
                        research_data = %s,
                        error_message = NULL,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (json.dumps(res.to_dict()), item_pk),
                )
            counts["success"] += 1
        except StepExecutionError as err:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE spike_state_items
                    SET status = 'failed',
                        error_message = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (str(err), item_pk),
                )
            counts["failed"] += 1

    return counts


def run_stage_score(
    conn: psycopg.Connection[Any],
    run_id: str,
    *,
    fail_items: set[str] | None = None,
) -> dict[str, int]:
    """Execute Stage 2 (Scoring) with FOR UPDATE SKIP LOCKED and failure isolation."""
    failures = fail_items or set()
    counts = {"success": 0, "failed": 0}

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, item_id, research_data
            FROM spike_state_items
            WHERE run_id = %s AND status = 'researched'
            FOR UPDATE SKIP LOCKED;
            """,
            (run_id,),
        )
        claimed_items = cur.fetchall()

    for row in claimed_items:
        item_pk = row["id"]
        item_id = row["item_id"]
        research_data = row["research_data"]

        try:
            summary = research_data.get("summary", "") if isinstance(research_data, dict) else ""
            res = fake_score_step(
                item_id,
                summary,
                should_fail=(item_id in failures),
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE spike_state_items
                    SET status = 'scored',
                        score_data = %s,
                        error_message = NULL,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (json.dumps(res.to_dict()), item_pk),
                )
            counts["success"] += 1
        except StepExecutionError as err:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE spike_state_items
                    SET status = 'failed',
                        error_message = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (str(err), item_pk),
                )
            counts["failed"] += 1

    return counts


def get_run_summary(conn: psycopg.Connection[Any], run_id: str) -> dict[str, Any]:
    """Fetch status and item counts for a run."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT run_id, campaign_id, status FROM spike_state_runs WHERE run_id = %s;",
            (run_id,),
        )
        run_row = cur.fetchone()
        if not run_row:
            raise KeyError(f"Run {run_id} not found")

        cur.execute(
            """
            SELECT item_id, status, error_message, score_data
            FROM spike_state_items
            WHERE run_id = %s
            ORDER BY item_id;
            """,
            (run_id,),
        )
        items = cur.fetchall()

    scored_count = sum(1 for it in items if it["status"] == "scored")
    failed_count = sum(1 for it in items if it["status"] == "failed")

    return {
        "run_id": run_id,
        "campaign_id": run_row["campaign_id"],
        "run_status": run_row["status"],
        "scored_count": scored_count,
        "failed_count": failed_count,
        "items": items,
    }


def execute_pipeline(
    conn: psycopg.Connection[Any],
    campaign_id: str,
    items: list[PipelineItem],
    *,
    fail_research_items: set[str] | None = None,
    fail_score_items: set[str] | None = None,
) -> dict[str, Any]:
    """Run full 2-stage pipeline (Research -> Score)."""
    run_id = create_run(conn, campaign_id, items)
    run_stage_research(conn, run_id, fail_items=fail_research_items)
    run_stage_score(conn, run_id, fail_items=fail_score_items)

    summary = get_run_summary(conn, run_id)
    final_status = "completed" if summary["failed_count"] == 0 else "failed"

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE spike_state_runs SET status = %s, updated_at = NOW() WHERE run_id = %s;",
            (final_status, run_id),
        )

    summary["run_status"] = final_status
    return summary


def resume_pipeline(
    conn: psycopg.Connection[Any],
    run_id: str,
    *,
    fail_research_items: set[str] | None = None,
    fail_score_items: set[str] | None = None,
) -> dict[str, Any]:
    """Resume an existing run, picking up only uncompleted/failed items."""
    with conn.cursor() as cur:
        cur.execute("UPDATE spike_state_runs SET status = 'running' WHERE run_id = %s;", (run_id,))

    # Stage 1: research any pending/failed
    run_stage_research(conn, run_id, fail_items=fail_research_items)
    # Stage 2: score any researched
    run_stage_score(conn, run_id, fail_items=fail_score_items)

    summary = get_run_summary(conn, run_id)
    final_status = "completed" if summary["failed_count"] == 0 else "failed"

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE spike_state_runs SET status = %s, updated_at = NOW() WHERE run_id = %s;",
            (final_status, run_id),
        )

    summary["run_status"] = final_status
    return summary
