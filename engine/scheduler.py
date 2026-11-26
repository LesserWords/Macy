"""Campaign scheduler helper and Run now service functions (F18 AC1-AC2, F17 AC3)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from engine import budget, db
from engine.monitors import runner as monitor_runner
from engine.pipeline import ActiveRunExistsError

CURRENT_CODE_VERSION: str = "0.1.0"
CURRENT_PACK_VERSION: str = "1.0.0"


def trigger_campaign_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    trigger: str = "manual",
    window_days: int = 7,
    created_by: uuid.UUID | None = None,
    code_version: str = CURRENT_CODE_VERSION,
    pack_version: str = CURRENT_PACK_VERSION,
) -> uuid.UUID:
    """Create a new run row enforcing one active run and monthly caps (F18 AC1-AC2, F17 AC3)."""
    campaign = db.get_campaign(conn, workspace_id=workspace_id, campaign_id=campaign_id)
    if not campaign:
        msg = f"Campaign {campaign_id} not found"
        raise ValueError(msg)

    # 1. Enforce monthly cap refusal (F17 AC3)
    monthly_cap = Decimal(str(campaign["monthly_cap_usd"]))
    budget.check_monthly_budget(conn, workspace_id=workspace_id, monthly_cap_usd=monthly_cap)

    # 2. Window and run cap snapshot
    now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)
    run_cap = Decimal(str(campaign["run_cap_usd"]))

    # 3. Create run row (unique partial index runs_one_active enforces 1 active run)
    try:
        run_id = db.create_run(
            conn,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            trigger=trigger,
            window_start=window_start,
            window_end=now,
            run_cap_usd=run_cap,
            code_version=code_version,
            pack_version=pack_version,
            created_by=created_by,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ActiveRunExistsError(
            f"An active run is already queued or running for campaign {campaign_id}"
        ) from exc

    return run_id


def generate_crontab(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None = None,
) -> str:
    """Generate Supercronic crontab reading schedule_cron in workspace timezone (F18 AC1)."""
    lines: list[str] = [
        "# Supercronic crontab for Macy Content Engine",
    ]
    for ws in _iter_workspaces(conn, workspace_id):
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        tz_name = ws.get("timezone", "UTC")
        for camp in db.list_campaigns(conn, workspace_id=ws_id):
            cron_expr = str(camp.get("schedule_cron", "0 9 * * 1")).strip()
            lines.append(
                f"CRON_TZ={tz_name} {cron_expr} macy run-week --workspace {ws_id} "
                f"--campaign {camp['id']}"
            )

    lines.append("CRON_TZ=UTC 0 2 * * * macy purge")
    return "\n".join(lines) + "\n"


def _iter_workspaces(
    conn: psycopg.Connection[Any], workspace_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    if workspace_id is not None:
        single = db.get_workspace(conn, workspace_id=workspace_id)
        return [single] if single else []
    return db.list_workspaces(conn)


def trigger_due_scheduled_runs(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Trigger scheduled runs for campaigns (F18 AC1)."""
    triggered: list[uuid.UUID] = []
    for ws in _iter_workspaces(conn, workspace_id):
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        campaigns = db.list_campaigns(conn, workspace_id=ws_id)
        for camp in campaigns:
            camp_id = uuid.UUID(str(camp["id"]))
            try:
                run_id = trigger_campaign_run(
                    conn,
                    workspace_id=ws_id,
                    campaign_id=camp_id,
                    trigger="scheduled",
                )
                triggered.append(run_id)
            except (ActiveRunExistsError, budget.MonthlyCapReached):
                continue

    return triggered


def run_due_url_monitors(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None = None,
    retry_delay_seconds: float = 30.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    tls_checker: Callable[[str], int | None] | None = None,
) -> list[dict[str, Any]]:
    """Run all configured URL/TLS route monitors across target workspaces (M6-03 D3)."""
    results: list[dict[str, Any]] = []
    checker = tls_checker or monitor_runner.check_tls_days_left
    for ws in _iter_workspaces(conn, workspace_id):
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        for mon in db.list_url_monitors(conn, workspace_id=ws_id):
            results.append(
                monitor_runner.run_route_monitor(
                    conn,
                    workspace_id=ws_id,
                    url=str(mon["url"]),
                    score_drop_threshold=float(mon["score_drop_threshold"]),
                    tls_expiry_warn_days=int(mon["tls_expiry_warn_days"]),
                    retry_delay_seconds=retry_delay_seconds,
                    sleep_fn=sleep_fn,
                    tls_checker=checker,
                )
            )
    return results
