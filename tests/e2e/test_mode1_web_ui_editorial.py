"""Mode 1 E2E Test: Web UI (Streamlit + Playwright Chromium) seasonal MOFU editorial lifecycle."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from engine import db, services
from engine.contracts import CampaignConfig
from engine.integrations.config import set_workspace_integration
from tests.e2e.editorial_helpers import (
    ROOT_DIR,
    _free_port,
    _wait_for_http,
)


def _seed_seasonal_workspace_for_offline_ui(
    conn: psycopg.Connection[Any],
    *,
    slug: str,
    owner_email: str,
    export_dir: Path,
) -> dict[str, Any]:
    export_dir.mkdir(parents=True, exist_ok=True)
    ws_id = db.create_workspace(
        conn,
        slug=slug,
        name=f"Seasonal WS {slug}",
        industry_pack="general",
    )
    owner_res = services.add_workspace_owner(conn, workspace_slug=slug, email=owner_email)
    user_id = uuid.UUID(str(owner_res["user"]["user_id"]))
    services.seed_brand(conn, workspace_slug=slug)
    set_workspace_integration(
        conn,
        workspace_id=ws_id,
        port="publisher",
        plugin="markdown_export",
        settings={"export_dir": str(export_dir), "default_mode": "draft"},
        secrets=None,
        updated_by=user_id,
    )
    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name="Campanha Sazonal Varejo PT-BR",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("120.00"),
        run_cap_usd=Decimal("20.00"),
        config=CampaignConfig(
            cta_intensity_limits={"TOFU": "low", "MOFU": "medium", "BOFU": "high"}
        ).model_dump(),
        config_schema_version=1,
        created_by=user_id,
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
        code_version="0.2.0",
        pack_version="1.0.0",
        created_by=user_id,
    )
    db.update_run_status(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        expected_status="queued",
        new_status="completed",
        stage="report",
    )
    doc = db.upsert_document(
        conn,
        workspace_id=ws_id,
        url="https://example.com/noticias/black-friday-fim-de-ano-estoque",
        canonical_url="https://example.com/noticias/black-friday-fim-de-ano-estoque",
        title="Black Friday e fim de ano exigem planejamento de estoque no varejo",
        published_at=now - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
        extracted_text=(
            "Black Friday e fim de ano elevam demanda em novembro e dezembro. "
            "Lojas que revisam curva ABC e prazos de entrega evitam rupturas no balcão."
        ),
    )
    rec = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=uuid.UUID(str(doc["id"])),
        status="extracted",
        schema_version=1,
        payload={
            "headline": doc["title"],
            "url": doc["url"],
            "source": "Retail Journal",
            "summary": doc["extracted_text"],
            "facts": [
                "Revisão semanal de estoque reduz rupturas em campanhas de novembro e dezembro."
            ],
            "published_at": str(doc["published_at"]),
        },
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=uuid.UUID(str(rec["id"])),
        scores={
            "novelty": {
                "score": 8.0,
                "evidence": "Tema sazonal de novembro",
                "is_estimate": False,
            },
            "audience_fit": {
                "score": 9.0,
                "evidence": "Gestores em planejamento",
                "is_estimate": False,
            },
            "brand_fit": {
                "score": 8.5,
                "evidence": "Foco em operação e processo",
                "is_estimate": False,
            },
            "competitor_gap": {
                "score": 7.5,
                "evidence": "Poucos checklists práticos",
                "is_estimate": False,
            },
            "trend": {
                "score": 9.0,
                "evidence": "Alta sazonal de fim de ano",
                "is_estimate": False,
            },
            "urgency": {
                "score": 9.0,
                "evidence": "Janela imediata de novembro",
                "is_estimate": False,
            },
        },
        total=Decimal("85.50"),
        angle="Checklist prático de estoque e metas para Black Friday e fim de ano (MOFU)",
        funnel_stage="mofu",
        confidence="high",
        status="scored",
    )
    return {
        "workspace_id": ws_id,
        "user_id": user_id,
        "campaign_id": camp_id,
        "run_id": run_id,
        "opportunity_id": uuid.UUID(str(opp["id"])),
        "slug": slug,
    }


def _drive_ui_editorial_cycle(base_url: str) -> list[str]:
    page_errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.goto(base_url, wait_until="networkidle")

        sidebar = page.locator('[data-testid="stSidebar"]')
        sidebar.get_by_text(re.compile(r"Weekly Report|Relatório Semanal")).first.click(
            timeout=20000
        )
        page.get_by_text(
            re.compile(r"Weekly Opportunity Report|Relatório Semanal de Oportunidades")
        ).first.wait_for(timeout=20000)

        note_input = page.get_by_label(re.compile(r"Decision Note|Nota de Decisão")).first
        note_input.wait_for(state="visible", timeout=20000)
        note_input.fill("Post sazonal de meio de funil sobre Black Friday e fim de ano")
        page.get_by_role("button", name=re.compile(r"^(Approve|Aprovar)$")).first.click()

        write_or_brief = page.get_by_role(
            "button",
            name=re.compile(
                r"Write & Evaluate Draft|Redigir e Avaliar Rascunho|Generate Brief|Gerar Briefing"
            ),
        ).first
        write_or_brief.wait_for(state="visible", timeout=30000)
        if "Brief" in (write_or_brief.inner_text() or ""):
            write_or_brief.click()
            write_btn = page.get_by_role(
                "button",
                name=re.compile(r"Write & Evaluate Draft|Redigir e Avaliar Rascunho"),
            ).first
            write_btn.wait_for(state="visible", timeout=30000)
        else:
            write_btn = write_or_brief
        write_btn.click()

        open_review_btn = page.get_by_role(
            "button",
            name=re.compile(r"Open Draft Review|Abrir Revisão do Rascunho"),
        ).first
        open_review_btn.wait_for(state="visible", timeout=45000)
        open_review_btn.click()

        approve_draft_btn = page.get_by_role(
            "button", name=re.compile(r"^(Approve|Aprovar)$")
        ).first
        approve_draft_btn.wait_for(state="visible", timeout=20000)
        body_text = page.locator("body").inner_text()
        assert "Claims" in body_text or "Afirmações" in body_text or "Fontes" in body_text
        approve_draft_btn.click()
        page.get_by_text(re.compile(r"Draft Published|Rascunho Publicado")).first.wait_for(
            timeout=25000
        )
        browser.close()
    return page_errors


def test_e2e_mode1_web_ui_seasonal_mofu_post_lifecycle(
    isolated_modes_e2e_db: str,
    tmp_path: Path,
) -> None:
    """Mode 1 E2E: Playwright drives Web UI from Weekly Report Gate 1 to Markdown publish."""
    export_dir = tmp_path / "exports_ui"
    owner_email = "ui-modes@example.com"
    enc_key = Fernet.generate_key().decode("ascii")
    with psycopg.connect(isolated_modes_e2e_db) as conn:
        ws = _seed_seasonal_workspace_for_offline_ui(
            conn,
            slug="seasonal-ui-ws",
            owner_email=owner_email,
            export_dir=export_dir,
        )
        conn.commit()

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    env.pop("ENV", None)
    env.update(
        {
            "APP_ENV": "test",
            "POCKET_MODE": "false",
            "AUTH_MODE": "local",
            "AUTO_MIGRATE": "false",
            "EMBEDDED_WORKER": "false",
            "DATABASE_URL": isolated_modes_e2e_db,
            "APP_ENCRYPTION_KEY": enc_key,
            "MACY_TEST_USER_EMAIL": owner_email,
        }
    )

    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_http(f"{base_url}/_stcore/health", timeout_sec=35.0)
        page_errors = _drive_ui_editorial_cycle(base_url)
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)

    assert page_errors == []
    exported_files = sorted(export_dir.rglob("*.md"))
    assert exported_files, "Expected Web UI Publish Now to write an exported .md file"
    md_content = exported_files[0].read_text(encoding="utf-8")
    assert "draft_id:" in md_content
    assert "locale: pt-BR" in md_content

    with psycopg.connect(isolated_modes_e2e_db) as conn:
        pub_rows = conn.execute(
            "SELECT status, external_id FROM publications WHERE workspace_id = %s",
            (ws["workspace_id"],),
        ).fetchall()
    assert pub_rows
