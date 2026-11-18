"""Tests for F18: Run orchestration, stage runner, state machine, failure isolation, and events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from engine import db, pipeline, services
from engine.contracts import ResearchRecord
from engine.integrations.fakes import FakeExtractor, FakeFeedSource


def _setup_test_workspace_and_campaign(
    conn: psycopg.Connection[Any],
    *,
    monthly_cap: Decimal = Decimal("200.00"),
    run_cap: Decimal = Decimal("10.00"),
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    slug = f"ws-pipe-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        conn,
        slug=slug,
        name="Pipeline Test Workspace",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        conn,
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        name="Pipeline Owner",
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
        name="Pipeline Orchestration Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=monthly_cap,
        run_cap_usd=run_cap,
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    return ws_id, user_id, camp_id


def test_one_active_run_constraint_f18_ac2(db_conn: psycopg.Connection[Any]) -> None:
    """WHEN Run now is clicked while a run is queued/running, THEN it is refused (F18 AC2)."""
    ws_id, user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)

    # 1. Trigger first run -> succeeds
    run_id_1 = services.run_now(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        user_id=user_id,
    )
    assert run_id_1 is not None

    # 2. Trigger second run while first is still queued -> refused with ActiveRunExistsError
    with pytest.raises(pipeline.ActiveRunExistsError):
        services.run_now(
            db_conn,
            workspace_id=ws_id,
            campaign_id=camp_id,
            user_id=user_id,
        )

    # 3. Transition run 1 to running -> still refused
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id_1,
        expected_status="queued",
        new_status="running",
    )
    with pytest.raises(pipeline.ActiveRunExistsError):
        services.run_now(
            db_conn,
            workspace_id=ws_id,
            campaign_id=camp_id,
            user_id=user_id,
        )

    # 4. Mark run 1 completed -> new run can now be queued
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id_1,
        expected_status="running",
        new_status="completed",
    )
    run_id_2 = services.run_now(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        user_id=user_id,
    )
    assert run_id_2 != run_id_1


def _seed_test_feed_source(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    name: str = "Optical News Feed",
    feed_url: str = "https://opticalnews.example.com/rss.xml",
) -> dict[str, Any]:
    return db.create_source(
        conn,
        workspace_id=workspace_id,
        name=name,
        domain="opticalnews.example.com",
        industry_pack="general",
        url="https://opticalnews.example.com",
        fetch_mode="feed",
        feed_url=feed_url,
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["trends"],
        enabled=True,
    )


def _make_pipeline_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
    run_cap: Decimal = Decimal("10.00"),
) -> uuid.UUID:
    now = datetime.now(UTC)
    return db.create_run(
        conn,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=run_cap,
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=user_id,
    )


def test_two_worker_claim_skip_locked_no_double_processing(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Two workers claiming with SKIP LOCKED never double-process the same run (F18 AC3)."""
    ws_id, user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)

    run_id = _make_pipeline_run(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)

    from tests.conftest import TEST_DATABASE_URL

    # Open two separate worker connections
    conn_worker1 = db.get_connection(TEST_DATABASE_URL)
    conn_worker2 = db.get_connection(TEST_DATABASE_URL)

    try:
        # Worker 1 starts transaction and claims the queued run
        with conn_worker1.transaction():
            claimed_w1 = db.claim_next_queued_run(conn_worker1, workspace_id=ws_id)
            assert claimed_w1 is not None
            assert claimed_w1["id"] == run_id

            # Worker 2 in parallel attempts to claim
            with conn_worker2.transaction():
                claimed_w2 = db.claim_next_queued_run(conn_worker2, workspace_id=ws_id)
                # Worker 2 must skip the row locked by Worker 1 and receive None
                assert claimed_w2 is None

            # Worker 1 transitions row to running
            db.update_run_status(
                conn_worker1,
                workspace_id=ws_id,
                run_id=run_id,
                expected_status="queued",
                new_status="running",
            )

        # After Worker 1 commits, Worker 2 still cannot claim it because it is no longer 'queued'
        with conn_worker2.transaction():
            claimed_w2_after = db.claim_next_queued_run(conn_worker2, workspace_id=ws_id)
            assert claimed_w2_after is None

    finally:
        conn_worker1.close()
        conn_worker2.close()


def test_failure_isolation_and_run_stage_rerun_f18_ac3(
    db_conn: psycopg.Connection[Any],
) -> None:
    """WHEN an item fails extraction, other rows continue and failed row can be re-run (F18 AC3)."""
    ws_id, user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)

    # Seed an enabled feed source
    _seed_test_feed_source(db_conn, workspace_id=ws_id)

    # Create feed adapter with 2 items
    class CustomFeedSource(FakeFeedSource):
        def fetch_feed(self, url: str, since: datetime | None = None) -> list[ResearchRecord]:
            return [
                ResearchRecord(
                    url="https://opticalnews.example.com/ok-story",
                    headline="História de Sucesso na Óptica",
                    summary="Sumário da matéria ok",
                    source="Optical News Feed",
                    source_type="feed",
                    published_at=datetime.now(UTC),
                ),
                ResearchRecord(
                    url="https://opticalnews.example.com/failing-story",
                    headline="História com Falha",
                    summary="Sumário da matéria que vai falhar",
                    source="Optical News Feed",
                    source_type="feed",
                    published_at=datetime.now(UTC),
                ),
            ]

    feed_adapter = CustomFeedSource()

    class FailingExtractor(FakeExtractor):
        def extract(self, url: str, html: str | None = None) -> ResearchRecord:
            if "failing-story" in url:
                return ResearchRecord(
                    url=url,
                    headline="História com Falha",
                    summary="Resumo de falha",
                    source="Optical News Feed",
                    raw={
                        "fetch_status": "http_error",
                        "http_status": 500,
                    },
                )
            return super().extract(url, html=html)

    run_id = _make_pipeline_run(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)

    # Execute run with FailingExtractor
    pipeline.execute_run(
        db_conn,
        run_id,
        workspace_id=ws_id,
        feed_adapter=feed_adapter,
        extractor_adapter=FailingExtractor(),
    )

    # 1. Check research_records: 1 extracted, 1 failed for optical news feed
    records = db.list_research_records(db_conn, workspace_id=ws_id, run_id=run_id)
    optical_records = [
        r
        for r in records
        if "opticalnews.example.com"
        in (r["payload"].get("url") or r["payload"].get("canonical_url") or "")
    ]
    assert len(optical_records) == 2
    status_map = {
        r["payload"].get("url") or r["payload"].get("canonical_url"): r["status"]
        for r in optical_records
    }
    assert status_map.get("https://opticalnews.example.com/ok-story") == "extracted"
    assert status_map.get("https://opticalnews.example.com/failing-story") == "failed"

    # 2. Re-run failed item using run_stage with working extractor
    failed_record = next(r for r in optical_records if r["status"] == "failed")
    res = services.run_stage(
        db_conn,
        stage_or_record_id=failed_record["id"],
        offline=True,
    )
    assert res["type"] == "research_record"
    updated_rec = res["record"]
    assert updated_rec["status"] == "extracted"


def test_pipeline_events_and_heartbeat_f18_ac5(
    db_conn: psycopg.Connection[Any],
) -> None:
    """WHEN a run completes or fails, events are emitted and heartbeat is sent (F18 AC5)."""
    ws_id, user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)
    _seed_test_feed_source(db_conn, workspace_id=ws_id)
    run_id = _make_pipeline_run(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)

    with patch("engine.observability.send_heartbeat") as mock_hb:
        pipeline.execute_run(db_conn, run_id, workspace_id=ws_id)
        mock_hb.assert_called_once_with(failed=False)

    # Verify run.completed event
    events = db.list_events(db_conn, workspace_id=ws_id)
    completed_events = [e for e in events if e["type"] == "run.completed"]
    assert len(completed_events) == 1
    assert completed_events[0]["payload"]["run_id"] == str(run_id)

    # Clean up run status to avoid conflict
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status=None,
        new_status="completed",
    )

    # Test run.failed event
    run_id_fail = _make_pipeline_run(
        db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id
    )

    class CrashingFeed(FakeFeedSource):
        def fetch_feed(self, *args: Any, **kwargs: Any) -> list[ResearchRecord]:
            raise RuntimeError("Feed provider network down")

    with (
        patch("engine.observability.send_heartbeat") as mock_hb,
        patch("engine.observability.capture_exception") as mock_cap,
    ):
        pipeline.execute_run(
            db_conn,
            run_id_fail,
            workspace_id=ws_id,
            feed_adapter=CrashingFeed(),
        )
        mock_hb.assert_called_once_with(failed=True)
        assert mock_cap.called
        cap_kwargs = mock_cap.call_args.kwargs
        assert cap_kwargs["workspace_id"] == str(ws_id)
        assert cap_kwargs["run_id"] == str(run_id_fail)

    fail_events = [
        e for e in db.list_events(db_conn, workspace_id=ws_id) if e["type"] == "run.failed"
    ]
    assert len(fail_events) == 1
    assert fail_events[0]["payload"]["run_id"] == str(run_id_fail)
    assert "Feed provider network down" in fail_events[0]["payload"]["error"]


def test_execute_run_live_mode_blocks_on_integration_missing_and_resumes(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live/prod execute_run blocks cleanly on missing integrations and resumes once configured."""
    from cryptography.fernet import Fernet

    from engine.integrations.config import set_workspace_integration

    ws_id, user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)
    _seed_test_feed_source(db_conn, workspace_id=ws_id)
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)
    monkeypatch.setenv("APP_ENV", "production")

    run_id = _make_pipeline_run(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)

    with (
        patch("engine.integrations.rss.RSSFeedSource.fetch_feed", return_value=[]),
        patch(
            "engine.integrations.trafilatura_extractor.TrafilaturaExtractor.extract",
            side_effect=FakeExtractor().extract,
        ),
        patch("engine.observability.send_heartbeat") as mock_hb,
    ):
        blocked = pipeline.execute_run(db_conn, run_id, workspace_id=ws_id)

    assert blocked["status"] == "blocked"
    assert blocked["blocked_reason"] == "integration_missing"
    mock_hb.assert_called_once_with(failed=True)
    blocked_events = [
        e for e in db.list_events(db_conn, workspace_id=ws_id) if e["type"] == "run.blocked"
    ]
    assert len(blocked_events) == 1
    assert blocked_events[0]["payload"]["missing_port"] == "news_search"

    # Configure only news_search; llm is still missing -> still blocks cleanly
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="news_search",
        plugin="fake",
        settings={},
        secrets=None,
        updated_by=user_id,
    )
    with (
        patch("engine.integrations.rss.RSSFeedSource.fetch_feed", return_value=[]),
        patch(
            "engine.integrations.trafilatura_extractor.TrafilaturaExtractor.extract",
            side_effect=FakeExtractor().extract,
        ),
    ):
        still_blocked = pipeline.execute_run(db_conn, run_id)
    assert still_blocked["status"] == "blocked"
    assert still_blocked["blocked_reason"] == "integration_missing"
    assert (still_blocked.get("error") or {}).get("port") == "llm"

    # Configure llm too and resume via run_stage(offline=False) -> completes
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="llm",
        plugin="fake",
        settings={},
        secrets=None,
        updated_by=user_id,
    )
    with (
        patch("engine.integrations.rss.RSSFeedSource.fetch_feed", return_value=[]),
        patch(
            "engine.integrations.trafilatura_extractor.TrafilaturaExtractor.extract",
            side_effect=FakeExtractor().extract,
        ),
        patch("engine.observability.send_heartbeat") as mock_hb_ok,
    ):
        resumed = services.run_stage(db_conn, stage_or_record_id=run_id, offline=False)

    assert resumed["run"]["status"] == "completed"
    assert resumed["run"]["blocked_reason"] is None
    mock_hb_ok.assert_called_once_with(failed=False)
    assert (
        pipeline.re_run_failed_stage_items(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            stage="unknown",
        )
        == []
    )


def test_live_pipeline_passes_run_context_to_brave_and_defaults_rss_trafilatura(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured Brave search gets run context for usage tracking and RSS/Trafilatura default."""
    import json

    from cryptography.fernet import Fernet

    from engine.integrations.config import set_workspace_integration
    from engine.safe_http import SafeResponse

    ws_id, user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)
    _seed_test_feed_source(db_conn, workspace_id=ws_id)
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        name="Site Search Source",
        domain="optica.example.com",
        industry_pack="general",
        url="https://optica.example.com",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["lentes"],
        enabled=True,
    )
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="llm",
        plugin="fake",
        settings={},
        secrets=None,
        updated_by=user_id,
    )
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="news_search",
        plugin="brave",
        settings={"count": 5},
        secrets={"api_key": "fake-brave-secret"},
        updated_by=user_id,
    )

    feed_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version='2.0'><channel><title>Optical RSS</title>"
        "<item><title>Story RSS</title><link>https://opticalnews.example.com/rss-1</link>"
        "<pubDate>Tue, 10 Nov 2026 09:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )
    brave_json = json.dumps(
        {
            "results": [
                {
                    "title": "Story Brave",
                    "url": "https://optica.example.com/brave-1",
                    "description": "Brave result summary",
                    "page_age": datetime.now(UTC).isoformat(),
                }
            ]
        }
    )
    article_html = (
        "<html><head><title>Full Article</title></head>"
        "<body><article><p>Texto longo suficiente para extração completa de artigo óptico "
        "com dados relevantes de varejo e receita no setor brasileiro de óticas.</p>"
        "</article></body></html>"
    )

    def _mock_safe_request(method: str, url: str, **kwargs: Any) -> SafeResponse:
        del method, kwargs
        if "robots.txt" in url:
            return SafeResponse(status_code=200, headers={}, content=b"User-agent: *\nAllow: /\n")
        if "api.search.brave.com" in url:
            return SafeResponse(status_code=200, headers={}, content=brave_json.encode("utf-8"))
        if "rss.xml" in url:
            return SafeResponse(status_code=200, headers={}, content=feed_xml.encode("utf-8"))
        return SafeResponse(status_code=200, headers={}, content=article_html.encode("utf-8"))

    run_id = _make_pipeline_run(db_conn, workspace_id=ws_id, campaign_id=camp_id, user_id=user_id)
    with (
        patch("engine.integrations.brave.safe_request", side_effect=_mock_safe_request),
        patch("engine.integrations.rss.safe_request", side_effect=_mock_safe_request),
        patch(
            "engine.integrations.trafilatura_extractor.safe_request",
            side_effect=_mock_safe_request,
        ),
    ):
        res = pipeline.execute_run(db_conn, run_id, workspace_id=ws_id, offline=False)

    assert res["status"] == "completed"
    usage = db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
    assert any(u["vendor"] == "brave" and u["run_id"] == run_id for u in usage)


def test_services_run_week_compiles_report_on_completion(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """services.run_week compiles recommendations on completion and skips report when blocked."""
    ws_id, _user_id, camp_id = _setup_test_workspace_and_campaign(db_conn)
    _seed_test_feed_source(db_conn, workspace_id=ws_id)

    res = services.run_week(
        db_conn,
        workspace_identifier=str(ws_id),
        campaign_identifier=str(camp_id),
        offline=True,
    )
    assert res["status"] == "completed"
    evts = db.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topics.awaiting_approval",
        entity_id=uuid.UUID(str(res["id"])),
    )
    assert len(evts) == 1

    monkeypatch.setenv("APP_ENV", "production")
    blocked_res = services.run_week(
        db_conn,
        workspace_identifier=str(ws_id),
        campaign_identifier=str(camp_id),
        offline=False,
    )
    assert blocked_res["status"] == "blocked"
    blocked_evts = db.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topics.awaiting_approval",
        entity_id=uuid.UUID(str(blocked_res["id"])),
    )
    assert blocked_evts == []
