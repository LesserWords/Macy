"""Tests for Spend & Cost View (M4-07 Step 1, F17 AC5, AC6, ADR-016, ADR-023)."""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
import streamlit.testing.v1 as st_testing

from engine import db, services

AppTest = st_testing.AppTest
APP_FILE = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def _insert_ledger_row_at(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    stage: str,
    cost_usd: Decimal,
    input_tokens: int,
    output_tokens: int,
    created_at: datetime,
) -> uuid.UUID:
    usage_id = db.record_usage(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        stage=stage,
        kind="llm",
        vendor="openrouter",
        model="anthropic/claude-opus-4",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=0,
        cost_usd=cost_usd,
        latency_ms=400,
        prompt_version="v1",
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE usage_ledger SET created_at = %(ts)s WHERE id = %(id)s",
            {"ts": created_at, "id": usage_id},
        )
    return usage_id


def _seed_published_article_for_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    brand_profile_id: uuid.UUID,
    title: str,
    slug: str,
    now: datetime,
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    doc = db.upsert_document(
        conn,
        workspace_id=workspace_id,
        url=f"https://example.com/{slug}-{tag}",
        canonical_url=f"https://example.com/{slug}-{tag}",
        title=f"Source for {title}",
        date_status="verified",
        fetch_status="ok",
        published_at=now,
        extracted_text="Verified industry source text.",
    )
    rec = db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        document_id=doc["id"],
        status="extracted",
        schema_version=1,
        payload={"title": title, "summary": "Summary"},
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        record_id=rec["id"],
        scores={"novelty": {"score": 8.0, "evidence": "ev"}},
        total=Decimal("80.0"),
        angle="Practical optical retail guide",
        funnel_stage="mofu",
        confidence="high",
        rank=1,
        status="approved",
        decided_by=user_id,
        decided_at=now,
    )
    brief = db.create_next_brief_version(
        conn,
        workspace_id=workspace_id,
        opportunity_id=opp["id"],
        brand_profile_id=brand_profile_id,
        prompt_version="brief-v1",
        keywords=[],
        schema_version=1,
        payload={"thesis": title, "target_keyword": slug},
    )
    draft = db.create_next_draft_revision(
        conn,
        workspace_id=workspace_id,
        brief_id=brief["id"],
        status="published",
        prompt_version="write-v1",
        schema_version=1,
        payload={
            "title": title,
            "slug": slug,
            "meta_description": f"Meta for {title}",
            "body_md": f"# {title}\n\nBody text.",
            "faq": [],
            "cta": "Saiba mais",
        },
        claims=[],
    )
    pub = db.upsert_draft_publication(
        conn,
        workspace_id=workspace_id,
        draft_id=draft["id"],
        destination="markdown_export",
        mode="draft",
        status="published",
        external_id=f"export:{slug}",
        url=f"https://blog.example.com/{slug}",
        published_at=now,
        title=title,
        response={"mode": "draft"},
    )
    return {"draft": draft, "publication": pub}


def _seed_cost_fixture(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    ws_id = db.create_workspace(
        conn,
        slug=f"cost-ws-{tag}",
        name="Cost View Workspace",
        industry_pack="general",
    )
    owner_email = f"cost-owner-{tag}@example.com"
    owner_res = services.add_workspace_owner(
        conn, workspace_slug=f"cost-ws-{tag}", email=owner_email
    )
    owner_id = uuid.UUID(str(owner_res["user"]["user_id"]))

    outsider = db.create_or_get_user(conn, email=f"cost-outsider-{tag}@example.com")
    outsider_id = uuid.UUID(str(outsider["user_id"]))

    brand = db.save_brand_profile(
        conn,
        workspace_id=ws_id,
        payload={"brand_name": "Acme", "voice": "Technical"},
        schema_version=1,
        created_by=owner_id,
    )
    brand_id = uuid.UUID(str(brand["id"]))

    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name="Optical Content Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("5.00"),
        config={},
        config_schema_version=1,
        created_by=owner_id,
    )

    nov_now = datetime(2026, 11, 9, 15, 0, 0, tzinfo=UTC)
    oct_prev = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)

    # Run 0: Previous month ($0.50)
    run0_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="scheduled",
        window_start=oct_prev - timedelta(days=7),
        window_end=oct_prev,
        run_cap_usd=Decimal("5.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=owner_id,
    )
    db.update_run_status(
        conn,
        workspace_id=ws_id,
        run_id=run0_id,
        expected_status=None,
        new_status="completed",
        cost_usd=Decimal("0.500000"),
    )
    _insert_ledger_row_at(
        conn,
        workspace_id=ws_id,
        run_id=run0_id,
        stage="research",
        cost_usd=Decimal("0.500000"),
        input_tokens=1000,
        output_tokens=200,
        created_at=oct_prev,
    )

    # Run 1: Current month ($2.00 across 5 stages, 1 published article)
    run1_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="scheduled",
        window_start=nov_now - timedelta(days=7),
        window_end=nov_now,
        run_cap_usd=Decimal("5.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=owner_id,
    )
    db.update_run_status(
        conn,
        workspace_id=ws_id,
        run_id=run1_id,
        expected_status=None,
        new_status="completed",
        cost_usd=Decimal("2.000000"),
    )
    for stage, cost, in_tok, out_tok in (
        ("research", Decimal("0.120000"), 1500, 300),
        ("score", Decimal("0.280000"), 4000, 800),
        ("brief", Decimal("0.450000"), 2500, 1200),
        ("write", Decimal("0.850000"), 3500, 2800),
        ("evaluate", Decimal("0.300000"), 4200, 600),
    ):
        _insert_ledger_row_at(
            conn,
            workspace_id=ws_id,
            run_id=run1_id,
            stage=stage,
            cost_usd=cost,
            input_tokens=in_tok,
            output_tokens=out_tok,
            created_at=nov_now,
        )
    art1 = _seed_published_article_for_run(
        conn,
        workspace_id=ws_id,
        run_id=run1_id,
        user_id=owner_id,
        brand_profile_id=brand_id,
        title="Lentes Free-Form no Varejo Óptico",
        slug="lentes-free-form-varejo-optico",
        now=nov_now,
    )

    # Run 2: Current month ($2.50 across 5 stages, 1 published article)
    run2_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=nov_now - timedelta(days=7),
        window_end=nov_now,
        run_cap_usd=Decimal("5.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=owner_id,
    )
    db.update_run_status(
        conn,
        workspace_id=ws_id,
        run_id=run2_id,
        expected_status=None,
        new_status="completed",
        cost_usd=Decimal("2.500000"),
    )
    for stage, cost, in_tok, out_tok in (
        ("research", Decimal("0.150000"), 1800, 350),
        ("score", Decimal("0.350000"), 4500, 900),
        ("brief", Decimal("0.500000"), 2800, 1300),
        ("write", Decimal("1.100000"), 4000, 3100),
        ("evaluate", Decimal("0.400000"), 4600, 700),
    ):
        _insert_ledger_row_at(
            conn,
            workspace_id=ws_id,
            run_id=run2_id,
            stage=stage,
            cost_usd=cost,
            input_tokens=in_tok,
            output_tokens=out_tok,
            created_at=nov_now,
        )
    art2 = _seed_published_article_for_run(
        conn,
        workspace_id=ws_id,
        run_id=run2_id,
        user_id=owner_id,
        brand_profile_id=brand_id,
        title="Gestão de Estoque para Armações",
        slug="gestao-estoque-armacoes",
        now=nov_now,
    )

    return {
        "ws_id": ws_id,
        "owner_id": owner_id,
        "owner_email": owner_email,
        "outsider_id": outsider_id,
        "camp_id": camp_id,
        "run0_id": run0_id,
        "run1_id": run1_id,
        "run2_id": run2_id,
        "art1": art1,
        "art2": art2,
        "nov_now": nov_now,
    }


def test_cost_summary_against_literal_ledger_fixtures(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F17 AC5 & AC6: spend-to-date vs caps, cost per run, cost per published article."""
    fx = _seed_cost_fixture(db_conn)

    summary = services.cost_summary(
        db_conn,
        workspace_id=fx["ws_id"],
        user_id=fx["owner_id"],
        campaign_id=fx["camp_id"],
        reference_date=fx["nov_now"],
    )

    # 1. Spend-to-date against caps (Nov 2026 = $2.00 + $2.50 = $4.50; Oct $0.50 excluded)
    assert summary["monthly_spend_usd"] == Decimal("4.500000")
    assert summary["monthly_cap_usd"] == Decimal("150.00")
    assert summary["monthly_remaining_usd"] == Decimal("145.500000")
    assert summary["run_cap_usd"] == Decimal("5.00")
    assert summary["total_spend_usd"] == Decimal("5.000000")
    assert summary["pilot_target_usd"] == Decimal("5.00")
    assert summary["within_pilot_target"] is True

    # 2. Cost per run
    runs_by_id = {r["run_id"]: r for r in summary["runs"]}
    assert set(runs_by_id.keys()) == {fx["run0_id"], fx["run1_id"], fx["run2_id"]}
    assert runs_by_id[fx["run1_id"]]["cost_usd"] == Decimal("2.000000")
    assert runs_by_id[fx["run1_id"]]["calls"] == 5
    assert runs_by_id[fx["run1_id"]]["published_articles_count"] == 1
    assert runs_by_id[fx["run2_id"]]["cost_usd"] == Decimal("2.500000")
    assert runs_by_id[fx["run2_id"]]["calls"] == 5
    assert runs_by_id[fx["run2_id"]]["published_articles_count"] == 1
    assert runs_by_id[fx["run0_id"]]["cost_usd"] == Decimal("0.500000")
    assert summary["average_cost_per_run_usd"] == Decimal("1.666667")

    # 3. Cost per published article (< $5.00 pilot target)
    articles_by_slug = {a["slug"]: a for a in summary["published_articles"]}
    assert set(articles_by_slug.keys()) == {
        "lentes-free-form-varejo-optico",
        "gestao-estoque-armacoes",
    }
    assert articles_by_slug["lentes-free-form-varejo-optico"]["cost_usd"] == Decimal("2.000000")
    assert articles_by_slug["lentes-free-form-varejo-optico"]["under_pilot_target"] is True
    assert articles_by_slug["gestao-estoque-armacoes"]["cost_usd"] == Decimal("2.500000")
    assert articles_by_slug["gestao-estoque-armacoes"]["under_pilot_target"] is True
    assert summary["average_cost_per_published_article_usd"] == Decimal("2.250000")


def test_cost_summary_permissions_and_empty_workspace(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Non-members get PermissionError; empty workspace returns zeroed Decimal metrics."""
    fx = _seed_cost_fixture(db_conn)
    with pytest.raises(PermissionError):
        services.cost_summary(
            db_conn,
            workspace_id=fx["ws_id"],
            user_id=fx["outsider_id"],
        )

    tag = uuid.uuid4().hex[:8]
    empty_ws_id = db.create_workspace(
        db_conn,
        slug=f"empty-cost-{tag}",
        name="Empty Cost WS",
        industry_pack="general",
    )
    owner_res = services.add_workspace_owner(
        db_conn,
        workspace_slug=f"empty-cost-{tag}",
        email=f"empty-owner-{tag}@example.com",
    )
    empty_user_id = uuid.UUID(str(owner_res["user"]["user_id"]))

    # Also test managed connection path (conn=None) and default campaign resolution
    default_camp_summary = services.cost_summary(
        db_conn,
        workspace_id=fx["ws_id"],
        user_id=fx["owner_id"],
        campaign_id=None,
        reference_date=fx["nov_now"],
    )
    assert default_camp_summary["campaign_id"] == fx["camp_id"]

    empty_summary = services.cost_summary(
        workspace_id=empty_ws_id,
        user_id=empty_user_id,
    )
    assert empty_summary["monthly_spend_usd"] == Decimal("0.000000")
    assert empty_summary["monthly_cap_usd"] == Decimal("0.00")
    assert empty_summary["run_cap_usd"] == Decimal("0.00")
    assert empty_summary["runs"] == []
    assert empty_summary["published_articles"] == []
    assert empty_summary["average_cost_per_run_usd"] == Decimal("0.000000")
    assert empty_summary["average_cost_per_published_article_usd"] == Decimal("0.000000")
    assert empty_summary["within_pilot_target"] is True


def test_cost_view_rendered_in_streamlit_ui_pt_and_en(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify F17 AC5 & AC6 in the Streamlit UI across Dashboard and Runs screens."""
    fx = _seed_cost_fixture(db_conn)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", fx["owner_email"])

    at = AppTest.from_file(APP_FILE, default_timeout=15)
    at.run(timeout=15)
    assert not at.exception

    # Navigate to Runs & Orchestration screen and verify Cost View is shown
    at.sidebar.radio(key="main_nav").set_value("runs").run(timeout=15)
    assert not at.exception

    all_md_pt = "\n".join(m.value for m in at.markdown)
    assert "Lentes Free-Form no Varejo Óptico" in all_md_pt
    assert "Gestão de Estoque para Armações" in all_md_pt
    assert "5,00" in all_md_pt

    # Switch to en-US and verify English Cost View
    at.sidebar.selectbox(key="language_selector").select("en-US").run(timeout=15)
    assert not at.exception
    at.sidebar.radio(key="main_nav").set_value("runs").run(timeout=15)
    assert not at.exception

    all_md_en = "\n".join(m.value for m in at.markdown)
    assert "Cost & Spend Overview" in all_md_en or "Cost per Published Article" in all_md_en
    assert "Lentes Free-Form no Varejo Óptico" in all_md_en
    assert "$ 2.00 USD" in all_md_en
    assert "$ 2.50 USD" in all_md_en
