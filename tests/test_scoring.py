"""Tests for Opportunity Scoring step, Hypothesis properties, and F09 requirements."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import db, pipeline, services
from engine.contracts import (
    KeywordData,
    Opportunity,
    ResearchRecord,
    ScoringWeights,
    SubScore,
)
from engine.integrations.fakes import FakeLLM
from engine.strategy import score

# ---------------------------------------------------------------------------
# Hypothesis Property Tests (bounds [0, 100] & monotonicity)
# ---------------------------------------------------------------------------


def _build_opportunity_with_scores(scores: list[float]) -> Opportunity:
    sub = [SubScore(score=s, evidence=f"Evidence for score {s}", is_estimate=False) for s in scores]
    return Opportunity(
        title="Hypothesis Test Opportunity",
        angle="Analytical angle",
        funnel_stage="mofu",
        confidence="high",
        novelty=sub[0],
        audience_fit=sub[1],
        brand_fit=sub[2],
        competitor_gap=sub[3],
        trend=sub[4],
        urgency=sub[5],
    )


@given(scores=st.lists(st.floats(min_value=0.0, max_value=10.0), min_size=6, max_size=6))
def test_hypothesis_weighted_total_bounds(scores: list[float]) -> None:
    """Hypothesis: Weighted total within [0.0, 100.0] for valid sub-scores."""
    opp = _build_opportunity_with_scores(scores)
    total = opp.compute_weighted_total(ScoringWeights())
    assert 0.0 <= total <= 100.0


@given(
    base_scores=st.lists(st.floats(min_value=0.0, max_value=9.0), min_size=6, max_size=6),
    idx=st.integers(min_value=0, max_value=5),
    delta=st.floats(min_value=0.01, max_value=1.0),
)
def test_hypothesis_weighted_total_monotonicity(
    base_scores: list[float], idx: int, delta: float
) -> None:
    """Hypothesis: Increasing any criterion score never decreases the total (monotonicity)."""
    opp_base = _build_opportunity_with_scores(base_scores)
    increased_scores = list(base_scores)
    increased_scores[idx] = min(10.0, increased_scores[idx] + delta)
    opp_increased = _build_opportunity_with_scores(increased_scores)

    weights = ScoringWeights()
    total_base = opp_base.compute_weighted_total(weights)
    total_increased = opp_increased.compute_weighted_total(weights)

    assert total_increased >= total_base


# ---------------------------------------------------------------------------
# SEO Potential & is_estimate Flag (F09 AC2)
# ---------------------------------------------------------------------------


def test_seo_potential_is_estimate_when_keyword_data_absent() -> None:
    """WHEN no keyword data exists yet, THEN SEO potential is flagged is_estimate (F09 AC2)."""
    record = {
        "headline": "Novas Lentes Fotossensíveis",
        "source": "Opticanet",
        "url": "https://opticanet.com.br/lentes-foto",
        "summary": "Fabricante lança nova geração de lentes fotossensíveis com ativação rápida.",
        "facts": ["Ativação em 15 segundos", "Bloqueio 100% UVA/UVB"],
        "entities": ["Lentes", "Proteção UV"],
        "verification_status": "verified",
    }

    # Case 1: No keyword data provided
    opp = score.score_record_with_prompt(record, keyword_data=None, llm_adapter=FakeLLM())
    assert opp.trend.is_estimate is True

    # Case 2: Keyword data with 0 volume (unverified / absent)
    kw_empty = KeywordData(keyword="lentes fotossensiveis", search_volume=0)
    opp2 = score.score_record_with_prompt(record, keyword_data=kw_empty, llm_adapter=FakeLLM())
    assert opp2.trend.is_estimate is True

    # Case 3: Verified keyword data present
    kw_verified = KeywordData(keyword="lentes fotossensiveis", search_volume=2400)
    opp3 = score.score_record_with_prompt(record, keyword_data=kw_verified, llm_adapter=FakeLLM())
    # Subscore retains is_estimate False when provider volume is present
    assert opp3.trend.is_estimate is False


# ---------------------------------------------------------------------------
# Database & Pipeline Integration Tests
# ---------------------------------------------------------------------------


def _setup_workspace_and_records(
    conn: psycopg.Connection[Any],
) -> tuple[uuid.UUID, uuid.UUID, list[dict[str, Any]]]:
    """Helper creating workspace, campaign, run, and research records."""
    ws_id = db.create_workspace(
        conn,
        name="Scoring Test WS",
        slug=f"ws-score-{uuid.uuid4().hex[:8]}",
        industry_pack="general",
    )
    user = db.create_or_get_user(
        conn, email=f"score-user-{uuid.uuid4().hex[:8]}@example.com", name="Scorer"
    )
    weights_config = {
        "scoring_weights": {
            "novelty": 30,
            "audience_fit": 20,
            "brand_fit": 20,
            "competitor_gap": 15,
            "trend": 10,
            "urgency": 5,
        }
    }
    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name="Scoring Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("20.00"),
        config=weights_config,
        config_schema_version=1,
        created_by=user["user_id"],
    )
    now = datetime.now(UTC)
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("20.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user["user_id"],
    )

    # Record 1: Verified date and status
    doc1 = db.upsert_document(
        conn,
        workspace_id=ws_id,
        url=f"https://abioptica.com.br/news-{uuid.uuid4().hex[:6]}",
        canonical_url=f"https://abioptica.com.br/news-{uuid.uuid4().hex[:6]}",
        title="Diretrizes de Miopia 2026",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"h1",
    )
    rr1 = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc1["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": "Diretrizes de Miopia 2026",
            "facts": ["Fato 1"],
            "summary": "Sum 1",
            "verification_status": "verified",
        },
    )

    # Record 2: Another verified record
    doc2 = db.upsert_document(
        conn,
        workspace_id=ws_id,
        url=f"https://opticanet.com.br/tributos-{uuid.uuid4().hex[:6]}",
        canonical_url=f"https://opticanet.com.br/tributos-{uuid.uuid4().hex[:6]}",
        title="Reforma Tributária Óticas",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"h2",
    )
    rr2 = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc2["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": "Reforma Tributária Óticas",
            "facts": ["Fato 2"],
            "summary": "Sum 2",
            "verification_status": "verified",
        },
    )

    # Record 3: Duplicate of rr1 (should NOT be scored)
    doc3 = db.upsert_document(
        conn,
        workspace_id=ws_id,
        url=f"https://fiquedeolho.com.br/dup-{uuid.uuid4().hex[:6]}",
        canonical_url=f"https://fiquedeolho.com.br/dup-{uuid.uuid4().hex[:6]}",
        title="Diretrizes de Miopia Cópia",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"h3",
    )
    rr3 = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc3["id"],
        status="duplicate",
        duplicate_of=rr1["id"],
        schema_version=1,
        payload={"headline": "Diretrizes de Miopia Cópia"},
    )

    return ws_id, run_id, [rr1, rr2, rr3]


def test_score_run_with_fake_llm(db_conn: psycopg.Connection[Any]) -> None:
    """Test full score_run pipeline with FakeLLM and rank assignments (F09 AC1, AC6)."""
    ws_id, run_id, records = _setup_workspace_and_records(db_conn)

    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None

    scored_opps = score.score_run(db_conn, run_row, llm_adapter=FakeLLM())

    # Only the 2 non-duplicate verified records should have been scored
    assert len(scored_opps) == 2

    # Ranks should be 1 and 2
    ranks = [o["rank"] for o in scored_opps]
    assert sorted(ranks) == [1, 2]

    for opp in scored_opps:
        assert opp["workspace_id"] == ws_id
        assert opp["run_id"] == run_id
        assert opp["status"] == "scored"
        assert 0.0 <= float(opp["total"]) <= 100.0
        assert opp["confidence"] in ("low", "medium", "high")
        assert opp["funnel_stage"] in ("tofu", "mofu", "bofu")
        assert opp["scores"]["trend"]["is_estimate"] is True

    # Services wrapper test
    srv_opps = services.score_run(db_conn, workspace_id=ws_id, run_id=run_id, offline=True)
    assert len(srv_opps) == 2


def test_deterministic_prompt_hash_matches_version() -> None:
    """Prompt hash is deterministic and shows up as prompt_version (M2-01 Step 1)."""
    prompt_text, prompt_ver = score.get_score_prompt_and_version()
    assert "score.md:" in prompt_ver
    assert len(prompt_ver) > 10
    assert "Opportunity Scoring Rubric" in prompt_text


def test_score_prompt_is_domain_agnostic() -> None:
    """M8-02 D1: prompts/score.md scores dynamically against industry and brand context."""
    prompt_text, _ = score.get_score_prompt_and_version()
    lowered = prompt_text.lower()
    for forbidden in ("optical", "optometrist", "optician"):
        assert forbidden not in lowered
    assert "Industry & Audience Context" in prompt_text
    assert "Brand Context" in prompt_text


def test_prompt_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """FileNotFoundError is raised when prompt file does not exist."""
    monkeypatch.setattr(score, "PROMPT_FILE", Path("/nonexistent/score.md"))
    with pytest.raises(FileNotFoundError, match="Score prompt file not found"):
        score.get_score_prompt_and_version()


def test_prepare_score_input_with_research_record_model() -> None:
    """_prepare_score_input serializes ResearchRecord Pydantic instance."""
    rr = ResearchRecord(
        headline="Material Inovador",
        source="Revista Óptica",
        url="https://ex.com/artigo",
        summary="Resumo do material",
        facts=["Fato A"],
        entities=["Titânio"],
    )
    payload = score._prepare_score_input(rr)
    assert payload["record"]["headline"] == "Material Inovador"
    assert payload["record"]["facts"] == ["Fato A"]


def test_services_score_run_missing_run(db_conn: psycopg.Connection[Any]) -> None:
    """services.score_run raises ValueError when run does not exist."""
    ws_id = uuid.uuid4()
    run_id = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        services.score_run(db_conn, workspace_id=ws_id, run_id=run_id)


def test_score_run_invalid_campaign_weights_fallback(db_conn: psycopg.Connection[Any]) -> None:
    """Corrupted campaign weights trigger fallback to default weights."""
    ws_id, run_id, _ = _setup_workspace_and_records(db_conn)
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None

    with db_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaigns
            SET config = '{"scoring_weights": {"novelty": 999}}'::jsonb
            WHERE id = %(c_id)s
            """,
            {"c_id": run_row["campaign_id"]},
        )

    opps = score.score_run(db_conn, run_row, llm_adapter=FakeLLM())
    assert len(opps) == 2


def test_pipeline_re_run_score_stage(db_conn: psycopg.Connection[Any]) -> None:
    """re_run_failed_stage_items works with stage='score'."""
    ws_id, run_id, _ = _setup_workspace_and_records(db_conn)

    res = pipeline.re_run_failed_stage_items(
        db_conn, workspace_id=ws_id, run_id=run_id, stage="score", llm_adapter=FakeLLM()
    )
    assert len(res) == 2

    empty_res = pipeline.re_run_failed_stage_items(
        db_conn, workspace_id=ws_id, run_id=uuid.uuid4(), stage="score"
    )
    assert empty_res == []


def test_services_score_run_managed_connection(db_conn: psycopg.Connection[Any]) -> None:
    """services.score_run supports managed connection when conn is None."""
    ws_id, run_id, _ = _setup_workspace_and_records(db_conn)
    opps = services.score_run(None, workspace_id=ws_id, run_id=run_id, offline=True)
    assert len(opps) == 2
