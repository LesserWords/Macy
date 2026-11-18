"""Pipeline stage runner, state machine transitions, and event dispatcher (F18)."""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from engine import budget, db, events, observability, research
from engine.integrations.config import plugin_config
from engine.integrations.ports import ExtractorPort, FeedSourcePort, LLMPort, NewsSearchPort
from engine.integrations.registry import IntegrationMissing
from engine.integrations.rss import RSSFeedSource
from engine.integrations.trafilatura_extractor import TrafilaturaExtractor

logger = logging.getLogger(__name__)

PIPELINE_STAGES: tuple[str, ...] = (
    "research",
    "score",
    "report",
    "keywords",
    "brief",
    "write",
    "evaluate",
    "publish",
)


class ActiveRunExistsError(Exception):
    """Raised when an active run already exists for a campaign (runs_one_active partial index)."""


def _transition_initial_run_status(
    conn: psycopg.Connection[Any],
    ws_id: uuid.UUID,
    run_id: uuid.UUID,
    run_row: dict[str, Any],
) -> dict[str, Any]:
    """Transition run to running state based on current status (F18 AC4)."""
    status = run_row.get("status")
    if status == "queued":
        db.update_run_status(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            expected_status="queued",
            new_status="running",
            stage="research",
            started_at=datetime.now(UTC),
        )
        camp_id = run_row.get("campaign_id")
        if camp_id:
            db.expire_previous_opportunities(
                conn,
                workspace_id=ws_id,
                campaign_id=camp_id,
                current_run_id=run_id,
            )
        return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row

    if status == "blocked" and run_row.get("blocked_reason") == "integration_missing":
        db.update_run_status(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            expected_status="blocked",
            new_status="running",
            error={},
        )
        return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row

    if status == "blocked" and run_row.get("blocked_reason") == "cap_reached":
        current_cost = db.get_run_total_cost(conn, workspace_id=ws_id, run_id=run_id)
        run_cap = Decimal(str(run_row["run_cap_usd"]))
        if current_cost < run_cap:
            db.update_run_status(
                conn,
                workspace_id=ws_id,
                run_id=run_id,
                expected_status="blocked",
                new_status="running",
            )
            return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row

    if status == "failed":
        db.update_run_status(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            expected_status="failed",
            new_status="running",
            error={},
        )
        return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row

    return run_row


def _finish_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    ws_id: uuid.UUID,
    run_id: uuid.UUID,
    campaign_id: Any,
    *,
    status: str,
    error: dict[str, Any] | None = None,
) -> None:
    """Finalize run status, emit audit event, and send heartbeat."""
    total_cost = db.get_run_total_cost(conn, workspace_id=ws_id, run_id=run_id)
    now_dt = datetime.now(UTC)
    payload: dict[str, Any] = {
        "run_id": str(run_id),
        "campaign_id": str(campaign_id),
        "cost_usd": str(total_cost),
    }
    kwargs: dict[str, Any] = {
        "cost_usd": total_cost,
        "finished_at": now_dt,
    }
    if error is not None:
        kwargs["error"] = error
        payload["error"] = str(error.get("message", ""))
        payload["failed_at"] = now_dt.isoformat()
    else:
        payload["finished_at"] = now_dt.isoformat()

    db.update_run_status(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="running",
        new_status=status,
        **kwargs,
    )
    events.emit(
        conn,
        workspace_id=ws_id,
        event_type=f"run.{status}",
        entity_type="run",
        entity_id=run_id,
        payload=payload,
    )
    observability.send_heartbeat(failed=(status == "failed"))


_LIVE_ENV_NAMES = frozenset({"staging", "production", "prod"})


def _is_live_mode(offline: bool | None) -> bool:
    if offline is not None:
        return not offline
    return os.environ.get("APP_ENV", "").strip().lower() in _LIVE_ENV_NAMES


def _instantiate_plugin(
    plugin_cls: type[Any],
    settings_obj: Any,
    *,
    conn: psycopg.Connection[Any],
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Any:
    params = inspect.signature(plugin_cls.__init__).parameters
    context_kwargs = {"conn": conn, "workspace_id": workspace_id, "run_id": run_id}
    kwargs = {k: v for k, v in context_kwargs.items() if k in params}
    return plugin_cls(settings_obj, **kwargs)


def _resolve_port_adapter(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    port: str,
    explicit: Any | None,
    offline: bool | None,
    fallback_factory: Any,
    required_in_live: bool,
) -> Any:
    if explicit is not None:
        return explicit
    if offline is True:
        return fallback_factory()
    live_mode = _is_live_mode(offline)
    try:
        plugin_cls, settings_obj = plugin_config(conn, workspace_id=workspace_id, port=port)
    except IntegrationMissing:
        if live_mode and required_in_live:
            raise
        return fallback_factory()
    return _instantiate_plugin(
        plugin_cls,
        settings_obj,
        conn=conn,
        workspace_id=workspace_id,
        run_id=run_id,
    )


def _resolve_run_adapters(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    ws_id: uuid.UUID,
    run_id: uuid.UUID,
    offline: bool | None,
    feed_adapter: FeedSourcePort | None,
    news_search_adapter: NewsSearchPort | None,
    extractor_adapter: ExtractorPort | None,
    llm_adapter: LLMPort | None,
) -> tuple[FeedSourcePort, NewsSearchPort, ExtractorPort, LLMPort]:
    from engine.integrations.fakes import FakeExtractor, FakeFeedSource, FakeLLM, FakeNewsSearch

    live_mode = _is_live_mode(offline)
    f_fallback = RSSFeedSource if live_mode else FakeFeedSource
    ext_fallback = TrafilaturaExtractor if live_mode else FakeExtractor

    f_adapter = _resolve_port_adapter(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        port="feed_source",
        explicit=feed_adapter,
        offline=offline,
        fallback_factory=f_fallback,
        required_in_live=False,
    )
    ext_adapter = _resolve_port_adapter(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        port="extractor",
        explicit=extractor_adapter,
        offline=offline,
        fallback_factory=ext_fallback,
        required_in_live=False,
    )
    ns_adapter = _resolve_port_adapter(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        port="news_search",
        explicit=news_search_adapter,
        offline=offline,
        fallback_factory=FakeNewsSearch,
        required_in_live=True,
    )
    l_adapter = _resolve_port_adapter(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        port="llm",
        explicit=llm_adapter,
        offline=offline,
        fallback_factory=FakeLLM,
        required_in_live=True,
    )
    return f_adapter, ns_adapter, ext_adapter, l_adapter


def _mark_integration_missing(
    conn: psycopg.Connection[Any],
    ws_id: uuid.UUID,
    run_id: uuid.UUID,
    run_row: dict[str, Any],
    exc: IntegrationMissing,
) -> dict[str, Any]:
    db.update_run_status(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="running",
        new_status="blocked",
        blocked_reason="integration_missing",
        error={"message": str(exc), "port": exc.port},
    )
    events.emit(
        conn,
        workspace_id=ws_id,
        event_type="run.blocked",
        entity_type="run",
        entity_id=run_id,
        payload={
            "run_id": str(run_id),
            "campaign_id": str(run_row["campaign_id"]),
            "reason": "integration_missing",
            "missing_port": exc.port,
        },
    )
    observability.send_heartbeat(failed=True)
    return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row


def execute_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    run_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID | None = None,
    offline: bool | None = None,
    feed_adapter: FeedSourcePort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any]:
    """Execute stages for a run with Postgres state machine and budget guards."""
    with conn.cursor() as cur:
        query = "SELECT workspace_id, id FROM runs WHERE id = %s"
        params: list[Any] = [run_id]
        if workspace_id is not None:
            query += " AND workspace_id = %s"
            params.append(workspace_id)
        cur.execute(query, tuple(params))
        row = cur.fetchone()
        if not row:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)
        ws_id = uuid.UUID(str(row[0]))

    run_row = db.get_run(conn, workspace_id=ws_id, run_id=run_id)
    if not run_row:
        msg = f"Run {run_id} not found"
        raise ValueError(msg)

    run_row = _transition_initial_run_status(conn, ws_id, run_id, run_row)
    if run_row["status"] != "running":
        return run_row

    observability.ensure_sentry_initialized()
    observability.set_observability_tags(workspace_id=str(ws_id), run_id=str(run_id))

    # 1. Check budget guard before research stage (F17 AC2)
    run_cap = Decimal(str(run_row["run_cap_usd"]))
    try:
        budget.check_run_budget(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            run_cap_usd=run_cap,
            estimated_cost=Decimal("0.000000"),
        )
    except budget.BudgetCapReached:
        return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row

    # 2. Resolve adapters (blocking cleanly if live mode lacks llm or news_search)
    try:
        f_adapter, ns_adapter, ext_adapter, l_adapter = _resolve_run_adapters(
            conn,
            ws_id=ws_id,
            run_id=run_id,
            offline=offline,
            feed_adapter=feed_adapter,
            news_search_adapter=news_search_adapter,
            extractor_adapter=extractor_adapter,
            llm_adapter=llm_adapter,
        )
    except IntegrationMissing as exc:
        return _mark_integration_missing(conn, ws_id, run_id, run_row, exc)

    # 3. Execute Research and Scoring Stages
    try:
        research.collect(
            conn,
            run_row,
            feed_adapter=f_adapter,
            news_search_adapter=ns_adapter,
            extractor_adapter=ext_adapter,
            llm_adapter=l_adapter,
        )

        from engine.strategy import score

        db.update_run_status(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            expected_status="running",
            new_status="running",
            stage="score",
        )
        score.score_run(conn, run_row, llm_adapter=l_adapter)

        _finish_run(conn, ws_id, run_id, run_row["campaign_id"], status="completed")
    except Exception as exc:
        observability.capture_exception(
            exc,
            workspace_id=str(ws_id),
            run_id=str(run_id),
            extra={"campaign_id": str(run_row.get("campaign_id"))},
        )
        _finish_run(
            conn,
            ws_id,
            run_id,
            run_row["campaign_id"],
            status="failed",
            error={"message": str(exc)},
        )

    return db.get_run(conn, workspace_id=ws_id, run_id=run_id) or run_row


def process_next_queued_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None = None,
    **pipeline_adapters: Any,
) -> dict[str, Any] | None:
    """Worker loop step: claim next queued run with SKIP LOCKED and execute it."""
    claimed = db.claim_next_queued_run(conn, workspace_id=workspace_id)
    if not claimed:
        return None

    return execute_run(
        conn,
        uuid.UUID(str(claimed["id"])),
        workspace_id=uuid.UUID(str(claimed["workspace_id"])),
        **pipeline_adapters,
    )


def re_run_failed_stage_items(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    stage: str = "research",
    offline: bool | None = None,
    extractor_adapter: ExtractorPort | None = None,
    llm_adapter: LLMPort | None = None,
) -> list[dict[str, Any]]:
    """Re-run failed items for a stage within a run (F18 AC3)."""
    if stage == "research":
        failed_records = db.list_research_records(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            status="failed",
        )
        if not failed_records:
            return []
        from engine.integrations.fakes import FakeExtractor, FakeLLM

        live_mode = _is_live_mode(offline)
        ext_fallback = TrafilaturaExtractor if live_mode else FakeExtractor
        ext_adapter = _resolve_port_adapter(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            port="extractor",
            explicit=extractor_adapter,
            offline=offline,
            fallback_factory=ext_fallback,
            required_in_live=False,
        )
        l_adapter = _resolve_port_adapter(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            port="llm",
            explicit=llm_adapter,
            offline=offline,
            fallback_factory=FakeLLM,
            required_in_live=True,
        )
        re_run_records: list[dict[str, Any]] = []
        for rec in failed_records:
            re_run = research.re_run_failed_record(
                conn,
                workspace_id=workspace_id,
                record_id=uuid.UUID(str(rec["id"])),
                extractor_adapter=ext_adapter,
                llm_adapter=l_adapter,
            )
            if re_run is not None:
                re_run_records.append(re_run)
        return re_run_records

    if stage == "score":
        from engine.integrations.fakes import FakeLLM
        from engine.strategy import score

        run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
        if run_row:
            l_adapter = _resolve_port_adapter(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                port="llm",
                explicit=llm_adapter,
                offline=offline,
                fallback_factory=FakeLLM,
                required_in_live=True,
            )
            return score.score_run(conn, run_row, llm_adapter=l_adapter)

    return []
