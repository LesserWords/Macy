"""Tests for BM-6 evaluate-kr key result evaluation and decision rule."""

from typing import Any

import pytest

from evals.claim_benchmark.evaluate_kr import evaluate_reports


def _method(  # noqa: PLR0913
    *,
    catch: float = 1.0,
    false_pass: float = 0.0,
    clean_reject: float = 0.0,
    usd: float = 0.0004,
    pt_catch: float = 1.0,
    en_catch: float = 1.0,
    pt_clean: float = 0.0,
    en_clean: float = 0.0,
    precision: float | None = 1.0,
) -> dict[str, Any]:
    return {
        "catch_rate": catch,
        "false_pass_rate": false_pass,
        "clean_reject_rate": clean_reject,
        "false_alarm_rate": clean_reject,
        "failed_runs": 0,
        "usd_per_article": usd,
        "resolved_models": ["anthropic/claude-haiku-5.5"],
        "escalation_rate": 0.1,
        "jev_accept_precision": precision,
        "by_locale": {
            "en-US": {"catch_rate": en_catch, "clean_reject_rate": en_clean},
            "pt-BR": {"catch_rate": pt_catch, "clean_reject_rate": pt_clean},
        },
        "rows": [
            {
                "id": "c1",
                "publish": False,
                "flagged": ["error"],
                "traces": [{"evidence": "source quote", "supported": True}],
            }
        ],
    }


def _fixture_run(
    *,
    dataset: str = "holdout",
    plain_tuned: dict[str, Any] | None = None,
    per_claim_v2: dict[str, Any] | None = None,
    jev_judge: dict[str, Any] | None = None,
    jev_cascade: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pt = plain_tuned or _method(clean_reject=0.30, usd=0.0003)
    pc = per_claim_v2 or _method(catch=0.98, false_pass=0.02, clean_reject=0.08, usd=0.0010)
    jj = jev_judge or _method(catch=0.98, false_pass=0.02, clean_reject=0.02, usd=0.0004)
    jc = jev_cascade or _method(catch=0.98, false_pass=0.02, clean_reject=0.05, usd=0.0005)

    return {
        "model": "anthropic/claude-haiku-5.5",
        "dataset": dataset,
        "dataset_sha256": "fakehash",
        "git_sha": "0123456",
        "git_dirty": False,
        "articles": 100,
        "methods": {
            "plain_tuned": pt,
            "per_claim_v2": pc,
            "jev_judge": jj,
            "jev_cascade": jc,
        },
    }


def test_go_jev_judge_when_cheaper_and_passes() -> None:
    r = _fixture_run()
    result = evaluate_reports([r, r, r], w7_audit_score=0.95)

    assert result.decision == "GO"
    assert result.winner == "jev_judge"
    assert result.candidate_results["jev_judge"].all_pass
    assert result.candidate_results["jev_cascade"].all_pass


def test_go_jev_cascade_when_cheaper_and_passes() -> None:
    r = _fixture_run(
        jev_judge=_method(catch=0.98, false_pass=0.02, clean_reject=0.02, usd=0.0004),
        jev_cascade=_method(catch=0.98, false_pass=0.02, clean_reject=0.02, usd=0.0003),
    )
    result = evaluate_reports([r, r, r], w7_audit_score=0.95)

    assert result.decision == "GO"
    assert result.winner == "jev_cascade"


def test_go_jev_judge_on_cost_tie() -> None:
    r = _fixture_run(
        jev_judge=_method(catch=0.98, false_pass=0.02, clean_reject=0.02, usd=0.0004),
        jev_cascade=_method(catch=0.98, false_pass=0.02, clean_reject=0.02, usd=0.0004),
    )
    result = evaluate_reports([r, r, r], w7_audit_score=0.95)

    assert result.decision == "GO"
    assert result.winner == "jev_judge"


def test_go_without_jev_when_candidates_fail_w4_or_w6() -> None:
    r = _fixture_run(
        jev_judge=_method(precision=0.90),
        jev_cascade=_method(precision=0.90),
    )
    result = evaluate_reports([r, r, r], w7_audit_score=0.95)

    assert result.decision == "GO WITHOUT JEV"
    assert result.winner is None


def test_stop_when_no_method_passes_w1_to_w3() -> None:
    bad = _method(catch=0.80, false_pass=0.20, clean_reject=0.30)
    r = _fixture_run(
        plain_tuned=bad,
        per_claim_v2=bad,
        jev_judge=bad,
        jev_cascade=bad,
    )
    result = evaluate_reports([r, r, r], w7_audit_score=0.95)

    assert result.decision == "STOP"


def test_individual_kr_evaluations() -> None:
    # Range is 0.98 - 0.90 = 0.08 > 0.05 -> W9 must FAIL
    r1 = _fixture_run(jev_judge=_method(catch=0.90))
    r2 = _fixture_run(jev_judge=_method(catch=0.94))
    r3 = _fixture_run(jev_judge=_method(catch=0.98))
    result = evaluate_reports([r1, r2, r3], w7_audit_score=0.95)
    assert not result.candidate_results["jev_judge"].krs["W9"].passed


def test_requires_exact_number_of_runs() -> None:
    r = _fixture_run()
    with pytest.raises(ValueError, match="Expected 3 reports"):
        evaluate_reports([r, r])


def test_requires_same_dataset() -> None:
    r1 = _fixture_run(dataset="holdout")
    r2 = _fixture_run(dataset="holdout")
    r3 = _fixture_run(dataset="dev")
    with pytest.raises(ValueError, match="Dataset mismatch"):
        evaluate_reports([r1, r2, r3])
