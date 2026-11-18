"""Tests for paid RankTrackerPort toggle, history persistence, and spend caps (M6-03 D4)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from engine import budget, db
from engine.safe_http import SafeResponse


def _seed_ws(conn: psycopg.Connection[Any], tag: str) -> uuid.UUID:
    ts = int(datetime.now(UTC).timestamp() * 1000)
    return db.create_workspace(
        conn, slug=f"rk-{tag}-{ts}", name=f"RK {tag}", industry_pack="general"
    )


def test_rank_tracker_plan_toggle_blocks_when_disabled(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify rank_tracking_enabled=False blocks SERP API calls and spend."""
    from engine.integrations.ports import RankTrackerPort
    from engine.integrations.serp_tracker import SerpRankTracker

    called = {"count": 0}
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda *a, **kw: called.__setitem__("count", called["count"] + 1),
    )

    ws_id = _seed_ws(db_conn, "disabled")
    tracker = SerpRankTracker(
        SerpRankTracker.Settings(
            provider="serper",
            api_key="test-key",
            rank_tracking_enabled=False,
        )
    )
    assert isinstance(tracker, RankTrackerPort)
    with pytest.raises(PermissionError, match="rank_tracking_enabled"):
        tracker.track_keyword(
            db_conn,
            workspace_id=ws_id,
            keyword="armacoes de titanio",
            domain="example.com",
            monthly_cap_usd=Decimal("10.00"),
        )
    assert called["count"] == 0


def test_rank_tracker_persistence_delta_and_budget_cap_exceeded(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Fake/Serp RankTrackerPort, keyword_rankings history/delta, and usage_ledger cap."""
    from engine.integrations.fakes import FakeRankTracker
    from engine.integrations.ports import RankTrackerPort
    from engine.integrations.serp_tracker import SerpRankTracker

    fake = FakeRankTracker()
    assert isinstance(fake, RankTrackerPort)
    fake.healthcheck()
    assert fake.check_rank(keyword="oticas", domain="example.com")["position"] == 7

    ws_id = _seed_ws(db_conn, "enabled")
    serper_payload = {
        "organic": [
            {"link": "https://other.com/a", "position": 1},
            {"link": "https://example.com/guia", "position": 5},
        ]
    }
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda *a, **kw: SafeResponse(200, {}, json.dumps(serper_payload).encode()),
    )

    tracker = SerpRankTracker(
        SerpRankTracker.Settings(
            provider="serper",
            api_key="live-key",
            rank_tracking_enabled=True,
            cost_per_check_usd=Decimal("0.005000"),
        )
    )
    tracker.healthcheck()

    first = tracker.track_keyword(
        db_conn,
        workspace_id=ws_id,
        keyword="gestao otica",
        domain="example.com",
        monthly_cap_usd=Decimal("10.00"),
    )
    assert first["position"] == 5
    assert first["delta_status"] == "entered_top_100"

    # Second check moves up to #2 -> improved
    serper_payload["organic"] = [{"link": "https://www.example.com/guia", "position": 2}]
    second = tracker.track_keyword(
        db_conn,
        workspace_id=ws_id,
        keyword="gestao otica",
        domain="example.com",
        monthly_cap_usd=Decimal("10.00"),
    )
    assert second["position"] == 2
    assert second["previous_position"] == 5
    assert second["delta_status"] == "improved"

    history = db.list_keyword_rankings(
        db_conn, workspace_id=ws_id, domain="example.com", keyword="gestao otica"
    )
    assert len(history) == 2

    # Spend cap exceeded raises MonthlyCapReached
    with pytest.raises(budget.MonthlyCapReached):
        tracker.track_keyword(
            db_conn,
            workspace_id=ws_id,
            keyword="gestao otica",
            domain="example.com",
            monthly_cap_usd=Decimal("0.01"),
        )


def test_rank_tracker_dataforseo_deltas_and_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify DataForSEO adapter, all compute_delta_status branches, and missing API key."""
    from engine.integrations.serp_tracker import SerpRankTracker, compute_delta_status

    assert compute_delta_status(None, 5) == "entered_top_100"
    assert compute_delta_status(5, None) == "dropped_out"
    assert compute_delta_status(None, None) == "unranked"
    assert compute_delta_status(8, 3) == "improved"
    assert compute_delta_status(3, 8) == "declined"
    assert compute_delta_status(4, 4) == "unchanged"

    bad = SerpRankTracker(
        SerpRankTracker.Settings(provider="dataforseo", api_key="", rank_tracking_enabled=True)
    )
    with pytest.raises(ValueError, match="API key"):
        bad.healthcheck()

    dfs_payload = {
        "tasks": [
            {
                "result": [
                    {
                        "items": [
                            {"type": "paid", "url": "https://example.com/ad", "rank_absolute": 1},
                            {
                                "type": "organic",
                                "url": "https://blog.example.com/post",
                                "rank_absolute": 4,
                            },
                        ]
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda *a, **kw: SafeResponse(200, {}, json.dumps(dfs_payload).encode()),
    )
    dfs = SerpRankTracker(
        SerpRankTracker.Settings(
            provider="dataforseo",
            api_login="u",
            api_key="p",
            rank_tracking_enabled=True,
        )
    )
    dfs.healthcheck()
    res = dfs.check_rank(keyword="oticas", domain="example.com")
    assert res["position"] == 4
    assert res["provider"] == "dataforseo"

    # Unmatched domain yields None position cleanly
    miss = dfs.check_rank(keyword="oticas", domain="unmatched.org")
    assert miss["position"] is None
    assert (
        SerpRankTracker(
            SerpRankTracker.Settings(provider="serper", api_key="k", rank_tracking_enabled=True)
        ).check_rank(keyword="oticas", domain="unmatched.org")["position"]
        is None
    )
