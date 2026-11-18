"""Tests for article writer and claim provenance (M4-01, F13 AC1-AC3, AC5, ADR-002, ADR-010)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
import yaml
from pydantic import ValidationError

from engine import db, services
from engine.contracts import (
    BrandProfile,
    ContentDraft,
    DraftClaim,
    EditorialBrief,
    Finding,
)
from engine.integrations.fakes import FakeExtractor, FakeKeywordProvider, FakeLLM, FakeNewsSearch
from engine.production import (
    DraftRevisionLimitReachedError,
    InvalidDraftProvenanceError,
    write,
    write_draft_with_prompt,
    writer,
)
from engine.production.writer import (
    _normalize_finding_entry,
    _resolve_brief_evidence_docs,
    build_write_system_prompt,
    get_write_prompt_and_version,
    validate_draft_claim_provenance,
)
from engine.strategy import brief as create_brief
from engine.strategy.brief import (
    _coerce_mapping,
    _safe_load_format_pack,
    _safe_load_industry_pack,
)


def _seed_workspace_and_brief(
    conn: psycopg.Connection[Any],
    *,
    locale: str = "pt-BR",
    market: str = "BR",
    funnel_stage: str = "mofu",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:8]
    user = db.create_or_get_user(conn, email=f"writer-{tag}@example.com", name="Writer Owner")
    user_id = uuid.UUID(str(user["user_id"]))
    ws_id = db.create_workspace(
        conn,
        slug=f"ws-wr-{tag}",
        name=f"Writer Workspace {tag}",
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

    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name=f"Campaign {tag}",
        locale=locale,
        market=market,
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("15.00"),
        config={"goal": "authority"},
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
        run_cap_usd=Decimal("15.00"),
        code_version="v1.0.0",
        pack_version="pack-v1",
        created_by=user_id,
    )

    data_src = db.create_source(
        conn,
        workspace_id=ws_id,
        industry_pack="general",
        name=f"IBGE {tag}",
        domain=f"ibge-{tag}.gov.br",
        url=f"https://ibge-{tag}.gov.br",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="data",
        locales=[locale],
        topics=["optica", "varejo"],
        enabled=True,
    )
    doc_primary = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=data_src["id"],
        url=f"https://ibge-{tag}.gov.br/pmc-2026",
        canonical_url=f"https://ibge-{tag}.gov.br/pmc-2026",
        title="Pesquisa Mensal de Comércio Optical 2026",
        published_at=now - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
        extracted_text="As vendas do varejo óptico cresceram 6,4% em 12 meses.",
        meta={
            "source_name": "IBGE",
            "source_role": "data",
            "summary": "Crescimento de 6,4% nas vendas ópticas.",
            "facts": ["Vendas do varejo óptico cresceram 6,4% em 12 meses."],
        },
    )
    doc_secondary = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=data_src["id"],
        url=f"https://ibge-{tag}.gov.br/estoque-2026",
        canonical_url=f"https://ibge-{tag}.gov.br/estoque-2026",
        title="Indicadores de Giro de Estoque em Óticas",
        published_at=now - timedelta(days=1),
        date_status="verified",
        fetch_status="ok",
        extracted_text="Armações classe C representam 38% do capital parado.",
        meta={
            "source_name": "IBGE",
            "source_role": "data",
            "summary": "Armações classe C retêm 38% do estoque.",
            "facts": ["Armações classe C representam 38% do capital parado."],
        },
    )
    rr = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc_primary["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": "Gestão de estoque e giro de armações em óticas",
            "summary": "Indicadores mostram ganho de margem com curva ABC.",
            "facts": ["Vendas do varejo óptico cresceram 6,4% em 12 meses."],
            "entities": ["gestao de estoque otica", "curva abc armacoes"],
        },
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rr["id"],
        scores={"novelty": {"score": 8.5, "evidence": "PMC"}},
        total=Decimal("84.00"),
        funnel_stage=funnel_stage,
        confidence="high",
        angle="Aplicação prática de curva ABC no estoque da ótica",
        status="approved",
        decided_by=user_id,
        decided_at=now,
        decision_note="Priorizar exemplos práticos de giro mensal",
    )
    brief_row = create_brief(
        opp["id"],
        conn=conn,
        workspace_id=ws_id,
        actor_user_id=user_id,
        offline=True,
        llm_adapter=FakeLLM(),
        keyword_provider=FakeKeywordProvider(),
        news_search_adapter=FakeNewsSearch(),
        extractor_adapter=FakeExtractor(),
    )
    return {
        "workspace_id": ws_id,
        "user_id": user_id,
        "campaign_id": camp_id,
        "run_id": run_id,
        "brand_row": brand_row,
        "doc_primary": doc_primary,
        "doc_secondary": doc_secondary,
        "opportunity": opp,
        "brief_row": brief_row,
    }


def test_write_prompt_versioning_and_system_prompt_builder() -> None:
    """Verify write_blog.md loads with deterministic SHA-256 hash and builds system prompt."""
    prompt_body, version_tag = get_write_prompt_and_version()
    assert len(prompt_body) > 200
    assert version_tag.startswith("write_blog.md:")
    assert len(version_tag.split(":")[1]) == 16

    brand_payload = yaml.safe_load(Path("fixtures/seed_example.yaml").read_text(encoding="utf-8"))
    brand = BrandProfile.model_validate(brand_payload)
    sys_prompt = build_write_system_prompt(
        base_prompt=prompt_body,
        brand_profile=brand,
        brand_version=2,
        industry_pack={"name": "Optical Retail", "slug": "general", "description": "Optics"},
        format_pack={"slug": "blog", "min_words": 800, "max_words": 2500, "structure": ["title"]},
    )
    assert "Pinned Brand Version: v2" in sys_prompt
    assert "Industry: Optical Retail" in sys_prompt
    assert "Word Range: 800-2500" in sys_prompt

    # Verify fallback when industry_pack is omitted or non-mapping values are coerced
    sys_no_ind = build_write_system_prompt(
        base_prompt=prompt_body,
        brand_profile=brand.model_dump(mode="json"),
        brand_version=1,
        industry_pack=None,
        format_pack=None,
    )
    assert "## Industry Context" not in sys_no_ind
    assert _coerce_mapping("not-a-dict") == {}
    assert _safe_load_industry_pack(None) == {}
    assert _safe_load_industry_pack("non-existent-pack-slug") == {"slug": "non-existent-pack-slug"}
    fallback_fmt = _safe_load_format_pack("non-existent-format-slug")
    assert fallback_fmt["slug"] == "non-existent-format-slug"

    with (
        patch("engine.production.writer.PROMPT_FILE", Path("non_existent_write_prompt.md")),
        pytest.raises(FileNotFoundError, match="Writer prompt template missing"),
    ):
        get_write_prompt_and_version()


def test_write_creates_revision_0_claims_and_usage_ledger(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F13 AC1, AC2, AC5: revision 0 draft, draft_claims rows, and write usage_ledger."""
    ctx = _seed_workspace_and_brief(db_conn, locale="pt-BR", market="BR")
    ws_id = ctx["workspace_id"]
    brief_row = ctx["brief_row"]

    draft_row = write(
        brief_row,
        conn=db_conn,
        workspace_id=ws_id,
        actor_user_id=ctx["user_id"],
        offline=True,
    )
    assert draft_row["workspace_id"] == ws_id
    assert draft_row["brief_id"] == brief_row["id"]
    assert draft_row["revision"] == 0
    assert draft_row["status"] == "draft"
    assert str(draft_row["prompt_version"]).startswith("write_blog.md:")

    # AC1: validates against ContentDraft
    draft_model = ContentDraft.model_validate(draft_row["payload"])
    assert draft_model.title
    assert draft_model.slug
    assert len(draft_model.body_md.split()) >= 200
    assert len(draft_model.faq) >= 2
    assert draft_model.cta
    assert len(draft_model.claims) >= 3

    # AC2: each claim written to draft_claims with fact document_id populated
    stored_claims = db.list_draft_claims(
        db_conn, workspace_id=ws_id, draft_id=uuid.UUID(str(draft_row["id"]))
    )
    assert len(stored_claims) == len(draft_model.claims)
    assert [int(c["idx"]) for c in stored_claims] == list(range(len(draft_model.claims)))
    fact_rows = [c for c in stored_claims if c["kind"] == "fact"]
    assert len(fact_rows) >= 1
    for fr in fact_rows:
        assert fr["document_id"] is not None

    # AC5: recorded in usage_ledger with stage='write' and prompt_version
    usage_rows = db.list_usage(db_conn, workspace_id=ws_id)
    write_usage = [u for u in usage_rows if u["stage"] == "write"]
    assert len(write_usage) == 1
    assert write_usage[0]["prompt_version"] == draft_row["prompt_version"]
    assert write_usage[0]["run_id"] == ctx["run_id"]
    payload_record = db.get_llm_payload(
        db_conn, workspace_id=ws_id, usage_id=uuid.UUID(str(write_usage[0]["id"]))
    )
    assert payload_record is not None

    # Audit event emitted
    evts = db.list_events(db_conn, workspace_id=ws_id)
    draft_evts = [e for e in evts if e["type"] == "draft.created"]
    assert len(draft_evts) == 1
    assert draft_evts[0]["payload"]["revision"] == 0


def test_write_fact_claim_without_document_or_invalid_source_rejected(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F13 AC2: a fact claim without document_id or with unknown/cross-tenant ID fails."""
    ctx = _seed_workspace_and_brief(db_conn, locale="en-US", market="US", funnel_stage="tofu")
    ws_id = ctx["workspace_id"]
    brief_row = ctx["brief_row"]

    # 1. Pydantic contract rejects fact without source_id
    with pytest.raises(ValidationError, match="Facts must have a source_id"):
        DraftClaim(text="Stat without source", kind="fact", source_id=None)

    # 2. Database CHECK constraint refuses a fact claim with document_id = NULL (SQLSTATE 23514)
    with pytest.raises(psycopg.errors.CheckViolation) as exc_db:
        db.create_next_draft_revision(
            db_conn,
            workspace_id=ws_id,
            brief_id=uuid.UUID(str(brief_row["id"])),
            prompt_version="write_blog.md:test",
            schema_version=1,
            payload={"title": "Invalid Fact Draft"},
            claims=[
                {
                    "kind": "fact",
                    "text": "Unsourced statistic directly to DB",
                    "document_id": None,
                }
            ],
        )
    assert exc_db.value.sqlstate == "23514"
    # Transaction rolled back: no draft persisted
    assert (
        db.get_latest_draft_for_brief(
            db_conn, workspace_id=ws_id, brief_id=uuid.UUID(str(brief_row["id"]))
        )
        is None
    )

    # 3. Malformed or non-existent UUID in LLM response is rejected before/during persistence
    bad_uuid_llm = FakeLLM(
        settings=FakeLLM.Settings(
            default_response={
                "title": "Valid Title",
                "meta_description": "Valid meta description for testing claim provenance checks.",
                "slug": "valid-title",
                "body_md": "# Valid Title\n\nBody text.",
                "faq": [{"question": "Q?", "answer": "A."}],
                "cta": {"headline": "CTA", "body": "Body", "button_text": "Go"},
                "claims": [
                    {
                        "text": "Fact with malformed UUID",
                        "kind": "fact",
                        "source_id": "not-a-uuid",
                    }
                ],
            }
        )
    )
    with pytest.raises(InvalidDraftProvenanceError, match="Invalid document UUID"):
        write(
            brief_row["id"],
            conn=db_conn,
            workspace_id=ws_id,
            llm_adapter=bad_uuid_llm,
        )

    unknown_doc_llm = FakeLLM(
        settings=FakeLLM.Settings(
            default_response={
                "title": "Valid Title",
                "meta_description": "Valid meta description for testing unknown document ID.",
                "slug": "valid-title",
                "body_md": "# Valid Title\n\nBody text.",
                "faq": [{"question": "Q?", "answer": "A."}],
                "cta": {"headline": "CTA", "body": "Body", "button_text": "Go"},
                "claims": [
                    {
                        "text": "Fact with unknown document UUID",
                        "kind": "fact",
                        "source_id": str(uuid.uuid4()),
                    }
                ],
            }
        )
    )
    with pytest.raises(InvalidDraftProvenanceError, match="not found in workspace"):
        write(
            brief_row["id"],
            conn=db_conn,
            workspace_id=ws_id,
            llm_adapter=unknown_doc_llm,
        )

    # Pure helper validation with valid_document_ids set
    draft_with_missing = ContentDraft.model_validate(unknown_doc_llm.settings.default_response)
    with pytest.raises(InvalidDraftProvenanceError, match="not present in workspace"):
        validate_draft_claim_provenance(
            draft_with_missing,
            valid_document_ids={uuid.UUID(str(ctx["doc_primary"]["id"]))},
        )

    # Bypassing model validator via model_construct still catches missing source_id on fact
    bypassed_claim = DraftClaim.model_construct(text="Bypassed", kind="fact", source_id=None)
    bypassed_draft = ContentDraft.model_construct(
        title="T",
        meta_description="M",
        slug="t",
        body_md="# T",
        faq=[],
        cta={},
        claims=[bypassed_claim],
    )
    with pytest.raises(InvalidDraftProvenanceError, match="missing a source_id"):
        validate_draft_claim_provenance(bypassed_draft)


def test_write_revisions_0_to_2_and_revision_3_impossible(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F13 AC3: revisions 0, 1, 2 include evaluator findings; revision 3 is rejected."""
    ctx = _seed_workspace_and_brief(db_conn, locale="pt-BR", market="BR", funnel_stage="bofu")
    ws_id = ctx["workspace_id"]
    user_id = ctx["user_id"]
    brief_id = uuid.UUID(str(ctx["brief_row"]["id"]))

    # Revision 0 via services.write_draft
    rev0 = services.write_draft(
        db_conn,
        workspace_id=ws_id,
        brief_id=brief_id,
        user_id=user_id,
        offline=True,
    )
    assert rev0["revision"] == 0

    # Revision 1 via services.revise_draft with Finding model + dict + str
    findings_r1: list[Any] = [
        Finding(
            criterion="search_optimization",
            severity="major",
            detail="Reforçar palavra-chave no subtítulo H2 inicial",
            responsible_module="write",
        ),
        {
            "criterion": "clarity_and_structure",
            "severity": "minor",
            "detail": "Detalhar rotina semanal de contagem física",
            "responsible_module": "write",
        },
    ]
    rev1 = services.revise_draft(
        db_conn,
        workspace_id=ws_id,
        brief_id=brief_id,
        user_id=user_id,
        findings=findings_r1,
        offline=True,
    )
    assert rev1["revision"] == 1
    assert "Reforçar palavra-chave no subtítulo H2 inicial" in rev1["payload"]["body_md"]

    # Revision 2 via services.revise_draft with string finding
    rev2 = services.revise_draft(
        db_conn,
        workspace_id=ws_id,
        brief_id=brief_id,
        user_id=user_id,
        findings=["Ajustar chamada final do CTA consultivo"],
        offline=True,
    )
    assert rev2["revision"] == 2
    assert "Ajustar chamada final do CTA consultivo" in rev2["payload"]["body_md"]

    # All 3 revisions (0, 1, 2) are preserved in history
    all_drafts = services.list_drafts(
        db_conn, workspace_id=ws_id, user_id=user_id, brief_id=brief_id
    )
    assert [int(d["revision"]) for d in all_drafts] == [2, 1, 0]
    latest = services.get_latest_draft(
        db_conn, workspace_id=ws_id, brief_id=brief_id, user_id=user_id
    )
    assert latest is not None
    assert latest["id"] == rev2["id"]

    # Revision 3 is impossible (F13 AC3)
    with pytest.raises(DraftRevisionLimitReachedError, match="maximum revision 2"):
        services.revise_draft(
            db_conn,
            workspace_id=ws_id,
            brief_id=brief_id,
            user_id=user_id,
            findings=["Tentativa extra além do limite"],
            offline=True,
        )

    # Also verify direct DB call when already at revision 2 raises CheckViolation (23514)
    with pytest.raises(psycopg.errors.CheckViolation) as exc_chk:
        db.create_next_draft_revision(
            db_conn,
            workspace_id=ws_id,
            brief_id=brief_id,
            prompt_version="write_blog.md:v1",
            schema_version=1,
            payload=rev2["payload"],
            claims=[],
        )
    assert exc_chk.value.sqlstate == "23514"


def test_write_service_guards_and_edge_cases(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify service permission checks, missing brief errors, managed conn, and helpers."""
    ctx = _seed_workspace_and_brief(db_conn, locale="en-US", market="US")
    ws_id = ctx["workspace_id"]
    user_id = ctx["user_id"]
    brief_id = uuid.UUID(str(ctx["brief_row"]["id"]))

    outsider = db.create_or_get_user(
        db_conn, email=f"outsider-{uuid.uuid4().hex[:6]}@example.com", name="Outsider"
    )
    outsider_id = uuid.UUID(str(outsider["user_id"]))

    with pytest.raises(PermissionError):
        services.write_draft(
            db_conn, workspace_id=ws_id, brief_id=brief_id, user_id=outsider_id, offline=True
        )

    with pytest.raises(ValueError, match="workspace_id is required"):
        write(brief_id, conn=db_conn, workspace_id=None)

    with pytest.raises(ValueError, match="not found in workspace"):
        write(uuid.uuid4(), conn=db_conn, workspace_id=ws_id, offline=True)

    with pytest.raises(ValueError, match="not found in workspace"):
        db.create_next_draft_revision(
            db_conn,
            workspace_id=ws_id,
            brief_id=uuid.uuid4(),
            prompt_version="write_blog.md:v1",
            schema_version=1,
            payload={},
            claims=[],
        )

    with pytest.raises(ValueError, match="Revision findings cannot be empty"):
        services.revise_draft(
            db_conn,
            workspace_id=ws_id,
            brief_id=brief_id,
            user_id=user_id,
            findings=[],
            offline=True,
        )

    with pytest.raises(ValueError, match="no prior draft revision exists"):
        services.revise_draft(
            db_conn,
            workspace_id=ws_id,
            brief_id=brief_id,
            user_id=user_id,
            findings=["Fix intro"],
            offline=True,
        )

    # Test _normalize_finding_entry with dict, Finding, and string
    assert _normalize_finding_entry("Simple note")["detail"] == "Simple note"
    assert (
        _normalize_finding_entry({"detail": "Dict note", "severity": "critical"})["severity"]
        == "critical"
    )

    # Test _resolve_brief_evidence_docs when primary doc is not in required_facts
    brief_ctx = db.get_brief_writing_context(db_conn, workspace_id=ws_id, brief_id=brief_id)
    assert brief_ctx is not None
    brief_no_facts = EditorialBrief(
        topic="Topic",
        target_audience="Audience",
        primary_keyword=" eyewear ",
        angle="Angle",
        outline=["S1", "S2", "S3", "S4"],
        required_facts=[],
        cta_goal="CTA",
    )
    resolved_docs = _resolve_brief_evidence_docs(
        db_conn, ws_id=ws_id, brief_ctx=brief_ctx, brief_model=brief_no_facts
    )
    assert any(d["id"] == str(ctx["doc_primary"]["id"]) for d in resolved_docs)


def test_write_managed_connections_and_pure_helper(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify managed connection paths (conn=None) and write_draft_with_prompt pure helper."""
    ctx = _seed_workspace_and_brief(db_conn, locale="en-US", market="US")
    ws_id = ctx["workspace_id"]
    user_id = ctx["user_id"]
    brief_id = uuid.UUID(str(ctx["brief_row"]["id"]))

    # Test write_draft_with_prompt pure helper with findings and analysis/recommendation claims
    brand_model = BrandProfile.model_validate(ctx["brand_row"]["payload"])
    synthetic_brief = {**ctx["brief_row"]["payload"], "required_facts": []}
    pure_draft = write_draft_with_prompt(
        brief=synthetic_brief,
        evidence_documents=[ctx["doc_primary"]],
        brand_profile=brand_model.model_dump(mode="json"),
        brand_version=1,
        locale="en-US",
        revision=1,
        findings=["Include clinical workflow steps"],
        llm_adapter=FakeLLM(),
    )
    assert "Include clinical workflow steps" in pure_draft.body_md

    # Test managed connection paths (conn=None) by patching db.get_connection
    from contextlib import contextmanager

    @contextmanager
    def _fake_get_conn() -> Any:
        yield db_conn

    with patch("engine.db.get_connection", _fake_get_conn):
        d_row = write(brief_id, workspace_id=ws_id, offline=True)
        d_id = uuid.UUID(str(d_row["id"]))
        fetched = services.get_draft(workspace_id=ws_id, draft_id=d_id, user_id=user_id)
        assert fetched is not None and fetched["id"] == d_id
        latest_d = services.get_latest_draft(workspace_id=ws_id, brief_id=brief_id, user_id=user_id)
        assert latest_d is not None and latest_d["id"] == d_id
        listed_d = services.list_drafts(workspace_id=ws_id, user_id=user_id, brief_id=brief_id)
        assert len(listed_d) == 1
        claims_d = services.list_draft_claims(workspace_id=ws_id, draft_id=d_id, user_id=user_id)
        assert len(claims_d) >= 1
        r1_row = services.revise_draft(
            workspace_id=ws_id,
            brief_id=brief_id,
            user_id=user_id,
            findings=["Refine conclusion"],
            offline=True,
        )
        assert r1_row["revision"] == 1

    # Also test services.write_draft with conn=None on a fresh brief
    ctx2 = _seed_workspace_and_brief(db_conn, locale="pt-BR", market="BR")
    with patch("engine.db.get_connection", _fake_get_conn):
        d2 = services.write_draft(
            workspace_id=ctx2["workspace_id"],
            brief_id=uuid.UUID(str(ctx2["brief_row"]["id"])),
            user_id=ctx2["user_id"],
            offline=True,
        )
        assert d2["revision"] == 0
    assert (
        services.get_draft(
            db_conn,
            workspace_id=ctx2["workspace_id"],
            draft_id=uuid.UUID(str(d2["id"])),
            user_id=ctx2["user_id"],
        )
        is not None
    )
    assert (
        len(
            services.list_draft_claims(
                db_conn,
                workspace_id=ctx2["workspace_id"],
                draft_id=uuid.UUID(str(d2["id"])),
                user_id=ctx2["user_id"],
            )
        )
        >= 1
    )


def test_parse_markdown_draft_extracts_frontmatter_and_defaults() -> None:
    """Verify M5-07: parse_markdown_draft handles YAML frontmatter and plain markdown."""
    md_with_fm = (
        "---\n"
        "title: Controle de Estoque em Oticas\n"
        "slug: controle-de-estoque-em-oticas\n"
        "meta_description: Aprenda como reduzir perdas e aumentar o giro de armacoes.\n"
        "---\n\n"
        "# Controle de Estoque em Oticas\n\n"
        "Texto principal do artigo com detalhes praticos.\n"
    )
    parsed = writer.parse_markdown_draft(md_with_fm)
    assert parsed.title == "Controle de Estoque em Oticas"
    assert parsed.slug == "controle-de-estoque-em-oticas"
    assert "reduzir perdas" in parsed.meta_description
    assert "Texto principal do artigo" in parsed.body_md

    plain_md = "# Guia Rapido de Vendas\n\nPrimeiro paragrafo sobre atendimento consultivo em loja."
    plain_parsed = writer.parse_markdown_draft(plain_md)
    assert plain_parsed.title == "Guia Rapido de Vendas"
    assert plain_parsed.slug == "guia-rapido-de-vendas"
    assert "atendimento consultivo" in plain_parsed.body_md


def test_extract_file_to_markdown_guards_and_pdf_support(tmp_path: Path) -> None:
    """Verify M5-07: extract_file_to_markdown enforces size/page limits and reads .md/.pdf."""
    md_file = tmp_path / "draft.md"
    md_file.write_text("# Titulo\n\nConteudo em markdown.", encoding="utf-8")
    assert "Conteudo em markdown." in writer.extract_file_to_markdown(md_file)

    bad_ext = tmp_path / "notes.docx"
    bad_ext.write_text("dummy", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file extension"):
        writer.extract_file_to_markdown(bad_ext)

    huge_md = tmp_path / "huge.md"
    huge_md.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="10 MB"):
        writer.extract_file_to_markdown(huge_md)

    import pymupdf

    pdf_ok = tmp_path / "sample.pdf"
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    page.insert_text((72, 72), "Optical Retail Benchmark 2026")
    doc.save(pdf_ok)  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    extracted_pdf = writer.extract_file_to_markdown(pdf_ok)
    assert "Optical Retail Benchmark 2026" in extracted_pdf

    pdf_too_long = tmp_path / "long.pdf"
    long_doc = pymupdf.open()  # type: ignore[no-untyped-call]
    for _ in range(51):
        long_doc.new_page()
    long_doc.save(pdf_too_long)  # type: ignore[no-untyped-call]
    long_doc.close()  # type: ignore[no-untyped-call]
    with pytest.raises(ValueError, match="50 pages"):
        writer.extract_file_to_markdown(pdf_too_long)
