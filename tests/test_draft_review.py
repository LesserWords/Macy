"""Unit and integration tests for Draft Review Gate 2 (F15 AC1-AC5, ADR-013, M4-04)."""

from __future__ import annotations

import concurrent.futures
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from engine import db, services
from tests.conftest import TEST_DATABASE_URL


def seed_review_workspace(
    conn: psycopg.Connection[Any],
    tag: str,
    *,
    include_other_owner: bool = True,
    headline: str | None = None,
    angle: str = "Gestão e margem em redes de óticas",
) -> dict[str, Any]:
    """Seed workspace, owner(s), brand profile, campaign, run, source, doc, record, and opp."""
    slug = f"dr-{tag}-{uuid.uuid4().hex[:6]}"
    ws_id = db.create_workspace(conn, slug=slug, name=f"DR {tag}", industry_pack="general")
    creator_email = f"dr-owner-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    creator = services.add_workspace_owner(conn, workspace_slug=slug, email=creator_email)["user"]
    creator_id = creator["user_id"]

    other_id = creator_id
    if include_other_owner:
        other_email = f"dr-other-{tag}-{uuid.uuid4().hex[:6]}@example.com"
        other_id = services.add_workspace_owner(conn, workspace_slug=slug, email=other_email)[
            "user"
        ]["user_id"]

    profile = yaml.safe_load(Path("fixtures/seed_example.yaml").read_text(encoding="utf-8"))
    brand = services.save_brand(conn, workspace_id=ws_id, user_id=creator_id, profile=profile)

    camp_kwargs: dict[str, Any] = {
        "name": f"Camp {tag}",
        "locale": "pt-BR",
        "market": "BR",
        "articles_per_week": 2,
        "schedule_cron": "0 9 * * 1",
        "monthly_cap_usd": Decimal("150.00"),
        "run_cap_usd": Decimal("25.00"),
        "config": {},
        "config_schema_version": 1,
        "created_by": creator_id,
    }
    camp_id = db.create_campaign(conn, workspace_id=ws_id, **camp_kwargs)

    now = datetime.now(UTC)
    run_kwargs: dict[str, Any] = {
        "campaign_id": camp_id,
        "trigger": "manual",
        "window_start": now - timedelta(days=7),
        "window_end": now,
        "run_cap_usd": Decimal("25.00"),
        "code_version": "0.1.0",
        "pack_version": "1.0.0",
        "created_by": creator_id,
    }
    run_id = db.create_run(conn, workspace_id=ws_id, **run_kwargs)

    src_kwargs: dict[str, Any] = {
        "industry_pack": "general",
        "name": f"Fonte Ótica {tag}",
        "domain": f"{tag}.otica.example.com",
        "url": f"https://{tag}.otica.example.com",
        "fetch_mode": "site_search",
        "feed_url": None,
        "tier": 1,
        "role": "data",
        "locales": ["pt-BR"],
        "topics": ["lentes"],
        "enabled": True,
    }
    src = db.create_source(conn, workspace_id=ws_id, **src_kwargs)

    doc_url = f"https://{tag}.otica.example.com/estudo-varejo"
    doc_kwargs: dict[str, Any] = {
        "source_id": src["id"],
        "url": doc_url,
        "canonical_url": doc_url,
        "title": f"Pesquisa Mensal de Comércio Óptico ({tag})",
        "published_at": now,
        "date_status": "verified",
        "fetch_status": "ok",
        "extracted_text": "O varejo óptico brasileiro cresceu 8% no terceiro trimestre de 2026.",
        "meta": {
            "source_role": "data",
            "source_name": src["name"],
            "facts": ["O varejo óptico cresceu 8% no 3T26."],
        },
    }
    doc = db.upsert_document(conn, workspace_id=ws_id, **doc_kwargs)
    rec = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": headline or f"Expansão de lentes digitais ({tag})",
            "summary": "Demanda por lentes multifocais digitais acelera nas redes regionais.",
        },
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rec["id"],
        scores={"novelty": {"score": 8.5, "evidence": "Alta busca"}},
        total=Decimal("88.00"),
        rank=1,
        funnel_stage="mofu",
        confidence="high",
        angle=angle,
        status="approved",
        decided_by=creator_id,
        decided_at=now,
    )
    return {
        "workspace_id": ws_id,
        "slug": slug,
        "creator_id": creator_id,
        "creator_email": creator_email,
        "other_user_id": other_id,
        "brand": brand,
        "campaign_id": camp_id,
        "run_id": run_id,
        "source": src,
        "document": doc,
        "record": rec,
        "opportunity": opp,
    }


def seed_follow_up_run_with_opp(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    prev_run_id: uuid.UUID,
    document_id: uuid.UUID,
    created_by: uuid.UUID,
    headline: str,
    total: Decimal = Decimal("94.00"),
) -> tuple[uuid.UUID, dict[str, Any]]:
    """Close prev_run_id and create a new run with a verified record/opportunity for document_id."""
    db.update_run_status(
        conn,
        workspace_id=workspace_id,
        run_id=prev_run_id,
        expected_status="queued",
        new_status="completed",
    )
    now = datetime.now(UTC)
    run2_id = db.create_run(
        conn,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("25.00"),
        code_version="0.1.0",
        pack_version="1.0.0",
        created_by=created_by,
    )
    rec = db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run2_id,
        document_id=document_id,
        status="verified",
        schema_version=1,
        payload={"headline": headline, "summary": "Reprocessamento de pauta."},
    )
    opp_kwargs: dict[str, Any] = {
        "run_id": run2_id,
        "record_id": rec["id"],
        "scores": {"novelty": {"score": 9.0, "evidence": "Alta"}},
        "total": total,
        "rank": 1,
        "funnel_stage": "mofu",
        "confidence": "high",
        "angle": "Outro ângulo",
    }
    opp = db.upsert_opportunity(conn, workspace_id=workspace_id, **opp_kwargs)
    return run2_id, opp


def _seed_draft_review_bed(
    conn: psycopg.Connection[Any],
    tag: str,
    *,
    draft_status: str = "pass",
) -> dict[str, Any]:
    base = seed_review_workspace(conn, tag, include_other_owner=True)
    ws_id = base["workspace_id"]
    creator_id = base["creator_id"]
    brand = base["brand"]
    run_id = base["run_id"]
    doc = base["document"]
    rec = base["record"]
    opp = base["opportunity"]
    brief = db.create_next_brief_version(
        conn,
        workspace_id=ws_id,
        opportunity_id=opp["id"],
        brand_profile_id=brand["id"],
        prompt_version="brief.md:v1",
        keywords=[{"keyword": "varejo optico", "search_volume": 1200, "is_estimate": False}],
        schema_version=1,
        payload={
            "topic": f"Expansão de lentes digitais ({tag})",
            "target_audience": "Gestores de óticas independentes",
            "angle": "Eficiência operacional com evidências de mercado",
            "primary_keyword": "varejo optico",
            "outline": ["Cenário", "Execução"],
            "cta_goal": "Conhecer guia operacional",
            "format": "blog",
            "brand_version": int(brand["version"]),
            "required_facts": [
                {"fact": "O varejo óptico cresceu 8% no 3T26.", "source_id": str(doc["id"])}
            ],
        },
    )
    draft_payload = {
        "schema_version": 1,
        "title": "Varejo Optico: como capturar o avanco em lentes digitais",
        "meta_description": (
            "Guia pratico sobre varejo optico para redes de oticas com dados do 3T26 "
            "e processos de atendimento padronizados para ampliar margem."
        ),
        "slug": "varejo-optico-lentes-digitais",
        "body_md": (
            "# Varejo Optico: como capturar o avanco em lentes digitais\n\n"
            "O mercado de varejo optico cresceu 8% no 3T26.\n\n"
            "## Proximos passos\n\nOrganize o catalogo e treine a equipe de balcao."
        ),
        "faq": [{"q": "Por que acompanhar o giro por grife?", "a": "Melhora margem e reposicao."}],
        "cta": "Solicite nosso checklist operacional gratuito.",
        "claims": [{"text": "O varejo óptico cresceu 8% no 3T26.", "source_id": str(doc["id"])}],
    }
    claims = [
        {"kind": "fact", "text": "O varejo óptico cresceu 8% no 3T26.", "document_id": doc["id"]},
        {"kind": "analysis", "text": "Lentes digitais elevam ticket médio.", "document_id": None},
    ]
    draft = db.create_next_draft_revision(
        conn,
        workspace_id=ws_id,
        brief_id=brief["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload=draft_payload,
        claims=claims,
        status=draft_status,
        revision=0,
    )
    db.upsert_quality_report(
        conn,
        workspace_id=ws_id,
        draft_id=draft["id"],
        verdict="pass" if draft_status == "pass" else "revision",
        score=Decimal("91.50"),
        checks={
            "language_match": {"check": "language_match", "passed": True, "severity": "critical"}
        },
        findings=[
            {
                "criterion": "clarity_and_structure",
                "severity": "minor",
                "detail": "Boa progressão entre cenário e execução.",
                "responsible_module": "write",
            }
        ],
        evaluator_model="fake-eval",
        prompt_version="evaluate.md:v1",
    )
    db.record_usage(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="brief",
        kind="llm",
        vendor="openrouter",
        model="google/gemini-2.5-flash",
        input_tokens=500,
        output_tokens=200,
        cost_usd=Decimal("0.012500"),
    )
    db.record_usage(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="write",
        kind="llm",
        vendor="openrouter",
        model="anthropic/claude-sonnet-4",
        input_tokens=1500,
        output_tokens=900,
        cost_usd=Decimal("0.042000"),
    )
    db.record_usage(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="evaluate",
        kind="llm",
        vendor="openrouter",
        model="openai/gpt-4.1",
        input_tokens=1200,
        output_tokens=300,
        cost_usd=Decimal("0.025000"),
    )
    return {
        "workspace_id": ws_id,
        "creator_id": creator_id,
        "other_user_id": base["other_user_id"],
        "campaign_id": base["campaign_id"],
        "run_id": run_id,
        "source": base["source"],
        "document": doc,
        "record": rec,
        "opportunity": opp,
        "brief": brief,
        "draft": draft,
    }


def test_decide_draft_approve_emits_event_and_queues_publish_f15_ac3(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F15 AC3: Campaign creator approves pass draft; status=approved and draft.decided emitted."""
    bed = _seed_draft_review_bed(db_conn, "appr-ac3")
    draft_id = bed["draft"]["id"]

    decided = services.decide_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=draft_id,
        decision="approve",
        user_id=bed["creator_id"],
        note="Texto alinhado com dados e tom da marca",
    )

    assert decided["status"] == "approved"
    assert decided["decided_by"] == bed["creator_id"]
    assert decided["decided_at"] is not None
    assert decided["decision_note"] == "Texto alinhado com dados e tom da marca"
    assert decided["publish_queued"] is True

    evts = db.list_events(
        db_conn,
        workspace_id=bed["workspace_id"],
        event_type="draft.decided",
        entity_id=draft_id,
    )
    assert len(evts) == 1
    assert evts[0]["actor_user_id"] == bed["creator_id"]
    assert evts[0]["payload"]["decision"] == "approved"
    assert evts[0]["payload"]["publish_queued"] is True
    assert evts[0]["payload"]["note"] == "Texto alinhado com dados e tom da marca"


def test_decide_draft_managed_conn_and_non_approver_refused_f15_ac5(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F15 AC5: Non-creator owner, viewer role, and non-member are refused with PermissionError."""
    bed = _seed_draft_review_bed(db_conn, "auth-ac5")
    draft_id = bed["draft"]["id"]

    with pytest.raises(PermissionError, match="forbidden"):
        services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=draft_id,
            decision="approved",
            user_id=bed["other_user_id"],
        )

    with pytest.raises(PermissionError, match="not a member"):
        services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=draft_id,
            decision="approved",
            user_id=uuid.uuid4(),
        )

    monkeypatch.setattr(services, "require_member", lambda *args, **kwargs: "viewer")
    with pytest.raises(PermissionError, match="forbidden"):
        services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=draft_id,
            decision="approved",
            user_id=bed["creator_id"],
        )
    monkeypatch.undo()

    # Managed conn=None succeeds for authorized campaign creator
    res = services.decide_draft(
        workspace_id=bed["workspace_id"],
        draft_id=draft_id,
        decision="approved",
        user_id=bed["creator_id"],
    )
    assert res["status"] == "approved"


def test_decide_draft_validation_and_guarded_transition(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Rejects invalid decisions, missing drafts, non-pass drafts, and re-decisions."""
    bed = _seed_draft_review_bed(db_conn, "guard")
    draft_id = bed["draft"]["id"]

    with pytest.raises(ValueError, match="Invalid decision"):
        services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=draft_id,
            decision="hold",
            user_id=bed["creator_id"],
        )

    with pytest.raises(ValueError, match="Draft .* not found"):
        services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=uuid.uuid4(),
            decision="approved",
            user_id=bed["creator_id"],
        )

    bed_draft = _seed_draft_review_bed(db_conn, "notpass", draft_status="draft")
    with pytest.raises(ValueError, match="expected 'pass'"):
        services.decide_draft(
            db_conn,
            workspace_id=bed_draft["workspace_id"],
            draft_id=bed_draft["draft"]["id"],
            decision="approved",
            user_id=bed_draft["creator_id"],
        )

    services.decide_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=draft_id,
        decision="approved",
        user_id=bed["creator_id"],
    )
    with pytest.raises(ValueError, match="expected 'pass'"):
        services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=draft_id,
            decision="declined",
            user_id=bed["creator_id"],
        )


def test_decide_draft_concurrent_race_atomic() -> None:
    """Two connections racing on the same pass draft: exactly one succeeds atomically."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as setup_conn:
        bed = _seed_draft_review_bed(setup_conn, "race")
        ws_id = bed["workspace_id"]
        draft_id = bed["draft"]["id"]
        creator_id = bed["creator_id"]

    results: list[dict[str, Any]] = []
    errors: list[Exception] = []

    def _race_worker(decision: str) -> None:
        try:
            with psycopg.connect(TEST_DATABASE_URL) as thread_conn:
                res = services.decide_draft(
                    thread_conn,
                    workspace_id=ws_id,
                    draft_id=draft_id,
                    decision=decision,
                    user_id=creator_id,
                    note=f"Race {decision}",
                )
                thread_conn.commit()
                results.append(res)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_race_worker, "approved")
        f2 = pool.submit(_race_worker, "declined")
        concurrent.futures.wait([f1, f2])

    assert len(results) == 1
    assert len(errors) == 1

    with psycopg.connect(TEST_DATABASE_URL) as verify_conn:
        evts = db.list_events(
            verify_conn, workspace_id=ws_id, event_type="draft.decided", entity_id=draft_id
        )
        assert len(evts) == 1


def test_decline_draft_excludes_story_and_cluster_from_future_reports_f15_ac4(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F15 AC4: Declined draft marks story and duplicate cluster non-recommended in later runs."""
    bed = _seed_draft_review_bed(db_conn, "decline-ac4")
    declined = services.decide_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=bed["draft"]["id"],
        decision="decline",
        user_id=bed["creator_id"],
        note="Angulo já abordado em campanha anterior",
    )
    assert declined["status"] == "declined"
    assert declined["decision_note"] == "Angulo já abordado em campanha anterior"
    assert declined["publish_queued"] is False

    evts = db.list_events(
        db_conn,
        workspace_id=bed["workspace_id"],
        event_type="draft.decided",
        entity_id=bed["draft"]["id"],
    )
    assert len(evts) == 1
    assert evts[0]["payload"]["decision"] == "declined"
    assert evts[0]["payload"]["publish_queued"] is False

    # Mark run 1 complete and create run 2 containing same-document + duplicate-cluster topics
    run2_id, opp_same = seed_follow_up_run_with_opp(
        db_conn,
        workspace_id=bed["workspace_id"],
        campaign_id=bed["campaign_id"],
        prev_run_id=bed["run_id"],
        document_id=bed["document"]["id"],
        created_by=bed["creator_id"],
        headline="Mesma notícia revisitada",
        total=Decimal("95.00"),
    )
    now = datetime.now(UTC)
    fresh_doc = db.upsert_document(
        db_conn,
        workspace_id=bed["workspace_id"],
        source_id=bed["source"]["id"],
        url=f"https://other-{uuid.uuid4().hex[:6]}.example.com/fresh",
        canonical_url=f"https://other-{uuid.uuid4().hex[:6]}.example.com/fresh",
        title="Nova notícia de lentes",
        published_at=now - timedelta(days=1),
        date_status="verified",
        fetch_status="ok",
    )
    rec_fresh = db.upsert_research_record(
        db_conn,
        workspace_id=bed["workspace_id"],
        run_id=run2_id,
        document_id=fresh_doc["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Tema inédito de armações", "summary": "Nova tendência."},
    )
    opp_fresh = db.upsert_opportunity(
        db_conn,
        workspace_id=bed["workspace_id"],
        run_id=run2_id,
        record_id=rec_fresh["id"],
        scores={"novelty": {"score": 8.0, "evidence": "Boa"}},
        total=Decimal("82.00"),
        rank=2,
        funnel_stage="tofu",
        confidence="high",
        angle="Design e estoque",
    )

    rep = services.report(db_conn, workspace_id=bed["workspace_id"], run_id=run2_id)
    by_id = {it["id"]: it for it in rep["items"]}
    assert by_id[opp_same["id"]]["is_rejected_story"] is True
    assert by_id[opp_same["id"]]["is_recommended"] is False
    assert by_id[opp_fresh["id"]]["is_rejected_story"] is False
    assert by_id[opp_fresh["id"]]["is_recommended"] is True


def test_list_draft_reviews_emits_awaiting_approval_and_loads_bundle_f15_ac1_ac2(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F15 AC1, AC2: Queue emits draft.awaiting_approval idempotently and returns provenance."""
    bed = _seed_draft_review_bed(db_conn, "bundle")
    ws_id = bed["workspace_id"]
    user_id = bed["creator_id"]
    draft_id = bed["draft"]["id"]

    # Add a revision under brief v2 to verify cross-brief revision history
    brief_v2 = db.create_next_brief_version(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=bed["opportunity"]["id"],
        brand_profile_id=bed["brief"]["brand_profile_id"],
        prompt_version="brief.md:v1",
        keywords=[],
        schema_version=1,
        payload=bed["brief"]["payload"],
    )
    draft_r1 = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws_id,
        brief_id=brief_v2["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload=bed["draft"]["payload"],
        claims=[
            {
                "kind": "fact",
                "text": "O varejo óptico cresceu 8% no 3T26.",
                "document_id": bed["document"]["id"],
            }
        ],
        status="pass",
        revision=1,
    )
    db.upsert_quality_report(
        db_conn,
        workspace_id=ws_id,
        draft_id=draft_r1["id"],
        verdict="revision",
        score=Decimal("78.00"),
        checks={},
        findings=[],
        evaluator_model="fake-eval",
        prompt_version="evaluate.md:v1",
    )

    queue = services.list_draft_reviews(db_conn, workspace_id=ws_id, user_id=user_id)
    assert len(queue) == 2
    assert any(item["id"] == draft_id for item in queue)

    evts_before = db.list_events(
        db_conn, workspace_id=ws_id, event_type="draft.awaiting_approval", entity_id=draft_id
    )
    assert len(evts_before) == 1

    # Second call does not duplicate draft.awaiting_approval
    services.list_draft_reviews(db_conn, workspace_id=ws_id, user_id=user_id)
    evts_after = db.list_events(
        db_conn, workspace_id=ws_id, event_type="draft.awaiting_approval", entity_id=draft_id
    )
    assert len(evts_after) == 1

    bundle = services.get_draft_review_bundle(
        db_conn, workspace_id=ws_id, draft_id=draft_r1["id"], user_id=user_id
    )
    assert bundle["draft"]["id"] == draft_r1["id"]
    assert bundle["surfaced_after_max_revisions"] is False
    r1_evts = db.list_events(
        db_conn, workspace_id=ws_id, event_type="draft.awaiting_approval", entity_id=draft_r1["id"]
    )
    assert len(r1_evts) == 1
    assert r1_evts[0]["payload"]["surfaced_after_max_revisions"] is False
    assert len(bundle["claims"]) == 1
    assert bundle["claims"][0]["document_title"].startswith("Pesquisa Mensal")
    assert bundle["claims"][0]["document_url"].endswith("/estudo-varejo")
    assert bundle["claims"][0]["document_published_at"] is not None
    assert bundle["claims"][0]["document_date_status"] == "verified"
    assert len(bundle["revision_history"]) == 2
    assert [r["revision"] for r in bundle["revision_history"]] == [0, 1]
    assert [r["brief_version"] for r in bundle["revision_history"]] == [1, 2]
    assert bundle["total_run_cost_usd"] == Decimal("0.079500")
    assert {c["stage"]: c["cost_usd"] for c in bundle["cost_breakdown"]} == {
        "brief": Decimal("0.012500"),
        "write": Decimal("0.042000"),
        "evaluate": Decimal("0.025000"),
    }

    # Revision 2 surfaced with verdict='revision' sets surfaced_after_max_revisions=True
    draft_r2 = db.create_next_draft_revision(
        db_conn,
        workspace_id=ws_id,
        brief_id=brief_v2["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload=bed["draft"]["payload"],
        claims=[],
        status="pass",
        revision=2,
    )
    db.upsert_quality_report(
        db_conn,
        workspace_id=ws_id,
        draft_id=draft_r2["id"],
        verdict="revision",
        score=Decimal("74.00"),
        checks={},
        findings=[],
        evaluator_model="fake-eval",
        prompt_version="evaluate.md:v1",
    )
    bundle_r2 = services.get_draft_review_bundle(
        workspace_id=ws_id, draft_id=draft_r2["id"], user_id=user_id
    )
    assert bundle_r2["surfaced_after_max_revisions"] is True
    r2_evts = db.list_events(
        db_conn, workspace_id=ws_id, event_type="draft.awaiting_approval", entity_id=draft_r2["id"]
    )
    assert len(r2_evts) == 1
    assert r2_evts[0]["payload"]["surfaced_after_max_revisions"] is True

    # Approve one draft and verify include_decided=False / True and bundle flags
    _assert_decided_and_max_rev_filtering(
        db_conn,
        ws_id=ws_id,
        user_id=user_id,
        approved_draft_id=draft_id,
        pending_ids=(draft_r1["id"], draft_r2["id"]),
    )

    with pytest.raises(ValueError, match="Draft .* not found"):
        services.get_draft_review_bundle(
            db_conn, workspace_id=ws_id, draft_id=uuid.uuid4(), user_id=user_id
        )


def _assert_decided_and_max_rev_filtering(
    db_conn: psycopg.Connection[Any],
    *,
    ws_id: uuid.UUID,
    user_id: uuid.UUID,
    approved_draft_id: uuid.UUID,
    pending_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    r1_id, r2_id = pending_ids
    services.decide_draft(
        db_conn,
        workspace_id=ws_id,
        draft_id=approved_draft_id,
        decision="approved",
        user_id=user_id,
    )
    pending_only = services.list_draft_reviews(
        workspace_id=ws_id, user_id=user_id, include_decided=False
    )
    pending_by_id = {row["id"]: row for row in pending_only}
    assert approved_draft_id not in pending_by_id
    assert pending_by_id[r1_id]["surfaced_after_max_revisions"] is False
    assert pending_by_id[r2_id]["surfaced_after_max_revisions"] is True

    all_rows = services.list_draft_reviews(
        db_conn, workspace_id=ws_id, user_id=user_id, include_decided=True
    )
    decided_row = next(row for row in all_rows if row["id"] == approved_draft_id)
    assert decided_row["status"] == "approved"
    assert decided_row["surfaced_after_max_revisions"] is False

    approved_bundle = services.get_draft_review_bundle(
        db_conn, workspace_id=ws_id, draft_id=approved_draft_id, user_id=user_id
    )
    assert approved_bundle["draft"]["status"] == "approved"
    assert approved_bundle["surfaced_after_max_revisions"] is False


def test_produce_and_evaluate_draft_emits_awaiting_approval_and_attaches_latest_draft(
    db_conn: psycopg.Connection[Any],
) -> None:
    """produce_and_evaluate_draft runs write + evaluate offline and emits awaiting_approval."""
    bed = _seed_draft_review_bed(db_conn, "produce", draft_status="draft")
    # Create clean opportunity without prior draft so revision starts at 0
    fresh_doc = db.upsert_document(
        db_conn,
        workspace_id=bed["workspace_id"],
        source_id=bed["source"]["id"],
        url=f"https://fresh-{uuid.uuid4().hex[:6]}.example.com/data",
        canonical_url=f"https://fresh-{uuid.uuid4().hex[:6]}.example.com/data",
        title="Relatório Óptico Brasil",
        published_at=datetime.now(UTC) - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
        meta={"facts": ["Mercado óptico cresceu 10% em 2026."]},
    )
    rec = db.upsert_research_record(
        db_conn,
        workspace_id=bed["workspace_id"],
        run_id=bed["run_id"],
        document_id=fresh_doc["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Lentes de alta margem", "summary": "Crescimento em redes."},
    )
    opp = db.upsert_opportunity(
        db_conn,
        workspace_id=bed["workspace_id"],
        run_id=bed["run_id"],
        record_id=rec["id"],
        scores={"trend": {"score": 9.0, "evidence": "Forte"}},
        total=Decimal("90.00"),
        rank=2,
        funnel_stage="mofu",
        confidence="high",
        angle="Estratégia",
        status="approved",
        decided_by=bed["creator_id"],
        decided_at=datetime.now(UTC),
    )
    brief = services.generate_brief(
        db_conn,
        workspace_id=bed["workspace_id"],
        opportunity_id=opp["id"],
        user_id=bed["creator_id"],
        offline=True,
    )
    res = services.produce_and_evaluate_draft(
        workspace_id=bed["workspace_id"],
        brief_id=brief["id"],
        user_id=bed["creator_id"],
        offline=True,
    )
    assert res["status"] == "pass"
    evts = db.list_events(
        db_conn,
        workspace_id=bed["workspace_id"],
        event_type="draft.awaiting_approval",
        entity_id=res["final_draft_id"],
    )
    assert len(evts) == 1

    rep = services.report(db_conn, workspace_id=bed["workspace_id"], run_id=bed["run_id"])
    item = next(it for it in rep["items"] if it["id"] == opp["id"])
    assert item["latest_draft"] is not None
    assert item["latest_draft"]["id"] == res["final_draft_id"]
    assert item["latest_draft"]["status"] == "pass"
