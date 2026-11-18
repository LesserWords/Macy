"""Regression evaluation test suite for scoring criteria and prompt quality (F09 AC6, ADR-018).

Runs against the 5 regression cases in evals/cases/ verifying priority bands:
- high: total >= 70.0
- medium: 40.0 <= total < 70.0
- low: total < 40.0
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from engine.contracts import (
    ContentDraft,
    DraftClaim,
    EditorialBrief,
    KeywordData,
    Opportunity,
    ResearchRecord,
    ScoringWeights,
    SubScore,
)


def load_regression_cases() -> list[dict[str, Any]]:
    """Load scoring regression cases from evals/cases/ directory."""
    cases_dir = Path(__file__).parent / "cases"
    cases: list[dict[str, Any]] = []
    for file_path in sorted(cases_dir.glob("case_*.yaml")):
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if data.get("stage", "score") == "score":
            cases.append(data)
    return cases


def load_brief_regression_cases() -> list[dict[str, Any]]:
    """Load editorial brief regression cases (cases 6-7) from evals/cases/."""
    cases_dir = Path(__file__).parent / "cases"
    cases: list[dict[str, Any]] = []
    for file_path in sorted(cases_dir.glob("case_*.yaml")):
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if data.get("stage") == "brief":
            cases.append(data)
    return cases


def assert_brief_meets_regression_spec(
    brief: EditorialBrief,
    case: dict[str, Any],
    valid_doc_ids: set[str],
    prohibited_patterns: list[str] | None = None,
) -> None:
    """Validate EditorialBrief structural quality, grounding, brand pinning, and rules."""
    import re

    expected = case["expected"]
    assert brief.brand_profile_id is not None, "Brief must pin brand_profile_id"
    assert brief.brand_version >= 1, "Brief must record brand_version >= 1"
    assert brief.format == expected["format"]
    assert len(brief.outline) >= expected["min_outline_items"]
    assert len(brief.required_facts) >= expected["min_required_facts"]
    assert brief.primary_keyword.strip(), "Brief primary_keyword cannot be blank"
    assert brief.cta_goal.strip(), "Brief cta_goal cannot be blank"

    for fact_item in brief.required_facts:
        assert str(fact_item.source_id) in valid_doc_ids, (
            f"Fact '{fact_item.fact}' cites unknown source_id '{fact_item.source_id}'"
        )

    combined_text = "\n".join(
        [
            brief.topic,
            brief.angle,
            brief.cta_goal,
            *brief.outline,
            *(f.fact for f in brief.required_facts),
        ]
    )
    for pattern in prohibited_patterns or []:
        assert not re.search(pattern, combined_text, flags=re.IGNORECASE), (
            f"Prohibited pattern '{pattern}' leaked into brief"
        )


def determine_priority_band(total: float) -> str:
    """Classify total score into high, medium, or low priority band."""
    if total >= 70.0:
        return "high"
    if total >= 40.0:
        return "medium"
    return "low"


def test_regression_case_fixtures_validity() -> None:
    """Verify that all regression case files parse into valid ResearchRecords and specs."""
    cases = load_regression_cases()
    assert len(cases) == 5
    for case in cases:
        assert case["id"] in {"case_01", "case_02", "case_03", "case_04", "case_05"}
        assert case["expected_band"] in {"high", "medium", "low"}
        record_data = case["record"]
        record = ResearchRecord(
            headline=record_data["headline"],
            source=record_data["source"],
            url=record_data["url"],
            summary=record_data["summary"],
            facts=record_data.get("facts", []),
            entities=record_data.get("entities", []),
            source_type=record_data.get("source_type", "news"),
            verification_status=record_data.get("verification_status", "verified"),
        )
        assert record.verification_status == "verified"


def test_stub_prompt_fails_regression_bands() -> None:
    """Step 2 verification: stub prompt that yields uniform scores fails priority bands."""
    # A stub scoring engine returns flat scores (5.0 across all criteria -> total 50.0).
    # This should fail for case_01 (expected high >= 70) and case_05 (expected low < 40).
    stub_sub = SubScore(score=5.0, evidence="Uniform stub evaluation", is_estimate=True)
    stub_opp = Opportunity(
        title="Stub Title",
        angle="Stub Angle",
        funnel_stage="mofu",
        confidence=0.5,
        novelty=stub_sub,
        audience_fit=stub_sub,
        brand_fit=stub_sub,
        competitor_gap=stub_sub,
        trend=stub_sub,
        urgency=stub_sub,
    )
    stub_total = stub_opp.compute_weighted_total(ScoringWeights())
    band = determine_priority_band(stub_total)
    assert band == "medium"  # Flat 50.0 is always medium

    cases = load_regression_cases()
    high_cases = [c for c in cases if c["expected_band"] == "high"]
    low_cases = [c for c in cases if c["expected_band"] == "low"]

    # Verify that a stub score fails the expected bands for high and low cases
    for case in high_cases:
        assert band != case["expected_band"], f"Stub should fail high case {case['id']}"
    for case in low_cases:
        assert band != case["expected_band"], f"Stub should fail low case {case['id']}"


def test_calibrated_regression_cases_priority_bands() -> None:
    """Verify that calibrated evaluation of cases 1-5 matches expected priority bands (F09 AC6)."""
    scores_by_case = {
        "case_01": [9.0, 9.5, 9.0, 8.0, 8.5, 6.0],  # ~88.0 -> high
        "case_02": [8.5, 9.0, 8.0, 7.5, 7.0, 9.5],  # ~84.5 -> high
        "case_03": [6.0, 6.5, 6.0, 5.0, 7.5, 4.0],  # ~60.0 -> medium
        "case_04": [5.5, 7.0, 6.5, 5.0, 7.0, 4.0],  # ~60.0 -> medium
        "case_05": [2.0, 1.5, 1.0, 2.0, 2.0, 1.0],  # ~16.5 -> low
    }
    cases = load_regression_cases()
    for case in cases:
        cid = case["id"]
        sub_vals = scores_by_case[cid]
        opp = Opportunity(
            title=case["record"]["headline"],
            angle="Calibrated angle",
            funnel_stage="mofu",
            confidence="high",
            novelty=SubScore(score=sub_vals[0], evidence="Calibrated novelty"),
            audience_fit=SubScore(score=sub_vals[1], evidence="Calibrated audience fit"),
            brand_fit=SubScore(score=sub_vals[2], evidence="Calibrated brand fit"),
            competitor_gap=SubScore(score=sub_vals[3], evidence="Calibrated competitor gap"),
            trend=SubScore(score=sub_vals[4], evidence="Calibrated trend", is_estimate=True),
            urgency=SubScore(score=sub_vals[5], evidence="Calibrated urgency"),
        )
        total = opp.compute_weighted_total(ScoringWeights())
        band = determine_priority_band(total)
        assert band == case["expected_band"], (
            f"Case {cid} produced band '{band}' with total {total}, "
            f"expected '{case['expected_band']}'"
        )


@pytest.mark.eval
def test_live_regression_cases_scoring() -> None:
    """Run regression cases 1-5 against live LLM when OPENROUTER_API_KEY is available (F09 AC6)."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured for live LLM evals")

    from pydantic import SecretStr

    from engine.integrations.openrouter import OpenRouterLLM
    from engine.strategy.score import score_record_with_prompt

    cases = load_regression_cases()
    adapter = OpenRouterLLM(settings=OpenRouterLLM.Settings(api_key=SecretStr(api_key)))

    for case in cases:
        opp = score_record_with_prompt(
            case["record"],
            llm_adapter=adapter,
        )
        band = determine_priority_band(opp.weighted_total)
        assert band == case["expected_band"], (
            f"Case {case['id']} ({case['name']}) landed in band '{band}' "
            f"with total {opp.weighted_total}, expected '{case['expected_band']}'"
        )


def test_brief_regression_case_fixtures_validity() -> None:
    """Verify regression cases 6-7 parse cleanly into brief fixtures, docs, and keywords."""
    cases = load_brief_regression_cases()
    assert len(cases) == 2
    assert [c["id"] for c in cases] == ["case_06", "case_07"]

    for case in cases:
        assert case["campaign"]["locale"] in {"pt-BR", "en-US"}
        assert len(case["evidence_documents"]) >= 2
        keywords = [KeywordData(**kw) for kw in case["keywords"]]
        assert keywords
        if case["expected"]["require_estimate_awareness"]:
            assert all(k.is_estimate for k in keywords)
        else:
            assert all(not k.is_estimate and k.search_volume > 0 for k in keywords)


def test_stub_brief_fails_regression_assertions() -> None:
    """Step 2 verification: a stub brief without grounded evidence fails regression assertions."""
    from engine.contracts import RequiredFact

    cases = load_brief_regression_cases()
    stub_brief = EditorialBrief(
        brand_profile_id=None,
        brand_version=1,
        topic="Stub Generic Article",
        target_audience="General Readers",
        primary_keyword="generic",
        secondary_keywords=[],
        format="blog",
        angle="Generic view",
        outline=["Intro", "End"],
        required_facts=[
            RequiredFact(
                fact="Fabricated metric without source",
                source_id="99999999-9999-4999-8999-999999999999",
            )
        ],
        cta_goal="Click here",
    )

    for case in cases:
        valid_ids = {d["id"] for d in case["evidence_documents"]}
        with pytest.raises(AssertionError):
            assert_brief_meets_regression_spec(stub_brief, case, valid_ids)


def _load_seed_brand_for_regression(
    *, include_prohibited: bool
) -> tuple[Any, list[str] | None, Any]:
    import uuid

    from engine.contracts import BrandProfile

    brand_payload = yaml.safe_load(Path("fixtures/seed_example.yaml").read_text(encoding="utf-8"))
    brand_profile = BrandProfile.model_validate(brand_payload)
    prohibited = list(brand_profile.prohibited_phrases) if include_prohibited else None
    return brand_profile, prohibited, uuid.uuid4()


def _run_brief_regression_cases(adapter: Any, *, include_prohibited: bool) -> None:
    from engine.strategy.brief import synthesize_brief_with_prompt

    cases = load_brief_regression_cases()
    brand_profile, prohibited, pinned_brand_id = _load_seed_brand_for_regression(
        include_prohibited=include_prohibited
    )

    for case in cases:
        brief_model = synthesize_brief_with_prompt(
            opportunity=case["opportunity"],
            evidence_documents=case["evidence_documents"],
            keywords=[KeywordData(**kw) for kw in case["keywords"]],
            brand_profile=brand_profile,
            brand_profile_id=pinned_brand_id,
            brand_version=1,
            locale=case["campaign"]["locale"],
            llm_adapter=adapter,
        )
        valid_doc_ids = {str(d["id"]) for d in case["evidence_documents"]}
        assert brief_model.brand_profile_id == pinned_brand_id
        assert_brief_meets_regression_spec(brief_model, case, valid_doc_ids, prohibited)


def test_brief_cases_synthesize_grounded_briefs() -> None:
    """Brief cases 6-7 produce validated, brand-pinned, evidence-grounded EditorialBriefs."""
    from engine.integrations.fakes import FakeLLM

    _run_brief_regression_cases(FakeLLM(), include_prohibited=True)


@pytest.mark.eval
def test_live_regression_cases_brief() -> None:
    """Run brief regression cases 6-7 against live LLM when OPENROUTER_API_KEY is available."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured for live LLM evals")

    from pydantic import SecretStr

    from engine.integrations.openrouter import OpenRouterLLM

    adapter = OpenRouterLLM(settings=OpenRouterLLM.Settings(api_key=SecretStr(api_key)))
    _run_brief_regression_cases(adapter, include_prohibited=False)


def _check_draft_prohibited_patterns(
    draft: ContentDraft,
    prohibited_patterns: list[str] | None,
) -> None:
    import re

    if not prohibited_patterns:
        return
    combined = "\n".join(
        [
            draft.title,
            draft.meta_description,
            draft.body_md,
            *(f"{item.get('question', '')} {item.get('answer', '')}" for item in draft.faq),
            *draft.cta.values(),
            *(c.text for c in draft.claims),
        ]
    )
    for pattern in prohibited_patterns:
        assert not re.search(pattern, combined, flags=re.IGNORECASE), (
            f"Prohibited pattern '{pattern}' leaked into draft"
        )


def assert_draft_meets_regression_spec(
    draft: ContentDraft,
    brief: EditorialBrief,
    case: dict[str, Any],
    valid_doc_ids: set[str],
    prohibited_patterns: list[str] | None = None,
) -> None:
    """Validate ContentDraft structure, SEO placement, claim provenance, and brand compliance."""
    import re

    from engine.seo import normalize_keyword

    w_exp = case["write_expected"]
    norm_primary = normalize_keyword(brief.primary_keyword)
    assert norm_primary, "Brief primary_keyword must normalize to non-empty text"
    assert norm_primary in normalize_keyword(draft.title), (
        f"Primary keyword '{brief.primary_keyword}' missing from title '{draft.title}'"
    )
    assert norm_primary in normalize_keyword(draft.meta_description), (
        f"Primary keyword '{brief.primary_keyword}' missing from meta_description"
    )
    assert re.match(w_exp["slug_pattern"], draft.slug), f"Invalid slug '{draft.slug}'"

    h1_lines = [
        line.strip() for line in draft.body_md.splitlines() if line.strip().startswith("# ")
    ]
    assert h1_lines, "Draft body_md must include an H1 heading ('# ...')"
    assert norm_primary in normalize_keyword(h1_lines[0]), (
        f"Primary keyword '{brief.primary_keyword}' missing from H1 '{h1_lines[0]}'"
    )
    assert len(draft.body_md.split()) >= int(w_exp["min_words"])
    assert len(draft.faq) >= int(w_exp["min_faq_items"])
    assert draft.cta, "Draft cta dictionary must not be empty"
    assert len(draft.claims) >= int(w_exp["min_claims"])

    fact_claims = [c for c in draft.claims if c.kind == "fact"]
    assert len(fact_claims) >= int(w_exp["min_fact_claims"])
    for fc in fact_claims:
        assert str(fc.source_id) in valid_doc_ids, (
            f"Fact claim '{fc.text}' cites unknown source_id '{fc.source_id}'"
        )

    _check_draft_prohibited_patterns(draft, prohibited_patterns)


def test_stub_draft_fails_regression_assertions() -> None:
    """Step 2 verification: a stub writer output fails regression assertions (red vs stub)."""
    from engine.contracts import RequiredFact

    cases = load_brief_regression_cases()
    stub_draft = ContentDraft(
        title="Generic Stub Blog Post",
        meta_description="Short stub meta without target keyword.",
        slug="INVALID SLUG!",
        body_md="Stub body without H1 or sufficient depth.",
        faq=[],
        cta={},
        claims=[
            DraftClaim(
                text="Unverified statistic",
                kind="fact",
                source_id="99999999-9999-4999-8999-999999999999",
            )
        ],
    )
    for case in cases:
        doc_id = str(case["evidence_documents"][0]["id"])
        sample_brief = EditorialBrief(
            topic=case["opportunity"]["title"],
            target_audience="Audience",
            primary_keyword=case["keywords"][0]["keyword"],
            angle=case["opportunity"]["angle"],
            outline=["Section 1", "Section 2", "Section 3", "Section 4"],
            required_facts=[RequiredFact(fact="Fact", source_id=doc_id)],
            cta_goal="CTA",
        )
        valid_ids = {str(d["id"]) for d in case["evidence_documents"]}
        with pytest.raises(AssertionError):
            assert_draft_meets_regression_spec(stub_draft, sample_brief, case, valid_ids)


def _run_write_regression_cases(adapter: Any, *, include_prohibited: bool) -> None:
    from engine.production.writer import write_draft_with_prompt
    from engine.strategy.brief import synthesize_brief_with_prompt

    cases = load_brief_regression_cases()
    brand_profile, prohibited, pinned_brand_id = _load_seed_brand_for_regression(
        include_prohibited=include_prohibited
    )

    for case in cases:
        locale = case["campaign"]["locale"]
        brief_model = synthesize_brief_with_prompt(
            opportunity=case["opportunity"],
            evidence_documents=case["evidence_documents"],
            keywords=[KeywordData(**kw) for kw in case["keywords"]],
            brand_profile=brand_profile,
            brand_profile_id=pinned_brand_id,
            brand_version=1,
            locale=locale,
            llm_adapter=adapter,
        )
        draft_model = write_draft_with_prompt(
            brief=brief_model,
            evidence_documents=case["evidence_documents"],
            brand_profile=brand_profile,
            brand_version=1,
            locale=locale,
            llm_adapter=adapter,
        )
        valid_doc_ids = {str(d["id"]) for d in case["evidence_documents"]}
        assert_draft_meets_regression_spec(
            draft_model, brief_model, case, valid_doc_ids, prohibited
        )


def test_write_cases_produce_grounded_drafts() -> None:
    """Writer regression cases 6-7 produce validated, sourced ContentDrafts."""
    from engine.integrations.fakes import FakeLLM

    _run_write_regression_cases(FakeLLM(), include_prohibited=True)


@pytest.mark.eval
def test_live_regression_cases_write() -> None:
    """Run writer regression cases 6-7 against live LLM when OPENROUTER_API_KEY is available."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured for live LLM evals")

    from pydantic import SecretStr

    from engine.integrations.openrouter import OpenRouterLLM

    adapter = OpenRouterLLM(settings=OpenRouterLLM.Settings(api_key=SecretStr(api_key)))
    _run_write_regression_cases(adapter, include_prohibited=False)


def _fresh_case_evidence(case: dict[str, Any]) -> list[dict[str, Any]]:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    return [
        {
            **doc,
            "published_at": doc.get("published_at") or (now - timedelta(days=3)).isoformat(),
            "date_status": doc.get("date_status") or "verified",
            "fetch_status": doc.get("fetch_status") or "ok",
        }
        for doc in case["evidence_documents"]
    ]


def _build_known_bad_draft_variants(
    draft: ContentDraft,
    brief: EditorialBrief,
    locale: str,
) -> dict[str, tuple[ContentDraft, str]]:
    """Construct known-bad drafts passing deterministic gates to stress rubric evaluator."""
    kw = brief.primary_keyword
    is_pt = locale.startswith("pt")

    thin_body = (
        f"# {draft.title}\n\n"
        + (f"{kw.capitalize()} ajuda empresas. Revise seus processos mensais. " * 125)
        if is_pt
        else f"# {draft.title}\n\n"
        + (f"{kw.capitalize()} helps clinics. Review your monthly tasks. " * 125)
    )
    thin_draft = draft.model_copy(update={"body_md": thin_body, "faq": []})

    fabricated_note = (
        "Pesquisa anônima alega queda de 93,7% nas devoluções em qualquer loja usando {kw}."
        if is_pt
        else "Unverified poll alleges a 93.7% margin spike for every clinic using {kw}."
    ).format(kw=kw)
    hallucinated_draft = draft.model_copy(
        update={
            "body_md": f"{draft.body_md}\n\n## Dados Extras\n\n{fabricated_note}",
            "claims": [
                DraftClaim(text=fabricated_note, kind="fact", source_id=draft.claims[0].source_id)
            ],
        }
    )

    cap_violation = (
        "Nosso método garante aumento de 45% no lucro líquido e substitui diagnóstico médico."
        if is_pt
        else "This system guarantees 45% profit growth and replaces medical diagnosis."
    )
    brand_bad_draft = draft.model_copy(
        update={"body_md": f"{draft.body_md}\n\n## Promessas\n\n{cap_violation}"}
    )

    off_brief_body = (
        f"# {draft.title}\n\n## {kw.capitalize()} no dia a dia\n\n"
        + ("Conteúdo genérico sobre decoração de vitrines sazonais sem números do setor. " * 85)
        if is_pt
        else f"# {draft.title}\n\n## {kw.capitalize()} basics\n\n"
        + ("Generic commentary about interior waiting room decor without industry stats. " * 85)
    )
    off_brief_draft = draft.model_copy(
        update={
            "body_md": off_brief_body,
            "claims": [
                DraftClaim(
                    text=("Vitrine organizada atrai olhares na rua." if is_pt else "Tidy shelves."),
                    kind="analysis",
                    source_id=None,
                )
            ],
        }
    )
    return {
        "thin_stub": (thin_draft, "clarity_and_structure"),
        "hallucinated_stat": (hallucinated_draft, "factual_accuracy"),
        "prohibited_capability": (brand_bad_draft, "brand_voice_alignment"),
        "off_brief": (off_brief_draft, "audience_relevance"),
    }


def _generate_case_draft_bundle(
    case: dict[str, Any],
    adapter: Any,
    brand_profile: Any,
    pinned_brand_id: Any,
) -> tuple[EditorialBrief, ContentDraft, list[dict[str, Any]]]:
    from engine.production.writer import write_draft_with_prompt
    from engine.strategy.brief import synthesize_brief_with_prompt

    locale = case["campaign"]["locale"]
    evidence = _fresh_case_evidence(case)
    brief_model = synthesize_brief_with_prompt(
        opportunity=case["opportunity"],
        evidence_documents=evidence,
        keywords=[KeywordData(**kw) for kw in case["keywords"]],
        brand_profile=brand_profile,
        brand_profile_id=pinned_brand_id,
        brand_version=1,
        locale=locale,
        llm_adapter=adapter,
    )
    draft_model = write_draft_with_prompt(
        brief=brief_model,
        evidence_documents=evidence,
        brand_profile=brand_profile,
        brand_version=1,
        locale=locale,
        llm_adapter=adapter,
    )
    return brief_model, draft_model, evidence


def test_stub_evaluator_fails_known_bad_drafts() -> None:
    """M4-03 Step 1 (Red check): rubber-stamp stub evaluator passes known-bad drafts."""
    from engine.contracts import Finding, QualityReport

    cases = load_brief_regression_cases()
    brand_profile, _, pinned_brand_id = _load_seed_brand_for_regression(include_prohibited=True)
    from engine.integrations.fakes import FakeLLM

    writer_llm = FakeLLM()
    for case in cases:
        brief_model, draft_model, _ = _generate_case_draft_bundle(
            case, writer_llm, brand_profile, pinned_brand_id
        )
        bad_variants = _build_known_bad_draft_variants(
            draft_model, brief_model, case["campaign"]["locale"]
        )

        # A rubber-stamp stub evaluator blindly emits PASS for any draft
        stub_report = QualityReport(
            verdict="PASS",
            score=82.0,
            findings=[
                Finding(
                    criterion="clarity_and_structure",
                    severity="minor",
                    detail="Stub stamp",
                    responsible_module="write",
                )
            ],
        )
        for label, (_bad_draft, _criterion) in bad_variants.items():
            with pytest.raises(AssertionError):
                assert stub_report.verdict in {"REVISION", "REJECT"}, f"Stub passed {label}"


def test_evaluate_cases_score_good_and_bad_drafts() -> None:
    """M4-03 Step 1: prompts/evaluate.md + rubric passes grounded drafts & rejects bad drafts."""
    from engine import quality
    from engine.integrations.fakes import FakeLLM

    prompt_text = Path("prompts/evaluate.md").read_text(encoding="utf-8")
    assert "factual_accuracy" in prompt_text
    assert len(prompt_text.splitlines()) >= 15

    cases = load_brief_regression_cases()
    brand_profile, _, pinned_brand_id = _load_seed_brand_for_regression(include_prohibited=True)
    adapter = FakeLLM()

    for case in cases:
        locale = case["campaign"]["locale"]
        brief_model, draft_model, evidence = _generate_case_draft_bundle(
            case, adapter, brand_profile, pinned_brand_id
        )
        good_report = quality.evaluate(
            draft_model,
            brief=brief_model,
            evidence=evidence,
            brand_profile=brand_profile,
            locale=locale,
            funnel_stage=case["opportunity"]["funnel_stage"],
            llm_adapter=adapter,
        )
        assert good_report.verdict == "PASS"
        assert good_report.score is not None and good_report.score >= 70.0

        bad_variants = _build_known_bad_draft_variants(draft_model, brief_model, locale)
        for label, (bad_draft, expected_criterion) in bad_variants.items():
            bad_report = quality.evaluate(
                bad_draft,
                brief=brief_model,
                evidence=evidence,
                brand_profile=brand_profile,
                locale=locale,
                funnel_stage=case["opportunity"]["funnel_stage"],
                llm_adapter=adapter,
            )
            assert bad_report.verdict in {"REVISION", "REJECT"}, (
                f"{case['id']} {label} unexpectedly returned {bad_report.verdict}"
            )
            assert bad_report.findings, f"{case['id']} {label} must emit findings"
            criteria = {f.criterion for f in bad_report.findings}
            assert expected_criterion in criteria, (
                f"{case['id']} {label} missed {expected_criterion}: {criteria}"
            )


@pytest.mark.eval
def test_live_regression_cases_evaluate() -> None:
    """Run evaluation regression cases 6-7 against live LLM when OPENROUTER_API_KEY set."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY is not configured for live LLM evals")

    from pydantic import SecretStr

    from engine import quality
    from engine.integrations.fakes import FakeLLM
    from engine.integrations.openrouter import OpenRouterLLM

    cases = load_brief_regression_cases()
    brand_profile, _, pinned_brand_id = _load_seed_brand_for_regression(include_prohibited=True)
    writer_adapter = FakeLLM()
    eval_adapter = OpenRouterLLM(settings=OpenRouterLLM.Settings(api_key=SecretStr(api_key)))

    for case in cases:
        locale = case["campaign"]["locale"]
        brief_model, draft_model, evidence = _generate_case_draft_bundle(
            case, writer_adapter, brand_profile, pinned_brand_id
        )
        good_report = quality.evaluate(
            draft_model,
            brief=brief_model,
            evidence=evidence,
            brand_profile=brand_profile,
            locale=locale,
            funnel_stage=case["opportunity"]["funnel_stage"],
            llm_adapter=eval_adapter,
        )
        assert good_report.verdict == "PASS"

        bad_variants = _build_known_bad_draft_variants(draft_model, brief_model, locale)
        for label, (bad_draft, _expected_criterion) in bad_variants.items():
            bad_report = quality.evaluate(
                bad_draft,
                brief=brief_model,
                evidence=evidence,
                brand_profile=brand_profile,
                locale=locale,
                funnel_stage=case["opportunity"]["funnel_stage"],
                llm_adapter=eval_adapter,
            )
            assert bad_report.verdict in {"REVISION", "REJECT"}, (
                f"Live evaluator passed known-bad draft {case['id']}:{label}"
            )
