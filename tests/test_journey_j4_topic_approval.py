"""Offline E2E Journey J4 & Journey J7 part 1: Topic Approval Gate 1 (F10, ADR-013).

Verifies:
- Journey J4 (Gate 1: approve top topic):
  1. Campaign creator views Weekly Report in pt-BR.
  2. Inputs optional decision note and clicks "Aprovar".
  3. Status updates to "Tópico Aprovado" with note and brief queued.
  4. Database confirms status='approved', decided_by, decided_at, and topic.decided event.
- Journey J7 part 1 (Reject path):
  1. Campaign creator clicks "Rejeitar" on a topic.
  2. Status updates to "Tópico Rejeitado".
  3. In subsequent report views, rejected story is not recommended.
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

from engine import db as db_layer
from engine import services as svc_layer


def _resolve_app_script_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_j4_campaign_and_run(
    conn: psycopg.Connection[Any],
    ws_id: uuid.UUID,
    user_id: uuid.UUID,
    tag: str,
    articles_per_week: int,
) -> tuple[uuid.UUID, uuid.UUID]:
    c_params: dict[str, Any] = {
        "articles_per_week": articles_per_week,
        "market": "BR",
        "locale": "pt-BR",
        "monthly_cap_usd": Decimal("150.00"),
        "run_cap_usd": Decimal("25.00"),
        "config_schema_version": 1,
        "config": {},
        "schedule_cron": "0 9 * * 1",
    }
    c_id = db_layer.create_campaign(
        conn,
        workspace_id=ws_id,
        name=f"J4 Campaign {tag}",
        created_by=user_id,
        **c_params,
    )
    cur_time = datetime.now(UTC)
    r_kwargs: dict[str, Any] = {
        "trigger": "manual",
        "window_start": cur_time - timedelta(days=7),
        "window_end": cur_time,
        "run_cap_usd": Decimal("25.00"),
        "code_version": "0.1.0",
        "pack_version": "1.0.0",
        "created_by": user_id,
    }
    r_id = db_layer.create_run(conn, workspace_id=ws_id, campaign_id=c_id, **r_kwargs)
    return c_id, r_id


def _seed_j4_environment(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tag: str,
    *,
    articles_per_week: int = 2,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)
    slug = f"j4-{tag}-{uuid.uuid4().hex[:6]}"
    ws_id = db_layer.create_workspace(
        db_conn,
        slug=slug,
        name=f"J4 Workspace {tag}",
        industry_pack="tech",
    )
    email = f"creator-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    res = svc_layer.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)

    _, run_id = _seed_j4_campaign_and_run(db_conn, ws_id, user_id, tag, articles_per_week)

    opp_ids: list[uuid.UUID] = []
    for idx in range(3):
        doc = db_layer.upsert_document(
            db_conn,
            workspace_id=ws_id,
            url=f"https://j4.example.com/{tag}/{idx}",
            canonical_url=f"https://j4.example.com/{tag}/{idx}",
            title=f"J4 Inovação #{idx + 1}",
            date_status="verified",
            fetch_status="ok",
        )
        rec = db_layer.upsert_research_record(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            document_id=doc["id"],
            status="verified",
            schema_version=1,
            payload={"headline": f"J4 Pauta #{idx + 1}", "summary": f"Resumo da pauta #{idx + 1}"},
        )
        opp = db_layer.upsert_opportunity(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_id=rec["id"],
            scores={"trend": {"score": 9.0, "evidence": "Alta busca"}},
            total=Decimal(str(90.0 - idx * 5)),
            funnel_stage="tofu",
            confidence="high",
            angle=f"Ângulo #{idx + 1}",
            status="scored",
        )
        db_layer.update_opportunity_rank(
            db_conn, workspace_id=ws_id, opportunity_id=opp["id"], rank=idx + 1
        )
        opp_ids.append(opp["id"])

    return ws_id, user_id, run_id, opp_ids


def test_journey_j4_gate_1_approve_path(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J4: Campaign creator views report, fills note, and approves top topic."""
    ws_id, user_id, run_id, opp_ids = _seed_j4_environment(db_conn, monkeypatch, "appr")

    app_path = _resolve_app_script_path()
    at = AppTest.from_file(app_path, default_timeout=15)
    at.run()

    # Navigate to Weekly Report page
    at.sidebar.radio(key="main_nav").set_value("report").run()
    assert not at.exception

    top_opp_id = opp_ids[0]
    note_key = f"note_{top_opp_id}"
    approve_key = f"approve_{top_opp_id}"

    # Fill optional decision note
    at.text_input(key=note_key).set_value("Focar em diferencial competitivo").run()
    assert not at.exception

    # Click approve button
    at.button(key=approve_key).click().run()
    assert not at.exception

    # Verify status reflects approval in UI
    assert any("Tópico Aprovado" in s.value for s in at.success)
    assert any("Focar em diferencial competitivo" in c.value for c in at.caption)

    # Verify database state and audit event (F10 AC1)
    opp = db_layer.get_opportunity(db_conn, workspace_id=ws_id, opportunity_id=top_opp_id)
    assert opp is not None
    assert opp["status"] == "approved"
    assert opp["decided_by"] == user_id
    assert opp["decision_note"] == "Focar em diferencial competitivo"
    assert opp["decided_at"] is not None

    events = db_layer.list_events(
        db_conn,
        workspace_id=ws_id,
        event_type="topic.decided",
        entity_id=top_opp_id,
    )
    assert len(events) == 1
    assert events[0]["payload"]["decision"] == "approved"
    assert events[0]["payload"]["brief_queued"] is True


def test_journey_j7_part1_reject_path(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J7 Part 1: Campaign creator rejects topic, confirming status update and exclusion."""
    ws_id, user_id, run_id, opp_ids = _seed_j4_environment(db_conn, monkeypatch, "rej")

    app_path = _resolve_app_script_path()
    at = AppTest.from_file(app_path, default_timeout=15)
    at.run()

    # Navigate to Weekly Report page
    at.sidebar.radio(key="main_nav").set_value("report").run()
    assert not at.exception

    target_opp_id = opp_ids[1]
    reject_key = f"reject_{target_opp_id}"

    # Click reject button
    at.button(key=reject_key).click().run()
    assert not at.exception

    # Verify status reflects rejection in UI
    assert any("Tópico Rejeitado" in e.value for e in at.error)

    # Verify database state
    opp = db_layer.get_opportunity(db_conn, workspace_id=ws_id, opportunity_id=target_opp_id)
    assert opp is not None
    assert opp["status"] == "rejected"
    assert opp["decided_by"] == user_id

    # Verify report exclusion (F10 AC5)
    rep = svc_layer.report(db_conn, workspace_id=ws_id, run_id=run_id)
    rep_item = next(it for it in rep["items"] if it["id"] == target_opp_id)
    assert rep_item["status"] == "rejected"
    assert rep_item["is_recommended"] is False
    assert rep_item["is_rejected_story"] is True


def _seed_example_brand_and_open_report(
    conn: psycopg.Connection[Any],
    ws_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AppTest:
    import yaml

    seed_brand = yaml.safe_load(Path("fixtures/seed_example.yaml").read_text(encoding="utf-8"))
    svc_layer.save_brand(conn, workspace_id=ws_id, user_id=user_id, profile=seed_brand)
    conn.commit()

    app = AppTest.from_file(_resolve_app_script_path(), default_timeout=15)
    app.run()
    app.sidebar.radio(key="main_nav").set_value("report").run()
    assert not app.exception
    return app


def test_journey_j4_approval_generates_and_renders_brief_with_keywords(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J4 + M3-02: Approving topic auto-generates brand-pinned brief and renders it bilingually."""
    from engine.contracts import KeywordData
    from engine.integrations.config import set_workspace_integration

    ws_id, user_id, _run_id, opp_ids = _seed_j4_environment(db_conn, monkeypatch, "brief-ui")

    # Seed a warm keyword_cache entry + configure fake LLM (no keyword_provider) to verify
    # warm search volume + cold "(Estimado)" / "(Estimated)" badge rendering in the UI.
    set_workspace_integration(
        db_conn, workspace_id=ws_id, port="llm", plugin="fake", updated_by=user_id
    )
    db_layer.upsert_keyword_cache(
        db_conn,
        provider="dataforseo",
        locale="pt-BR",
        market="BR",
        keywords_data=[
            {
                "keyword_norm": "j4 pauta #1",
                "data": KeywordData(
                    keyword="J4 Pauta #1",
                    search_volume=2400,
                    difficulty=25.0,
                    cpc_usd=1.10,
                    intent="informational",
                    is_estimate=False,
                ).model_dump(mode="json"),
            }
        ],
    )
    at = _seed_example_brand_and_open_report(db_conn, ws_id, user_id)

    top_opp_id = opp_ids[0]
    at.text_input(key=f"note_{top_opp_id}").set_value("Destacar retorno operacional").run()
    at.button(key=f"approve_{top_opp_id}").click().run()
    assert not at.exception

    # pt-BR brief assertions
    all_md = "\n".join(m.value for m in at.markdown)
    all_captions = "\n".join(c.value for c in at.caption)
    assert any("Briefing Editorial (v1)" in e.label for e in at.expander)
    assert "Versão da Marca:** v1" in all_captions
    assert "Palavras-Chave Alvo" in all_md
    assert "2,400" in all_md
    assert "Estrutura" in all_md
    assert "Objetivo de CTA" in all_md
    assert "Fatos Verificados" in all_md

    # Switch UI language to en-US and verify translated brief headings
    at.sidebar.selectbox(key="language_selector").set_value("en-US").run()
    at.sidebar.radio(key="main_nav").set_value("report").run()
    assert not at.exception

    all_md_en = "\n".join(m.value for m in at.markdown)
    all_captions_en = "\n".join(c.value for c in at.caption)
    assert any("Editorial Brief (v1)" in e.label for e in at.expander)
    assert "Brand Version:** v1" in all_captions_en
    assert "Target Keywords" in all_md_en
    assert "Outline" in all_md_en
    assert "CTA Goal" in all_md_en
    assert "Verified Facts" in all_md_en

    # Verify DB persistence & brief.created event
    latest_brief = db_layer.get_latest_brief_for_opportunity(
        db_conn, workspace_id=ws_id, opportunity_id=top_opp_id
    )
    assert latest_brief is not None
    assert latest_brief["version"] == 1
    assert latest_brief["payload"]["brand_version"] == 1

    created_events = db_layer.list_events(
        db_conn, workspace_id=ws_id, event_type="brief.created", entity_id=latest_brief["id"]
    )
    assert len(created_events) == 1


def test_journey_j4_manual_generate_brief_button_when_missing(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J4 + M3-02: Approved topic without a brief shows Generate Brief button and persists v1."""
    ws_id, user_id, _run_id, opp_ids = _seed_j4_environment(db_conn, monkeypatch, "gen-btn")
    top_opp_id = opp_ids[0]
    svc_layer.decide_topic(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=top_opp_id,
        decision="approved",
        user_id=user_id,
    )
    at = _seed_example_brand_and_open_report(db_conn, ws_id, user_id)

    at.button(key=f"gen_brief_{top_opp_id}").click().run()
    assert not at.exception

    assert any("Briefing Editorial (v1)" in e.label for e in at.expander)
    latest_brief = db_layer.get_latest_brief_for_opportunity(
        db_conn, workspace_id=ws_id, opportunity_id=top_opp_id
    )
    assert latest_brief is not None
    assert latest_brief["version"] == 1
