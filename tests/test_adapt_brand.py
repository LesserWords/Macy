"""Tests for M11-02: pass-by-value adapt_brand rewrite, uniqueness gate, and services."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
import yaml

from engine import config, db, services
from engine.contracts import BrandProfile, ContentDraft, DraftClaim
from engine.integrations.fakes import FakeLLM
from engine.production.rewrite import rewrite_draft
from engine.quality.checks import check_adaptation_uniqueness


def _sample_source_draft(*, doc_id: str | None = None) -> ContentDraft:
    claim_id = doc_id or str(uuid.uuid4())
    body = (
        "# Emissão de NF-e e NFC-e no fechamento da venda no varejo\n\n"
        "No varejo em geral, integrar a emissão de NF-e e NFC-e ao PDV reduz retrabalho "
        "no fechamento de caixa e evita filas no balcão. O Core ERP centraliza TEF, PIX, "
        "importação de XML de fornecedores e DRE gerencial para lojas e distribuidoras.\n\n"
        "## Fluxo fiscal no caixa da loja\n\n"
        "Conferir CPF ou CNPJ no início do atendimento reduz rejeições da SEFAZ e mantém "
        "o controle financeiro alinhado ao estoque por código de barras.\n"
    )
    # Pad above 800 words cleanly for standard blog checks
    pad = " ".join(
        [
            "Processo estruturado de vendas no balcão garante rastreabilidade fiscal e financeira "
            "para gestores de comércio varejista e atacado com conferência contábil regular."
        ]
        * 45
    )
    return ContentDraft(
        title="Emissão de NF-e e NFC-e no fechamento da venda no varejo",
        meta_description=(
            "Guia prático sobre emissão de NF-e e NFC-e no PDV do varejo para evitar filas, "
            "rejeições da SEFAZ e falhas no fechamento financeiro."
        ),
        slug="emissao-nfe-nfce-fechamento-venda-varejo",
        body_md=f"{body}\n{pad}",
        faq=[
            {
                "question": "Qual a diferença entre NF-e e NFC-e no varejo?",
                "answer": "A NFC-e atende venda ao consumidor no balcão e a NF-e operações B2B.",
            },
            {
                "question": "A integração fiscal dispensa o contador?",
                "answer": "Não. A contabilidade segue responsável pelo enquadramento tributário.",
            },
        ],
        cta={
            "headline": "Organize o PDV da sua loja",
            "body": "Baixe o checklist de fechamento fiscal para varejo com o Core ERP.",
            "button_text": "Baixar checklist fiscal",
            "intensity": "medium",
        },
        claims=[
            DraftClaim(
                text="Integrar a emissão fiscal ao fechamento comercial reduz retrabalho.",
                kind="fact",
                source_id=claim_id,
            ),
            DraftClaim(
                text="Checklist diário apoia conferência de caixa.",
                kind="recommendation",
                source_id=None,
            ),
        ],
    )


def _target_vertical_brand() -> BrandProfile:
    return BrandProfile(
        brand_name="Core Specialty ERP",
        target_persona="Donos de lojas especializadas e laboratórios técnicos",
        canonical_domain="vertical.example.com",
        voice="Consultivo e técnico para gestores do varejo especializado",
        positioning=(
            "Core Specialty ERP é o sistema ERP e PDV para lojas especializadas e laboratórios."
        ),
        capabilities=[
            "Frente de caixa (PDV) com TEF, PIX e emissão fiscal NF-e e NFC-e",
            "Controle de Ordens de Serviço (O.S.) de laboratório especializado",
        ],
        prohibited_claims=["Prometer aumento garantido de receita"],
        prohibited_phrases=[r"\bCore ERP\b"],
        cta_rules=["MOFU: oferecer checklist prático de O.S. e PDV do Core Specialty ERP"],
    )


def test_rewrite_draft_adapt_brand_with_fake_llm() -> None:
    """rewrite_draft(mode='adapt_brand') adapts headline, slug, body, CTA, and keeps facts."""
    source = _sample_source_draft()
    target_brand = _target_vertical_brand()
    adapted = rewrite_draft(
        source,
        mode="adapt_brand",
        target_brand=target_brand,
        target_industry={
            "name": "Specialty Retail",
            "slug": "specialty-retail",
            "description": "Lojas especializadas e laboratórios.",
        },
        locale="pt-BR",
        llm_adapter=FakeLLM(),
    )

    assert adapted.slug != source.slug
    assert adapted.title != source.title
    assert "Core Specialty ERP" in adapted.body_md
    assert "Core ERP" not in adapted.body_md
    assert any(c.kind == "fact" and c.source_id for c in adapted.claims)
    assert check_adaptation_uniqueness(source, adapted).passed

    # Also exercise en-US path in FakeLLM._build_fake_adapted_draft
    adapted_en = rewrite_draft(
        source,
        mode="adapt_brand",
        target_brand=target_brand,
        target_industry={"name": "Specialty Retail", "slug": "specialty-retail"},
        locale="en-US",
        llm_adapter=FakeLLM(),
    )
    assert "Core Specialty ERP" in adapted_en.body_md
    assert "core-specialty-erp" in adapted_en.slug
    assert adapted_en.slug != source.slug


def test_check_adaptation_uniqueness_thresholds() -> None:
    """Anti-cannibalization gate rejects identical slugs/titles or excessive shingle overlap."""
    source = _sample_source_draft()
    same_slug = source.model_copy(update={"title": "Título diferente"})
    res_slug = check_adaptation_uniqueness(source, same_slug)
    assert not res_slug.passed
    assert res_slug.check == "adaptation_uniqueness"

    same_title = source.model_copy(update={"slug": "slug-completamente-novo"})
    assert not check_adaptation_uniqueness(source, same_title).passed

    high_overlap = source.model_copy(
        update={
            "title": "Emissão de NF-e e NFC-e na loja especializada",
            "slug": "emissao-nfe-nfce-loja-especializada",
        }
    )
    res_overlap = check_adaptation_uniqueness(source, high_overlap, max_similarity=0.65)
    assert not res_overlap.passed
    assert res_overlap.metadata["similarity"] > 0.65

    # Short-text shingle fallback (< width=3 words)
    short_a = source.model_copy(
        update={"title": "Curto A", "slug": "curto-a", "body_md": "One two"}
    )
    short_b = source.model_copy(
        update={"title": "Curto B", "slug": "curto-b", "body_md": "Three four"}
    )
    res_short = check_adaptation_uniqueness(short_a, short_b)
    assert res_short.passed
    assert res_short.metadata["similarity"] == 0.0


def test_adapt_draft_stateless_offline_and_openrouter_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """adapt_draft_stateless works cleanly zero-DB offline and resolves live OpenRouter env."""
    fixture_file = tmp_path / "seed_vertical.yaml"
    fixture_file.write_text(
        yaml.safe_dump(_target_vertical_brand().model_dump(mode="json"), allow_unicode=True),
        encoding="utf-8",
    )
    pack_dir = tmp_path / "packs" / "industries" / "specialty-retail"
    pack_dir.mkdir(parents=True)
    (pack_dir / "profile.yaml").write_text(
        "name: Specialty\nslug: specialty-retail\nmarket: BR\n"
        "locales:\n  - pt-BR\ndescription: Ops\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        config,
        "get_packs_roots",
        lambda explicit_base=None: (
            [tmp_path / "packs"] if explicit_base is None else [Path(explicit_base)]
        ),
    )

    source = _sample_source_draft()
    src_md = services.render_draft_markdown(
        source,
        source_url_by_doc_id={str(source.claims[0].source_id): "https://example.org/fiscal-source"},
    )
    src_file = tmp_path / "source.md"
    src_file.write_text(src_md, encoding="utf-8")

    out = services.adapt_draft_stateless(
        file_path=src_file,
        brand_fixture=fixture_file,
        industry_pack="specialty-retail",
        locale="pt-BR",
        offline=True,
        deterministic_only=True,
    )
    assert out["stateless"] is True
    assert out["verdict"] == "PASS"
    assert out["uniqueness"]["passed"] is True
    assert "Core Specialty ERP" in out["adapted_markdown"]
    assert "Core ERP" not in out["adapted_markdown"]

    # Live adapter resolution guard when offline=False and OPENROUTER_API_KEY missing
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        services.adapt_draft_stateless(
            markdown_text=src_md,
            brand_fixture=fixture_file,
            industry_pack="specialty-retail",
            offline=False,
        )

    # Also test OPENROUTER_API_KEY adapter initialization path cleanly with FakeLLM stand-in
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    from engine.integrations import openrouter as openrouter_mod

    fake_or = MagicMock(side_effect=lambda _settings: FakeLLM())
    fake_or.Settings = openrouter_mod.OpenRouterLLM.Settings
    monkeypatch.setattr(openrouter_mod, "OpenRouterLLM", fake_or)
    out_live_cfg = services.adapt_draft_stateless(
        markdown_text=src_md,
        brand_fixture=fixture_file,
        industry_pack="specialty-retail",
        offline=False,
        deterministic_only=True,
    )
    assert out_live_cfg["verdict"] == "PASS"

    # Non-mapping source_urls + missing source_urls fallback
    assert services._extract_source_url_map(file_path=None, markdown_text=None) == {}
    bad_map_md = "---\ntitle: T\nslug: s\nmeta_description: m\nsource_urls: invalid\n---\n\nBody\n"
    assert services._extract_source_url_map(file_path=None, markdown_text=bad_map_md) == {}


def test_adapt_draft_for_workspace_unit_rebinds_claims_and_isolates_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """adapt_draft_for_workspace re-ingests public URLs into target WS and stages Gate 2."""
    target_ws = uuid.uuid4()
    actor_id = uuid.uuid4()
    target_doc_id = uuid.uuid4()
    old_doc_id = str(uuid.uuid4())
    source = _sample_source_draft(doc_id=old_doc_id)
    # Include a second fact whose URL will NOT match any target source so fallback branch runs too
    source.claims.append(
        DraftClaim(
            text="Fato secundário sem URL correspondente.",
            kind="fact",
            source_id=str(uuid.uuid4()),
        )
    )
    src_md = services.render_draft_markdown(
        source,
        source_url_by_doc_id={
            old_doc_id: "https://example.org/source-1",
            str(uuid.uuid4()): "https://example.org/extra-source-url",
        },
    )

    monkeypatch.setattr(services, "require_member", lambda conn, workspace_id, user_id: "owner")
    monkeypatch.setattr(db, "record_usage", lambda *args, **kwargs: uuid.uuid4())
    monkeypatch.setattr(db, "record_llm_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        db,
        "get_workspace",
        lambda conn, workspace_id: {
            "workspace_id": workspace_id,
            "config": {"industry_pack": "general"},
        },
    )
    monkeypatch.setattr(
        db,
        "get_current_brand_profile",
        lambda conn, workspace_id: {
            "id": uuid.uuid4(),
            "payload": _target_vertical_brand().model_dump(mode="json"),
        },
    )
    monkeypatch.setattr(
        db,
        "list_campaigns",
        lambda conn, workspace_id: [{"locale": "pt-BR", "payload": {}}],
    )
    monkeypatch.setattr(
        services,
        "_persist_external_doc_and_record",
        lambda conn, workspace_id, run_id, doc_spec: (target_doc_id, uuid.uuid4()),
    )
    monkeypatch.setattr(
        services,
        "_create_completed_adhoc_run",
        lambda conn, workspace_id, user_id: uuid.uuid4(),
    )
    captured_import: dict[str, Any] = {}

    def _fake_import_exec(conn: Any, params: dict[str, Any]) -> dict[str, Any]:
        captured_import.update(params)
        return {
            "draft_id": uuid.uuid4(),
            "brief_id": uuid.uuid4(),
            "run_id": uuid.uuid4(),
            "revision": 0,
            "status": "pass",
            "verdict": "PASS",
            "score": 88.0,
            "findings": [],
        }

    monkeypatch.setattr(services, "_execute_import_external_draft", _fake_import_exec)

    res = services.adapt_draft_for_workspace(
        conn=object(),  # type: ignore[arg-type]
        workspace_id=target_ws,
        user_id=actor_id,
        markdown_text=src_md,
        sources=[
            {"title": "Source 1", "url": "https://example.org/source-1", "summary": "S1"},
            {"title": "Operator Note", "summary": "Note without URL"},
        ],
        offline=True,
        deterministic_only=True,
    )
    assert res["verdict"] == "PASS"
    assert res["uniqueness"]["passed"] is True
    imported_draft: ContentDraft = captured_import["draft_model"]
    assert all(
        c.kind != "fact" or str(c.source_id) == str(target_doc_id) for c in imported_draft.claims
    )


def test_adapt_draft_for_workspace_missing_brand_and_managed_conn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """adapt_draft_for_workspace opens managed connection when conn=None and requires brand."""
    ws_id = uuid.uuid4()
    uid = uuid.uuid4()

    class _FakeConnCtx:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(db, "get_connection", lambda: _FakeConnCtx())
    monkeypatch.setattr(services, "require_member", lambda conn, workspace_id, user_id: "owner")
    monkeypatch.setattr(db, "get_workspace", lambda conn, workspace_id: {"config": {}})
    monkeypatch.setattr(db, "get_current_brand_profile", lambda conn, workspace_id: None)

    with pytest.raises(ValueError, match="no active brand profile"):
        services.adapt_draft_for_workspace(
            workspace_id=ws_id,
            user_id=uid,
            markdown_text=services.render_draft_markdown(_sample_source_draft()),
        )


def test_adapt_draft_for_workspace_real_db_tenant_isolation(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Real DB integration: adaptation writes strictly into target workspace with zero FK leaks."""
    src_slug = f"src-{uuid.uuid4().hex[:6]}"
    dst_slug = f"dst-{uuid.uuid4().hex[:6]}"
    ws_src = db.create_workspace(db_conn, slug=src_slug, name="Src Brand", industry_pack="general")
    ws_dst = db.create_workspace(db_conn, slug=dst_slug, name="Dst Brand", industry_pack="general")
    owner = services.add_workspace_owner(
        db_conn, workspace_slug=dst_slug, email=f"adapt-{uuid.uuid4().hex[:6]}@example.com"
    )
    owner_id = uuid.UUID(str(owner["user"]["user_id"]))
    # Save distinct vertical brand on ws_dst only
    services.save_brand(
        db_conn,
        workspace_id=ws_dst,
        user_id=owner_id,
        profile=_target_vertical_brand(),
    )
    services.save_campaign(
        db_conn,
        workspace_id=ws_dst,
        user_id=owner_id,
        name="Dst Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 8 * * 1",
        monthly_cap_usd="50.00",
        run_cap_usd="10.00",
    )

    src_draft = _sample_source_draft()
    src_md = services.render_draft_markdown(src_draft)
    res = services.adapt_draft_for_workspace(
        db_conn,
        workspace_id=ws_dst,
        user_id=owner_id,
        markdown_text=src_md,
        sources=[
            {
                "title": "Public Fiscal Benchmark",
                "url": "https://example.org/fiscal-benchmark",
                "summary": "Integrar emissão fiscal ao PDV reduz retrabalho operacional.",
                "facts": ["Integrar a emissão fiscal ao fechamento comercial reduz retrabalho."],
            }
        ],
        offline=True,
        deterministic_only=True,
    )
    assert res["verdict"] == "PASS"
    stored = db.get_draft(db_conn, workspace_id=ws_dst, draft_id=uuid.UUID(str(res["draft_id"])))
    assert stored is not None
    assert (
        db.get_draft(db_conn, workspace_id=ws_src, draft_id=uuid.UUID(str(res["draft_id"]))) is None
    )
