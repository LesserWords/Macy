"""Tests for GSC diagnostic analyzers: striking-distance, decay, cannibalization (M6-03 D2)."""

from __future__ import annotations


def test_striking_distance_finder_ranks_high_impression_low_ctr_queries() -> None:
    """Verify striking-distance finds pos 4..20 above-median impressions and low CTR."""
    from engine.strategy.gsc_insights import find_striking_distance_queries

    rows = [
        {
            "query": "armação titânio",
            "page": "/titanio",
            "position": 6.0,
            "impressions": 2000,
            "clicks": 20,
            "ctr": 0.01,
        },
        {
            "query": "lente multifocal",
            "page": "/lentes",
            "position": 12.0,
            "impressions": 1850,
            "clicks": 6,
            "ctr": 0.004,
        },
        {
            "query": "marca top1",
            "page": "/",
            "position": 1.2,
            "impressions": 5000,
            "clicks": 1500,
            "ctr": 0.30,
        },
        {
            "query": "low vol",
            "page": "/low",
            "position": 8.0,
            "impressions": 50,
            "clicks": 0,
            "ctr": 0.0,
        },
        {
            "query": "high ctr",
            "page": "/good",
            "position": 5.0,
            "impressions": 1800,
            "clicks": 180,
            "ctr": 0.10,
        },
    ]
    res = find_striking_distance_queries(rows)
    queries = [item["query"] for item in res]
    assert queries == ["armação titânio", "lente multifocal"]
    assert res[0]["potential_extra_clicks"] > res[1]["potential_extra_clicks"]
    assert find_striking_distance_queries([]) == []


def test_content_decay_triage_7d_and_28d() -> None:
    """Verify 7d and 28d decay detection flags >=30% click or impression declines."""
    from engine.strategy.gsc_insights import detect_content_decay

    prev_rows = [
        {"page": "https://example.com/decayed-major", "clicks": 200, "impressions": 4000},
        {"page": "https://example.com/decayed-minor", "clicks": 100, "impressions": 1000},
        {"page": "https://example.com/stable", "clicks": 100, "impressions": 1000},
        {"page": "", "clicks": 50, "impressions": 500},
    ]
    curr_rows = [
        {"page": "https://example.com/decayed-major", "clicks": 80, "impressions": 1800},
        {"page": "https://example.com/decayed-minor", "clicks": 65, "impressions": 900},
        {"page": "https://example.com/stable", "clicks": 95, "impressions": 980},
    ]
    decayed = detect_content_decay(curr_rows, prev_rows, window_label="28d")
    assert [d["page"] for d in decayed] == [
        "https://example.com/decayed-major",
        "https://example.com/decayed-minor",
    ]
    assert decayed[0]["severity"] == "major"
    assert decayed[0]["window_label"] == "28d"
    assert decayed[1]["severity"] == "minor"


def test_cannibalization_detector_and_combined_summary() -> None:
    """Verify cannibalization detects queries split across >=2 URLs >=20% impression share."""
    from engine.strategy.gsc_insights import build_gsc_insight_summary, detect_cannibalization

    qp_rows = [
        {
            "query": "gestao otica",
            "page": "/guia",
            "impressions": 600,
            "clicks": 20,
            "position": 6.0,
        },
        {
            "query": "gestao otica",
            "page": "/sistema",
            "impressions": 400,
            "clicks": 10,
            "position": 8.0,
        },
        {"query": "dominada", "page": "/unica", "impressions": 950, "clicks": 50, "position": 2.0},
        {"query": "dominada", "page": "/tiny", "impressions": 50, "clicks": 0, "position": 18.0},
        {"query": "", "page": "/blank", "impressions": 100, "clicks": 1, "position": 9.0},
        {"query": "tiny total", "page": "/t1", "impressions": 2, "clicks": 0, "position": 9.0},
        {"query": "tiny total", "page": "/t2", "impressions": 2, "clicks": 0, "position": 10.0},
    ]
    cannibal = detect_cannibalization(qp_rows)
    assert len(cannibal) == 1
    assert cannibal[0]["query"] == "gestao otica"
    assert len(cannibal[0]["pages"]) == 2

    summary = build_gsc_insight_summary(
        query_rows=[],
        current_pages=[],
        previous_pages=[],
        query_page_rows=qp_rows,
    )
    assert summary["counts"]["cannibalization"] == 1
