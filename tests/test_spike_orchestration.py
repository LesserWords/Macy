"""Unified test suite for M0-09 orchestration spike: State Machine vs DBOS Transact.

Verifies the same 4 criteria on both implementations:
1. End-to-end multi-item execution (research -> score)
2. Failure isolation (single item failure does not abort other items)
3. Resume (re-running completes uncompleted items without re-doing scored work)
4. Single active run constraint per campaign
"""

import uuid
from collections.abc import Generator
from typing import Any

import psycopg
import pytest

from spikes.orchestration.common import ActiveRunExistsError, PipelineItem
from spikes.orchestration.dbos_workflow import (
    execute_dbos_pipeline,
    init_dbos_schema,
    setup_dbos,
    teardown_dbos,
)
from spikes.orchestration.state_machine import (
    execute_pipeline as execute_state_machine_pipeline,
)
from spikes.orchestration.state_machine import (
    init_schema as init_state_machine_schema,
)
from spikes.orchestration.state_machine import (
    resume_pipeline as resume_state_machine_pipeline,
)

POSTGRES_OWNER_URL = "postgres://owner:secret@localhost:5432/macy?sslmode=disable"


@pytest.fixture(scope="module")
def postgres_conn() -> Generator[psycopg.Connection, None, None]:
    """Module-level Postgres connection for Option A (State Machine)."""
    conn = psycopg.connect(POSTGRES_OWNER_URL, autocommit=True)
    init_state_machine_schema(conn)
    yield conn
    with conn.cursor() as cur:
        cur.execute(
            """
            DROP TABLE IF EXISTS spike_state_items CASCADE;
            DROP TABLE IF EXISTS spike_state_runs CASCADE;
            """
        )
    conn.close()


@pytest.fixture(scope="module")
def dbos_runtime() -> Generator[None, None, None]:
    """Module-level DBOS runtime setup and teardown for Option B."""
    init_dbos_schema(POSTGRES_OWNER_URL)
    setup_dbos(POSTGRES_OWNER_URL)
    yield
    teardown_dbos()
    conn = psycopg.connect(POSTGRES_OWNER_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            """
            DROP TABLE IF EXISTS spike_dbos_runs CASCADE;
            DROP SCHEMA IF EXISTS dbos CASCADE;
            """
        )
    conn.close()


# =========================================================================
# 1. Option A Tests (PostgreSQL State Machine)
# =========================================================================


def test_state_machine_e2e_success(postgres_conn: psycopg.Connection) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [
        PipelineItem("item_1", campaign_id, "Cloud security developments"),
        PipelineItem("item_2", campaign_id, "PostgreSQL performance tuning"),
        PipelineItem("item_3", campaign_id, "AI-driven code generation"),
    ]

    result = execute_state_machine_pipeline(postgres_conn, campaign_id, items)

    assert result["run_status"] == "completed"
    assert result["scored_count"] == 3
    assert result["failed_count"] == 0
    assert all(it["status"] == "scored" for it in result["items"])


def _verify_failure_isolation(result: dict[str, Any], expected_failed_id: str) -> None:
    assert result["run_status"] == "failed"
    assert result["scored_count"] == 2
    assert result["failed_count"] == 1

    item_statuses = {it["item_id"]: it["status"] for it in result["items"]}
    assert item_statuses["item_1"] == "scored"
    assert item_statuses[expected_failed_id] == "failed"
    assert item_statuses["item_3"] == "scored"

    failed_item = next(it for it in result["items"] if it["item_id"] == expected_failed_id)
    assert f"Research failed for item {expected_failed_id}" in (failed_item["error_message"] or "")


def test_state_machine_failure_isolation(postgres_conn: psycopg.Connection) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [
        PipelineItem("item_1", campaign_id, "Valid topic 1"),
        PipelineItem("item_2", campaign_id, "Faulty topic 2"),
        PipelineItem("item_3", campaign_id, "Valid topic 3"),
    ]

    # Item 2 fails during research; items 1 & 3 must proceed and score
    result = execute_state_machine_pipeline(
        postgres_conn,
        campaign_id,
        items,
        fail_research_items={"item_2"},
    )
    _verify_failure_isolation(result, "item_2")


def test_state_machine_resume(postgres_conn: psycopg.Connection) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [
        PipelineItem("item_1", campaign_id, "Topic 1"),
        PipelineItem("item_2", campaign_id, "Topic 2 (temporary failure)"),
        PipelineItem("item_3", campaign_id, "Topic 3"),
    ]

    # Initial run: item_2 fails in scoring
    initial_res = execute_state_machine_pipeline(
        postgres_conn,
        campaign_id,
        items,
        fail_score_items={"item_2"},
    )
    assert initial_res["run_status"] == "failed"
    assert initial_res["scored_count"] == 2
    run_id = initial_res["run_id"]

    # Resume: cause is resolved (no failure set)
    resumed_res = resume_state_machine_pipeline(postgres_conn, run_id)
    assert resumed_res["run_status"] == "completed"
    assert resumed_res["scored_count"] == 3
    assert resumed_res["failed_count"] == 0
    assert all(it["status"] == "scored" for it in resumed_res["items"])


def test_state_machine_one_active_run_constraint(postgres_conn: psycopg.Connection) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [PipelineItem("item_1", campaign_id, "Topic 1")]

    # Start run 1 (leaves it in running state in DB)
    with postgres_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO spike_state_runs (run_id, campaign_id, status)
            VALUES (%s, %s, 'running');
            """,
            (f"run_{uuid.uuid4().hex[:8]}", campaign_id),
        )

    # Attempting to start run 2 for same campaign must raise ActiveRunExistsError
    with pytest.raises(ActiveRunExistsError, match="active run already exists"):
        execute_state_machine_pipeline(postgres_conn, campaign_id, items)


# =========================================================================
# 2. Option B Tests (DBOS Transact Workflow)
# =========================================================================


def test_dbos_e2e_success(dbos_runtime: None) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [
        PipelineItem("item_1", campaign_id, "Cloud security developments"),
        PipelineItem("item_2", campaign_id, "PostgreSQL performance tuning"),
        PipelineItem("item_3", campaign_id, "AI-driven code generation"),
    ]

    result = execute_dbos_pipeline(campaign_id, items)

    assert result["run_status"] == "completed"
    assert result["scored_count"] == 3
    assert result["failed_count"] == 0
    assert all(it["status"] == "scored" for it in result["items"])


def test_dbos_failure_isolation(dbos_runtime: None) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [
        PipelineItem("item_1", campaign_id, "Valid topic 1"),
        PipelineItem("item_2", campaign_id, "Faulty topic 2"),
        PipelineItem("item_3", campaign_id, "Valid topic 3"),
    ]

    # Item 2 fails during research; items 1 & 3 must proceed and score
    result = execute_dbos_pipeline(
        campaign_id,
        items,
        fail_research_items={"item_2"},
    )
    _verify_failure_isolation(result, "item_2")


def test_dbos_one_active_run_constraint(dbos_runtime: None) -> None:
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    items = [PipelineItem("item_1", campaign_id, "Topic 1")]

    # Insert an active running record in DBOS run tracker
    conn = psycopg.connect(POSTGRES_OWNER_URL, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO spike_dbos_runs (run_id, campaign_id, status) VALUES (%s, %s, 'running');",
            (f"dbos_run_{uuid.uuid4().hex[:8]}", campaign_id),
        )
    conn.close()

    # Attempting to start run 2 for same campaign must raise ActiveRunExistsError
    with pytest.raises(ActiveRunExistsError, match="active run already exists"):
        execute_dbos_pipeline(campaign_id, items)
