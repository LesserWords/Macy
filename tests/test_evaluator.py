"""Tests for isolated LLM rubric evaluator and revision routing (M4-03, F14 AC2-AC4, AC6)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from engine import db, quality, services
from engine.contracts import (
    ContentDraft,
    DraftClaim,
    EditorialBrief,
    Finding,
    QualityReport,
    RequiredFact,
)
from engine.integrations.fakes import FakeExtractor, FakeKeywordProvider, FakeLLM, FakeNewsSearch
from engine.production import writer
from tests.test_writer import _seed_workspace_and_brief


class _RecordingEvalLLM:
    """Captures exact system prompt and user input passed by quality.evaluate."""

    class Settings:
        model_name = "recording-eval-v1"
        models_by_stage = {"evaluate": "openai/gpt-eval-test"}

    def __init__(self, reports: list[dict[str, Any]] | None = None) -> None:
        self.settings = self.Settings()
        self.calls: list[str] = []
        self._reports = list(reports or [])

    def healthcheck(self) -> None:
        return None

    def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
        del schema
        self.calls.append(prompt)
        if self._reports:
            return self._reports.pop(0)
        return {
            "verdict": "PASS",
            "score": 88.0,
            "findings": [],
        }


def _seed_draft_bundle(
    conn: psycopg.Connection[Any],
    *,
    locale: str = "pt-BR",
    market: str = "BR",
    funnel_stage: str = "mofu",
) -> dict[str, Any]:
    ctx = _seed_workspace_and_brief(conn, locale=locale, market=market, funnel_stage=funnel_stage)
    draft_row = writer.write(
        ctx["brief_row"],
        conn=conn,
        workspace_id=ctx["workspace_id"],
        actor_user_id=ctx["user_id"],
        offline=True,
    )
    ctx["draft_row"] = draft_row
    return ctx


def _extract_input_json(captured_prompt: str) -> dict[str, Any]:
    marker = "\n\nInput:\n"
    assert marker in captured_prompt
    raw = captured_prompt.rsplit(marker, 1)[-1]
    raw_json = raw.split("\nValidation error in previous attempt:", 1)[0].strip()
    parsed = json.loads(raw_json)
    assert isinstance(parsed, dict)
    return parsed


def test_evaluate_prompt_loading_rubric_and_writer_isolation() -> None:
    """Verify F14 AC2 & AC6: isolated prompt, rubric load, no write prompt or reasoning."""
    from engine.quality.evaluate import (
        build_evaluate_system_prompt,
        get_evaluate_prompt_and_version,
        load_rubric,
    )

    prompt_body, version_tag = get_evaluate_prompt_and_version()
    assert len(prompt_body) > 250
    assert version_tag.startswith("evaluate.md:")
    assert len(version_tag.split(":")[1]) == 16

    rubric = load_rubric(format_slug="blog")
    assert sum(c.weight for c in rubric) == 100
    assert any(c.criterion == "factual_accuracy" and c.critical for c in rubric)

    with (
        patch("engine.quality.evaluate.PROMPT_FILE", Path("missing_eval_prompt.md")),
        pytest.raises(FileNotFoundError, match="Evaluator prompt template missing"),
    ):
        get_evaluate_prompt_and_version()

    custom_rubric = load_rubric(
        {"rubric": [{"criterion": "factual_accuracy", "weight": 100, "critical": True}]}
    )
    assert len(custom_rubric) == 1

    # Poisoned draft/brief inputs carrying writer prompt and reasoning keys must be stripped
    doc_id = "11111111-1111-4111-8111-111111111101"
    brief_model = EditorialBrief(
        topic="Gestao de estoque em oticas",
        target_audience="Gestores de oticas",
        primary_keyword="gestao de estoque otica",
        angle="Curva ABC aplicada ao varejo optico",
        outline=["Panorama", "Curva ABC", "Rotina semanal", "Proximos passos"],
        required_facts=[RequiredFact(fact="Vendas cresceram 6,4%.", source_id=doc_id)],
        cta_goal="Baixar checklist diagnostico",
    )
    draft_dict = {
        "title": "Guia de gestao de estoque otica na loja",
        "meta_description": (
            "Aprenda como aplicar gestao de estoque otica na rotina da sua loja "
            "com dados reais e indicadores de giro mensal."
        ),
        "slug": "gestao-de-estoque-otica",
        "body_md": (
            "# Guia de gestao de estoque otica\n\n"
            "Aplicar controle de giro melhora a rentabilidade da otica independente.\n\n"
            "## Curva ABC e reposicao\n\n"
            "Vendas do segmento optico cresceram 6,4% segundo o IBGE.\n\n"
            "## Rotina semanal\n\nRevisar indicadores toda segunda-feira reduz rupturas."
        ),
        "faq": [{"question": "Como medir o giro?", "answer": "Divida vendas pelo saldo medio."}],
        "cta": {
            "headline": "Baixe o checklist",
            "body": "Checklist diagnostico gratuito.",
            "button_text": "Baixar guia",
        },
        "claims": [{"text": "Vendas cresceram 6,4%.", "kind": "fact", "source_id": doc_id}],
        "reasoning": "SECRET_WRITER_REASONING_TRACE_DO_NOT_LEAK",
        "thinking": "SECRET_WRITER_THINKING_DO_NOT_LEAK",
        "writer_prompt": "SECRET_WRITER_PROMPT_OVERRIDE",
    }
    now = datetime.now(UTC)
    evidence = [
        {
            "id": doc_id,
            "title": "PMC IBGE",
            "url": "https://ibge.gov.br/pmc",
            "published_at": now - timedelta(days=2),
            "date_status": "verified",
            "fetch_status": "ok",
            "facts": ["Vendas cresceram 6,4%."],
        }
    ]

    recorder = _RecordingEvalLLM()
    brief_dict = brief_model.model_dump(mode="json")
    brief_dict["writer_prompt"] = "SECRET_BRIEF_WRITER_LEAK"

    report = quality.evaluate(
        draft_dict,
        brief=brief_dict,
        evidence=evidence,
        locale="pt-BR",
        funnel_stage="tofu",
        options=quality.checks.DeterministicRunOptions(min_words=10, max_words=2500),
        llm_adapter=recorder,
    )
    assert report.verdict == "PASS"
    assert len(recorder.calls) == 1

    captured = recorder.calls[0]
    write_prompt_text = Path("prompts/write_blog.md").read_text(encoding="utf-8")
    assert "Blog Article Writing Guide" in write_prompt_text
    assert "Blog Article Writing Guide" not in captured
    assert "Non-Negotiable Writing & Provenance Rules" not in captured
    assert "SECRET_WRITER_REASONING_TRACE_DO_NOT_LEAK" not in captured
    assert "SECRET_WRITER_THINKING_DO_NOT_LEAK" not in captured
    assert "SECRET_WRITER_PROMPT_OVERRIDE" not in captured
    assert "SECRET_BRIEF_WRITER_LEAK" not in captured

    parsed_input = _extract_input_json(captured)
    assert "reasoning" not in parsed_input["draft"]
    assert "thinking" not in parsed_input["draft"]
    assert "writer_prompt" not in parsed_input["draft"]

    # Also ensure build_evaluate_system_prompt tolerates industry pack and dict brand
    sys_prompt = build_evaluate_system_prompt(
        base_prompt=prompt_body,
        rubric=rubric,
        brand_profile={"voice": "Consultivo"},
        brand_version=2,
        industry_pack={"name": "Optical Retail"},
    )
    assert "factual_accuracy" in sys_prompt
    assert "## Brand Compliance Guardrails (v2)" in sys_prompt
    assert "Optical Retail" in sys_prompt


def test_evaluate_rubric_critical_invariants_and_route_matrix() -> None:
    """Verify critical rubric criterion enforcement and determine_revision_route priority."""
    from engine.quality.evaluate import determine_revision_route

    doc_id = "11111111-1111-4111-8111-111111111101"
    now = datetime.now(UTC)
    draft = ContentDraft(
        title="Guia completo de vendas",  # missing primary_keyword -> non-critical major finding
        meta_description="Meta curta",  # non-critical major finding
        slug="guia-completo-de-vendas",
        body_md=(
            "# Guia completo de vendas\n\n"
            "Gestao de estoque otica ajuda lojas independentes a controlar giro.\n\n"
            "## Indicadores\n\nDados mostram crescimento consistente."
        ),
        faq=[{"question": "Como comecar?", "answer": "Organize os dados semanais."}],
        cta={"headline": "Guia", "body": "Leia o guia.", "button_text": "Ler mais"},
        claims=[DraftClaim(text="Crescimento de 6,4%.", kind="fact", source_id=doc_id)],
    )
    evidence = [
        {
            "id": doc_id,
            "title": "IBGE",
            "url": "https://ibge.gov.br/pmc",
            "published_at": now - timedelta(days=2),
            "date_status": "verified",
            "fetch_status": "ok",
            "facts": ["Crescimento de 6,4%."],
        }
    ]

    # LLM wrongly says PASS while reporting critical rubric criterion -> upgraded to REVISION
    contradictory_llm = _RecordingEvalLLM(
        reports=[
            {
                "verdict": "PASS",
                "score": 85.0,
                "findings": [
                    {
                        "criterion": "factual_accuracy",
                        "severity": "major",
                        "detail": "Metric 6.4% distorted in paragraph 2",
                        "responsible_module": "write",
                    }
                ],
            }
        ]
    )
    report = quality.evaluate(
        draft,
        brief={"primary_keyword": "gestao de estoque otica"},
        evidence=evidence,
        locale="pt-BR",
        funnel_stage="tofu",
        llm_adapter=contradictory_llm,
    )
    assert report.verdict == "REVISION"
    assert report.score is not None and report.score < 70.0
    criteria_seen = {f.criterion for f in report.findings}
    assert "factual_accuracy" in criteria_seen
    # Non-critical deterministic major findings are preserved
    assert "meta_length" in criteria_seen

    assert determine_revision_route(QualityReport(verdict="PASS", score=90.0)) == "pass"
    assert determine_revision_route(QualityReport(verdict="REJECT", score=20.0)) == "research"
    assert determine_revision_route(QualityReport(verdict="REVISION", findings=[])) == "write"
    assert (
        determine_revision_route(
            QualityReport(
                verdict="REVISION",
                findings=[
                    Finding(
                        criterion="clarity",
                        severity="minor",
                        detail="Tighten intro",
                        responsible_module="write",
                    ),
                    Finding(
                        criterion="angle",
                        severity="critical",
                        detail="Brief angle misses MOFU audience",
                        responsible_module="brief",
                    ),
                ],
            )
        )
        == "brief"
    )
    assert (
        determine_revision_route(
            QualityReport(
                verdict="REVISION",
                findings=[
                    Finding(
                        criterion="angle",
                        severity="critical",
                        detail="Brief broken",
                        responsible_module="brief",
                    ),
                    Finding(
                        criterion="factual_accuracy",
                        severity="critical",
                        detail="Source stats contradict story premise",
                        responsible_module="research",
                    ),
                ],
            )
        )
        == "research"
    )


def test_evaluate_persists_quality_report_usage_ledger_and_det_short_circuit(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify DB quality_reports (1:1), drafts.status transitions, and usage_ledger stage."""
    ctx = _seed_draft_bundle(db_conn, locale="pt-BR", market="BR", funnel_stage="mofu")
    ws_id = ctx["workspace_id"]
    user_id = ctx["user_id"]
    draft_id = uuid.UUID(str(ctx["draft_row"]["id"]))

    outcome = services.evaluate_draft(
        db_conn,
        workspace_id=ws_id,
        draft_id=draft_id,
        user_id=user_id,
        offline=True,
    )
    assert outcome["route"] == "pass"
    assert outcome["report"].verdict == "PASS"
    assert outcome["draft"]["status"] == "pass"

    stored_qr = db.get_quality_report(db_conn, workspace_id=ws_id, draft_id=draft_id)
    assert stored_qr is not None
    assert stored_qr["verdict"] == "pass"
    assert float(stored_qr["score"]) >= 70.0
    assert str(stored_qr["prompt_version"]).startswith("evaluate.md:")
    assert "language_match" in stored_qr["checks"]

    usage_rows = db.list_usage(db_conn, workspace_id=ws_id)
    eval_usage = [u for u in usage_rows if u["stage"] == "evaluate"]
    assert len(eval_usage) == 1
    assert eval_usage[0]["prompt_version"] == stored_qr["prompt_version"]
    assert eval_usage[0]["run_id"] == ctx["run_id"]
    payload_rec = db.get_llm_payload(
        db_conn, workspace_id=ws_id, usage_id=uuid.UUID(str(eval_usage[0]["id"]))
    )
    assert payload_rec is not None
    assert "Blog Article Writing Guide" not in json.dumps(payload_rec["request"])

    evts = db.list_events(db_conn, workspace_id=ws_id)
    assert [e for e in evts if e["type"] == "draft.evaluated"]
    assert len([e for e in evts if e["type"] == "draft.awaiting_approval"]) == 1

    # Deterministic failure short-circuit: 0 evaluate LLM usage rows added
    ctx_det = _seed_draft_bundle(db_conn, locale="pt-BR", market="BR", funnel_stage="tofu")
    det_ws = ctx_det["workspace_id"]
    bad_payload = dict(ctx_det["draft_row"]["payload"])
    bad_payload["slug"] = "INVALID SLUG"
    bad_draft = db.create_next_draft_revision(
        db_conn,
        workspace_id=det_ws,
        brief_id=uuid.UUID(str(ctx_det["brief_row"]["id"])),
        prompt_version="write_blog.md:test",
        schema_version=1,
        payload=bad_payload,
        claims=db.list_draft_claims(
            db_conn, workspace_id=det_ws, draft_id=uuid.UUID(str(ctx_det["draft_row"]["id"]))
        ),
    )
    det_outcome = services.evaluate_draft(
        db_conn,
        workspace_id=det_ws,
        draft_id=uuid.UUID(str(bad_draft["id"])),
        user_id=ctx_det["user_id"],
        offline=True,
    )
    assert det_outcome["route"] == "write"
    assert det_outcome["draft"]["status"] == "revision"
    det_usage = [u for u in db.list_usage(db_conn, workspace_id=det_ws) if u["stage"] == "evaluate"]
    assert len(det_usage) == 0


def _scripted_evaluator(sequence: list[QualityReport]) -> Any:
    it: Iterator[QualityReport] = iter(sequence)

    def _eval(**_kwargs: Any) -> QualityReport:
        return next(it)

    return _eval


def test_evaluate_normalization_pack_fallbacks_and_edge_cases() -> None:
    """Verify pack fallback, evidence=None, injected evaluator, and route/verdict edge cases."""
    from engine.quality.evaluate import (
        _normalize_quality_report,
        determine_revision_route,
        load_rubric,
    )

    doc_id = uuid.UUID("11111111-1111-4111-8111-111111111101")
    minimal_draft = ContentDraft(
        title="Gestao de estoque otica na pratica",
        meta_description=(
            "Aprenda gestao de estoque otica com rotina clara de indicadores e compras "
            "para manter armacoes saudaveis sem capital parado na loja."
        ),
        slug="gestao-de-estoque-otica-pratica",
        body_md=(
            "# Gestao de estoque otica na pratica\n\n"
            "Aplicar gestao de estoque otica organiza compras semanais.\n\n"
            "## Giro e cobertura\n\nAcompanhar o giro mensal evita excessos."
        ),
        faq=[{"question": "O que acompanhar?", "answer": "Giro mensal de categorias."}],
        cta={"headline": "Checklist", "body": "Baixe o checklist.", "button_text": "Baixar"},
        claims=[],
    )
    # Injected evaluator + industry pack + brief=None / evidence=None coverage
    inj_report = quality.evaluate(
        minimal_draft,
        brief={"format": "blog", "primary_keyword": "gestao de estoque otica"},
        evidence=None,
        industry_pack="general",
        options=quality.checks.DeterministicRunOptions(min_words=10, max_words=2500),
        llm_evaluator=lambda **_kw: QualityReport(verdict="PASS", score=84.0, findings=[]),
    )
    assert inj_report.verdict == "PASS"

    no_brief_report = quality.evaluate(
        minimal_draft,
        brief=None,
        evidence=None,
        llm_adapter=_RecordingEvalLLM(),
    )
    assert no_brief_report.verdict == "PASS"

    fake_reject = quality.evaluate(
        minimal_draft,
        brief={"format": "blog", "primary_keyword": "gestao de estoque otica"},
        evidence=None,
        options=quality.checks.DeterministicRunOptions(min_words=10, max_words=2500),
        llm_adapter=FakeLLM(),
    )
    assert fake_reject.verdict == "REJECT"

    fallback_report = quality.evaluate(
        minimal_draft,
        brief={"format": "unknown-format", "primary_keyword": "gestao de estoque otica"},
        evidence=[{"id": doc_id, "title": "Guia", "url": "https://ex.com", "facts": []}],
        industry_pack="nonexistent-industry",
        rubric=[{"criterion": "factual_accuracy", "weight": 100, "critical": True}],
        model="custom/eval-model",
        llm_adapter=_RecordingEvalLLM(),
    )
    assert fallback_report.verdict == "PASS"

    rubric = load_rubric(format_slug="blog")
    det_short = quality.checks.run(
        minimal_draft.model_copy(update={"meta_description": "Curta"}),
        brief={"primary_keyword": "gestao de estoque otica"},
        evidence_documents=[],
        locale="pt-BR",
    )
    dedup_report = _normalize_quality_report(
        QualityReport(
            verdict="REVISION",
            score=65.0,
            findings=[
                Finding(
                    criterion="meta_length",
                    severity="major",
                    detail="Duplicate meta criterion from LLM",
                    responsible_module="write",
                )
            ],
        ),
        rubric=rubric,
        det_report=det_short,
    )
    assert len([f for f in dedup_report.findings if f.criterion == "meta_length"]) == 1
    det_ok = quality.checks.run(
        minimal_draft,
        brief={"primary_keyword": "gestao de estoque otica"},
        evidence_documents=[],
        locale="pt-BR",
    )
    low_score_pass = _normalize_quality_report(
        QualityReport(
            verdict="PASS",
            score=62.0,
            findings=[
                Finding(
                    criterion="factual_accuracy",
                    severity="critical",
                    detail="Unsupported premise",
                    responsible_module="research",
                )
            ],
        ),
        rubric=rubric,
        det_report=det_ok,
    )
    assert low_score_pass.verdict == "REJECT"

    unknown_empty = _normalize_quality_report(
        QualityReport(verdict="WEIRD", score=None, findings=[]),
        rubric=rubric,
        det_report=det_ok,
    )
    assert unknown_empty.verdict == "REVISION"
    assert unknown_empty.findings[0].criterion == "overall_quality"

    assert (
        determine_revision_route(
            QualityReport(
                verdict="REVISION",
                findings=[
                    Finding(
                        criterion="style",
                        severity="critical",
                        detail="Bad formatting",
                        responsible_module="unknown_mod",
                    )
                ],
            )
        )
        == "write"
    )


def test_routing_brief_creates_new_version_and_draft(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F14 AC3 & F12 AC4: brief finding creates brief v+1 preserving v and rev+1 draft."""
    ctx_brief = _seed_draft_bundle(db_conn, locale="pt-BR", market="BR")
    ws_b = ctx_brief["workspace_id"]
    uid_b = ctx_brief["user_id"]
    opp_b = uuid.UUID(str(ctx_brief["opportunity"]["id"]))
    brief_v1_id = uuid.UUID(str(ctx_brief["brief_row"]["id"]))
    draft_0_id = uuid.UUID(str(ctx_brief["draft_row"]["id"]))

    res_brief = services.evaluate_and_route_draft(
        db_conn,
        workspace_id=ws_b,
        draft_id=draft_0_id,
        user_id=uid_b,
        offline=True,
        llm_evaluator=_scripted_evaluator(
            [
                QualityReport(
                    verdict="REVISION",
                    score=58.0,
                    findings=[
                        Finding(
                            criterion="audience_relevance",
                            severity="critical",
                            detail="Refocar angulo no custo de capital parado em armacoes",
                            responsible_module="brief",
                        )
                    ],
                ),
                QualityReport(verdict="PASS", score=89.0, findings=[]),
            ]
        ),
        keyword_provider=FakeKeywordProvider(),
        news_search_adapter=FakeNewsSearch(),
        extractor_adapter=FakeExtractor(),
        llm_adapter=FakeLLM(),
    )
    assert res_brief["verdict"] == "PASS"
    assert res_brief["status"] == "pass"
    briefs = db.list_briefs(db_conn, workspace_id=ws_b, opportunity_id=opp_b)
    assert sorted(int(b["version"]) for b in briefs) == [1, 2]
    v1_brief = db.get_brief(db_conn, workspace_id=ws_b, brief_id=brief_v1_id)
    assert v1_brief is not None and v1_brief["version"] == 1
    assert "custo de capital parado" in briefs[0]["payload"]["angle"]
    final_d_b = db.get_draft(db_conn, workspace_id=ws_b, draft_id=res_brief["final_draft_id"])
    assert final_d_b is not None
    assert final_d_b["revision"] == 1
    assert final_d_b["brief_id"] == briefs[0]["id"]
    assert final_d_b["status"] == "pass"

    # Exercise list_quality_reports with and without brief_id filter + get None
    assert db.get_quality_report(db_conn, workspace_id=ws_b, draft_id=uuid.uuid4()) is None
    by_brief = db.list_quality_reports(db_conn, workspace_id=ws_b, brief_id=briefs[0]["id"])
    all_qr = db.list_quality_reports(db_conn, workspace_id=ws_b)
    assert len(by_brief) == 1
    assert len(all_qr) == 2


def test_routing_write_caps_at_revision_2_and_surfaces_to_owner(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F14 AC3-AC4 & F13 AC3: write loop stops at revision 2 and surfaces as-is."""
    ctx_cap = _seed_draft_bundle(db_conn, locale="en-US", market="US")
    ws_c = ctx_cap["workspace_id"]
    uid_c = ctx_cap["user_id"]
    brief_c_id = uuid.UUID(str(ctx_cap["brief_row"]["id"]))
    draft_c0_id = uuid.UUID(str(ctx_cap["draft_row"]["id"]))
    fail_write = QualityReport(
        verdict="REVISION",
        score=61.0,
        findings=[
            Finding(
                criterion="clarity_and_structure",
                severity="major",
                detail="Strengthen parent transition examples in section 2",
                responsible_module="write",
            )
        ],
    )
    res_cap = services.evaluate_and_route_draft(
        workspace_id=ws_c,
        draft_id=draft_c0_id,
        user_id=uid_c,
        offline=True,
        llm_evaluator=_scripted_evaluator([fail_write, fail_write, fail_write]),
        llm_adapter=FakeLLM(),
    )
    assert res_cap["surfaced_to_owner"] is True
    assert res_cap["verdict"] == "REVISION"
    all_drafts_c = db.list_drafts(db_conn, workspace_id=ws_c, brief_id=brief_c_id)
    assert [int(d["revision"]) for d in all_drafts_c] == [2, 1, 0]
    rev2_row = all_drafts_c[0]
    assert rev2_row["status"] == "pass"
    rev2_qr = db.get_quality_report(
        db_conn, workspace_id=ws_c, draft_id=uuid.UUID(str(rev2_row["id"]))
    )
    assert rev2_qr is not None and rev2_qr["verdict"] == "revision"
    assert rev2_qr["findings"][0]["detail"] == "Strengthen parent transition examples in section 2"
    # Revision 2 row must explicitly be blocked from moving to status 'revision'
    assert (
        db.transition_draft_status(
            db_conn,
            workspace_id=ws_c,
            draft_id=uuid.UUID(str(rev2_row["id"])),
            expected_statuses=("pass",),
            new_status="revision",
        )
        is None
    )
    awaiting = [
        e
        for e in db.list_events(db_conn, workspace_id=ws_c)
        if e["type"] == "draft.awaiting_approval"
    ]
    assert len(awaiting) == 1
    assert awaiting[0]["payload"]["surfaced_after_max_revisions"] is True


def test_routing_research_reject_returns_opportunity_to_pool_and_errors(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F14 AC3 research REJECT frees quota + managed conn + idempotent approval event."""
    from engine.quality.evaluate import (
        _emit_draft_awaiting_approval_if_needed,
        _load_draft_evidence_docs,
    )

    ctx_rej = _seed_draft_bundle(db_conn, locale="pt-BR", market="BR")
    ws_r = ctx_rej["workspace_id"]
    uid_r = ctx_rej["user_id"]
    opp_r_id = uuid.UUID(str(ctx_rej["opportunity"]["id"]))
    # Enforce articles_per_week = 1 and seed a second scored opportunity in the same run
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE campaigns SET articles_per_week = 1 WHERE workspace_id = %s AND id = %s",
            (ws_r, ctx_rej["campaign_id"]),
        )
    db_conn.commit()
    rr_alt = db.upsert_research_record(
        db_conn,
        workspace_id=ws_r,
        run_id=ctx_rej["run_id"],
        document_id=ctx_rej["doc_secondary"]["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Alternativa 2", "summary": "Nova pauta"},
    )
    opp_alt = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_r,
        run_id=ctx_rej["run_id"],
        record_id=rr_alt["id"],
        scores={"novelty": {"score": 7.5, "evidence": "OK"}},
        total=Decimal("78.00"),
        funnel_stage="tofu",
        confidence="high",
        angle="Segunda oportunidade elegivel",
    )
    with pytest.raises(ValueError, match="Approval limit reached"):
        services.decide_topic(
            db_conn,
            workspace_id=ws_r,
            opportunity_id=opp_alt["id"],
            decision="approved",
            user_id=uid_r,
        )

    res_rej = services.evaluate_and_route_draft(
        db_conn,
        workspace_id=ws_r,
        draft_id=uuid.UUID(str(ctx_rej["draft_row"]["id"])),
        user_id=uid_r,
        offline=True,
        llm_evaluator=_scripted_evaluator(
            [
                QualityReport(
                    verdict="REVISION",
                    score=25.0,
                    findings=[
                        Finding(
                            criterion="factual_accuracy",
                            severity="critical",
                            detail="Fonte nao sustenta a tese central do artigo",
                            responsible_module="research",
                        )
                    ],
                )
            ]
        ),
    )
    assert res_rej["verdict"] == "REJECT"
    assert res_rej["status"] == "reject"
    assert res_rej["topic_returned_to_pool"] is True
    opp_after = db.get_opportunity(db_conn, workspace_id=ws_r, opportunity_id=opp_r_id)
    assert opp_after is not None and opp_after["status"] == "scored"
    assert opp_after["decided_by"] is None and opp_after["decided_at"] is None
    assert (
        db.count_approved_opportunities(db_conn, workspace_id=ws_r, run_id=ctx_rej["run_id"]) == 0
    )
    # Re-returning an already-'scored' opportunity is a safe no-op returning None
    assert (
        db.return_opportunity_to_pool(
            db_conn, workspace_id=ws_r, opportunity_id=opp_r_id, note="noop"
        )
        is None
    )
    approved_alt = services.decide_topic(
        db_conn,
        workspace_id=ws_r,
        opportunity_id=opp_alt["id"],
        decision="approved",
        user_id=uid_r,
    )
    assert approved_alt["status"] == "approved"

    # Guard errors: missing draft + wrong status, plus conn=None and idempotent awaiting event
    with pytest.raises(ValueError, match="not found in workspace"):
        services.evaluate_draft(workspace_id=ws_r, draft_id=uuid.uuid4(), user_id=uid_r)

    with pytest.raises(ValueError, match="expected 'draft'"):
        services.evaluate_draft(
            db_conn,
            workspace_id=ws_r,
            draft_id=uuid.UUID(str(ctx_rej["draft_row"]["id"])),
            user_id=uid_r,
        )

    fresh_ctx = _seed_draft_bundle(db_conn, locale="pt-BR", market="BR")
    pass_out = services.evaluate_draft(
        workspace_id=fresh_ctx["workspace_id"],
        draft_id=uuid.UUID(str(fresh_ctx["draft_row"]["id"])),
        user_id=fresh_ctx["user_id"],
        offline=True,
    )
    assert pass_out["draft"]["status"] == "pass"
    _emit_draft_awaiting_approval_if_needed(
        db_conn,
        ctx=pass_out["context"],
        report=pass_out["report"],
        surfaced_after_max=False,
        actor_user_id=fresh_ctx["user_id"],
    )
    evts = db.list_events(
        db_conn,
        workspace_id=fresh_ctx["workspace_id"],
        event_type="draft.awaiting_approval",
        entity_id=uuid.UUID(str(fresh_ctx["draft_row"]["id"])),
    )
    assert len(evts) == 1
    found_docs = _load_draft_evidence_docs(
        db_conn,
        workspace_id=fresh_ctx["workspace_id"],
        ctx=pass_out["context"],
        brief_model=EditorialBrief(
            topic="Missing doc",
            target_audience="Owners",
            primary_keyword="missing doc",
            angle="Missing",
            outline=["Intro"],
            required_facts=[RequiredFact(fact="Unlinked fact", source_id=str(uuid.uuid4()))],
            cta_goal="CTA",
        ),
        claims=[],
    )
    assert len(found_docs) == 1


def test_evaluate_deterministic_only_skips_llm_rubric() -> None:
    """Verify M5-07: deterministic_only=True runs deterministic checks without calling LLM."""
    from unittest.mock import MagicMock

    exploding_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))

    def _exploding_evaluator(**_kwargs: Any) -> QualityReport:
        raise AssertionError("llm_evaluator must not be called when deterministic_only=True")

    draft = ContentDraft(
        title="Gestao de estoque em oticas",
        slug="gestao-de-estoque-em-oticas",
        meta_description=(
            "Guia pratico de gestao de estoque para melhorar o giro e a margem na sua otica "
            "com indicadores simples e acompanhamento semanal."
        ),
        body_md="# Gestao de estoque em oticas\n\n" + " ".join(["otica"] * 450),
        claims=[],
        cta={"headline": "Fale conosco", "button_text": "Saiba mais"},
    )
    brief = EditorialBrief(
        topic="Gestao de estoque em oticas",
        target_audience="Gestores de otica",
        primary_keyword="gestao de estoque",
        angle="Controle semanal",
        outline=["Introducao"],
        required_facts=[],
        cta_goal="Agendar diagnostico",
        format="blog-short",
    )

    report = quality.evaluate(
        draft,
        brief,
        deterministic_only=True,
        llm_adapter=exploding_llm,
        llm_evaluator=_exploding_evaluator,
    )
    assert report.verdict == "PASS"
    assert report.score is None
    assert report.findings == []

    # Under-length draft fails deterministic word_count check for blog-short (min 400)
    short_draft = draft.model_copy(update={"body_md": " ".join(["otica"] * 120)})
    fail_report = quality.evaluate(
        short_draft,
        brief,
        deterministic_only=True,
        llm_adapter=exploding_llm,
    )
    assert fail_report.verdict == "REVISION"
    assert any(f.criterion == "word_count" for f in fail_report.findings)
