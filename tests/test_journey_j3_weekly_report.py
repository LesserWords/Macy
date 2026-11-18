"""Offline E2E Journey J3: Weekly Opportunity Report Screen (F09, ADR-002, ADR-004, ADR-014).

Verifies Journey J3 from PRD §9.6, F09 AC3-AC5, and M2-02:
- J3.1: Navigate to Weekly Report screen in pt-BR; verify top N recommended matching volume (min 3);
        verify all fields rendered (URL, publication date, angle, funnel stage, total, confidence);
        verify date integrity (unverified date is never labelled verified, F09 AC4);
        switch language to en-US and verify all labels in English.
- J3.2: Trigger "View Weekly Report" button directly from Runs page and verify navigation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from engine import db, services

APP_FILE = str(Path(__file__).parents[1] / "app.py")


def _seed_j3_environment(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
    *,
    articles_per_week: int = 2,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="J3 Report Eyewear WS",
        industry_pack="general",
    )
    email = f"editor-{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)

    camp_id = db.create_campaign(
        db_conn,
        workspace_id=ws_id,
        name="Eyewear Retail Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=articles_per_week,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("25.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )

    now = datetime.now(UTC)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("25.00"),
        code_version="v3",
        pack_version="p3",
        created_by=user_id,
    )

    # 4 Opportunities: #0 (verified), #1 (unverified), #2 (verified), #3 (verified)
    for i in range(4):
        is_unverified = i == 1
        doc = db.upsert_document(
            db_conn,
            workspace_id=ws_id,
            url=f"https://otica-j3.example.com/pauta-{i}",
            canonical_url=f"https://otica-j3.example.com/pauta-{i}",
            title=f"Lentes Especiais Estudo {i + 1}",
            date_status="unverified" if is_unverified else "verified",
            published_at=now - timedelta(days=i + 1),
            fetch_status="ok",
            content_hash=f"h-j3-{i}".encode(),
        )
        rec = db.upsert_research_record(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            document_id=doc["id"],
            status="verified",
            schema_version=1,
            payload={
                "headline": f"Inovação em Lentes Ópticas #{i + 1}",
                "summary": f"Resumo aprofundado sobre tecnologia oftálmica #{i + 1}.",
            },
        )
        db.upsert_opportunity(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_id=rec["id"],
            scores={
                "novelty": {"score": 8.0, "evidence": "Novidade no mercado"},
                "audience_fit": {"score": 9.0, "evidence": "Alta demanda"},
                "brand_fit": {"score": 8.5, "evidence": "Alinhado com a marca"},
                "competitor_gap": {"score": 7.5, "evidence": "Pouca concorrência"},
                "trend": {"score": 6.5, "evidence": "Tendência", "is_estimate": True},
                "urgency": {"score": 5.0, "evidence": "Momento ideal"},
            },
            total=Decimal(str(90.0 - (i * 8.0))),
            rank=i + 1,
            funnel_stage="tofu" if i == 0 else "mofu",
            confidence="high",
            angle=f"Ângulo de mercado #{i + 1}",
        )

    return ws_id, run_id, email


def test_journey_j3_1_report_screen_provenance_and_date_integrity(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J3.1: Navigate to report in pt-BR then en-US; verify provenance and date integrity."""
    ws_id, run_id, _ = _seed_j3_environment(db_conn, monkeypatch, "j3-view", articles_per_week=2)

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()

    # Navigate to report page
    at.sidebar.radio(key="main_nav").set_value("report").run()
    assert not at.exception
    assert any("Relatório Semanal de Oportunidades" in s.value for s in at.subheader)

    # Check metrics: articles/week = 2, recommended = max(3, 2) = 3, total = 4
    metrics_text = [m.label for m in at.metric]
    assert any("Artigos / Semana" in label for label in metrics_text)
    assert any("Tópicos Recomendados" in label for label in metrics_text)
    assert any("Total de Oportunidades" in label for label in metrics_text)

    # Check recommendation highlights
    md_values = [m.value for m in at.markdown]
    rec_count = sum(1 for m in md_values if "Tópico Recomendado" in m)
    opp_count = sum(1 for m in md_values if "Oportunidade de Pauta" in m)
    assert rec_count == 3
    assert opp_count == 1

    # Check fields present
    assert any("Inovação em Lentes Ópticas #1" in m for m in md_values)
    assert any("Ângulo de mercado #1" in m for m in md_values)
    assert any("TOFU" in m for m in md_values)
    assert any("90.0/100" in m for m in md_values)
    assert any("https://otica-j3.example.com/pauta-0" in m for m in md_values)

    # F09 AC4: Date integrity check
    # Item 1 was seeded with date_status="unverified"
    assert any("Não Verificada" in m for m in md_values)
    assert not any("Inovação em Lentes Ópticas #2" in m and "Verificada" in m for m in md_values)

    # Switch UI language to English (en-US)
    at.sidebar.selectbox(key="language_selector").set_value("en-US").run()
    assert not at.exception
    assert any("Weekly Opportunity Report" in s.value for s in at.subheader)

    en_metrics = [m.label for m in at.metric]
    assert any("Articles / Week" in label for label in en_metrics)
    assert any("Recommended Topics" in label for label in en_metrics)

    en_md = [m.value for m in at.markdown]
    assert any("Recommended Topic" in m for m in en_md)
    assert any("Unverified" in m for m in en_md)


def test_journey_j3_2_navigation_from_runs_page(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J3.2: Direct navigation from Runs screen via 'View Weekly Report' button."""
    ws_id, run_id, _ = _seed_j3_environment(db_conn, monkeypatch, "j3-nav", articles_per_week=1)

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run()

    # Navigate to Runs screen
    at.sidebar.radio(key="main_nav").set_value("runs").run()
    assert not at.exception
    assert any("Execuções e Orquestração" in s.value for s in at.subheader)

    # Click 'View Weekly Report'
    assert at.button(key="btn_view_report_from_runs") is not None
    at.button(key="btn_view_report_from_runs").click().run()
    assert not at.exception

    # Verify report is rendered
    assert any("Relatório Semanal de Oportunidades" in s.value for s in at.subheader)
    assert any("Tópicos Recomendados" in m.label for m in at.metric)
