"""Option B: DBOS Transact Durable Workflow Runner.

Uses:
- @DBOS.workflow and @DBOS.step decorators
- Durable execution checkpointing into PostgreSQL DBOS system tables
- Workflow ID / tracking table enforcing single active run
- Per-item step exception handling for failure isolation
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any

import psycopg
from dbos import DBOS, DBOSConfig, SetWorkflowID

from spikes.orchestration.common import (
    ActiveRunExistsError,
    PipelineItem,
    StepExecutionError,
    fake_research_step,
    fake_score_step,
)

DEFAULT_SYSTEM_DB_URL = "postgresql://owner:secret@localhost:5432/macy"


def init_dbos_schema(db_url: str = DEFAULT_SYSTEM_DB_URL) -> None:
    """Create active run tracking table for DBOS workflows."""
    conn = psycopg.connect(db_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS spike_dbos_runs (
                    run_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_summary JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE UNIQUE INDEX IF NOT EXISTS spike_dbos_runs_one_active_idx
                ON spike_dbos_runs (campaign_id)
                WHERE status IN ('queued', 'running');
                """
            )
    finally:
        conn.close()


def setup_dbos(db_url: str = DEFAULT_SYSTEM_DB_URL) -> None:
    """Initialize and launch DBOS runtime."""
    os.environ["DBOS_APP_NAME"] = "macy_spike"
    os.environ["DBOS_SYSTEM_DATABASE_URL"] = db_url

    cfg: DBOSConfig = {
        "name": "macy_spike",
        "system_database_url": db_url,
        "run_migrations": True,
    }
    DBOS(config=cfg)
    DBOS.launch()


def teardown_dbos() -> None:
    """Cleanly destroy DBOS runtime and background threads."""
    with contextlib.suppress(Exception):
        DBOS.destroy()


@DBOS.step()
def dbos_research_step(
    item_id: str,
    raw_text: str,
    should_fail: bool,
) -> dict[str, Any]:
    """Execute research step with DBOS durability checkpointing."""
    res = fake_research_step(item_id, raw_text, should_fail=should_fail)
    return res.to_dict()


@DBOS.step()
def dbos_score_step(
    item_id: str,
    summary: str,
    should_fail: bool,
) -> dict[str, Any]:
    """Execute scoring step with DBOS durability checkpointing."""
    res = fake_score_step(item_id, summary, should_fail=should_fail)
    return res.to_dict()


@DBOS.workflow()
def dbos_pipeline_workflow(
    run_id: str,
    campaign_id: str,
    items: list[dict[str, str]],
    fail_research_items: list[str],
    fail_score_items: list[str],
) -> dict[str, Any]:
    """2-stage pipeline executed as a durable DBOS workflow."""
    fail_res_set = set(fail_research_items)
    fail_score_set = set(fail_score_items)

    item_results: list[dict[str, Any]] = []

    for it in items:
        item_id = it["item_id"]
        raw_text = it["raw_text"]
        item_entry: dict[str, Any] = {
            "item_id": item_id,
            "status": "pending",
            "research_data": None,
            "score_data": None,
            "error_message": None,
        }

        # Stage 1: Research with failure isolation
        try:
            res_dict = dbos_research_step(
                item_id,
                raw_text,
                should_fail=(item_id in fail_res_set),
            )
            item_entry["research_data"] = res_dict
            item_entry["status"] = "researched"
        except StepExecutionError as err:
            item_entry["status"] = "failed"
            item_entry["error_message"] = str(err)
            item_results.append(item_entry)
            continue

        # Stage 2: Scoring with failure isolation
        try:
            summary = res_dict.get("summary", "")
            score_dict = dbos_score_step(
                item_id,
                summary,
                should_fail=(item_id in fail_score_set),
            )
            item_entry["score_data"] = score_dict
            item_entry["status"] = "scored"
        except StepExecutionError as err:
            item_entry["status"] = "failed"
            item_entry["error_message"] = str(err)

        item_results.append(item_entry)

    scored_count = sum(1 for r in item_results if r["status"] == "scored")
    failed_count = sum(1 for r in item_results if r["status"] == "failed")
    final_status = "completed" if failed_count == 0 else "failed"

    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "run_status": final_status,
        "scored_count": scored_count,
        "failed_count": failed_count,
        "items": item_results,
    }


def execute_dbos_pipeline(  # noqa: PLR0913
    campaign_id: str,
    items: list[PipelineItem],
    *,
    run_id: str | None = None,
    fail_research_items: set[str] | None = None,
    fail_score_items: set[str] | None = None,
    db_url: str = DEFAULT_SYSTEM_DB_URL,
) -> dict[str, Any]:
    """Run DBOS pipeline with active-run constraint and checkpointing."""
    actual_run_id = run_id or f"dbos_run_{uuid.uuid4().hex[:12]}"
    conn = psycopg.connect(db_url, autocommit=True)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO spike_dbos_runs (run_id, campaign_id, status)
                VALUES (%s, %s, 'running');
                """,
                (actual_run_id, campaign_id),
            )
    except psycopg.errors.UniqueViolation as err:
        conn.close()
        raise ActiveRunExistsError(
            f"An active run already exists for campaign {campaign_id}"
        ) from err

    items_payload = [{"item_id": it.item_id, "raw_text": it.raw_text} for it in items]
    fail_res_list = list(fail_research_items or set())
    fail_score_list = list(fail_score_items or set())

    # Execute workflow with deterministic workflow_id for replay/resume
    SetWorkflowID(actual_run_id)
    summary = dbos_pipeline_workflow(
        actual_run_id,
        campaign_id,
        items_payload,
        fail_res_list,
        fail_score_list,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE spike_dbos_runs
            SET status = %s, updated_at = NOW()
            WHERE run_id = %s;
            """,
            (summary["run_status"], actual_run_id),
        )
    conn.close()

    return summary
