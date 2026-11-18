"""Evaluates the 9 Worth Key Results (W1-W9) and executes the amended BM-6 decision rule.

Usage:
    uv run python -m evals.claim_benchmark.evaluate_kr r1.json r2.json r3.json [--w7-audit 0.95]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.claim_benchmark import rules


@dataclass(frozen=True)
class KeyResultStatus:
    name: str
    passed: bool
    threshold: str
    observed: str
    detail: str = ""


@dataclass
class MethodMetrics:
    name: str
    catch_mean: float
    catch_range: float
    false_pass_mean: float
    false_pass_range: float
    clean_reject_mean: float
    clean_reject_range: float
    usd_mean: float
    jev_precision_mean: float | None
    language_catch_gap: float
    language_clean_gap: float
    has_evidence_passages: bool


@dataclass
class MethodEvaluation:
    method: str
    metrics: MethodMetrics
    krs: dict[str, KeyResultStatus]

    @property
    def all_pass(self) -> bool:
        return all(kr.passed for kr in self.krs.values())


@dataclass
class EvaluationResult:
    decision: Literal["GO", "GO WITHOUT JEV", "STOP", "NO GO"]
    winner: str | None
    candidate_results: dict[str, MethodEvaluation]
    reference_results: dict[str, MethodEvaluation]


def _extract_metrics(method_name: str, runs: list[dict[str, Any]]) -> MethodMetrics:
    method_runs = [r["methods"][method_name] for r in runs]
    catches = [float(m["catch_rate"]) for m in method_runs]
    fps = [float(m["false_pass_rate"]) for m in method_runs]
    cjs = [float(m["clean_reject_rate"]) for m in method_runs]
    usds = [float(m.get("usd_per_article", 0.0)) for m in method_runs]

    precisions = [
        float(m["jev_accept_precision"])
        for m in method_runs
        if m.get("jev_accept_precision") is not None
    ]
    precision_mean = statistics.mean(precisions) if precisions else None

    pt_catches = [
        float(m["by_locale"]["pt-BR"]["catch_rate"])
        for m in method_runs
        if "pt-BR" in m.get("by_locale", {})
    ]
    en_catches = [
        float(m["by_locale"]["en-US"]["catch_rate"])
        for m in method_runs
        if "en-US" in m.get("by_locale", {})
    ]
    pt_cleans = [
        float(m["by_locale"]["pt-BR"]["clean_reject_rate"])
        for m in method_runs
        if "pt-BR" in m.get("by_locale", {})
    ]
    en_cleans = [
        float(m["by_locale"]["en-US"]["clean_reject_rate"])
        for m in method_runs
        if "en-US" in m.get("by_locale", {})
    ]

    lang_catch_gap = (
        abs(statistics.mean(pt_catches) - statistics.mean(en_catches))
        if pt_catches and en_catches
        else 0.0
    )
    lang_clean_gap = (
        abs(statistics.mean(pt_cleans) - statistics.mean(en_cleans))
        if pt_cleans and en_cleans
        else 0.0
    )

    has_evidence = True
    for m in method_runs:
        for row in m.get("rows", []):
            for t in row.get("traces", []):
                if (t.get("supported") or t.get("accepted_by_jev")) and not t.get("evidence"):
                    has_evidence = False

    return MethodMetrics(
        name=method_name,
        catch_mean=statistics.mean(catches),
        catch_range=max(catches) - min(catches),
        false_pass_mean=statistics.mean(fps),
        false_pass_range=max(fps) - min(fps),
        clean_reject_mean=statistics.mean(cjs),
        clean_reject_range=max(cjs) - min(cjs),
        usd_mean=statistics.mean(usds),
        jev_precision_mean=precision_mean,
        language_catch_gap=lang_catch_gap,
        language_clean_gap=lang_clean_gap,
        has_evidence_passages=has_evidence,
    )


def _eval_method(
    target: MethodMetrics,
    plain_tuned: MethodMetrics,
    per_claim_v2: MethodMetrics,
    *,
    is_candidate: bool,
    w7_audit_score: float,
) -> MethodEvaluation:
    krs: dict[str, KeyResultStatus] = {}

    # W1: Beats a tuned prompt
    better_fp = target.false_pass_mean <= plain_tuned.false_pass_mean - rules.W1_MIN_IMPROVEMENT
    better_cr = target.clean_reject_mean <= plain_tuned.clean_reject_mean - rules.W1_MIN_IMPROVEMENT
    not_worse_cr = (
        target.clean_reject_mean <= plain_tuned.clean_reject_mean + rules.W1_MAX_REGRESSION
    )
    not_worse_fp = target.false_pass_mean <= plain_tuned.false_pass_mean + rules.W1_MAX_REGRESSION
    w1_passed = (better_fp and not_worse_cr) or (better_cr and not_worse_fp)
    fp_delta = target.false_pass_mean - plain_tuned.false_pass_mean
    cr_delta = target.clean_reject_mean - plain_tuned.clean_reject_mean
    krs["W1"] = KeyResultStatus(
        name="W1 Beats tuned prompt",
        passed=w1_passed,
        threshold=f"-{rules.W1_MIN_IMPROVEMENT:.0%} FP/CR, <=+{rules.W1_MAX_REGRESSION:.0%} other",
        observed=f"FP Δ={fp_delta:+.1%}, CR Δ={cr_delta:+.1%}",
    )

    # W2: Safe
    w2_passed = (
        target.catch_mean >= rules.W2_MIN_CATCH
        and target.false_pass_mean <= rules.W2_MAX_FALSE_PASS
    )
    krs["W2"] = KeyResultStatus(
        name="W2 Safe",
        passed=w2_passed,
        threshold=f"catch >= {rules.W2_MIN_CATCH:.0%}, false-pass <= {rules.W2_MAX_FALSE_PASS:.0%}",
        observed=f"catch={target.catch_mean:.1%}, false-pass={target.false_pass_mean:.1%}",
    )

    # W3: Usable in a loop
    w3_passed = target.clean_reject_mean <= rules.W3_MAX_CLEAN_REJECT
    krs["W3"] = KeyResultStatus(
        name="W3 Usable in a loop",
        passed=w3_passed,
        threshold=f"clean-reject <= {rules.W3_MAX_CLEAN_REJECT:.0%}",
        observed=f"clean-reject={target.clean_reject_mean:.1%}",
    )

    if is_candidate:
        # W4: Cheaper
        cost_limit = per_claim_v2.usd_mean * rules.W4_MAX_COST_RATIO
        w4_passed = target.usd_mean <= cost_limit or per_claim_v2.usd_mean == 0.0
        krs["W4"] = KeyResultStatus(
            name="W4 Cheaper",
            passed=w4_passed,
            threshold=f"cost <= {rules.W4_MAX_COST_RATIO:.0%} of per_claim_v2 (${cost_limit:.5f})",
            observed=f"${target.usd_mean:.5f}/art vs ${per_claim_v2.usd_mean:.5f}/art",
        )

        # W5: No quality loss from Jev
        catch_min = per_claim_v2.catch_mean - rules.W5_MAX_GAP
        fp_max = per_claim_v2.false_pass_mean + rules.W5_MAX_GAP
        w5_passed = target.catch_mean >= catch_min and target.false_pass_mean <= fp_max
        krs["W5"] = KeyResultStatus(
            name="W5 No quality loss from Jev",
            passed=w5_passed,
            threshold=f"catch >= {catch_min:.1%}, false-pass <= {fp_max:.1%}",
            observed=f"catch={target.catch_mean:.1%}, false-pass={target.false_pass_mean:.1%}",
        )

        # W6: Trustworthy acceptance
        prec = target.jev_precision_mean if target.jev_precision_mean is not None else 0.0
        w6_passed = prec >= rules.W6_MIN_ACCEPT_PRECISION
        krs["W6"] = KeyResultStatus(
            name="W6 Trustworthy acceptance",
            passed=w6_passed,
            threshold=f"precision >= {rules.W6_MIN_ACCEPT_PRECISION:.0%}",
            observed=f"precision={prec:.1%}" if target.jev_precision_mean is not None else "N/A",
        )

    # W7: Shows its evidence
    w7_passed = target.has_evidence_passages and w7_audit_score >= rules.W7_MIN_EVIDENCE_CORRECT
    krs["W7"] = KeyResultStatus(
        name="W7 Shows its evidence",
        passed=w7_passed,
        threshold=f"all passages present & audit >= {rules.W7_MIN_EVIDENCE_CORRECT:.0%}",
        observed=f"passages_present={target.has_evidence_passages}, audit={w7_audit_score:.1%}",
    )

    # W8: Works in both languages
    w8_passed = (
        target.language_catch_gap <= rules.W8_MAX_LANGUAGE_GAP
        and target.language_clean_gap <= rules.W8_MAX_LANGUAGE_GAP
    )
    obs_w8 = f"catch gap={target.language_catch_gap:.1%}, clean gap={target.language_clean_gap:.1%}"
    krs["W8"] = KeyResultStatus(
        name="W8 Works in both languages",
        passed=w8_passed,
        threshold=f"locale gap <= {rules.W8_MAX_LANGUAGE_GAP:.0%}",
        observed=obs_w8,
    )

    # W9: Stable across runs
    w9_passed = (
        target.catch_range <= rules.W9_MAX_RUN_RANGE
        and target.false_pass_range <= rules.W9_MAX_RUN_RANGE
        and target.clean_reject_range <= rules.W9_MAX_RUN_RANGE
    )
    obs_w9 = (
        f"catch={target.catch_range:.1%}, "
        f"fp={target.false_pass_range:.1%}, "
        f"cr={target.clean_reject_range:.1%}"
    )
    krs["W9"] = KeyResultStatus(
        name="W9 Stable across runs",
        passed=w9_passed,
        threshold=f"run range <= {rules.W9_MAX_RUN_RANGE:.0%}",
        observed=obs_w9,
    )

    return MethodEvaluation(method=target.name, metrics=target, krs=krs)


def evaluate_reports(
    reports: list[dict[str, Any]], *, w7_audit_score: float = 1.0
) -> EvaluationResult:
    if len(reports) != rules.RUNS_PER_MEASUREMENT:
        msg = f"Expected {rules.RUNS_PER_MEASUREMENT} reports, got {len(reports)}"
        raise ValueError(msg)

    datasets = {r.get("dataset") for r in reports}
    if len(datasets) != 1:
        msg = f"Dataset mismatch across reports: {datasets}"
        raise ValueError(msg)

    plain_metrics = _extract_metrics("plain_tuned", reports)
    per_claim_metrics = _extract_metrics("per_claim_v2", reports)

    candidates = {
        name: _extract_metrics(name, reports)
        for name in rules.JEV_CANDIDATES
        if name in reports[0]["methods"]
    }

    eval_plain = _eval_method(
        plain_metrics,
        plain_metrics,
        per_claim_metrics,
        is_candidate=False,
        w7_audit_score=w7_audit_score,
    )
    eval_pc = _eval_method(
        per_claim_metrics,
        plain_metrics,
        per_claim_metrics,
        is_candidate=False,
        w7_audit_score=w7_audit_score,
    )

    candidate_evals = {
        name: _eval_method(
            metrics,
            plain_metrics,
            per_claim_metrics,
            is_candidate=True,
            w7_audit_score=w7_audit_score,
        )
        for name, metrics in candidates.items()
    }

    passing_candidates = [ce for ce in candidate_evals.values() if ce.all_pass]

    all_methods = [eval_plain, eval_pc, *candidate_evals.values()]
    any_pass_w1_w3 = any(
        m.krs["W1"].passed and m.krs["W2"].passed and m.krs["W3"].passed for m in all_methods
    )

    decision: Literal["GO", "GO WITHOUT JEV", "STOP", "NO GO"]
    if passing_candidates:
        passing_candidates.sort(
            key=lambda c: (c.metrics.usd_mean, 0 if c.method == "jev_judge" else 1)
        )
        winner = passing_candidates[0].method
        decision = "GO"
    elif eval_pc.all_pass:
        winner = None
        decision = "GO WITHOUT JEV"
    elif not any_pass_w1_w3:
        winner = None
        decision = "STOP"
    else:
        winner = None
        decision = "NO GO"

    return EvaluationResult(
        decision=decision,
        winner=winner,
        candidate_results=candidate_evals,
        reference_results={"plain_tuned": eval_plain, "per_claim_v2": eval_pc},
    )


def print_evaluation(result: EvaluationResult) -> None:
    print("=" * 78)
    print("WORTH KEY RESULTS (W1-W9) EVALUATION")
    print("=" * 78)

    for name, ce in result.candidate_results.items():
        print(f"\nCandidate: {name} (Cost: ${ce.metrics.usd_mean:.5f}/article)")
        print("-" * 78)
        for code, kr in ce.krs.items():
            status = "PASS" if kr.passed else "FAIL"
            print(f"  {code:3} {status:4} | {kr.name:28} | {kr.observed}")
        print(f"  Overall: {'PASS' if ce.all_pass else 'FAIL'}")

    print("\nReference Methods:")
    print("-" * 78)
    for name, re in result.reference_results.items():
        w1 = re.krs["W1"].passed
        w2 = re.krs["W2"].passed
        w3 = re.krs["W3"].passed
        print(f"  {name:15}: W1={w1} W2={w2} W3={w3} All={re.all_pass}")

    print("=" * 78)
    if result.decision == "GO":
        print(f"DECISION: {result.decision} ({result.winner})")
    else:
        print(f"DECISION: {result.decision}")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="Paths to 3 benchmark run JSON reports")
    parser.add_argument(
        "--w7-audit-score",
        type=float,
        default=1.0,
        help="Human audit accuracy on 50 evidence quotes (fraction in [0, 1])",
    )
    args = parser.parse_args(argv)

    if len(args.reports) != rules.RUNS_PER_MEASUREMENT:
        msg = (
            f"Error: evaluate-kr requires {rules.RUNS_PER_MEASUREMENT} reports, "
            f"got {len(args.reports)}"
        )
        print(msg, file=sys.stderr)
        return 2

    loaded_reports: list[dict[str, Any]] = []
    for path_str in args.reports:
        p = Path(path_str)
        if not p.exists():
            print(f"Error: file not found: {p}", file=sys.stderr)
            return 2
        loaded_reports.append(json.loads(p.read_text(encoding="utf-8")))

    try:
        result = evaluate_reports(loaded_reports, w7_audit_score=args.w7_audit_score)
    except Exception as exc:  # noqa: BLE001
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 1

    print_evaluation(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
