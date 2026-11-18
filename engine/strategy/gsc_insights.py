"""GSC diagnostic analyzers: striking-distance, content decay, cannibalization (M6-03 D2)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def benchmark_ctr(position: float) -> float:
    """Return conservative expected organic CTR threshold by SERP position bucket."""
    if position <= 5.0:
        return 0.06
    return 0.03 if position <= 10.0 else 0.015


def find_striking_distance_queries(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_pos: float = 4.0,
    max_pos: float = 20.0,
) -> list[dict[str, Any]]:
    """Identify queries at position 4..20 with above-median impressions and below-benchmark CTR."""
    if not rows:
        return []
    median_imp = statistics.median(int(r.get("impressions", 0)) for r in rows)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        pos = float(row.get("position", 0.0))
        impressions = int(row.get("impressions", 0))
        ctr = float(row.get("ctr", 0.0))
        exp_ctr = benchmark_ctr(pos)
        if min_pos <= pos <= max_pos and impressions >= median_imp and ctr < exp_ctr:
            candidates.append(
                {
                    "query": str(row.get("query", "")),
                    "page": str(row.get("page", "")),
                    "position": round(pos, 1),
                    "impressions": impressions,
                    "clicks": int(row.get("clicks", 0)),
                    "ctr": round(ctr, 4),
                    "expected_ctr": exp_ctr,
                    "potential_extra_clicks": round((exp_ctr - ctr) * impressions, 1),
                }
            )
    return sorted(candidates, key=lambda x: float(x["potential_extra_clicks"]), reverse=True)


def detect_content_decay(
    current_rows: Sequence[Mapping[str, Any]],
    previous_rows: Sequence[Mapping[str, Any]],
    *,
    window_label: str = "28d",
    drop_threshold: float = 0.30,
) -> list[dict[str, Any]]:
    """Detect pages whose clicks or impressions declined by >= drop_threshold vs prior period."""
    curr_by_page = {str(r.get("page", "")): r for r in current_rows if r.get("page")}
    decayed: list[dict[str, Any]] = []
    for prev in previous_rows:
        page = str(prev.get("page", ""))
        if not page:
            continue
        curr = curr_by_page.get(page, {})
        prev_clicks = int(prev.get("clicks", 0))
        prev_imp = int(prev.get("impressions", 0))
        curr_clicks = int(curr.get("clicks", 0))
        curr_imp = int(curr.get("impressions", 0))
        click_drop = max(0.0, (prev_clicks - curr_clicks) / prev_clicks) if prev_clicks > 0 else 0.0
        imp_drop = max(0.0, (prev_imp - curr_imp) / prev_imp) if prev_imp > 0 else 0.0
        worst = max(click_drop, imp_drop)
        if worst >= drop_threshold:
            decayed.append(
                {
                    "page": page,
                    "window_label": window_label,
                    "prev_clicks": prev_clicks,
                    "curr_clicks": curr_clicks,
                    "click_drop_pct": round(click_drop * 100.0, 1),
                    "impression_drop_pct": round(imp_drop * 100.0, 1),
                    "severity": "major" if worst >= 0.50 else "minor",
                }
            )
    return sorted(
        decayed,
        key=lambda x: max(float(x["click_drop_pct"]), float(x["impression_drop_pct"])),
        reverse=True,
    )


def detect_cannibalization(
    query_page_rows: Sequence[Mapping[str, Any]],
    *,
    min_share: float = 0.20,
    min_impressions: int = 10,
) -> list[dict[str, Any]]:
    """Flag queries where >= 2 pages share >= min_share of total query impressions."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in query_page_rows:
        query = str(row.get("query", "")).strip()
        if query:
            grouped[query].append(row)

    findings: list[dict[str, Any]] = []
    for query, items in grouped.items():
        total_imp = sum(int(r.get("impressions", 0)) for r in items)
        if total_imp < min_impressions:
            continue
        contenders = [
            {
                "page": str(r.get("page", "")),
                "impressions": int(r.get("impressions", 0)),
                "share": round(int(r.get("impressions", 0)) / total_imp, 3),
                "position": float(r.get("position", 0.0)),
            }
            for r in items
            if int(r.get("impressions", 0)) / total_imp >= min_share
        ]
        if len(contenders) >= 2:
            findings.append(
                {
                    "query": query,
                    "total_impressions": total_imp,
                    "pages": sorted(contenders, key=lambda x: int(x["impressions"]), reverse=True),
                }
            )
    return sorted(findings, key=lambda x: int(x["total_impressions"]), reverse=True)


def build_gsc_insight_summary(
    *,
    query_rows: Sequence[Mapping[str, Any]],
    current_pages: Sequence[Mapping[str, Any]],
    previous_pages: Sequence[Mapping[str, Any]],
    query_page_rows: Sequence[Mapping[str, Any]],
    window_label: str = "28d",
) -> dict[str, Any]:
    """Run all three GSC diagnostic analyzers and return combined summary counts."""
    sd = find_striking_distance_queries(query_rows)
    dc = detect_content_decay(current_pages, previous_pages, window_label=window_label)
    cb = detect_cannibalization(query_page_rows)
    return {
        "striking_distance": sd,
        "content_decay": dc,
        "cannibalization": cb,
        "counts": {
            "striking_distance": len(sd),
            "content_decay": len(dc),
            "cannibalization": len(cb),
        },
    }
