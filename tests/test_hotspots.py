import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scripts.hotspots import crap_score, main, rank_hotspots


def test_crap_score_extremes() -> None:
    assert crap_score(complexity=5, coverage_pct=100.0) == pytest.approx(5.0)
    assert crap_score(complexity=5, coverage_pct=0.0) == pytest.approx(30.0)


@given(
    complexity=st.integers(min_value=0, max_value=100),
    cov_low=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    cov_high=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
)
def test_crap_score_monotonic_in_coverage(complexity: int, cov_low: float, cov_high: float) -> None:
    low, high = sorted((cov_low, cov_high))
    score_low_cov = crap_score(complexity, low)
    score_high_cov = crap_score(complexity, high)
    assert score_low_cov >= score_high_cov >= float(complexity)


def test_rank_hotspots_joins_and_limits_to_top_10() -> None:
    complexipy_items = [
        {
            "complexity": i,
            "file_name": "mod.py",
            "function_name": f"fn_{i}",
            "path": "//?/repo/engine/mod.py",
        }
        for i in range(1, 13)
    ]
    coverage_data = {
        "files": {
            "engine\\mod.py": {
                "summary": {"percent_covered": 50.0},
                "functions": {
                    "fn_12": {"summary": {"percent_covered": 0.0}},
                    "fn_11": {"summary": {"percent_covered": 100.0}},
                },
            }
        }
    }

    ranked = rank_hotspots(complexipy_items, coverage_data)
    assert len(ranked) == 10
    assert ranked[0]["function_name"] == "fn_12"
    assert ranked[0]["crap"] == pytest.approx(156.0)


def test_hotspots_cli_writes_json_report(tmp_path: Path) -> None:
    comp_path = tmp_path / "complexipy.json"
    cov_path = tmp_path / "coverage.json"
    out_path = tmp_path / "hotspots.json"

    comp_path.write_text(
        json.dumps(
            [
                {
                    "complexity": 4,
                    "file_name": "__init__.py",
                    "function_name": "helper",
                    "path": "engine/__init__.py",
                }
            ]
        ),
        encoding="utf-8",
    )
    cov_path.write_text(
        json.dumps({"files": {"engine/__init__.py": {"summary": {"percent_covered": 100.0}}}}),
        encoding="utf-8",
    )

    rc = main([str(comp_path), str(cov_path), str(out_path)])
    assert rc == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(report) == 1
    assert report[0]["function_name"] == "helper"
    assert report[0]["crap"] == pytest.approx(4.0)
