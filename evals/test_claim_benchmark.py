"""Offline tests for the planted-error benchmark (no LLM calls)."""

from pathlib import Path

import pytest

from evals.claim_benchmark import BenchmarkDataError, rules
from evals.claim_benchmark.harness import (
    MethodResult,
    PlantedError,
    Variant,
    build_variants,
    guard_status,
    load_dataset,
    load_variants,
    score_results,
)

CASE = {
    "id": "x",
    "locale": "en-US",
    "title": "T",
    "source": "Sales rose 10%.",
    "sentences": [
        {
            "text": "Sales rose 10%.",
            "errors": [{"type": "number", "find": "10%", "replace": "20%"}],
        },
        {"text": "Worth watching."},
        {"text": "Made up claim.", "planted_sentence": "fabricated"},
    ],
}
SOURCE = "O investimento foi de R$ 2,4 milhões em 2025. Sales reached 1,240 units."


def test_build_variants_plants_one_error_per_article() -> None:
    variants = build_variants(CASE)

    assert [(v.id, v.sentences, v.error) for v in variants] == [
        ("x/clean", ("Sales rose 10%.", "Worth watching."), None),
        ("x/s0e0", ("Sales rose 20%.", "Worth watching."), PlantedError("number", 0)),
        (
            "x/insert2",
            ("Sales rose 10%.", "Worth watching.", "Made up claim."),
            PlantedError("fabricated", 2),
        ),
    ]


def test_build_variants_rejects_ambiguous_find() -> None:
    case = {
        **CASE,
        "sentences": [{"text": "a a", "errors": [{"type": "t", "find": "a", "replace": "b"}]}],
    }

    with pytest.raises(BenchmarkDataError):
        build_variants(case)


def test_benchmark_cases_load() -> None:
    variants = load_variants()

    assert sum(v.error is None for v in variants) == 12
    assert sum(v.error is not None for v in variants) == 68


def _variant(suffix: str, error: PlantedError | None) -> Variant:
    return Variant(
        suffix, "c", "en-US", "T", "S", ("First one.", "Second one.", "Third one."), error
    )


def test_score_results_hand_worked_example() -> None:
    error = PlantedError("number", 1)
    rows = [
        (_variant("clean", None), MethodResult(publish=True, flagged=[])),
        (_variant("caught", error), MethodResult(publish=False, flagged=["Second one."])),
        (_variant("missed", error), MethodResult(publish=True, flagged=[])),
        (_variant("wrong", error), MethodResult(publish=False, flagged=["Third one."])),
        (_variant("failed", error), None),
    ]

    score = score_results(rows)

    assert score.errors_total == 3
    assert score.catch_rate == pytest.approx(1 / 3)
    assert score.false_pass_rate == pytest.approx(1 / 3)
    assert score.clean_reject_rate == 0.0
    assert score.false_alarm_rate == pytest.approx(1 / 9)
    assert score.failed_runs == 1
    assert score.by_type["number"] == [1, 3]


def test_score_results_article_level_method_uses_verdict() -> None:
    rows = [
        (_variant("caught", PlantedError("date", 0)), MethodResult(publish=False)),
        (_variant("clean", None), MethodResult(publish=False)),
    ]

    score = score_results(rows)

    assert score.catch_rate == 1.0
    assert score.clean_reject_rate == 1.0
    assert score.clean_sentences == 0


@pytest.mark.parametrize(
    ("claim", "status", "evidence", "expected"),
    [
        (
            "Investiu R$ 2,4 milhões",
            "supported",
            "O investimento foi de R$ 2,4 milhões em 2025.",
            "supported",
        ),
        (
            "Investiu R$ 24 milhões",
            "supported",
            "O investimento foi de R$ 2,4 milhões em 2025.",
            "unverifiable",
        ),
        ("Sold 1.240 units", "supported", "Sales reached 1,240 units.", "supported"),
        ("Sold many units", "supported", "A quote that is not in the source.", "unverifiable"),
        ("Sold many units", "supported", "", "unverifiable"),
        ("Sold 9 units", "contradicted", "", "contradicted"),
    ],
)
def test_guard_status(claim: str, status: str, evidence: str, expected: str) -> None:
    assert guard_status(claim, status, evidence, SOURCE) == expected


def test_frozen_rules_match_proposal() -> None:
    # Literals copied from openspec/changes/2026-11-10-claim-judge-jev-cascade/proposal.md.
    assert {
        "w1_min_improvement": rules.W1_MIN_IMPROVEMENT,
        "w1_max_regression": rules.W1_MAX_REGRESSION,
        "w2_min_catch": rules.W2_MIN_CATCH,
        "w2_max_false_pass": rules.W2_MAX_FALSE_PASS,
        "w3_max_clean_reject": rules.W3_MAX_CLEAN_REJECT,
        "w4_max_cost_ratio": rules.W4_MAX_COST_RATIO,
        "w5_max_gap": rules.W5_MAX_GAP,
        "w6_min_accept_precision": rules.W6_MIN_ACCEPT_PRECISION,
        "w7_min_evidence_correct": rules.W7_MIN_EVIDENCE_CORRECT,
        "w7_audit_claims": rules.W7_AUDIT_CLAIMS,
        "w8_max_language_gap": rules.W8_MAX_LANGUAGE_GAP,
        "w9_max_run_range": rules.W9_MAX_RUN_RANGE,
        "runs": rules.RUNS_PER_MEASUREMENT,
        "d4_min_articles": rules.D4_MIN_ARTICLES,
        "d4_min_per_language": rules.D4_MIN_PER_LANGUAGE,
        "d4_min_errors": rules.D4_MIN_ERRORS,
        "d4_min_clean": rules.D4_MIN_CLEAN,
        "d4_min_multi_error_share": rules.D4_MIN_MULTI_ERROR_SHARE,
        "d4_min_median_source_words": rules.D4_MIN_MEDIAN_SOURCE_WORDS,
        "threshold_grid": rules.THRESHOLD_GRID,
        "jev_model": rules.JEV_MODEL,
        "jev_candidates": rules.JEV_CANDIDATES,
        "max_state_bytes": rules.MAX_STATE_BYTES,
        "budget_usd": rules.BUDGET_USD,
    } == {
        "w1_min_improvement": 0.10,
        "w1_max_regression": 0.03,
        "w2_min_catch": 0.90,
        "w2_max_false_pass": 0.10,
        "w3_max_clean_reject": 0.25,
        "w4_max_cost_ratio": 0.50,
        "w5_max_gap": 0.03,
        "w6_min_accept_precision": 0.97,
        "w7_min_evidence_correct": 0.90,
        "w7_audit_claims": 50,
        "w8_max_language_gap": 0.05,
        "w9_max_run_range": 0.05,
        "runs": 3,
        "d4_min_articles": 30,
        "d4_min_per_language": 15,
        "d4_min_errors": 120,
        "d4_min_clean": 30,
        "d4_min_multi_error_share": 0.15,
        "d4_min_median_source_words": 800,
        "threshold_grid": (0.70, 0.75, 0.80, 0.85, 0.90, 0.95),
        "jev_model": "typesafe/jev-1.13",
        "jev_candidates": ("jev_judge", "jev_cascade"),
        "max_state_bytes": 30_000,
        "budget_usd": 5.0,
    }


def test_frozen_dev_dataset_loads() -> None:
    assert len(load_dataset("dev")) == 80


def test_frozen_holdout_refused_when_hash_differs(tmp_path: Path) -> None:
    holdout = tmp_path / "cases.yaml"
    holdout.write_text("[]\n", encoding="utf-8")

    with pytest.raises(BenchmarkDataError, match="SHA-256"):
        load_dataset("holdout", path=holdout, expected_sha256="0" * 64)


def test_frozen_hash_ignores_line_endings(tmp_path: Path) -> None:
    unix, windows = tmp_path / "a.yaml", tmp_path / "b.yaml"
    unix.write_bytes(b"[]\n")
    windows.write_bytes(b"[]\r\n")
    expected = "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570"  # sha256sum

    assert load_dataset("dev", path=unix, expected_sha256=expected) == []
    assert load_dataset("dev", path=windows, expected_sha256=expected) == []


def test_frozen_holdout_refused_before_hash_is_set(tmp_path: Path) -> None:
    holdout = tmp_path / "cases.yaml"
    holdout.write_text("[]\n", encoding="utf-8")

    with pytest.raises(BenchmarkDataError, match="not frozen"):
        load_dataset("holdout", path=holdout, expected_sha256=None)
