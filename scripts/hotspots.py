"""Join complexipy JSON and coverage JSON into a CRAP-style top-10 hotspot report (ADR-006)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def crap_score(complexity: int, coverage_pct: float) -> float:
    """Compute CRAP(m) = comp(m)^2 * (1 - cov(m))^3 + comp(m)."""
    clamped = max(0.0, min(100.0, coverage_pct))
    uncovered = 1.0 - (clamped / 100.0)
    return float((complexity**2) * (uncovered**3) + complexity)


def _lookup_coverage(item: dict[str, Any], files_cov: dict[str, Any]) -> float:
    norm_item_path = str(item.get("path", "")).replace("\\", "/")
    fn_name = str(item.get("function_name", ""))
    for cov_path, cov_info in files_cov.items():
        norm_cov_path = cov_path.replace("\\", "/")
        if norm_item_path.endswith(norm_cov_path) or norm_cov_path.endswith(
            str(item.get("file_name", ""))
        ):
            fn_cov = cov_info.get("functions", {}).get(fn_name, {}).get("summary", {})
            if "percent_covered" in fn_cov:
                return float(fn_cov["percent_covered"])
            return float(cov_info.get("summary", {}).get("percent_covered", 0.0))
    return 0.0


def rank_hotspots(
    complexipy_items: list[dict[str, Any]], coverage_data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return top 10 functions ranked by descending CRAP score."""
    files_cov: dict[str, Any] = coverage_data.get("files", {})
    scored: list[dict[str, Any]] = []
    for item in complexipy_items:
        comp = int(item.get("complexity", 0))
        cov_pct = _lookup_coverage(item, files_cov)
        scored.append(
            {
                "file_name": str(item.get("file_name", "")),
                "function_name": str(item.get("function_name", "")),
                "complexity": comp,
                "coverage_pct": round(cov_pct, 2),
                "crap": round(crap_score(comp, cov_pct), 2),
            }
        )
    scored.sort(key=lambda row: (float(row["crap"]), int(row["complexity"])), reverse=True)
    return scored[:10]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    comp_path = Path(args[0]) if len(args) > 0 else Path("artifacts/complexipy.json")
    cov_path = Path(args[1]) if len(args) > 1 else Path("artifacts/coverage.json")
    out_path = Path(args[2]) if len(args) > 2 else Path("artifacts/hotspots.json")

    comp_items: list[dict[str, Any]] = (
        json.loads(comp_path.read_text(encoding="utf-8")) if comp_path.is_file() else []
    )
    cov_data: dict[str, Any] = (
        json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.is_file() else {}
    )
    top_10 = rank_hotspots(comp_items, cov_data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(top_10, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
