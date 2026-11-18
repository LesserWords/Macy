"""Offline E2E Journeys J5, J6, and J7 Part 2: Draft Review Gate 2 (F15, F19, ADR-013, ADR-014).

Verifies:
- Journey J5: Produce & evaluate draft offline -> `draft.awaiting_approval` emitted ->
  owner opens Draft Review screen (pt-BR and en-US) and verifies rendered Markdown,
  claim -> source provenance table (title, URL link, publication date, date_status),
  evaluator findings/checks/score, revision history, and per-stage cost breakdown.
- Journey J6: Campaign creator approves draft on Draft Review screen -> status updates to
  "Rascunho Aprovado" / "Draft Approved", DB sets approved + decided_by + decided_at + note,
  and emits `draft.decided` with publish_queued=True.
- Journey J7 part 2: Campaign creator declines draft with note -> status updates to
  "Rascunho Recusado" / "Draft Declined" with note, emits `draft.decided`
  (publish_queued=False), and prevents the declined story from being recommended later.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from engine import db, safe_http, services
from tests.test_draft_review import seed_follow_up_run_with_opp, seed_review_workspace
from tests.test_publishing import TEST_ENC_KEY, _start_publish_server

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_j5_j6_j7_workspace(
    conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tag: str,
) -> dict[str, Any]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ENV", raising=False)
    base = seed_review_workspace(
        conn,
        f"e2e-{tag}",
        include_other_owner=False,
        headline="Lentes personalizadas no Brasil",
        angle="Padronização de atendimento em loja",
    )
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", base["creator_email"])
    ws_id = base["workspace_id"]
    user_id = base["creator_id"]
    opp = base["opportunity"]

    brief = services.generate_brief(
        conn, workspace_id=ws_id, opportunity_id=opp["id"], user_id=user_id, offline=True
    )
    return {
        "workspace_id": ws_id,
        "user_id": user_id,
        "campaign_id": base["campaign_id"],
        "run_id": base["run_id"],
        "source": base["source"],
        "document": base["document"],
        "opportunity": opp,
        "brief": brief,
    }


def _seed_j5_usage(db_conn: psycopg.Connection[Any], env: dict[str, Any]) -> None:
    db.record_usage(
        db_conn,
        workspace_id=env["workspace_id"],
        run_id=env["run_id"],
        stage="write",
        kind="llm",
        vendor="openrouter",
        model="fake",
        cost_usd=Decimal("0.040000"),
    )
    db.record_usage(
        db_conn,
        workspace_id=env["workspace_id"],
        run_id=env["run_id"],
        stage="evaluate",
        kind="llm",
        vendor="openrouter",
        model="fake",
        cost_usd=Decimal("0.020000"),
    )


def _assert_j5_pt_and_en_draft_review(
    at: AppTest,
    db_conn: psycopg.Connection[Any],
    env: dict[str, Any],
    draft_row: dict[str, Any],
) -> None:
    at.sidebar.radio(key="main_nav").set_value("draft_review").run()
    assert not at.exception
    all_md = "\n".join(m.value for m in at.markdown)
    assert "Revisão de Rascunhos" in "\n".join(s.value for s in at.subheader)
    assert draft_row["payload"]["slug"] in all_md
    assert "Reivindicações e Fontes" in all_md
    fact_claims = [
        c
        for c in db.list_draft_claims(
            db_conn, workspace_id=env["workspace_id"], draft_id=draft_row["id"]
        )
        if c.get("document_id") is not None
    ]
    assert fact_claims
    for claim in fact_claims:
        assert claim["document_title"] in all_md
        assert claim["document_url"] in all_md
    assert "https://e2e-j5j6.otica.example.com/estudo-varejo" in all_md
    assert "Verificada" in all_md
    assert "Avaliação de Qualidade" in all_md
    assert "Histórico de Revisões" in all_md
    assert "Custos da Execução" in all_md
    assert "$ 0,06 USD" in all_md

    at.sidebar.selectbox(key="language_selector").set_value("en-US").run()
    at.sidebar.radio(key="main_nav").set_value("draft_review").run()
    assert not at.exception
    all_md_en = "\n".join(m.value for m in at.markdown)
    assert "Draft Review" in "\n".join(s.value for s in at.subheader)
    assert "Claims & Sources" in all_md_en
    assert "Verified" in all_md_en
    assert "Quality Evaluation" in all_md_en
    assert "Revision History" in all_md_en
    assert "Run Cost Breakdown" in all_md_en
    assert "$ 0.06 USD" in all_md_en


def test_journey_j5_j6_write_evaluate_surface_and_approve_bilingual(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J5 + J6: Generate/evaluate draft, inspect provenance/cost, and approve (pt-BR/en-US)."""
    env = _seed_j5_j6_j7_workspace(db_conn, monkeypatch, "j5j6")
    _seed_j5_usage(db_conn, env)

    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()

    # Step J5a: From Weekly Report, trigger Write & Evaluate Draft for the approved brief
    at.sidebar.radio(key="main_nav").set_value("report").run()
    assert not at.exception
    at.button(key=f"write_draft_{env['opportunity']['id']}").click().run()
    assert not at.exception

    # Verify draft passed evaluation and emitted draft.awaiting_approval
    draft_row = db.get_latest_draft_for_brief(
        db_conn, workspace_id=env["workspace_id"], brief_id=env["brief"]["id"]
    )
    assert draft_row is not None
    assert draft_row["status"] == "pass"
    await_evts = db.list_events(
        db_conn,
        workspace_id=env["workspace_id"],
        event_type="draft.awaiting_approval",
        entity_id=draft_row["id"],
    )
    assert len(await_evts) == 1

    # Step J5b: Inspect Draft Review screen in pt-BR and en-US
    _assert_j5_pt_and_en_draft_review(at, db_conn, env, draft_row)

    # Step J6: Approve draft with optional note
    draft_id = draft_row["id"]
    at.text_input(key=f"draft_note_{draft_id}").set_value("Ready for CMS webhook").run()
    at.button(key=f"draft_approve_{draft_id}").click().run()
    assert not at.exception
    assert any("Draft Approved" in s.value for s in at.success)

    updated = db.get_draft(db_conn, workspace_id=env["workspace_id"], draft_id=draft_id)
    assert updated is not None
    assert updated["status"] == "approved"
    assert updated["decided_by"] == env["user_id"]
    assert updated["decision_note"] == "Ready for CMS webhook"

    decided_evts = db.list_events(
        db_conn,
        workspace_id=env["workspace_id"],
        event_type="draft.decided",
        entity_id=draft_id,
    )
    assert len(decided_evts) == 1
    assert decided_evts[0]["payload"]["publish_queued"] is True


def _produce_and_open_draft_review(
    db_conn: psycopg.Connection[Any],
    env: dict[str, Any],
) -> tuple[Any, AppTest]:
    eval_res = services.produce_and_evaluate_draft(
        db_conn,
        workspace_id=env["workspace_id"],
        brief_id=env["brief"]["id"],
        user_id=env["user_id"],
        offline=True,
    )
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("draft_review").run()
    assert not at.exception
    return eval_res["final_draft_id"], at


def test_journey_j7_part2_decline_draft_with_note_and_exclusion(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J7 Part 2: Owner declines draft with note in pt-BR; topic excluded in future run."""
    env = _seed_j5_j6_j7_workspace(db_conn, monkeypatch, "j7decl")
    draft_id, at = _produce_and_open_draft_review(db_conn, env)

    at.text_input(key=f"draft_note_{draft_id}").set_value("Precisa de estudo local").run()
    at.button(key=f"draft_decline_{draft_id}").click().run()
    assert not at.exception

    assert any("Rascunho Recusado" in e.value for e in at.error)
    assert any("Precisa de estudo local" in c.value for c in at.caption)

    updated = db.get_draft(db_conn, workspace_id=env["workspace_id"], draft_id=draft_id)
    assert updated is not None
    assert updated["status"] == "declined"
    assert updated["decision_note"] == "Precisa de estudo local"

    # Subsequent run with same document cannot re-recommend the declined story (F15 AC4)
    run2_id, opp2 = seed_follow_up_run_with_opp(
        db_conn,
        workspace_id=env["workspace_id"],
        campaign_id=env["campaign_id"],
        prev_run_id=env["run_id"],
        document_id=env["document"]["id"],
        created_by=env["user_id"],
        headline="Tema recusado anteriormente",
    )
    rep = services.report(db_conn, workspace_id=env["workspace_id"], run_id=run2_id)
    item = next(it for it in rep["items"] if it["id"] == opp2["id"])
    assert item["is_rejected_story"] is True
    assert item["is_recommended"] is False


def test_journey_j5_surfaced_max_revisions_navigation_and_empty_queue(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty queue renders notice; r2 surfaced draft shows warning, findings, and r0-r2 history."""
    env = _seed_j5_j6_j7_workspace(db_conn, monkeypatch, "r2surf")

    # Initially empty queue renders info notice cleanly
    at_empty = AppTest.from_file(APP_PATH, default_timeout=15)
    at_empty.run()
    at_empty.sidebar.radio(key="main_nav").set_value("draft_review").run()
    assert not at_empty.exception
    assert any("Nenhum rascunho" in i.value for i in at_empty.info)

    payload_base = {
        "schema_version": 1,
        "title": "Guia de Atendimento Óptico",
        "meta_description": "Guia operacional completo para equipes de loja.",
        "slug": "guia-atendimento-optico",
        "body_md": "# Guia de Atendimento Óptico\n\nConteúdo validado.",
        "faq": [{"question": "Como treinar a equipe?", "answer": "Com roteiros semanais."}],
        "cta": {"headline": "Diagnostico", "body": "Baixe o guia", "button_text": "Baixar"},
        "claims": [],
    }
    for rev, verdict, status in ((0, "revision", "revision"), (1, "revision", "revision")):
        d = db.create_next_draft_revision(
            db_conn,
            workspace_id=env["workspace_id"],
            brief_id=env["brief"]["id"],
            prompt_version="write_blog.md:v1",
            schema_version=1,
            payload=payload_base,
            claims=[],
            status=status,
            revision=rev,
        )
        db.upsert_quality_report(
            db_conn,
            workspace_id=env["workspace_id"],
            draft_id=d["id"],
            verdict=verdict,
            score=Decimal("70.00"),
            checks={},
            findings=[
                {
                    "criterion": "depth",
                    "severity": "major",
                    "detail": f"Ajustar profundidade r{rev}",
                    "responsible_module": "write",
                }
            ],
            evaluator_model="fake-eval",
            prompt_version="evaluate.md:v1",
        )

    r2 = db.create_next_draft_revision(
        db_conn,
        workspace_id=env["workspace_id"],
        brief_id=env["brief"]["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload=payload_base,
        claims=[],
        status="pass",
        revision=2,
    )
    db.upsert_quality_report(
        db_conn,
        workspace_id=env["workspace_id"],
        draft_id=r2["id"],
        verdict="revision",
        score=Decimal("76.50"),
        checks={
            "language_match": {"check": "language_match", "passed": True, "severity": "critical"}
        },
        findings=[
            {
                "criterion": "cta_specificity",
                "severity": "minor",
                "detail": "Tornar CTA mais direto",
                "responsible_module": "write",
            }
        ],
        evaluator_model="fake-eval",
        prompt_version="evaluate.md:v1",
    )

    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.sidebar.radio(key="main_nav").set_value("report").run()
    assert not at.exception
    at.button(key=f"open_draft_review_{env['opportunity']['id']}").click().run()
    assert not at.exception

    assert any("Encaminhado após 2 revisões" in w.value for w in at.warning)
    all_md = "\n".join(m.value for m in at.markdown)
    assert "Tornar CTA mais direto" in all_md
    assert "| r0 |" in all_md
    assert "| r1 |" in all_md
    assert "| r2 |" in all_md


def test_journey_j6_approve_publishes_markdown_export_and_renders_url_bilingual(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """J6 (M4-05): Approving draft with markdown_export writes file, saves row, shows URL."""
    monkeypatch.setattr(safe_http, "resolve_and_validate_host", lambda _host: ["93.184.216.34"])
    env = _seed_j5_j6_j7_workspace(db_conn, monkeypatch, "j6pub")
    export_dir = tmp_path / "j6_exports"
    services.save_integration(
        db_conn,
        workspace_id=env["workspace_id"],
        user_id=env["user_id"],
        port="publisher",
        plugin="markdown_export",
        settings={
            "export_dir": str(export_dir),
            "base_url": "https://blog.e2e-j6.example.com",
        },
    )
    draft_id, at = _produce_and_open_draft_review(db_conn, env)

    at.button(key=f"draft_approve_{draft_id}").click().run()
    assert not at.exception
    assert any("Rascunho Publicado" in m.value for m in at.info)

    pub = db.get_publication_for_draft(db_conn, workspace_id=env["workspace_id"], draft_id=draft_id)
    assert pub is not None
    assert pub["status"] == "published"
    assert pub["mode"] == "draft"
    assert pub["url"] is not None
    assert pub["url"].startswith("https://blog.e2e-j6.example.com/")
    assert any(pub["url"] in m.value for m in at.markdown)
    assert len(list(export_dir.rglob("*.md"))) == 1

    # Click Re-publish cleanly on the Draft Review screen (idempotent update, 1 row)
    at.button(key=f"draft_republish_{draft_id}").click().run()
    assert not at.exception
    pubs_after = db.list_publications_for_draft(
        db_conn, workspace_id=env["workspace_id"], draft_id=draft_id
    )
    assert len(pubs_after) == 1

    # Switch to en-US and verify bilingual publication section and URL still render cleanly
    at.sidebar.selectbox(key="language_selector").set_value("en-US").run()
    at.sidebar.radio(key="main_nav").set_value("draft_review").run()
    assert not at.exception
    assert any("Draft Published" in m.value for m in at.info)
    assert any(pub["url"] in m.value for m in at.markdown)

    pub_evts = db.list_events(
        db_conn,
        workspace_id=env["workspace_id"],
        event_type="article.published",
        entity_id=draft_id,
    )
    assert len(pub_evts) == 1
    assert pub_evts[0]["payload"]["url"] == pub["url"]


def test_journey_j6_publish_now_failed_webhook_error_and_retry_ui(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J6 (M4-05): Approved draft triggers Publish Now, shows HTTP 500 error, and retries."""
    monkeypatch.setenv("APP_ENCRYPTION_KEY", TEST_ENC_KEY)
    monkeypatch.setattr(safe_http, "is_ip_allowed", lambda _ip: True)
    env = _seed_j5_j6_j7_workspace(db_conn, monkeypatch, "j6retry")

    eval_res = services.produce_and_evaluate_draft(
        db_conn,
        workspace_id=env["workspace_id"],
        brief_id=env["brief"]["id"],
        user_id=env["user_id"],
        offline=True,
    )
    draft_id = eval_res["final_draft_id"]
    services.decide_draft(
        db_conn,
        workspace_id=env["workspace_id"],
        draft_id=draft_id,
        decision="approved",
        user_id=env["user_id"],
    )

    srv, url, secret = _start_publish_server()
    try:
        srv.mode = "500"
        services.save_integration(
            db_conn,
            workspace_id=env["workspace_id"],
            user_id=env["user_id"],
            port="publisher",
            plugin="http_webhook",
            settings={"endpoint_url": url},
            secrets={"signing_secret": secret},
            encryption_key=TEST_ENC_KEY,
        )

        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio(key="main_nav").set_value("draft_review").run()
        assert not at.exception
        assert any("Rascunho Aprovado" in s.value for s in at.success)

        # Click Publish Now while webhook returns 500 -> shows error & Retry Publish
        at.button(key=f"draft_publish_now_{draft_id}").click().run()
        assert not at.exception
        assert any("Falha na publicação" in e.value and "500" in e.value for e in at.error)

        # Recover webhook and click Retry Publish -> transitions to published + renders URL
        srv.mode = "good"
        at.button(key=f"draft_retry_publish_{draft_id}").click().run()
        assert not at.exception
        assert any("Rascunho Publicado" in i.value for i in at.info)
        pub = db.get_publication_for_draft(
            db_conn, workspace_id=env["workspace_id"], draft_id=draft_id
        )
        assert pub is not None and pub["status"] == "published" and pub["url"] is not None
        assert any(pub["url"] in m.value for m in at.markdown)

        # Simulate unexpected error during re-publish click to cover UI error banner handler
        def _boom(**_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("transient ui error")

        with monkeypatch.context() as mp:
            mp.setattr(services, "publish_draft", _boom)
            at.button(key=f"draft_republish_{draft_id}").click().run()
            assert not at.exception
            assert any("transient ui error" in e.value for e in at.error)

        # Removing publisher integration renders publication details without action buttons
        with db_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM workspace_integrations WHERE workspace_id = %s AND port = 'publisher'",
                (env["workspace_id"],),
            )
        at2 = AppTest.from_file(APP_PATH, default_timeout=15)
        at2.run()
        at2.sidebar.radio(key="main_nav").set_value("draft_review").run()
        assert not at2.exception
        assert any(pub["url"] in m.value for m in at2.markdown)
    finally:
        srv.shutdown()
        srv.server_close()
