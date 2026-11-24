"""M10-04: Brief funnel_stage persistence/eval precedence & fetch_status='ok' gating."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import yaml

from engine import db, llm, services
from engine.contracts import CampaignConfig, ContentDraft, EditorialBrief, RequiredFact
from engine.integrations.fakes import FakeExtractor, FakeKeywordProvider, FakeLLM, FakeNewsSearch
from engine.production import writer
from engine.quality.checks import DeterministicRunOptions
from engine.quality.evaluate import EvalExecutionSpec, evaluate_persisted_draft
from engine.strategy import score

brief_mod = importlib.import_module("engine.strategy.brief")


def _seed_workspace_and_opportunity(
    conn: psycopg.Connection[Any],
    *,
    funnel_stage: str = "tofu",
    cta_limits: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:8]
    user = db.create_or_get_user(conn, email=f"m10-04-{tag}@example.com", name="Editor Owner")
    user_id = uuid.UUID(str(user["user_id"]))
    ws_id = db.create_workspace(
        conn,
        slug=f"ws-m10-04-{tag}",
        name=f"M10-04 Workspace {tag}",
        industry_pack="general",
    )
    db.create_membership(conn, workspace_id=ws_id, user_id=user_id, role="owner")

    brand_payload = yaml.safe_load(Path("fixtures/seed_example.yaml").read_text(encoding="utf-8"))
    brand_row = db.save_brand_profile(
        conn,
        workspace_id=ws_id,
        schema_version=1,
        payload=brand_payload,
        created_by=user_id,
    )

    cfg = CampaignConfig(
        cta_intensity_limits=cta_limits or {"TOFU": "low", "MOFU": "medium", "BOFU": "high"}
    )
    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name=f"Seasonal Retail Campaign {tag}",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("10.00"),
        config=cfg.model_dump(mode="json"),
        config_schema_version=1,
        created_by=user_id,
    )
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_id,
    )
    doc = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=None,
        url=f"https://evidence-{tag}.example.com/retail-year-end",
        canonical_url=f"https://evidence-{tag}.example.com/retail-year-end",
        title="Year-End Retail Operations Benchmark 2026",
        published_at=now - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
        extracted_text="Standardized inventory tracking reduced stockouts by 15% in Q4 2026.",
        meta={
            "source_name": "Retail Benchmark",
            "source_role": "data",
            "summary": "Inventory tracking reduced Q4 stockouts by 15%.",
            "facts": ["Standardized inventory tracking reduced stockouts by 15% in Q4 2026."],
        },
    )
    rr = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": "Year-End Retail Operations Benchmark 2026",
            "summary": "Inventory tracking reduced Q4 stockouts by 15%.",
            "facts": ["Standardized inventory tracking reduced stockouts by 15% in Q4 2026."],
            "entities": ["gestao de estoque", "vendas de fim de ano"],
        },
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores={"novelty": {"score": 8.0, "evidence": "Benchmark", "is_estimate": False}},
        total=Decimal("82.00"),
        funnel_stage=funnel_stage,
        confidence="high",
        angle="Structured year-end operations workflow",
        rank=1,
        status="approved",
        decided_by=user_id,
        decided_at=now,
    )
    return {
        "workspace_id": ws_id,
        "user_id": user_id,
        "campaign_id": camp_id,
        "run_id": run_id,
        "brand_row": brand_row,
        "document": doc,
        "research_record": rr,
        "opportunity": opp,
    }


def test_editorial_brief_funnel_stage_validator() -> None:
    base = {
        "topic": "Year-end operations",
        "target_audience": "Store managers",
        "primary_keyword": "year end retail",
        "angle": "Practical workflow",
        "cta_goal": "Download checklist",
    }
    assert EditorialBrief(**base).funnel_stage is None
    assert EditorialBrief(**base, funnel_stage="MOFU").funnel_stage == "mofu"
    assert EditorialBrief(**base, funnel_stage=" tofu ").funnel_stage == "tofu"
    assert EditorialBrief(**base, funnel_stage="BoFu").funnel_stage == "bofu"
    for invalid in (None, "", "   ", "invalid", 123):
        assert EditorialBrief(**base, funnel_stage=invalid).funnel_stage is None


def test_strategy_brief_persists_resolved_funnel_stage(db_conn: psycopg.Connection[Any]) -> None:
    ctx = _seed_workspace_and_opportunity(db_conn, funnel_stage="tofu")
    row = brief_mod.brief(
        ctx["opportunity"]["id"],
        conn=db_conn,
        workspace_id=ctx["workspace_id"],
        actor_user_id=ctx["user_id"],
        offline=True,
        funnel_stage="mofu",
        llm_adapter=FakeLLM(),
        keyword_provider=FakeKeywordProvider(),
        news_search_adapter=FakeNewsSearch(),
        extractor_adapter=FakeExtractor(),
    )
    assert row["payload"]["funnel_stage"] == "mofu"
    stored = db.get_brief(db_conn, workspace_id=ctx["workspace_id"], brief_id=row["id"])
    assert stored is not None
    assert stored["payload"]["funnel_stage"] == "mofu"


def test_get_or_create_mcp_brief_idempotent_on_funnel_stage_override(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    ctx = _seed_workspace_and_opportunity(db_conn, funnel_stage="tofu")
    ws_id = ctx["workspace_id"]
    opp_id = ctx["opportunity"]["id"]
    user_id = ctx["user_id"]

    res1 = services.get_or_create_mcp_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        offline=True,
        funnel_stage="mofu",
    )
    assert res1["created"] is True
    assert res1["funnel_stage"] == "mofu"
    assert res1["payload"]["funnel_stage"] == "mofu"

    res2 = services.get_or_create_mcp_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        offline=True,
        funnel_stage="mofu",
    )
    assert res2["created"] is False
    assert res2["id"] == res1["id"]
    assert res2["funnel_stage"] == "mofu"

    res3 = services.get_or_create_mcp_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        offline=True,
        funnel_stage="bofu",
    )
    assert res3["created"] is True
    assert res3["id"] != res1["id"]
    assert res3["funnel_stage"] == "bofu"
    assert res3["payload"]["funnel_stage"] == "bofu"

    res4 = services.get_or_create_mcp_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        offline=True,
        funnel_stage=None,
    )
    assert res4["created"] is False
    assert res4["id"] == res3["id"]
    assert res4["funnel_stage"] == "bofu"


def test_quality_evaluate_honors_brief_funnel_stage_and_legacy_fallback(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    ctx = _seed_workspace_and_opportunity(
        db_conn,
        funnel_stage="tofu",
        cta_limits={"TOFU": "low", "MOFU": "medium", "BOFU": "high"},
    )
    ws_id = ctx["workspace_id"]
    doc_id = str(ctx["document"]["id"])

    mofu_brief_row = brief_mod.brief(
        ctx["opportunity"]["id"],
        conn=db_conn,
        workspace_id=ws_id,
        actor_user_id=ctx["user_id"],
        offline=True,
        funnel_stage="mofu",
        llm_adapter=FakeLLM(),
        keyword_provider=FakeKeywordProvider(),
        news_search_adapter=FakeNewsSearch(),
        extractor_adapter=FakeExtractor(),
    )
    brief_model = EditorialBrief.model_validate(mofu_brief_row["payload"])
    kw = brief_model.primary_keyword

    draft_payload = ContentDraft(
        title=f"Guia prático de {kw}",
        meta_description=f"Aprenda a aplicar {kw} com checklist operacional para o time.",
        slug="guia-pratico-estoque",
        body_md=(
            f"# Guia prático de {kw}\n\n"
            f"Este guia apresenta como estruturar {kw} na operação da loja.\n\n"
            "## Indicadores e execução\n\n"
            "Em benchmarks recentes, o controle padronizado reduziu rupturas "
            "em 15% no último trimestre."
        ),
        faq=[
            {
                "question": "Por que revisar o estoque antes do pico?",
                "answer": "Para evitar faltas.",
            }
        ],
        cta={
            "headline": "Solicite uma demonstração guiada",
            "body": "Veja como estruturar sua operação no dia a dia.",
            "button_text": "Ver demonstração",
            "url": "https://example.com/demo",
            "intensity": "medium",
        },
        claims=[
            {
                "text": "Controle padronizado reduziu rupturas em 15%.",
                "kind": "fact",
                "source_id": doc_id,
            }
        ],
    )

    mofu_draft = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws_id,
        brief_id=mofu_brief_row["id"],
        prompt_version="test:v1",
        schema_version=1,
        payload=draft_payload.model_dump(mode="json"),
        claims=[
            {
                "claim_text": "Controle padronizado reduziu rupturas em 15%.",
                "kind": "fact",
                "document_id": uuid.UUID(doc_id),
                "verified": True,
            }
        ],
    )
    eval_mofu = evaluate_persisted_draft(
        db_conn,
        EvalExecutionSpec(
            workspace_id=ws_id,
            draft_id=mofu_draft["id"],
            deterministic_only=True,
            options=DeterministicRunOptions(min_words=10, max_words=2500),
        ),
    )["report"]
    cta_findings_mofu = [f for f in eval_mofu.findings if f.criterion == "cta_intensity"]
    assert cta_findings_mofu == []

    # Legacy brief payload without funnel_stage falls back to opportunities.funnel_stage ('tofu')
    legacy_payload = dict(mofu_brief_row["payload"])
    legacy_payload.pop("funnel_stage", None)
    legacy_brief_row = db.create_next_brief_version(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=ctx["opportunity"]["id"],
        brand_profile_id=uuid.UUID(str(ctx["brand_row"]["id"])),
        prompt_version="legacy:v1",
        keywords=mofu_brief_row["keywords"],
        schema_version=1,
        payload=legacy_payload,
    )
    legacy_draft = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws_id,
        brief_id=legacy_brief_row["id"],
        prompt_version="test:v1",
        schema_version=1,
        payload=draft_payload.model_dump(mode="json"),
        claims=[],
    )
    eval_tofu = evaluate_persisted_draft(
        db_conn,
        EvalExecutionSpec(
            workspace_id=ws_id,
            draft_id=legacy_draft["id"],
            deterministic_only=True,
            options=DeterministicRunOptions(min_words=10, max_words=2500),
        ),
    )["report"]
    cta_findings_tofu = [f for f in eval_tofu.findings if f.criterion == "cta_intensity"]
    assert len(cta_findings_tofu) == 1
    assert "stage 'tofu'" in cta_findings_tofu[0].detail


def test_ensure_external_brief_persists_mofu_funnel_stage(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    ctx = _seed_workspace_and_opportunity(db_conn, funnel_stage="tofu")
    res = services.import_external_draft(
        db_conn,
        workspace_id=ctx["workspace_id"],
        user_id=ctx["user_id"],
        topic="Guia externo de operação",
        markdown_text=(
            "# Guia externo de operação\n\nConteúdo prático de meio de funil para gestores."
        ),
        primary_keyword="guia externo",
        offline=True,
        deterministic_only=True,
    )
    brief_row = db.get_brief(
        db_conn, workspace_id=ctx["workspace_id"], brief_id=uuid.UUID(str(res["brief_id"]))
    )
    assert brief_row is not None
    assert brief_row["payload"]["funnel_stage"] == "mofu"


def test_score_run_excludes_failed_records_and_non_ok_documents(
    db_conn: psycopg.Connection[Any],
) -> None:
    ctx = _seed_workspace_and_opportunity(db_conn, funnel_stage="mofu")
    ws_id = ctx["workspace_id"]
    now = datetime.now(UTC)

    # Create a fresh run to isolate exact records scored
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=ctx["campaign_id"],
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=ctx["user_id"],
        status="completed",
    )

    def _add_doc_record(tag: str, *, fetch_status: str, rec_status: str) -> dict[str, Any]:
        doc = db.upsert_document(
            db_conn,
            workspace_id=ws_id,
            url=f"https://score-filter-{tag}.example.com/item",
            canonical_url=f"https://score-filter-{tag}.example.com/item",
            title=f"Item {tag}",
            published_at=now - timedelta(days=1),
            date_status="verified",
            fetch_status=fetch_status,
            extracted_text=f"Body for {tag}" if fetch_status == "ok" else None,
        )
        return db.upsert_research_record(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            document_id=doc["id"],
            status=rec_status,
            schema_version=1,
            payload={
                "headline": f"Headline {tag}",
                "summary": f"Summary {tag}",
                "facts": [f"Fact {tag}"],
                "verification_status": "verified",
            },
        )

    _add_doc_record("blocked-failed", fetch_status="blocked", rec_status="failed")
    _add_doc_record("blocked-extracted", fetch_status="blocked", rec_status="extracted")
    _add_doc_record("ok-failed", fetch_status="ok", rec_status="failed")
    rec_d = _add_doc_record("ok-extracted", fetch_status="ok", rec_status="extracted")

    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    opps = score.score_run(db_conn, run_row, llm_adapter=FakeLLM())
    assert len(opps) == 1
    assert opps[0]["record_id"] == rec_d["id"]


def test_brief_and_writer_filter_non_ok_evidence_documents() -> None:
    ws_id = uuid.uuid4()
    primary_id = uuid.uuid4()
    base_ctx = {
        "workspace_id": str(ws_id),
        "primary_document_id": str(primary_id),
        "document_title": "Primary Doc",
        "document_url": "https://news.example.com/blocked",
        "document_canonical_url": "https://news.example.com/blocked",
        "record_payload": {"headline": "Primary Doc", "summary": "Short summary"},
    }
    with (
        patch.object(brief_mod, "_sync_evidence_sources"),
        patch.object(brief_mod.db, "list_evidence_documents", return_value=[]),
    ):
        blocked_docs = brief_mod._gather_evidence_documents(
            MagicMock(),
            {**base_ctx, "document_fetch_status": "blocked"},
            offline=True,
            adapters=brief_mod.BriefAdapters(),
        )
        assert blocked_docs == []

        ok_docs = brief_mod._gather_evidence_documents(
            MagicMock(),
            {**base_ctx, "document_fetch_status": "ok"},
            offline=True,
            adapters=brief_mod.BriefAdapters(),
        )
        assert len(ok_docs) == 1
        assert ok_docs[0]["id"] == str(primary_id)

        legacy_docs = brief_mod._gather_evidence_documents(
            MagicMock(),
            base_ctx,
            offline=True,
            adapters=brief_mod.BriefAdapters(),
        )
        assert len(legacy_docs) == 1

    # Writer defense-in-depth: skip non-ok documents referenced in brief
    bad_id = uuid.uuid4()
    brief_model = EditorialBrief(
        topic="Topic",
        target_audience="Managers",
        primary_keyword="topic",
        angle="Angle",
        required_facts=[RequiredFact(fact="F", source_id=str(bad_id))],
        cta_goal="CTA",
    )
    rows_by_id = {
        bad_id: {"id": bad_id, "title": "Blocked", "fetch_status": "blocked"},
        primary_id: {"id": primary_id, "title": "Healthy", "fetch_status": "ok"},
    }
    with patch.object(
        db,
        "get_document",
        side_effect=lambda _c, *, workspace_id, document_id: rows_by_id.get(document_id),
    ):
        filtered = writer._resolve_brief_evidence_docs(
            MagicMock(),
            ws_id=ws_id,
            brief_ctx={"primary_document_id": str(primary_id)},
            brief_model=brief_model,
        )
    assert [d["id"] for d in filtered] == [str(primary_id)]


def test_score_run_forwards_brand_profile_payload(db_conn: psycopg.Connection[Any]) -> None:
    ctx = _seed_workspace_and_opportunity(db_conn, funnel_stage="mofu")
    ws_id = ctx["workspace_id"]
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=ctx["run_id"])
    assert run_row is not None

    captured_brands: list[dict[str, Any]] = []
    real_call = llm.structured_call

    def _capture_call(**kwargs: Any) -> Any:
        captured_brands.append(dict((kwargs.get("input") or {}).get("brand") or {}))
        return real_call(**kwargs)

    with patch.object(llm, "structured_call", side_effect=_capture_call):
        score.score_run(db_conn, run_row, llm_adapter=FakeLLM())

    assert len(captured_brands) == 1
    assert captured_brands[0]["voice"] == ctx["brand_row"]["payload"]["voice"]
    assert captured_brands[0]["positioning"] == ctx["brand_row"]["payload"]["positioning"]

    # When no active brand profile exists, score_run falls back cleanly to {}
    captured_empty: list[dict[str, Any]] = []

    def _capture_empty(**kwargs: Any) -> Any:
        captured_empty.append(dict((kwargs.get("input") or {}).get("brand") or {}))
        return real_call(**kwargs)

    with (
        patch.object(db, "get_current_brand_profile", return_value=None),
        patch.object(llm, "structured_call", side_effect=_capture_empty),
    ):
        score.score_run(db_conn, run_row, llm_adapter=FakeLLM())

    assert captured_empty == [{}]
