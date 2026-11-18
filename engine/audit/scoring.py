"""Dynamic 7-category weighted scoring for SEO + GEO audits (M6-02 D1, ADR-028)."""

from __future__ import annotations

from collections import defaultdict

from engine.contracts import AuditCategory, AuditCheckResult, CategoryScore

CATEGORY_WEIGHTS: dict[AuditCategory, int] = {
    "onpage": 30,
    "performance": 20,
    "usability": 12,
    "security": 12,
    "links": 10,
    "social": 8,
    "ai": 8,
}

STATUS_MULTIPLIERS: dict[str, float] = {
    "pass": 1.0,
    "warning": 0.5,
    "error": 0.0,
}

GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (95.0, "A+"),
    (90.0, "A"),
    (85.0, "A-"),
    (80.0, "B+"),
    (75.0, "B"),
    (70.0, "B-"),
    (65.0, "C+"),
    (60.0, "C"),
    (50.0, "D"),
)


def score_to_grade(score: float) -> str:
    """Convert normalized 0..100 score to letter grade A+..F."""
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def _summarize_category(
    category: AuditCategory,
    items: list[AuditCheckResult],
) -> CategoryScore | None:
    passed = sum(1 for item in items if item.status == "pass")
    warnings = sum(1 for item in items if item.status == "warning")
    errors = sum(1 for item in items if item.status == "error")
    info = sum(1 for item in items if item.status == "info")

    scored = [item for item in items if item.status != "info"]
    if not scored:
        return None

    pct = round((sum(STATUS_MULTIPLIERS[i.status] for i in scored) / len(scored)) * 100.0, 1)
    return CategoryScore(
        category=category,
        weight=CATEGORY_WEIGHTS[category],
        score=max(0.0, min(100.0, pct)),
        passed=passed,
        warnings=warnings,
        errors=errors,
        info=info,
    )


def score_audit_checks(
    checks: list[AuditCheckResult],
) -> tuple[float, str, dict[AuditCategory, CategoryScore]]:
    """Compute dynamically normalized category scores, overall score (0..100), and letter grade."""
    grouped: dict[AuditCategory, list[AuditCheckResult]] = defaultdict(list)
    for item in checks:
        grouped[item.category].append(item)

    categories: dict[AuditCategory, CategoryScore] = {}
    for cat, weight in CATEGORY_WEIGHTS.items():
        _ = weight
        summary = _summarize_category(cat, grouped.get(cat, []))
        if summary is not None:
            categories[cat] = summary

    weight_sum = sum(cat.weight for cat in categories.values())
    if weight_sum == 0:
        return 0.0, "F", categories

    weighted_total = sum(cat.score * cat.weight for cat in categories.values()) / weight_sum
    overall = round(max(0.0, min(100.0, weighted_total)), 1)
    return overall, score_to_grade(overall), categories
