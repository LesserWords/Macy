"""Additional coverage tests for runs, pipeline, scheduler, and CLI edge cases."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from engine import cli, db, research, services
from engine.integrations.fakes import FakeExtractor, FakeLLM


def _setup_coverage_env(
    conn: psycopg.Connection[Any],
) -> tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]:
    slug = f"cov-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        conn,
        slug=slug,
        name="Coverage Workspace",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        conn,
        email=f"cov-{uuid.uuid4().hex[:8]}@example.com",
        name="Coverage User",
    )
    user_id = user["user_id"]
    db.create_membership(conn, workspace_id=ws_id, user_id=user_id, role="owner")
    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name="Coverage Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("5.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    return slug, ws_id, user_id, camp_id


def test_db_list_workspaces_and_crontab_all(db_conn: psycopg.Connection[Any]) -> None:
    slug, ws_id, user_id, camp_id = _setup_coverage_env(db_conn)
    ws_list = db.list_workspaces(db_conn)
    assert any(w["workspace_id"] == ws_id for w in ws_list)

    crontab = services.generate_crontab(db_conn, workspace_id=None)
    assert f"--workspace {ws_id}" in crontab


def test_re_run_failed_record_edge_cases(
    db_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, ws_id, user_id, camp_id = _setup_coverage_env(db_conn)
    # 1. Nonexistent record
    assert (
        research.re_run_failed_record(db_conn, workspace_id=ws_id, record_id=uuid.uuid4()) is None
    )

    # 2. Record pointing to missing document (simulated via get_document returning None)
    run_id = services.run_now(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)
    doc_res = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/bad",
        canonical_url="https://example.com/bad",
        title="Bad Doc",
        date_status="unverified",
        fetch_status="ok",
    )
    doc_id = uuid.UUID(str(doc_res["id"]))
    bad_rec = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_id,
        status="failed",
        schema_version=1,
        payload={"url": "https://example.com/bad"},
    )
    with patch("engine.db.get_document", return_value=None):
        assert (
            research.re_run_failed_record(
                db_conn,
                workspace_id=ws_id,
                record_id=uuid.UUID(str(bad_rec["id"])),
            )
            is None
        )

    # 3. Document fetch_status != ok and extractor fails
    doc_blocked = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/blocked",
        canonical_url="https://example.com/blocked",
        title="Blocked Doc",
        date_status="unverified",
        fetch_status="blocked",
    )
    doc_blocked_id = uuid.UUID(str(doc_blocked["id"]))
    rec_blocked = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_blocked_id,
        status="failed",
        schema_version=1,
        payload={"url": "https://example.com/blocked"},
    )

    from engine.contracts import ResearchRecord

    class FailingExtractor(FakeExtractor):
        def extract(self, url: str, html: str | None = None) -> Any:
            return ResearchRecord(
                url=url,
                headline="Blocked Headline",
                summary="Blocked Summary",
                source="Blocked Source",
                raw={"fetch_status": "extraction_failed"},
            )

    res_blocked = research.re_run_failed_record(
        db_conn,
        workspace_id=ws_id,
        record_id=uuid.UUID(str(rec_blocked["id"])),
        extractor_adapter=FailingExtractor(),
    )
    assert res_blocked is not None
    assert res_blocked["status"] == "failed"

    # 4. LLM failure during re_run
    doc_ok = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/story-llm-fail",
        canonical_url="https://example.com/story-llm-fail",
        title="Valid Doc",
        date_status="unverified",
        fetch_status="ok",
        extracted_text="Raw text content passing minimum word requirements for research.",
    )
    doc_ok_id = uuid.UUID(str(doc_ok["id"]))
    rec_llm_fail = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_ok_id,
        status="failed",
        schema_version=1,
        payload={"url": "https://example.com/story-llm-fail"},
    )

    class CrashingLLM(FakeLLM):
        def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM rate limit reached")

    res_llm = research.re_run_failed_record(
        db_conn,
        workspace_id=ws_id,
        record_id=uuid.UUID(str(rec_llm_fail["id"])),
        llm_adapter=CrashingLLM(),
    )
    assert res_llm is not None
    assert res_llm["status"] == "failed"


def _create_failed_research_record(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    url: str,
) -> dict[str, Any]:
    doc_res = db.upsert_document(
        conn,
        workspace_id=workspace_id,
        url=url,
        canonical_url=url,
        title="Valid Story",
        date_status="unverified",
        fetch_status="ok",
        extracted_text="Raw text content passing minimum word requirements for research.",
    )
    doc_id = uuid.UUID(str(doc_res["id"]))
    return db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        document_id=doc_id,
        status="failed",
        schema_version=1,
        payload={"url": url},
    )


def test_services_resolve_and_run_stage_edge_cases(db_conn: psycopg.Connection[Any]) -> None:
    slug, ws_id, user_id, camp_id = _setup_coverage_env(db_conn)

    # Empty workspace campaigns
    empty_slug = f"empty-ws-{uuid.uuid4().hex[:8]}"
    empty_ws_id = db.create_workspace(
        db_conn,
        slug=empty_slug,
        name="Empty Workspace",
        industry_pack="general",
    )
    with pytest.raises(ValueError, match="No campaigns found"):
        services.resolve_campaign(db_conn, workspace_id=empty_ws_id)

    # Nonexistent campaign identifier
    with pytest.raises(ValueError, match="not found in workspace"):
        services.resolve_campaign(
            db_conn, workspace_id=ws_id, campaign_identifier="nonexistent-campaign"
        )

    # Nonexistent run ID
    with pytest.raises(ValueError, match="No run or research record found"):
        services.run_stage(db_conn, stage_or_record_id=uuid.uuid4())

    # run_stage with research record
    run_id = services.run_now(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)
    rec = _create_failed_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        url="https://example.com/rec-test",
    )
    res_stage_rec = services.run_stage(
        db_conn,
        stage_or_record_id=str(rec["id"]),
        offline=True,
    )
    assert res_stage_rec["type"] == "research_record"
    assert res_stage_rec["record"]["status"] == "extracted"


def test_cli_error_and_edge_paths(db_conn: psycopg.Connection[Any]) -> None:
    slug, ws_id, user_id, camp_id = _setup_coverage_env(db_conn)

    # 1. run-week error
    code1 = cli.main(["run-week", "nonexistent-ws-slug-xyz", "--offline"])
    assert code1 == 1

    # 2. run-stage error
    code2 = cli.main(["run-stage", str(uuid.uuid4())])
    assert code2 == 1

    # 3. run-now error
    code3 = cli.main(["run-now", "nonexistent-ws-slug-xyz"])
    assert code3 == 1

    # 4. crontab error
    code4 = cli.main(["crontab", "--workspace", "nonexistent-ws-slug-xyz"])
    assert code4 == 1

    # 5. run-stage with record id
    run_id = services.run_now(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)
    rec = _create_failed_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        url="https://example.com/cli-rec",
    )
    code5 = cli.main(["run-stage", str(rec["id"]), "--offline"])
    assert code5 == 0


def test_services_managed_conn_fallbacks(
    db_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that service entrypoints call get_connection() when conn is None."""
    slug, ws_id, user_id, camp_id = _setup_coverage_env(db_conn)

    @contextmanager
    def _mock_conn(*args: Any, **kwargs: Any) -> Any:
        yield db_conn

    monkeypatch.setattr(db, "get_connection", _mock_conn)

    # 1. generate_crontab with conn=None
    ct = services.generate_crontab(conn=None, workspace_id=ws_id)
    assert str(ws_id) in ct

    # 2. list_runs with conn=None
    runs = services.list_runs(conn=None, workspace_id=ws_id, user_id=user_id)
    assert isinstance(runs, list)

    # 3. resolve_campaign with conn=None
    camp = services.resolve_campaign(conn=None, workspace_id=ws_id)
    assert camp["id"] == camp_id

    # 4. resolve_workspace with conn=None
    ws = services.resolve_workspace(conn=None, workspace_identifier=slug)
    assert ws["workspace_id"] == ws_id

    # 5. run_now with conn=None
    run_id = services.run_now(conn=None, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)
    assert isinstance(run_id, uuid.UUID)

    # 6. get_run with conn=None
    r = services.get_run(conn=None, workspace_id=ws_id, run_id=run_id, user_id=user_id)
    assert r is not None

    # 7. run_week with conn=None
    # clean up running status first so we can trigger a weekly run
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status=None,
        new_status="completed",
    )
    week_res = services.run_week(conn=None, workspace_identifier=slug, offline=True)
    assert week_res.get("status") in ("completed", "running")

    # 8. trigger_scheduled_runs with conn=None
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status=None,
        new_status="completed",
    )
    triggered = services.trigger_scheduled_runs(conn=None, workspace_id=ws_id)
    assert len(triggered) == 1

    # 9. run_stage with conn=None
    stage_res = services.run_stage(conn=None, stage_or_record_id=run_id, offline=True)
    assert stage_res["type"] == "run"


def test_pipeline_and_services_edge_coverage(  # noqa: PLR0915
    db_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, ws_id, user_id, camp_id = _setup_coverage_env(db_conn)

    # 1. scheduler lines 33-34: campaign not found
    with pytest.raises(ValueError, match="not found"):
        from engine import scheduler

        scheduler.trigger_campaign_run(
            db_conn,
            workspace_id=ws_id,
            campaign_id=uuid.uuid4(),
            created_by=user_id,
        )

    # 2. pipeline lines 148-149: run not found by execute_run
    with pytest.raises(ValueError, match="not found"):
        from engine import pipeline

        pipeline.execute_run(db_conn, run_id=uuid.uuid4(), workspace_id=ws_id)

    # 3. pipeline line 216: process_next_queued_run when queue is empty
    empty_claim = pipeline.process_next_queued_run(db_conn, workspace_id=ws_id)
    assert empty_claim is None

    # 4. pipeline line 257: re_run_failed_stage_items with non-research stage
    assert (
        pipeline.re_run_failed_stage_items(
            db_conn, workspace_id=ws_id, run_id=uuid.uuid4(), stage="score"
        )
        == []
    )

    # 5. pipeline lines 54-58, 65, 67-68, 76, 78, 159, 171-172:
    # Create run with cap $5.00
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("5.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=user_id,
    )
    # Set to blocked(cap_reached) with cost 10.00 > cap 5.00
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="queued",
        new_status="blocked",
        blocked_reason="cap_reached",
        cost_usd=Decimal("10.00"),
    )
    # Record usage ledger to make db.get_run_total_cost reflect 10.00
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="research",
        kind="llm",
        vendor="fake",
        model="mock",
        input_tokens=1000,
        output_tokens=1000,
        cost_usd=Decimal("10.00"),
    )
    # Executing run when cost >= cap returns run_row without running (lines 54-58, 159)
    res_blocked = pipeline.execute_run(db_conn, run_id=run_id, workspace_id=ws_id)
    assert res_blocked["status"] == "blocked"

    # Set to failed state and re-run (lines 65, 67-68)
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="blocked",
        new_status="failed",
        error={"msg": "fail"},
    )
    # Executing run resumes from failed to running, then hits BudgetCapReached (lines 171-172)
    res_fail_rerun = pipeline.execute_run(db_conn, run_id=run_id, workspace_id=ws_id)
    assert res_fail_rerun["status"] == "blocked"

    # Now raise cap to 20.00 so cost 10.00 < cap 20.00
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="blocked",
        new_status="blocked",
        run_cap_usd=Decimal("20.00"),
    )
    # execute_run will resume from blocked to running (lines 54-58) and complete
    res_resumed = pipeline.execute_run(db_conn, run_id=run_id, workspace_id=ws_id)
    assert res_resumed["status"] == "completed"

    # Re-run stage on already completed run (services line 1776)
    stage_res = services.run_stage(db_conn, stage_or_record_id=run_id, offline=True)
    assert stage_res["run"]["status"] == "completed"

    # 6. services lines 1536-1537: user not a member
    unrelated_user = db.create_or_get_user(db_conn, email="stranger@example.com", name="Stranger")
    with pytest.raises(PermissionError, match="not a member"):
        services.list_runs(db_conn, workspace_id=ws_id, user_id=unrelated_user["user_id"])

    # 7. services lines 1572-1573: get_run user not member
    with pytest.raises(PermissionError, match="not a member"):
        services.get_run(
            db_conn, workspace_id=ws_id, run_id=run_id, user_id=unrelated_user["user_id"]
        )
    # resolve_workspace line 1590: workspace not found
    with pytest.raises(ValueError, match="not found"):
        services.resolve_workspace(db_conn, workspace_identifier=str(uuid.uuid4()))

    # 8. services line 1627: find campaign by name
    camp_by_name = services.resolve_campaign(
        db_conn, workspace_id=ws_id, campaign_identifier="Coverage Campaign"
    )
    assert camp_by_name["id"] == camp_id

    # 9. services lines 1676-1677: run_week no user or owner found
    # Create workspace with no owner membership
    orphan_slug = f"orphan-ws-{uuid.uuid4().hex[:8]}"
    db.create_workspace(
        db_conn,
        slug=orphan_slug,
        name="Orphan Workspace",
        industry_pack="general",
    )
    with pytest.raises(ValueError, match="No user or owner found"):
        services.run_week(db_conn, workspace_identifier=orphan_slug)

    # 10. cli lines 144-145: run-now with no user or owner
    cli_code = cli.main(["run-now", orphan_slug])
    assert cli_code == 1

    # 11. services line 1710: run_week with conn passed
    run_week_res = services.run_week(db_conn, workspace_identifier=str(ws_id), offline=True)
    assert run_week_res["status"] == "completed"

    # 12. services lines 1821-1822: run_stage run not found on db.get_run
    with monkeypatch.context() as m:
        m.setattr(db, "get_run", lambda *_, **__: None)
        with pytest.raises(ValueError, match="not found"):
            services.run_stage(db_conn, stage_or_record_id=run_id, offline=True)

    # 13. services line 1862: trigger_scheduled_runs with conn passed
    triggered = services.trigger_scheduled_runs(db_conn, workspace_id=ws_id)
    assert isinstance(triggered, list)

    # 14. services line 1537: list_runs with conn passed
    runs_list = services.list_runs(db_conn, workspace_id=ws_id, user_id=user_id)
    assert len(runs_list) >= 1

    # 15. services line 1573: get_run with conn passed
    run_detail = services.get_run(db_conn, workspace_id=ws_id, run_id=run_id, user_id=user_id)
    assert run_detail is not None and run_detail["id"] == run_id

    # 16. pipeline lines 154-155: execute_run when get_run returns None
    with monkeypatch.context() as m:
        m.setattr(db, "get_run", lambda *_, **__: None)
        with pytest.raises(ValueError, match="not found"):
            pipeline.execute_run(db_conn, run_id=run_id, workspace_id=ws_id)

    # 17. pipeline line 216: process_next_queued_run with an actual claimed run
    camp2_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Campaign Two",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("50.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    _queued_run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp2_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("50.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=user_id,
    )
    claimed_result = pipeline.process_next_queued_run(db_conn, workspace_id=ws_id)
    assert claimed_result is not None and claimed_result["status"] == "completed"

    # 18. pipeline lines 247, 254: re_run_failed_stage_items with actual failed record
    src = db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack="general",
        name="Test Source",
        domain="example.com",
        url="https://example.com",
        feed_url=None,
        fetch_mode="site_search",
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["general"],
    )
    raw_doc = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://example.com/failed-doc",
        canonical_url="https://example.com/failed-doc",
        source_id=src["id"],
        title="Failed Doc",
        lang="pt-BR",
        published_at=now,
        date_status="unverified",
        fetch_status="ok",
        http_status=200,
        extracted_text="Content for failed doc extraction",
    )
    db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=raw_doc["id"],
        status="failed",
        schema_version=1,
        payload={"error": "simulated"},
    )
    rerun_recs = pipeline.re_run_failed_stage_items(
        db_conn, workspace_id=ws_id, run_id=run_id, stage="research"
    )
    assert len(rerun_recs) == 1

    # 19. services line 1776: run_stage when run is in failed state
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status=None,
        new_status="failed",
        error={"msg": "failure to rerun"},
    )
    stage_rerun = services.run_stage(db_conn, stage_or_record_id=run_id, offline=True)
    assert stage_rerun["run"]["status"] in ("completed", "running")
