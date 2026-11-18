"""Harness and contract tests for the Live E2E subsystem (M2-07, ADR-018).

Validates:
- Budget guard fixture logic: ensures budget overruns abort the suite immediately.
- Storage state resolution from path or serialized JSON content.
- Workflow configuration in `.github/workflows/e2e-live.yml`: cron, dispatch,
  Playwright installation, artifact retention, and execution gating.
- Pyproject marker registration and test isolation.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from engine import db, services
from tests.test_live_e2e_j0_j4 import (
    BudgetGuard,
    BudgetOverrunError,
    resolve_storage_state,
)


def test_budget_guard_passes_when_under_cap() -> None:
    """Budget guard does not raise when spend is within the cap."""
    guard = BudgetGuard(default_cap_usd=Decimal("5.00"))
    guard.check_spend(Decimal("2.50"))
    guard.check_spend(Decimal("5.00"))
    guard.check_spend(Decimal("0.99"), cap=Decimal("1.00"))


def test_budget_guard_aborts_on_overrun() -> None:
    """Budget guard raises BudgetOverrunError when spend exceeds the cap."""
    guard = BudgetGuard(default_cap_usd=Decimal("5.00"))

    with pytest.raises(BudgetOverrunError, match="budget overrun"):
        guard.check_spend(Decimal("5.01"))

    with pytest.raises(BudgetOverrunError, match="budget overrun"):
        guard.check_spend(Decimal("10.00"), cap=Decimal("2.00"))


def test_budget_guard_queries_workspace_spend(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Budget guard queries runs table and validates total workspace cost."""
    slug = f"ws-budget-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Budget Test WS",
        industry_pack="general",
    )
    email = f"budget-owner-{uuid.uuid4().hex[:8]}@example.com"
    owner_info = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = owner_info["user"]["user_id"]
    campaign_id = uuid.uuid4()
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO campaigns (
                id, workspace_id, name, locale, market,
                articles_per_week, schedule_cron, monthly_cap_usd, run_cap_usd,
                config, config_schema_version, created_by
            )
            VALUES (
                %s, %s, 'Test Campaign', 'pt-BR', 'BR', 1, '0 9 * * 1',
                20.00, 5.00, '{}', 1, %s
            )
            """,
            (campaign_id, ws_id, user_id),
        )
    db_conn.commit()

    guard = BudgetGuard(default_cap_usd=Decimal("5.00"))

    # Initial spend is zero
    spend = guard.assert_workspace_spend(db_conn, ws_id)
    assert spend == Decimal("0.0")

    # Record a run with cost $3.50 -> still within $5.00 cap
    run_id_1 = uuid.uuid4()
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (
                id, workspace_id, campaign_id, trigger, status,
                window_start, window_end, run_cap_usd, code_version, pack_version, cost_usd
            )
            VALUES (
                %s, %s, %s, 'manual', 'completed',
                NOW(), NOW() + interval '1 day', 5.00, 'v1', 'v1', %s
            )
            """,
            (run_id_1, ws_id, campaign_id, 3.50),
        )
    db_conn.commit()

    spend = guard.assert_workspace_spend(db_conn, ws_id)
    assert spend == Decimal("3.5")

    # Record another run with cost $2.00 -> total $5.50 -> exceeds $5.00 cap
    run_id_2 = uuid.uuid4()
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (
                id, workspace_id, campaign_id, trigger, status,
                window_start, window_end, run_cap_usd, code_version, pack_version, cost_usd
            )
            VALUES (
                %s, %s, %s, 'manual', 'completed',
                NOW(), NOW() + interval '1 day', 5.00, 'v1', 'v1', %s
            )
            """,
            (run_id_2, ws_id, campaign_id, 2.00),
        )
    db_conn.commit()

    with pytest.raises(BudgetOverrunError, match="budget overrun"):
        guard.assert_workspace_spend(db_conn, ws_id)


def test_resolve_storage_state_from_env_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """resolve_storage_state writes JSON content from env to a temp file."""
    monkeypatch.delenv("PLAYWRIGHT_STORAGE_STATE_PATH", raising=False)
    payload = {"cookies": [{"name": "auth_cookie", "value": "xyz"}], "origins": []}
    monkeypatch.setenv("PLAYWRIGHT_STORAGE_STATE", json.dumps(payload))

    resolved = resolve_storage_state()
    assert resolved is not None
    assert resolved.is_file()

    with open(resolved, encoding="utf-8") as f:
        data = json.load(f)
    assert data["cookies"][0]["value"] == "xyz"

    # Cleanup
    resolved.unlink(missing_ok=True)


def test_resolve_storage_state_from_file_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """resolve_storage_state respects an existing file path."""
    state_file = tmp_path / "state.json"
    state_file.write_text('{"cookies": []}', encoding="utf-8")

    monkeypatch.setenv("PLAYWRIGHT_STORAGE_STATE_PATH", str(state_file))
    monkeypatch.delenv("PLAYWRIGHT_STORAGE_STATE", raising=False)

    resolved = resolve_storage_state()
    assert resolved == state_file


def test_resolve_storage_state_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_storage_state returns None when no env var is provided."""
    monkeypatch.delenv("PLAYWRIGHT_STORAGE_STATE_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_STORAGE_STATE", raising=False)
    assert resolve_storage_state() is None


def test_e2e_live_workflow_file_structure() -> None:
    """Verify .github/workflows/e2e-live.yml adheres to M2-07 specification."""
    repo_root = Path(__file__).resolve().parent.parent
    workflow_path = repo_root / ".github" / "workflows" / "e2e-live.yml"
    assert workflow_path.is_file(), f"Workflow {workflow_path} not found"

    with open(workflow_path, encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    # Triggers
    triggers = workflow.get("on") or workflow.get(True) or {}
    assert "workflow_dispatch" in triggers, "Missing workflow_dispatch trigger"
    assert "schedule" in triggers, "Missing schedule trigger"

    cron_entries = [s["cron"] for s in triggers["schedule"]]
    assert "0 3 * * *" in cron_entries, "Schedule must include nightly cron 0 3 * * *"

    # Jobs
    jobs = workflow.get("jobs", {})
    assert "e2e-live" in jobs, "Missing e2e-live job"

    job = jobs["e2e-live"]
    step_names = [s.get("name", "") for s in job.get("steps", [])]

    # Verify key steps
    assert any("Playwright" in name for name in step_names), "Missing Playwright install step"
    assert any("Live E2E" in name and "J0–J8" in name for name in step_names), (
        "Live E2E step must cover J0–J8"
    )
    assert "J0–J8" in job.get("name", ""), "Job name must reference J0–J8"
    assert any("artifact" in name.lower() or "upload" in name.lower() for name in step_names), (
        "Missing trace/screenshot upload step"
    )


def test_live_e2e_suite_exports_journeys_j0_through_j8() -> None:
    """Verify tests/test_live_e2e_j0_j4.py defines all 9 live E2E journeys J0–J8."""
    import tests.test_live_e2e_j0_j4 as live_suite

    expected = [
        "test_journey_j0_live_auth_and_isolation",
        "test_journey_j1_live_campaign_settings_and_i18n",
        "test_journey_j2_live_sources_management",
        "test_journey_j3_live_run_and_weekly_report",
        "test_journey_j4_live_topic_approval_gate",
        "test_journey_j5_live_draft_and_evaluation",
        "test_journey_j6_live_publish_draft_mode",
        "test_journey_j7_live_reject_paths",
        "test_journey_j8_live_integrations_and_webhooks",
    ]
    for fn_name in expected:
        assert hasattr(live_suite, fn_name), f"Missing live E2E journey: {fn_name}"
        assert callable(getattr(live_suite, fn_name))


def test_pyproject_registers_live_e2e_marker() -> None:
    """Verify live_e2e marker is declared and excluded by default in pyproject.toml."""
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_path = repo_root / "pyproject.toml"

    content = pyproject_path.read_text(encoding="utf-8")
    assert "live_e2e:" in content
    assert "not live_e2e" in content
