"""Routing, evidence, threshold and report tests for the Jev methods (no network)."""

from typing import Any

import pytest

from evals.claim_benchmark.harness import (
    MethodResult,
    PlantedError,
    Variant,
    evidence_passage,
    jev_cascade,
    jev_judge,
    method_report,
    reliability_table,
    select_threshold,
)
from evals.claim_benchmark.jev import ClaimDecision, DecisionBatch

SOURCE = "Sales rose 10% in 2025. The firm has 3 stores. Prices were stable."
SENTENCES = ("Sales rose 10% in 2025.", "The firm has 4 stores.", "Prices were stable.")
VARIANT = Variant("c/s1e0", "c", "en-US", "T", SOURCE, SENTENCES, PlantedError("number", 1))


class ScriptedJev:
    def __init__(self, decisions: dict[str, ClaimDecision]) -> None:
        self.decisions = decisions

    def judge_claims(self, source: str, claims: list[str]) -> DecisionBatch:
        return DecisionBatch([self.decisions[c] for c in claims], 0.0001, {"typesafe/jev-1.13-x"})


class ScriptedLLM:
    def __init__(self, judge_status: str = "supported") -> None:
        self.judge_status = judge_status
        self.judged: list[str] = []

    def healthcheck(self) -> None: ...

    def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        if schema.__name__ == "ClaimListV2":
            return {"claims": [{"text": s, "kind": "fact"} for s in SENTENCES]}
        claim = prompt.rsplit("CLAIM:", 1)[1].strip()
        self.judged.append(claim)
        quote = "Sales rose 10% in 2025." if "10%" in claim else "Prices were stable."
        return {"status": self.judge_status, "evidence_quote": quote}


def _yes(confidence: float = 0.95) -> ClaimDecision:
    return ClaimDecision("supported", confidence, {"supported": confidence})


def test_cascade_accepts_confident_supported_claim_without_llm_judge() -> None:
    jev = ScriptedJev({s: _yes() for s in SENTENCES})
    llm = ScriptedLLM()

    result = jev_cascade(llm, VARIANT, jev=jev, threshold=0.9)

    accepted = [t for t in result.traces if t.accepted_by_jev]
    assert [t.text for t in accepted] == ["Sales rose 10% in 2025.", "Prices were stable."]
    assert "Sales rose 10% in 2025." not in llm.judged
    assert accepted[0].evidence == "Sales rose 10% in 2025."


def test_cascade_escalates_when_claim_number_not_in_source() -> None:
    jev = ScriptedJev({s: _yes() for s in SENTENCES})
    llm = ScriptedLLM(judge_status="contradicted")

    result = jev_cascade(llm, VARIANT, jev=jev, threshold=0.9)

    assert llm.judged == ["The firm has 4 stores."]
    assert result.flagged == ["The firm has 4 stores."]
    assert result.publish is False


def test_cascade_escalates_low_confidence_and_refusal() -> None:
    jev = ScriptedJev(
        {
            SENTENCES[0]: _yes(0.5),
            SENTENCES[1]: ClaimDecision("contradicted", 0.9, {}),
            SENTENCES[2]: ClaimDecision(None, 0.0, {}),
        }
    )
    llm = ScriptedLLM(judge_status="supported")

    result = jev_cascade(llm, VARIANT, jev=jev, threshold=0.9)

    assert llm.judged == list(SENTENCES)
    assert sum(t.escalated for t in result.traces) == 3


def test_jev_judge_uses_labels_without_llm_judge() -> None:
    jev = ScriptedJev(
        {
            SENTENCES[0]: _yes(0.6),
            SENTENCES[1]: ClaimDecision("contradicted", 0.9, {}),
            SENTENCES[2]: ClaimDecision(None, 0.0, {}),
        }
    )
    llm = ScriptedLLM()

    result = jev_judge(llm, VARIANT, jev=jev)

    assert llm.judged == []
    assert result.flagged == ["The firm has 4 stores.", "Prices were stable."]
    assert [t.truly_supported for t in result.traces] == [True, False, True]


def test_evidence_passage_prefers_overlap_then_numbers() -> None:
    source = "Revenue grew 5% last year. Revenue grew 12% this year. Staff were hired."

    assert evidence_passage("Revenue grew 12% this year", source) == "Revenue grew 12% this year."
    assert evidence_passage("Staff hired", source) == "Staff were hired."


def test_select_threshold_lowest_grid_value_meeting_precision() -> None:
    # At 0.70: 40 of 42 correct (95.2%); at 0.75: 40 of 40.
    points = [(0.72, False)] * 2 + [(0.78, True)] * 10 + [(0.91, True)] * 30
    assert select_threshold(points) == 0.75
    assert select_threshold([(0.99, False)]) is None


def test_reliability_table_bins_by_tenth() -> None:
    table = reliability_table([(0.05, False), (0.95, True), (0.97, False), (1.0, True)])

    assert table == [
        {"bin": "0.0-0.1", "count": 1, "mean_p": 0.05, "observed": 0.0},
        {
            "bin": "0.9-1.0",
            "count": 3,
            "mean_p": pytest.approx(0.9733, abs=1e-4),
            "observed": 2 / 3,
        },
    ]


def test_method_report_has_audit_fields() -> None:
    jev = ScriptedJev({s: _yes() for s in SENTENCES})
    result = jev_cascade(ScriptedLLM(judge_status="contradicted"), VARIANT, jev=jev, threshold=0.9)
    rows: list[tuple[Variant, MethodResult | None]] = [(VARIANT, result)]

    report = method_report(rows, cost_usd=0.002, models={"m"})

    assert {
        "catch_rate",
        "false_pass_rate",
        "clean_reject_rate",
        "failed_runs",
        "usd_per_article",
        "resolved_models",
        "escalation_rate",
        "jev_accept_precision",
        "jev_reliability",
        "by_locale",
        "rows",
    } <= report.keys()
    assert report["escalation_rate"] == pytest.approx(1 / 3)
    assert report["jev_accept_precision"] == 1.0
    assert report["by_locale"]["en-US"]["catch_rate"] == 1.0
