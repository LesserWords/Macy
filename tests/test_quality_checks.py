"""Tests for deterministic draft quality checks (M4-02, F14 AC1, AC5, ADR-025)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import quality
from engine.contracts import (
    BrandProfile,
    CampaignConfig,
    ContentDraft,
    DraftClaim,
    EditorialBrief,
    QualityReport,
)
from engine.integrations.fakes import FakeLLM
from engine.quality import checks

DEFAULT_DOC_ID = "11111111-1111-4111-8111-111111111101"


def _build_valid_draft(
    *,
    locale: str = "pt-BR",
    primary_keyword: str | None = None,
    cta: dict[str, str] | None = None,
    slug: str | None = None,
    meta_description: str | None = None,
) -> ContentDraft:
    doc_id = DEFAULT_DOC_ID
    if locale.startswith("pt"):
        kw = primary_keyword or "gestao de estoque otica"
        title = f"Guia de {kw} para lojas independentes"
        default_meta = (
            f"Guia completo sobre {kw} com dados reais do setor optico brasileiro e "
            "passos praticos para melhorar margem e giro no estoque."
        )[:158]
        body_md = (
            f"# {title}\n\n"
            f"A aplicacao disciplinada de {kw} ajuda gestores de oticas brasileiras "
            "a equilibrar compras de armacoes, capital de giro e atendimento consultivo.\n\n"
            "## Indicadores e rotina de loja\n\n"
            "Revisar o giro semanal reduz imobilizacao em vitrine e melhora o fluxo de caixa."
        )
        default_cta = {
            "headline": "Estruture sua operacao com leitura guiada",
            "body": "Baixe nosso guia diagnostico gratuito sobre indicadores operacionais.",
            "button_text": "Baixar guia educativo",
        }
        faq = [
            {
                "question": f"Como iniciar a {kw} na loja?",
                "answer": "Classifique o estoque atual por giro mensal e margem.",
            }
        ]
        claims = [
            DraftClaim(
                text="Vendas do setor optico cresceram 6,4% nos ultimos 12 meses.",
                kind="fact",
                source_id=doc_id,
            ),
            DraftClaim(
                text="Padronizar contagem semanal evita falta de armacoes de alto giro.",
                kind="recommendation",
                source_id=None,
            ),
        ]
    else:
        kw = primary_keyword or "pediatric myopia control"
        title = f"Clinical guide to {kw} in independent practices"
        default_meta = (
            f"Evidence-based playbook for {kw} covering verified industry benchmarks, "
            "patient communication workflows, and specialty lens retention."
        )[:158]
        body_md = (
            f"# {title}\n\n"
            f"Building an evidence-based {kw} service helps independent optometry clinics "
            "align parent education, comprehensive exams, and follow-up compliance.\n\n"
            "## Clinical workflow benchmarks\n\n"
            "Tracking recall cadence improves long-term patient adherence and practice growth."
        )
        default_cta = {
            "headline": "Explore the practice readiness checklist",
            "body": "Download our free educational guide for clinical teams.",
            "button_text": "Read the educational guide",
        }
        faq = [
            {
                "question": f"How can practices launch {kw} smoothly?",
                "answer": "Start by educating parents during routine comprehensive eye exams.",
            }
        ]
        claims = [
            DraftClaim(
                text="Annual pediatric eye exams grew 14% across independent clinics.",
                kind="fact",
                source_id=doc_id,
            ),
            DraftClaim(
                text="Consistent parent follow-up strengthens treatment compliance.",
                kind="recommendation",
                source_id=None,
            ),
        ]

    return ContentDraft(
        title=title,
        meta_description=meta_description if meta_description is not None else default_meta,
        slug=slug if slug is not None else kw.lower().replace(" ", "-"),
        body_md=body_md,
        faq=faq,
        cta=cta if cta is not None else default_cta,
        claims=claims,
    )


def _build_evidence_docs(
    *,
    doc_id: str = DEFAULT_DOC_ID,
    now: datetime | None = None,
    age_days: int = 5,
    date_status: str = "verified",
    fetch_status: str = "ok",
) -> list[dict[str, Any]]:
    ref_now = now or datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    url = "https://ibge.gov.br/pmc-varejo-otico"
    return [
        {
            "id": doc_id,
            "title": "Pesquisa Mensal de Comercio",
            "url": url,
            "canonical_url": url,
            "published_at": ref_now - timedelta(days=age_days),
            "date_status": date_status,
            "fetch_status": fetch_status,
        }
    ]


def test_check_fact_claims_have_source() -> None:
    draft = _build_valid_draft()
    docs = _build_evidence_docs()

    ok_res = checks.check_fact_claims_have_source(draft, docs)
    assert ok_res.passed is True
    assert ok_res.to_finding() is None

    # Fact claim with unknown source_id fails critically
    unknown_draft = draft.model_copy(
        update={
            "claims": [
                DraftClaim(
                    text="Statistic from unknown document",
                    kind="fact",
                    source_id="99999999-9999-4999-8999-999999999999",
                )
            ]
        }
    )
    unknown_res = checks.check_fact_claims_have_source(unknown_draft, docs)
    assert unknown_res.passed is False
    assert unknown_res.severity == "critical"
    assert unknown_res.responsible_module == "write"
    assert unknown_res.to_finding() is not None

    # Model-constructed fact claim without source_id fails critically
    unsourced_claim = DraftClaim.model_construct(text="Unsourced stat", kind="fact", source_id=None)
    unsourced_draft = draft.model_copy(update={"claims": [unsourced_claim]})
    unsourced_res = checks.check_fact_claims_have_source(unsourced_draft)
    assert unsourced_res.passed is False
    assert unsourced_res.severity == "critical"


def test_check_sources_fresh_and_cited_urls_fetched_ok() -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    doc_id = DEFAULT_DOC_ID
    stale_id = "22222222-2222-4222-8222-222222222202"
    draft = _build_valid_draft()

    fresh_docs = _build_evidence_docs(doc_id=doc_id, now=now, age_days=90)
    stale_uncited = _build_evidence_docs(
        doc_id=stale_id, now=now, age_days=180, fetch_status="http_error"
    )
    # Cited doc is fresh & ok; uncited bad doc must not fail checks
    assert checks.check_sources_fresh(draft, fresh_docs + stale_uncited, reference_time=now).passed
    assert checks.check_cited_urls_fetched_ok(draft, fresh_docs + stale_uncited).passed

    # Cited stale doc fails critically
    stale_docs = _build_evidence_docs(doc_id=doc_id, now=now, age_days=91)
    stale_res = checks.check_sources_fresh(draft, stale_docs, max_age_days=90, reference_time=now)
    assert stale_res.passed is False
    assert stale_res.severity == "critical"
    assert stale_res.responsible_module == "research"

    # Unverified date fails critically
    unverified_docs = _build_evidence_docs(doc_id=doc_id, now=now, date_status="unverified")
    assert checks.check_sources_fresh(draft, unverified_docs, reference_time=now).passed is False

    # Missing published_at fails critically
    undated_docs = [{**fresh_docs[0], "published_at": None, "date_status": "verified"}]
    assert checks.check_sources_fresh(draft, undated_docs, reference_time=now).passed is False

    # Cited URL with non-ok fetch_status fails critically
    for bad_status in ("http_error", "blocked", "extraction_failed", "paywalled"):
        bad_docs = _build_evidence_docs(doc_id=doc_id, now=now, fetch_status=bad_status)
        fetch_res = checks.check_cited_urls_fetched_ok(draft, bad_docs)
        assert fetch_res.passed is False
        assert fetch_res.severity == "critical"
        assert fetch_res.responsible_module == "research"


def test_check_seo_keyword_meta_and_slug_literals() -> None:
    draft = _build_valid_draft(primary_keyword="gestao de estoque otica")
    assert checks.check_primary_keyword_placement(draft, "gestão de estoque ótica").passed is True
    assert checks.check_meta_length(draft).passed is True
    assert checks.check_slug_format(draft).passed is True

    no_h1_draft = draft.model_copy(update={"body_md": "Sem heading h1 mas tem texto."})
    kw_fail = checks.check_primary_keyword_placement(no_h1_draft, "gestao de estoque otica")
    assert kw_fail.passed is False
    assert kw_fail.severity == "major"
    assert "h1" in kw_fail.metadata["missing_slots"]

    short_meta_draft = draft.model_copy(update={"meta_description": "Meta curta demais."})
    meta_fail = checks.check_meta_length(short_meta_draft)
    assert meta_fail.passed is False
    assert meta_fail.severity == "major"

    bad_slug_draft = draft.model_copy(update={"slug": "Slug Invalido_01"})
    slug_fail = checks.check_slug_format(bad_slug_draft)
    assert slug_fail.passed is False
    assert slug_fail.severity == "critical"


@given(
    tokens=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
        min_size=1,
        max_size=5,
    )
)
def test_hypothesis_valid_slugs_pass(tokens: list[str]) -> None:
    slug = "-".join(tokens)
    draft = _build_valid_draft(slug=slug)
    assert checks.check_slug_format(draft, max_length=100).passed is True


@given(
    bad_slug=st.one_of(
        st.just(""),
        st.just("ab--cd"),
        st.just("-leading"),
        st.just("trailing-"),
        st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_ !?áéí", min_size=1, max_size=20),
        st.integers(min_value=101, max_value=140).map(lambda n: "a" * n),
    )
)
def test_hypothesis_invalid_slugs_fail(bad_slug: str) -> None:
    draft = _build_valid_draft(slug=bad_slug)
    res = checks.check_slug_format(draft, max_length=100)
    assert res.passed is False
    assert res.severity == "critical"


@given(
    meta=st.text(max_size=220),
    low=st.integers(min_value=10, max_value=90),
    span=st.integers(min_value=0, max_value=80),
)
def test_hypothesis_meta_length_bounds(meta: str, low: int, span: int) -> None:
    high = low + span
    draft = _build_valid_draft(meta_description=meta)
    expected = low <= len(meta.strip()) <= high
    res = checks.check_meta_length(draft, min_length=low, max_length=high)
    assert res.passed is expected


@given(
    words=st.lists(
        st.text(alphabet="abcdefghijáéíóú", min_size=2, max_size=8),
        min_size=1,
        max_size=3,
    ),
    use_upper=st.booleans(),
    drop_slot=st.sampled_from(["none", "title", "h1", "meta"]),
)
def test_hypothesis_primary_keyword_placement(
    words: list[str],
    use_upper: bool,
    drop_slot: str,
) -> None:
    kw = " ".join(words)
    variant = "  ".join(words).upper() if use_upper else kw
    unrelated = "klnpqrst vwxyz klnpqrst"

    title_val = unrelated if drop_slot == "title" else f"Guia completo: {variant}!"
    h1_val = unrelated if drop_slot == "h1" else f"Panorama de {variant} em foco"
    meta_val = (
        f"Descricao tecnica sobre {unrelated} sem o termo esperado e com tamanho padrao."
        if drop_slot == "meta"
        else f"Entenda como aplicar {variant} na loja hoje com eficiencia operacional."
    )
    draft = ContentDraft(
        title=title_val,
        meta_description=meta_val,
        slug="slug-valido",
        body_md=f"# {h1_val}\n\nCorpo explicativo.",
        cta={"headline": "H", "button_text": "Ler guia"},
    )
    res = checks.check_primary_keyword_placement(draft, kw)
    if drop_slot == "none":
        assert res.passed is True
    else:
        assert res.passed is False
        assert drop_slot in res.metadata["missing_slots"]


@given(
    phrase=st.text(alphabet="abcdefghij", min_size=4, max_size=10),
    present=st.booleans(),
)
def test_hypothesis_prohibited_phrases_detection(phrase: str, present: bool) -> None:
    snippet = f"Texto com {phrase} proibido." if present else "Texto neutro xptoqrs."
    draft = ContentDraft(
        title="Titulo neutro",
        meta_description="Descricao neutra.",
        slug="slug-neutro",
        body_md=f"# Titulo neutro\n\n{snippet}",
        cta={"headline": "H"},
    )
    res = checks.check_no_prohibited_phrases(draft, [rf"\b{phrase}\b"])
    assert res.passed is (not present)


@given(
    stage=st.sampled_from(["tofu", "mofu", "bofu"]),
    limit=st.sampled_from(["none", "low", "medium", "high"]),
    explicit=st.sampled_from(["low", "medium", "high"]),
)
def test_hypothesis_cta_intensity_rank_comparison(
    stage: str,
    limit: str,
    explicit: str,
) -> None:
    draft = ContentDraft(
        title="Titulo",
        meta_description="Descricao.",
        slug="slug-neutro",
        body_md="# Titulo\n\nTexto educativo.",
        cta={"headline": "Leitura", "button_text": "Ler guia", "intensity": explicit},
    )
    res = checks.check_cta_intensity(
        draft,
        funnel_stage=stage,
        cta_intensity_limits={stage.upper(): limit},
    )
    expected = checks.CTA_INTENSITY_RANK[explicit] <= checks.CTA_INTENSITY_RANK[limit]
    assert res.passed is expected


def test_check_no_prohibited_phrases_and_cta_intensity() -> None:
    brand = BrandProfile(
        voice="Consultivo",
        positioning="ERP para oticas",
        prohibited_phrases=[r"\bo melhor ERP do Brasil\b", r"\blucro garantido\b"],
    )
    clean_draft = _build_valid_draft()
    assert checks.check_no_prohibited_phrases(clean_draft, brand).passed is True

    bad_phrase_draft = clean_draft.model_copy(
        update={"body_md": f"{clean_draft.body_md}\n\nEscolha o melhor ERP do Brasil hoje."}
    )
    phrase_res = checks.check_no_prohibited_phrases(bad_phrase_draft, brand)
    assert phrase_res.passed is False
    assert phrase_res.severity == "critical"

    # CTA intensity limits: TOFU allows low, forbids medium/high
    cfg = CampaignConfig(cta_intensity_limits={"TOFU": "low", "MOFU": "medium", "BOFU": "high"})
    assert checks.check_cta_intensity(clean_draft, funnel_stage="tofu", campaign_config=cfg).passed

    bofu_cta_draft = _build_valid_draft(
        cta={
            "headline": "Transforme suas vendas agora",
            "body": "Fale com vendas para implantacao imediata.",
            "button_text": "Compre agora e agende uma demonstracao",
        }
    )
    tofu_fail = checks.check_cta_intensity(bofu_cta_draft, funnel_stage="tofu", campaign_config=cfg)
    assert tofu_fail.passed is False
    assert tofu_fail.severity == "critical"

    # High CTA is allowed on BOFU
    assert checks.check_cta_intensity(
        bofu_cta_draft, funnel_stage="bofu", campaign_config=cfg
    ).passed

    # Medium CTA allowed on MOFU, blocked on TOFU
    mofu_cta_draft = _build_valid_draft(
        cta={
            "headline": "Compare cenarios na pratica",
            "body": "Veja uma demonstracao guiada aplicada a sua otica.",
            "button_text": "Fale com um especialista",
        }
    )
    assert checks.check_cta_intensity(
        mofu_cta_draft, funnel_stage="mofu", campaign_config=cfg
    ).passed
    assert not checks.check_cta_intensity(
        mofu_cta_draft, funnel_stage="tofu", campaign_config=cfg
    ).passed


def test_check_language_matches_pt_and_en() -> None:
    pt_draft = _build_valid_draft(locale="pt-BR")
    en_draft = _build_valid_draft(locale="en-US")

    assert checks.check_language_matches(pt_draft, expected_locale="pt-BR").passed is True
    assert checks.check_language_matches(en_draft, expected_locale="en-US").passed is True

    # Language mismatch (F14 AC5)
    pt_on_en = checks.check_language_matches(pt_draft, expected_locale="en-US")
    assert pt_on_en.passed is False
    assert pt_on_en.severity == "critical"
    assert pt_on_en.metadata["detected"] == "pt"
    assert pt_on_en.metadata["expected"] == "en"

    en_on_pt = checks.check_language_matches(en_draft, expected_locale="pt-BR")
    assert en_on_pt.passed is False
    assert en_on_pt.severity == "critical"
    assert en_on_pt.metadata["detected"] == "en"
    assert en_on_pt.metadata["expected"] == "pt"


def test_quality_brief_smoke_helper() -> None:
    brief = EditorialBrief(
        brand_profile_id=uuid.uuid4(),
        topic="Gestao de estoque",
        target_audience="Oticas independentes",
        primary_keyword="gestao de estoque otica",
        angle="Curva ABC",
        outline=["Secao 1", "Secao 2"],
        cta_goal="Baixar guia",
    )
    assert brief.primary_keyword == "gestao de estoque otica"


def _make_spy_llm() -> Any:
    fake = FakeLLM()
    spy = MagicMock()
    spy.complete = MagicMock(side_effect=fake.complete)
    spy.healthcheck = MagicMock(return_value=None)
    return spy


def test_checks_run_full_pass_and_bare_call() -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    draft = _build_valid_draft(locale="pt-BR", primary_keyword="gestao de estoque")
    docs = _build_evidence_docs(now=now, age_days=10)
    brief = EditorialBrief(
        brand_profile_id=uuid.uuid4(),
        topic="Gestao de estoque em oticas",
        target_audience="Gestores de otica",
        primary_keyword="gestao de estoque",
        angle="Curva ABC",
        outline=["Intro", "Aplicacao"],
        cta_goal="Baixar guia",
    )
    brand = BrandProfile(
        voice="Consultivo",
        positioning="ERP para oticas",
        prohibited_phrases=[r"\bo melhor ERP do Brasil\b"],
    )
    cfg = CampaignConfig()

    report = quality.checks.run(
        draft,
        brief=brief,
        evidence_documents=docs,
        brand_profile=brand,
        campaign_config=cfg,
        locale="pt-BR",
        funnel_stage="tofu",
        options=checks.DeterministicRunOptions(reference_time=now),
    )
    assert report.verdict == "PASS"
    assert report.all_passed is True
    assert report.critical_passed is True
    assert report.findings == []
    assert set(report.checks) == {
        "fact_sources",
        "source_freshness",
        "cited_urls_fetched",
        "primary_keyword_placement",
        "meta_length",
        "slug_format",
        "prohibited_phrases",
        "cta_intensity",
        "language_match",
    }

    # Bare call quality.checks.run(draft) safely runs draft-only checks and skips context checks
    bare_report = quality.checks.run(draft)
    assert bare_report.verdict == "PASS"
    assert bare_report.checks["source_freshness"].metadata["skipped"] is True
    assert bare_report.checks["primary_keyword_placement"].metadata["skipped"] is True


def test_checks_run_major_seo_finding_does_not_force_revision() -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    draft = _build_valid_draft(
        locale="pt-BR",
        meta_description="Guia de gestao de estoque otica.",
    )
    docs = _build_evidence_docs(now=now, age_days=3)
    report = checks.run(
        draft,
        evidence_documents=docs,
        primary_keyword="gestao de estoque otica",
        locale="pt-BR",
        options=checks.DeterministicRunOptions(reference_time=now),
    )
    assert report.all_passed is False
    assert report.critical_passed is True
    assert report.verdict == "PASS"
    assert [f.criterion for f in report.findings] == ["meta_length"]
    assert report.findings[0].severity == "major"

    # Primary keyword missing is also major-only (does not force REVISION alone)
    kw_missing_report = checks.run(
        draft.model_copy(
            update={
                "meta_description": (
                    "Guia completo sobre varejo optico brasileiro com dicas praticas de "
                    "gestao financeira, margem e reposicao inteligente na loja."
                )
            }
        ),
        evidence_documents=docs,
        primary_keyword="gestao de estoque otica",
        locale="pt-BR",
        options=checks.DeterministicRunOptions(reference_time=now),
    )
    assert kw_missing_report.verdict == "PASS"
    assert [f.criterion for f in kw_missing_report.findings] == ["primary_keyword_placement"]
    assert kw_missing_report.findings[0].severity == "major"


@pytest.mark.parametrize(
    "failure_mode",
    [
        "language_mismatch",
        "missing_fact_source",
        "stale_source",
        "failed_fetch",
        "prohibited_phrase",
        "invalid_slug",
        "cta_above_limit",
    ],
)
def test_critical_deterministic_failure_returns_revision_without_llm_call(
    failure_mode: str,
) -> None:
    """Verify F14 AC1 and AC5: critical failure yields REVISION with 0 LLM calls."""
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    locale = "en-US" if failure_mode == "language_mismatch" else "pt-BR"
    draft = _build_valid_draft(locale="pt-BR")
    docs = _build_evidence_docs(now=now, age_days=5)
    brand = BrandProfile(
        voice="Consultivo",
        positioning="ERP",
        prohibited_phrases=[r"\bo melhor ERP do Brasil\b"],
    )

    if failure_mode == "missing_fact_source":
        draft = draft.model_copy(
            update={
                "claims": [
                    DraftClaim(
                        text="Claim citing nonexistent document",
                        kind="fact",
                        source_id="99999999-9999-4999-8999-999999999999",
                    )
                ]
            }
        )
    elif failure_mode == "stale_source":
        docs = _build_evidence_docs(now=now, age_days=120)
    elif failure_mode == "failed_fetch":
        docs = _build_evidence_docs(now=now, fetch_status="paywalled")
    elif failure_mode == "prohibited_phrase":
        draft = draft.model_copy(
            update={"body_md": f"{draft.body_md}\n\nEscolha o melhor ERP do Brasil."}
        )
    elif failure_mode == "invalid_slug":
        draft = draft.model_copy(update={"slug": "Slug Com Espaco"})
    elif failure_mode == "cta_above_limit":
        draft = draft.model_copy(
            update={
                "cta": {
                    "headline": "Garanta agora",
                    "body": "Fale com vendas para contratar.",
                    "button_text": "Compre agora",
                }
            }
        )

    spy_llm = _make_spy_llm()
    spy_eval = MagicMock(return_value=QualityReport(verdict="PASS"))

    report = quality.evaluate(
        draft,
        brief={"primary_keyword": "gestao de estoque otica"},
        evidence=docs,
        brand_profile=brand,
        campaign_config=CampaignConfig(),
        locale=locale,
        funnel_stage="tofu",
        options=checks.DeterministicRunOptions(reference_time=now),
        llm_adapter=spy_llm,
        llm_evaluator=spy_eval,
    )

    assert report.verdict == "REVISION"
    assert any(f.severity == "critical" for f in report.findings)
    assert spy_llm.complete.call_count == 0
    assert spy_eval.call_count == 0


def test_evaluate_invokes_evaluator_when_deterministic_checks_pass() -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    draft = _build_valid_draft(locale="en-US")
    docs = _build_evidence_docs(now=now, age_days=2)

    spy_eval = MagicMock(return_value=QualityReport(verdict="PASS", score=92.0))
    passed_report = quality.evaluate(
        draft,
        brief={"primary_keyword": "pediatric myopia control"},
        evidence=docs,
        locale="en-US",
        funnel_stage="tofu",
        options=checks.DeterministicRunOptions(reference_time=now),
        llm_evaluator=spy_eval,
    )
    assert passed_report.verdict == "PASS"
    assert spy_eval.call_count == 1

    default_report = quality.evaluate(
        draft,
        brief={"primary_keyword": "pediatric myopia control"},
        evidence=docs,
        locale="en-US",
        funnel_stage="tofu",
        options=checks.DeterministicRunOptions(reference_time=now),
    )
    assert default_report.verdict == "PASS"


def test_deterministic_checks_additional_edge_cases() -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    doc_id = DEFAULT_DOC_ID
    url = "https://sebrae.com.br/artigo-estoque"
    draft = _build_valid_draft()

    # Evidence passed as dict mapping with cited + uncited docs and ISO / bad / missing dates
    docs_map: dict[str, dict[str, Any]] = {
        doc_id: {
            "url": url,
            "published_at": (now - timedelta(days=3)).isoformat(),
            "date_status": "verified",
            "fetch_status": "ok",
        },
        "custom-url-doc": {
            "id": "custom-url-doc",
            "canonical_url": url,
            "published_at": "not-a-date",
            "fetch_status": "http_error",
        },
        "uncited-other-doc": {
            "id": "uncited-other-doc",
            "url": "https://other.example.com/unrelated",
            "published_at": (now - timedelta(days=3)).isoformat(),
            "date_status": "verified",
            "fetch_status": "ok",
        },
    }
    draft_with_body_url = draft.model_copy(
        update={
            "body_md": f"{draft.body_md}\n\nFonte complementar: [Sebrae]({url}).",
            "claims": [
                DraftClaim(text="Analysis citing doc", kind="analysis", source_id=doc_id),
                DraftClaim(text="No source", kind="recommendation", source_id=None),
            ],
        }
    )
    assert (
        checks.check_sources_fresh(draft_with_body_url, docs_map, reference_time=now).passed
        is False
    )
    assert checks.check_cited_urls_fetched_ok(draft_with_body_url, docs_map).passed is False

    # Raw dict draft input & brief/campaign_config mappings without optional keys
    raw_report = checks.run(
        draft.model_dump(),
        brief={},
        campaign_config={"goal": "authority"},
    )
    assert raw_report.verdict == "PASS"
    assert raw_report.checks["primary_keyword_placement"].metadata["skipped"] is True

    # Future date beyond +24h tolerance fails freshness; date object and naive datetime accepted
    future_doc = _build_evidence_docs(doc_id=doc_id, now=now, age_days=-5)
    assert checks.check_sources_fresh(draft, future_doc, reference_time=now).passed is False

    date_only_doc = [
        {
            "id": doc_id,
            "published_at": (now - timedelta(days=2)).date(),
            "fetch_status": "ok",
        }
    ]
    assert checks.check_sources_fresh(draft, date_only_doc, reference_time=now).passed is True

    naive_dt_doc = [
        {
            "id": doc_id,
            "published_at": (now - timedelta(days=2)).replace(tzinfo=None),
            "fetch_status": "ok",
        }
    ]
    assert checks.check_sources_fresh(draft, naive_dt_doc, reference_time=now).passed is True

    # Blank keyword / blank text edge cases
    assert checks.check_primary_keyword_placement(draft, "   ").passed is False
    blank_text_draft = draft.model_copy(update={"title": "   ", "meta_description": ""})
    assert checks.check_primary_keyword_placement(blank_text_draft, "gestao").passed is False

    # Unsupported locale and blank language sample
    assert checks.check_language_matches(draft, expected_locale="es-ES").passed is False
    empty_lang_draft = draft.model_copy(
        update={
            "title": "123",
            "meta_description": "---",
            "body_md": "https://a.com",
            "faq": [],
            "cta": {},
        }
    )
    assert checks.check_language_matches(empty_lang_draft, expected_locale="pt-BR").passed is False

    # None / dict / list prohibited_phrases, dict campaign_config, explicit CTA intensity
    assert checks.check_no_prohibited_phrases(draft).passed is True
    assert (
        checks.check_no_prohibited_phrases(
            draft, {"prohibited_phrases": [r"\bnao existe\b"]}
        ).passed
        is True
    )
    assert checks.check_no_prohibited_phrases(draft, [r"\bgestao\b"]).passed is False

    empty_cta_draft = _build_valid_draft(cta={})
    assert checks.check_cta_intensity(empty_cta_draft, funnel_stage="tofu").passed is True

    explicit_high_draft = _build_valid_draft(
        cta={"headline": "Leitura", "button_text": "Ler mais", "intensity": "high"}
    )
    assert (
        checks.check_cta_intensity(
            explicit_high_draft,
            funnel_stage="tofu",
            campaign_config={"cta_intensity_limits": {"TOFU": "low"}},
        ).passed
        is False
    )
    assert (
        checks.check_cta_intensity(
            draft,
            funnel_stage="tofu",
            cta_intensity_limits={"TOFU": "invalid-limit"},
        ).metadata["limit"]
        == "medium"
    )
    # Unknown non-fact source_id is caught when evidence_documents provided
    bad_analysis = draft.model_copy(
        update={
            "claims": [
                DraftClaim(
                    text="Analysis referencing unknown",
                    kind="analysis",
                    source_id="99999999-9999-4999-8999-999999999999",
                )
            ]
        }
    )
    assert checks.check_fact_claims_have_source(bad_analysis, docs_map).passed is False
    # Blank url/date parsing edge cases
    assert checks._coerce_utc_datetime("   ") is None
    assert checks._normalize_doc_id("   ") is None
    assert checks._index_evidence_documents([{"title": "no-id"}]) == {}


def test_word_count_respects_format_bounds_and_skips_when_unset() -> None:
    """Verify M5-07: check_word_count enforces min/max bounds and skips when unset."""

    def _draft_with_words(count: int) -> ContentDraft:
        return _build_valid_draft().model_copy(update={"body_md": " ".join(["palavra"] * count)})

    # blog-short (400..800)
    short_under = checks.check_word_count(_draft_with_words(399), min_words=400, max_words=800)
    assert short_under.passed is False
    assert short_under.severity == "critical"
    assert short_under.check == "word_count"

    assert (
        checks.check_word_count(_draft_with_words(400), min_words=400, max_words=800).passed is True
    )
    assert (
        checks.check_word_count(_draft_with_words(800), min_words=400, max_words=800).passed is True
    )

    short_over = checks.check_word_count(_draft_with_words(801), min_words=400, max_words=800)
    assert short_over.passed is False
    assert short_over.severity == "major"

    # blog (800..2500)
    assert (
        checks.check_word_count(_draft_with_words(799), min_words=800, max_words=2500).severity
        == "critical"
    )
    assert (
        checks.check_word_count(_draft_with_words(1000), min_words=800, max_words=2500).passed
        is True
    )
    assert (
        checks.check_word_count(_draft_with_words(2501), min_words=800, max_words=2500).severity
        == "major"
    )

    # blog-long (1800..3500)
    assert (
        checks.check_word_count(_draft_with_words(1799), min_words=1800, max_words=3500).severity
        == "critical"
    )
    assert (
        checks.check_word_count(_draft_with_words(2000), min_words=1800, max_words=3500).passed
        is True
    )
    assert (
        checks.check_word_count(_draft_with_words(3501), min_words=1800, max_words=3500).severity
        == "major"
    )

    # Unset bounds in checks.run() skips word_count (backward-compatible)
    default_report = checks.run(_draft_with_words(45))
    assert default_report.verdict == "PASS"

    # Explicit bounds in DeterministicRunOptions enforce word_count
    bounded_report = checks.run(
        _draft_with_words(45),
        options=checks.DeterministicRunOptions(min_words=400, max_words=800),
    )
    assert bounded_report.verdict == "REVISION"
    assert any(f.criterion == "word_count" for f in bounded_report.findings)


def test_extended_keyword_density_first_paragraph_headings_and_subheadings() -> None:
    """Verify M6-01 D1: density, first paragraph, H2/H3 keyword, and subheading ratio."""
    kw = "gestao de estoque"
    filler = " ".join(["palavra"] * 90)
    good_body = (
        "# Guia de gestao de estoque\n\n"
        f"A {kw} moderna reduz desperdicio na loja. {filler}\n\n"
        f"## Como aplicar {kw} na pratica\n\n"
        f"{filler} {filler}\n\n"
        "### Rotina semanal de inventario\n\n"
        f"Com {kw} bem estruturada, o giro melhora. {filler}"
    )
    good_draft = _build_valid_draft(locale="pt-BR", primary_keyword=kw).model_copy(
        update={"body_md": good_body}
    )

    density_ok = checks.check_keyword_density(good_draft, kw)
    assert density_ok.passed is True
    assert 0.5 <= density_ok.metadata["density"] <= 2.5

    assert checks.check_keyword_in_first_paragraph(good_draft, kw).passed is True
    assert checks.check_keyword_in_headings(good_draft, kw).passed is True
    assert checks.check_subheading_density(good_draft).passed is True

    # Zero -> major fail; low (<0.5%) & high (2.5..4.0%) -> minor; >4.0% -> major
    no_kw_body = "# Titulo\n\n" + " ".join(["texto"] * 100)
    no_kw = good_draft.model_copy(update={"body_md": no_kw_body})
    zero_res = checks.check_keyword_density(no_kw, kw)
    assert zero_res.passed is False
    assert zero_res.severity == "major"
    assert checks.check_keyword_in_first_paragraph(no_kw, kw).passed is False
    assert checks.check_keyword_in_first_paragraph(no_kw, kw).severity == "minor"
    assert checks.check_keyword_in_headings(no_kw, kw).passed is False
    assert checks.check_keyword_in_headings(no_kw, kw).severity == "minor"

    low_body = "# Titulo\n\n" + f"{kw} " + " ".join(["texto"] * 700)
    low_res = checks.check_keyword_density(good_draft.model_copy(update={"body_md": low_body}), kw)
    assert low_res.passed is False
    assert low_res.severity == "minor"

    high_minor_body = "# Titulo\n\n" + f"{kw} " * 3 + " ".join(["texto"] * 90)
    high_minor_res = checks.check_keyword_density(
        good_draft.model_copy(update={"body_md": high_minor_body}), kw
    )
    assert high_minor_res.passed is False
    assert high_minor_res.severity == "minor"

    stuffed_body = "# Titulo\n\n" + f"{kw} " * 10 + " ".join(["texto"] * 100)
    stuffed_res = checks.check_keyword_density(
        good_draft.model_copy(update={"body_md": stuffed_body}), kw
    )
    assert stuffed_res.passed is False
    assert stuffed_res.severity == "major"

    # Subheading density: short skips, >=300 words with insufficient subheadings fails minor
    assert checks.check_subheading_density(no_kw).metadata["skipped"] is True
    long_flat = good_draft.model_copy(
        update={"body_md": "# Titulo\n\n" + " ".join(["palavra"] * 650)}
    )
    sub_fail = checks.check_subheading_density(long_flat)
    assert sub_fail.passed is False
    assert sub_fail.severity == "minor"

    # Wired via DeterministicRunOptions(check_extended_seo=True)
    ext_report = checks.run(
        good_draft,
        primary_keyword=kw,
        options=checks.DeterministicRunOptions(check_extended_seo=True),
    )
    assert "keyword_density" in ext_report.checks
    assert "keyword_in_first_paragraph" in ext_report.checks
    assert "keyword_in_headings" in ext_report.checks
    assert "subheading_density" in ext_report.checks
    assert "readability" in ext_report.checks

    # Extended SEO checks safely skip keyword rules when primary_keyword is absent
    no_kw_ext = checks.run(
        good_draft,
        options=checks.DeterministicRunOptions(check_extended_seo=True),
    )
    assert no_kw_ext.checks["keyword_density"].metadata["skipped"] is True


def test_readability_bilingual_martins_pt_and_tiered_severity() -> None:
    """Verify M6-01 D2: en-US Flesch, pt-BR Martins et al. Flesch, and tiered minor/major."""
    assert checks.count_syllables("A casa nova tem luz.") >= 5
    assert checks.count_syllables("Simple words work well.", lang="en") >= 4

    # Short draft (<100 words) skips cleanly
    short_draft = _build_valid_draft(locale="pt-BR")
    short_res = checks.check_readability(short_draft, locale="pt-BR")
    assert short_res.passed is True
    assert short_res.metadata["skipped"] is True

    # Easy pt-BR draft (>= 60 under Martins et al. 248.835) passes
    easy_pt_sent = "A loja vende oculos bons e claros para cada cliente. "
    easy_pt_draft = short_draft.model_copy(update={"body_md": "# Guia\n\n" + (easy_pt_sent * 12)})
    easy_pt_res = checks.check_readability(easy_pt_draft, locale="pt-BR")
    assert easy_pt_res.passed is True
    assert easy_pt_res.metadata["formula"] == "flesch_martins_pt"
    assert easy_pt_res.metadata["score"] >= 60.0

    # Moderate difficulty (40 <= score < 60) -> minor advisory finding
    mod_en_sent = (
        "Local optical shops track daily orders and monitor product turns each single week better. "
    )
    mod_en_draft = _build_valid_draft(locale="en-US").model_copy(
        update={"body_md": "# Guide\n\n" + (mod_en_sent * 10)}
    )
    mod_en_res = checks.check_readability(mod_en_draft, locale="en-US")
    assert mod_en_res.metadata["formula"] == "flesch_en"
    assert 40.0 <= mod_en_res.metadata["score"] < 60.0
    assert mod_en_res.passed is False
    assert mod_en_res.severity == "minor"

    # Empty text edge cases for 100% branch coverage
    assert checks.count_syllables("") == 0
    assert checks.check_keyword_density(short_draft, "   ").passed is False
    empty_para_draft = short_draft.model_copy(update={"body_md": "# Heading Only"})
    assert checks.check_keyword_in_first_paragraph(empty_para_draft, "otica").passed is False

    # Very difficult (< 40) -> major finding
    hard_pt_sent = (
        "A operacionalizacao multidisciplinar contemporanea das metodologias "
        "administrativas institucionais hipercomplexas frequentemente impossibilita "
        "implementacoes simplificadas extraordinariamente abrangentes nas organizacoes "
        "especializadas brasileiras. "
    )
    hard_pt_draft = short_draft.model_copy(update={"body_md": "# Guia\n\n" + (hard_pt_sent * 7)})
    hard_pt_res = checks.check_readability(hard_pt_draft, locale="pt-BR")
    assert hard_pt_res.metadata["score"] < 40.0
    assert hard_pt_res.passed is False
    assert hard_pt_res.severity == "major"


@given(n_filler=st.integers(min_value=60, max_value=140))
def test_hypothesis_keyword_density_band(n_filler: int) -> None:
    kw = "myopia control"
    body = f"# Guide\n\n{kw} helps clinics. " + " ".join(["word"] * n_filler)
    draft = _build_valid_draft(locale="en-US", primary_keyword=kw).model_copy(
        update={"body_md": body}
    )
    res = checks.check_keyword_density(draft, kw)
    assert res.passed is (0.5 <= res.metadata["density"] <= 2.5)
