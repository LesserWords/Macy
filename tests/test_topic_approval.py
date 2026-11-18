"""Unit and integration tests for Topic Approval Gate 1 (F10 AC1-AC6, ADR-013, M2-03).

Verifies:
- AC1: Creator approval transitions opportunity to 'approved', sets decided_by/at,
       emits 'topic.decided' event with brief_queued=True.
- AC2: Any non-creator or non-member user is refused with PermissionError (Forbidden).
- AC3: Approvals exceeding campaign articles_per_week limit are refused with ValueError.
- AC4: Two-connection race on same opportunity succeeds for exactly one, with guarded transition.
- AC4: Concurrent limit race between different opportunities serializes on run lock and cap.
- AC5: Rejected opportunity transitions to 'rejected', records note, emits event,
       and prevents same document or duplicate cluster from recommendation in future reports.
- AC6: Starting the next run expires still-scored opportunities from previous runs.
"""

from __future__ import annotations

import concurrent.futures
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import db, services
from tests.conftest import TEST_DATABASE_URL


def _setup_approval_testbed(
    conn: psycopg.Connection[Any],
    tag: str,
    *,
    articles_per_week: int = 2,
    num_opps: int = 3,
) -> dict[str, Any]:
    """Helper to set up workspace, users, campaign, run, documents, and scored opportunities."""
    slug = f"ws-{tag}-{uuid.uuid4().hex[:6]}"
    ws_id = db.create_workspace(conn, slug=slug, name=f"WS {tag}", industry_pack="tech")

    creator_res = services.add_workspace_owner(
        conn, workspace_slug=slug, email=f"creator-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    )
    creator_id = creator_res["user"]["user_id"]

    other_res = services.add_workspace_owner(
        conn, workspace_slug=slug, email=f"other-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    )
    other_user_id = other_res["user"]["user_id"]

    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name=f"Campaign {tag}",
        locale="pt-BR",
        market="BR",
        articles_per_week=articles_per_week,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("25.00"),
        config={},
        config_schema_version=1,
        created_by=creator_id,
    )

    now = datetime.now(UTC)
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("25.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=creator_id,
    )

    opp_ids: list[uuid.UUID] = []
    doc_ids: list[uuid.UUID] = []
    for idx in range(num_opps):
        doc = db.upsert_document(
            conn,
            workspace_id=ws_id,
            url=f"https://example.com/{tag}/{idx}",
            canonical_url=f"https://example.com/{tag}/{idx}",
            title=f"Article {tag} #{idx}",
            date_status="verified",
            fetch_status="ok",
        )
        doc_ids.append(doc["id"])
        rec = db.upsert_research_record(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            document_id=doc["id"],
            status="verified",
            schema_version=1,
            payload={"headline": f"Story {tag} #{idx}", "summary": f"Summary #{idx}"},
        )
        opp_row = db.upsert_opportunity(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_id=rec["id"],
            scores={"novelty": {"score": 8.0, "evidence": "Good"}},
            total=Decimal(str(80.0 - idx * 5)),
            funnel_stage="tofu",
            confidence="high",
            angle=f"Angle #{idx}",
            status="scored",
        )
        opp_ids.append(opp_row["id"])
        db.update_opportunity_rank(
            conn, workspace_id=ws_id, opportunity_id=opp_row["id"], rank=idx + 1
        )

    return {
        "workspace_id": ws_id,
        "creator_id": creator_id,
        "other_user_id": other_user_id,
        "campaign_id": camp_id,
        "run_id": run_id,
        "opportunity_ids": opp_ids,
        "document_ids": doc_ids,
    }


def test_decide_topic_approve_ac1(db_conn: psycopg.Connection[Any]) -> None:
    """AC1: Creator approves scored opportunity; becomes approved with audit event."""
    env = _setup_approval_testbed(db_conn, "appr-ac1")
    opp_id = env["opportunity_ids"][0]

    updated = services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opp_id,
        decision="approved",
        user_id=env["creator_id"],
        note="Approved for editorial calendar",
    )

    assert updated["status"] == "approved"
    assert updated["decided_by"] == env["creator_id"]
    assert updated["decided_at"] is not None
    assert updated["decision_note"] == "Approved for editorial calendar"

    # Verify audit event emitted (F10 AC1)
    events = db.list_events(
        db_conn,
        workspace_id=env["workspace_id"],
        event_type="topic.decided",
        entity_id=opp_id,
    )
    assert len(events) == 1
    evt = events[0]
    assert evt["actor_user_id"] == env["creator_id"]
    payload = evt["payload"]
    assert payload["decision"] == "approved"
    assert payload["brief_queued"] is True
    assert payload["note"] == "Approved for editorial calendar"
    assert payload["campaign_id"] == str(env["campaign_id"])


def test_decide_topic_managed_connection(db_conn: psycopg.Connection[Any]) -> None:
    """Verify decide_topic works with conn=None using managed DB connection."""
    env = _setup_approval_testbed(db_conn, "mng-conn", num_opps=1)
    opp_id = env["opportunity_ids"][0]
    res = services.decide_topic(
        workspace_id=env["workspace_id"],
        opportunity_id=opp_id,
        decision="approved",
        user_id=env["creator_id"],
        note="Managed conn test",
    )
    assert res["status"] == "approved"


def test_decide_topic_reject_ac5(db_conn: psycopg.Connection[Any]) -> None:
    """AC5: Reject topic; emits event with brief_queued=False, and excludes from recommendations."""
    env = _setup_approval_testbed(db_conn, "rej-ac5", num_opps=2)
    opp_id = env["opportunity_ids"][0]

    rejected = services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opp_id,
        decision="reject",
        user_id=env["creator_id"],
        note="Out of scope for this quarter",
    )
    assert rejected["status"] == "rejected"
    assert rejected["decided_by"] == env["creator_id"]
    assert rejected["decision_note"] == "Out of scope for this quarter"

    # Verify audit event
    events = db.list_events(
        db_conn,
        workspace_id=env["workspace_id"],
        event_type="topic.decided",
        entity_id=opp_id,
    )
    assert len(events) == 1
    assert events[0]["payload"]["decision"] == "rejected"
    assert events[0]["payload"]["brief_queued"] is False

    # Check report: rejected topic should not be recommended
    rep = services.report(db_conn, workspace_id=env["workspace_id"], run_id=env["run_id"])
    item0 = next(it for it in rep["items"] if it["id"] == opp_id)
    assert item0["status"] == "rejected"
    assert item0["is_recommended"] is False
    assert item0["is_rejected_story"] is True


def test_decide_topic_unauthorized_forbidden_ac2(db_conn: psycopg.Connection[Any]) -> None:
    """AC2: Non-creator owner and non-member user are forbidden from deciding topics."""
    env = _setup_approval_testbed(db_conn, "auth-ac2")
    opp_id = env["opportunity_ids"][0]

    # Non-creator owner attempt
    with pytest.raises(PermissionError, match="forbidden"):
        services.decide_topic(
            db_conn,
            workspace_id=env["workspace_id"],
            opportunity_id=opp_id,
            decision="approved",
            user_id=env["other_user_id"],
        )

    # Non-member user attempt
    random_user_id = uuid.uuid4()
    with pytest.raises(PermissionError, match="not a member"):
        services.decide_topic(
            db_conn,
            workspace_id=env["workspace_id"],
            opportunity_id=opp_id,
            decision="approved",
            user_id=random_user_id,
        )


def test_decide_topic_volume_cap_exceeded_ac3(db_conn: psycopg.Connection[Any]) -> None:
    """AC3: Refuses approvals exceeding articles_per_week limit with ValueError."""
    env = _setup_approval_testbed(db_conn, "cap-ac3", articles_per_week=2, num_opps=3)
    opps = env["opportunity_ids"]

    # First approval succeeds
    services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opps[0],
        decision="approved",
        user_id=env["creator_id"],
    )
    # Second approval succeeds (articles_per_week = 2)
    services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opps[1],
        decision="approved",
        user_id=env["creator_id"],
    )

    # Third approval attempt exceeds limit
    with pytest.raises(ValueError, match="Approval limit reached"):
        services.decide_topic(
            db_conn,
            workspace_id=env["workspace_id"],
            opportunity_id=opps[2],
            decision="approved",
            user_id=env["creator_id"],
        )

    # Rejection of third opportunity is still allowed (cap applies only to approvals)
    rej = services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opps[2],
        decision="rejected",
        user_id=env["creator_id"],
    )
    assert rej["status"] == "rejected"


def test_decide_topic_guarded_transition_and_validation(db_conn: psycopg.Connection[Any]) -> None:
    """Verify guarded transition: cannot re-decide already decided or non-existent opportunity."""
    env = _setup_approval_testbed(db_conn, "guard-val")
    opp_id = env["opportunity_ids"][0]

    # Invalid decision string
    with pytest.raises(ValueError, match="Invalid decision"):
        services.decide_topic(
            db_conn,
            workspace_id=env["workspace_id"],
            opportunity_id=opp_id,
            decision="postpone",
            user_id=env["creator_id"],
        )

    # Non-existent opportunity
    with pytest.raises(ValueError, match="Opportunity .* not found"):
        services.decide_topic(
            db_conn,
            workspace_id=env["workspace_id"],
            opportunity_id=uuid.uuid4(),
            decision="approved",
            user_id=env["creator_id"],
        )

    # Approve once
    services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opp_id,
        decision="approved",
        user_id=env["creator_id"],
    )

    # Second decision attempt on same opportunity fails guarded transition
    with pytest.raises(ValueError, match="expected 'scored'"):
        services.decide_topic(
            db_conn,
            workspace_id=env["workspace_id"],
            opportunity_id=opp_id,
            decision="rejected",
            user_id=env["creator_id"],
        )


def test_expire_previous_opportunities_on_new_run_ac6(db_conn: psycopg.Connection[Any]) -> None:
    """AC6: Starting the next run expires still-scored opportunities from previous runs."""
    env = _setup_approval_testbed(db_conn, "exp-ac6", num_opps=3)
    opps = env["opportunity_ids"]

    # Approve opp 0, leave opp 1 and opp 2 as scored
    services.decide_topic(
        db_conn,
        workspace_id=env["workspace_id"],
        opportunity_id=opps[0],
        decision="approved",
        user_id=env["creator_id"],
    )

    # Complete run 1 so run 2 can be created (runs_one_active partial unique index)
    db.update_run_status(
        db_conn,
        workspace_id=env["workspace_id"],
        run_id=env["run_id"],
        expected_status="queued",
        new_status="completed",
    )

    # Create run 2 for same campaign
    now = datetime.now(UTC)
    run2_id = db.create_run(
        db_conn,
        workspace_id=env["workspace_id"],
        campaign_id=env["campaign_id"],
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("25.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=env["creator_id"],
    )

    # Simulate next run starting (transition to running)
    db.expire_previous_opportunities(
        db_conn,
        workspace_id=env["workspace_id"],
        campaign_id=env["campaign_id"],
        current_run_id=run2_id,
    )

    opp0 = db.get_opportunity(db_conn, workspace_id=env["workspace_id"], opportunity_id=opps[0])
    opp1 = db.get_opportunity(db_conn, workspace_id=env["workspace_id"], opportunity_id=opps[1])
    opp2 = db.get_opportunity(db_conn, workspace_id=env["workspace_id"], opportunity_id=opps[2])

    assert opp0 is not None and opp0["status"] == "approved"
    assert opp1 is not None and opp1["status"] == "expired"
    assert opp2 is not None and opp2["status"] == "expired"


def test_two_connection_concurrent_race_ac4() -> None:
    """AC4: Concurrent connections racing on same opportunity: exactly one succeeds."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as setup_conn:
        env = _setup_approval_testbed(setup_conn, "race-ac4", num_opps=1)
        opp_id = env["opportunity_ids"][0]
        ws_id = env["workspace_id"]
        creator_id = env["creator_id"]

    results: list[dict[str, Any]] = []
    errors: list[Exception] = []

    def _attempt_approve() -> None:
        try:
            with psycopg.connect(TEST_DATABASE_URL) as thread_conn:
                res = services.decide_topic(
                    thread_conn,
                    workspace_id=ws_id,
                    opportunity_id=opp_id,
                    decision="approved",
                    user_id=creator_id,
                    note="Concurrent attempt",
                )
                thread_conn.commit()
                results.append(res)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_attempt_approve)
        f2 = executor.submit(_attempt_approve)
        concurrent.futures.wait([f1, f2])

    # Exactly one succeeded and one failed
    assert len(results) == 1
    assert len(errors) == 1
    assert "scored" in str(errors[0]) or "could not be updated" in str(errors[0])

    with psycopg.connect(TEST_DATABASE_URL) as verify_conn:
        opp = db.get_opportunity(verify_conn, workspace_id=ws_id, opportunity_id=opp_id)
        assert opp is not None
        assert opp["status"] == "approved"
        assert opp["decided_by"] == creator_id


def test_two_connection_concurrent_limit_race() -> None:
    """Concurrent connections racing on different opportunities with limit 1: 1 succeeds."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as setup_conn:
        env = _setup_approval_testbed(setup_conn, "lim-race", articles_per_week=1, num_opps=2)
        opp_id1 = env["opportunity_ids"][0]
        opp_id2 = env["opportunity_ids"][1]
        ws_id = env["workspace_id"]
        creator_id = env["creator_id"]

    results: list[dict[str, Any]] = []
    errors: list[Exception] = []

    def _attempt_approve_target(opp_target: uuid.UUID) -> None:
        try:
            with psycopg.connect(TEST_DATABASE_URL) as thread_conn:
                res = services.decide_topic(
                    thread_conn,
                    workspace_id=ws_id,
                    opportunity_id=opp_target,
                    decision="approved",
                    user_id=creator_id,
                )
                thread_conn.commit()
                results.append(res)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_attempt_approve_target, opp_id1)
        f2 = executor.submit(_attempt_approve_target, opp_id2)
        concurrent.futures.wait([f1, f2])

    # With articles_per_week = 1 and row locking on runs, exactly 1 succeeds
    assert len(results) == 1
    assert len(errors) == 1
    assert "Approval limit reached" in str(errors[0])

    with psycopg.connect(TEST_DATABASE_URL) as verify_conn:
        count = db.count_approved_opportunities(
            verify_conn, workspace_id=ws_id, run_id=env["run_id"]
        )
        assert count == 1


def test_validate_topic_approver_edge_cases(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify approver validation handles non-owner roles and missing campaigns."""
    env = _setup_approval_testbed(db_conn, "appr-edges", num_opps=1)
    ws_id = env["workspace_id"]
    creator_id = env["creator_id"]
    camp_id = env["campaign_id"]

    # Role other than owner
    monkeypatch.setattr(services, "require_member", lambda *args, **kwargs: "viewer")
    with pytest.raises(PermissionError, match="role 'viewer' is forbidden"):
        services._validate_topic_approver(
            db_conn,
            workspace_id=ws_id,
            user_id=creator_id,
            campaign_id=camp_id,
        )

    # Missing campaign
    monkeypatch.undo()
    with pytest.raises(ValueError, match="Campaign .* not found"):
        services._validate_topic_approver(
            db_conn,
            workspace_id=ws_id,
            user_id=creator_id,
            campaign_id=uuid.uuid4(),
        )


def test_decide_topic_missing_run(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify decide_topic raises ValueError when run cannot be locked."""
    env = _setup_approval_testbed(db_conn, "no-run", num_opps=1)
    opp_id = env["opportunity_ids"][0]
    ws_id = env["workspace_id"]
    creator_id = env["creator_id"]

    real_get_opp = db.get_opportunity

    def _fake_get_opp(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        row = real_get_opp(*args, **kwargs)
        if row:
            row_copy = dict(row)
            row_copy["run_id"] = uuid.uuid4()
            return row_copy
        return None

    monkeypatch.setattr(db, "get_opportunity", _fake_get_opp)
    with pytest.raises(ValueError, match="Run .* not found"):
        services.decide_topic(
            db_conn,
            workspace_id=ws_id,
            opportunity_id=opp_id,
            decision="approved",
            user_id=creator_id,
        )
