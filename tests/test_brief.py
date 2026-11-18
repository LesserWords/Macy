"""Comprehensive unit and integration tests for Editorial Brief generation (M3-02, F12, F03 AC2)."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from engine import budget, db, services, strategy
from engine.contracts import BrandProfile, EditorialBrief, KeywordData, RequiredFact, ResearchRecord
from engine.integrations.config import set_workspace_integration
from engine.integrations.fakes import FakeExtractor, FakeLLM, FakeNewsSearch
from engine.strategy import InvalidEvidenceReferenceError
from engine.strategy.brief import (
    PROMPT_FILE,
    BriefAdapters,
    _coerce_mapping,
    _extract_brief_seed_terms,
    _format_evidence_doc,
    _gather_evidence_documents,
    _safe_load_format_pack,
    _safe_load_industry_pack,
    _search_source_for_evidence,
    get_brief_prompt_and_version,
    synthesize_brief_with_prompt,
    validate_brief_evidence_references,
)


def _load_seed_brand() -> dict[str, Any]:
    raw = yaml.safe_load(Path("fixtures/seed_example.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _setup_approved_opportunity_env(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    tag: str,
    *,
    locale: str = "pt-BR",
    market: str = "BR",
    with_brand: bool = True,
    opp_status: str = "approved",
    record_payload: dict[str, Any] | None = None,
    primary_source_role: str = "news",
) -> dict[str, Any]:
    slug = f"brief-{tag}-{uuid.uuid4().hex[:6]}"
    ws_id = db.create_workspace(conn, slug=slug, name=f"Brief WS {tag}", industry_pack="general")
    email = f"owner-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    owner_res = services.add_workspace_owner(conn, workspace_slug=slug, email=email)
    user_id = owner_res["user"]["user_id"]

    brand_row: dict[str, Any] | None = None
    if with_brand:
        brand_row = services.save_brand(
            conn, workspace_id=ws_id, user_id=user_id, profile=_load_seed_brand()
        )

    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name=f"Brief Camp {tag}",
        locale=locale,
        market=market,
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("25.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    now = datetime.now(UTC)
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        run_cap_usd=Decimal("25.00"),
        window_start=now - timedelta(days=7),
        window_end=now,
        pack_version="general-v1",
        code_version="0.1.0",
        created_by=user_id,
    )
    primary_src = db.create_source(
        conn,
        workspace_id=ws_id,
        industry_pack="general",
        name=f"Primary {primary_source_role}",
        domain=f"primary-{tag}.example.com",
        url=f"https://primary-{tag}.example.com",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role=primary_source_role,
        locales=[locale],
        topics=["optica", "estoque"],
        enabled=True,
    )
    primary_doc = db.upsert_document(
        conn,
        workspace_id=ws_id,
        source_id=primary_src["id"],
        url=f"https://primary-{tag}.example.com/story-1",
        canonical_url=f"https://primary-{tag}.example.com/story-1",
        title="Gestão de estoque e giro de armações em óticas",
        published_at=now - timedelta(days=1),
        date_status="verified",
        fetch_status="ok",
        extracted_text="Resumo setorial sobre estoque de óticas.",
        meta={"source_name": primary_src["name"], "source_role": primary_source_role},
    )
    payload = (
        record_payload
        if record_payload is not None
        else {
            "headline": "Gestão de estoque e margem operacional em óticas",
            "summary": "Óticas reduzem capital parado aplicando curva ABC no ERP.",
            "facts": ["Giro médio de armações melhora 22% com reposição automatizada."],
            "entities": ["Acme Content OS", "varejo óptico"],
        }
    )
    rec = db.upsert_research_record(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=primary_doc["id"],
        status="verified",
        schema_version=1,
        payload=payload,
    )
    opp = db.upsert_opportunity(
        conn,
        workspace_id=ws_id,
        run_id=run_id,
        record_id=rec["id"],
        scores={"novelty": {"score": 8.5, "evidence": "Evidência sólida"}},
        total=Decimal("86.00"),
        rank=1,
        funnel_stage="mofu",
        confidence="high",
        angle="Curva ABC prática para óticas",
        status=opp_status,
        decided_by=user_id if opp_status in ("approved", "rejected") else None,
        decided_at=now if opp_status in ("approved", "rejected") else None,
        decision_note="Focar em indicadores financeiros" if opp_status == "approved" else None,
    )
    return {
        "workspace_id": ws_id,
        "user_id": user_id,
        "campaign_id": camp_id,
        "run_id": run_id,
        "brand": brand_row,
        "primary_doc": primary_doc,
        "opportunity": opp,
    }


def test_brief_generation_pins_brand_logs_usage_and_emits_event_ac1(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F12 AC1: Approved topic produces v1 EditorialBrief with pinned brand, event, and usage."""
    env = _setup_approved_opportunity_env(db_conn, "ac1")
    ws_id = env["workspace_id"]
    user_id = env["user_id"]
    opp_id = env["opportunity"]["id"]
    run_id = env["run_id"]
    brand_row = env["brand"]
    assert brand_row is not None

    brief_row = services.generate_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        offline=True,
    )

    assert brief_row["version"] == 1
    assert brief_row["brand_profile_id"] == brand_row["id"]
    assert brief_row["prompt_version"].startswith("brief.md:")
    assert brief_row["schema_version"] == 1
    assert len(brief_row["keywords"]) >= 1

    model = EditorialBrief.model_validate(brief_row["payload"])
    assert model.brand_profile_id == brand_row["id"]
    assert model.brand_version == 1
    assert len(model.outline) >= 4
    assert len(model.required_facts) >= 1
    for fact_item in model.required_facts:
        doc_uuid = uuid.UUID(str(fact_item.source_id))
        assert db.get_document(db_conn, workspace_id=ws_id, document_id=doc_uuid) is not None

    fetched = db.get_brief(db_conn, workspace_id=ws_id, brief_id=brief_row["id"])
    assert fetched is not None
    assert fetched["id"] == brief_row["id"]

    latest = services.get_latest_brief(
        db_conn, workspace_id=ws_id, opportunity_id=opp_id, user_id=user_id
    )
    assert latest is not None
    assert latest["version"] == 1

    events = db.list_events(
        db_conn, workspace_id=ws_id, event_type="brief.created", entity_id=brief_row["id"]
    )
    assert len(events) == 1
    assert events[0]["payload"]["brand_version"] == 1
    assert events[0]["payload"]["revision"] is False

    usage_rows = [
        u
        for u in db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
        if u["stage"] == "brief"
    ]
    assert len(usage_rows) == 1
    llm_payload = db.get_llm_payload(db_conn, workspace_id=ws_id, usage_id=usage_rows[0]["id"])
    assert llm_payload is not None
    assert "input" in llm_payload["request"]

    rep = services.report(db_conn, workspace_id=ws_id, run_id=run_id)
    assert rep["items"][0]["latest_brief"]["id"] == brief_row["id"]


def test_brief_retains_historical_brand_version_when_brand_updated_f03_ac2(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F03 AC2: Brief v1 stays pinned to Brand v1 after Brand v2 is saved; new briefs pin v2."""
    env = _setup_approved_opportunity_env(db_conn, "brand-pin")
    ws_id = env["workspace_id"]
    user_id = env["user_id"]
    opp1_id = env["opportunity"]["id"]
    brand_v1 = env["brand"]
    assert brand_v1 is not None

    brief1 = services.generate_brief(
        db_conn, workspace_id=ws_id, opportunity_id=opp1_id, user_id=user_id, offline=True
    )
    assert brief1["brand_profile_id"] == brand_v1["id"]
    assert brief1["payload"]["brand_version"] == 1

    updated_profile = _load_seed_brand()
    updated_profile["voice"] = "Tom técnico atualizado para redes ópticas"
    brand_v2 = services.save_brand(
        db_conn, workspace_id=ws_id, user_id=user_id, profile=updated_profile
    )
    assert brand_v2["version"] == 2

    # Second approved topic uses Brand v2 while brief1 keeps Brand v1 reference untouched
    doc2 = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://primary-brand-pin.example.com/story-2",
        canonical_url="https://primary-brand-pin.example.com/story-2",
        title="Receituário digital integrado em redes de óticas",
        date_status="verified",
        fetch_status="ok",
    )
    rec2 = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=env["run_id"],
        document_id=doc2["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Receituário digital em óticas", "summary": "Novo processo."},
    )
    opp2 = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_id,
        run_id=env["run_id"],
        record_id=rec2["id"],
        scores={"novelty": {"score": 8.0, "evidence": "Boa"}},
        total=Decimal("82.00"),
        rank=2,
        funnel_stage="tofu",
        confidence="high",
        angle="Eficiência no balcão",
        status="approved",
        decided_by=user_id,
        decided_at=datetime.now(UTC),
    )

    brief2 = services.generate_brief(
        db_conn, workspace_id=ws_id, opportunity_id=opp2["id"], user_id=user_id, offline=True
    )
    assert brief2["brand_profile_id"] == brand_v2["id"]
    assert brief2["payload"]["brand_version"] == 2

    reloaded_brief1 = db.get_brief(db_conn, workspace_id=ws_id, brief_id=brief1["id"])
    assert reloaded_brief1 is not None
    assert reloaded_brief1["brand_profile_id"] == brand_v1["id"]
    assert reloaded_brief1["payload"]["brand_version"] == 1


def test_brief_rejects_fabricated_or_cross_tenant_document_ids_ac2(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F12 AC2: Non-existent, malformed, or cross-tenant required_facts[].source_id raises."""
    env_a = _setup_approved_opportunity_env(db_conn, "inv-a")
    env_b = _setup_approved_opportunity_env(db_conn, "inv-b")
    ws_a = env_a["workspace_id"]
    opp_a = env_a["opportunity"]["id"]

    bad_brief_base = {
        "brand_profile_id": str(env_a["brand"]["id"]),
        "brand_version": 1,
        "topic": "Tópico com citação inválida",
        "target_audience": "Gestores",
        "primary_keyword": "otica",
        "secondary_keywords": [],
        "format": "blog",
        "angle": "Ângulo",
        "outline": ["Intro", "Corpo"],
        "cta_goal": "Agendar",
    }

    for bad_source_id in (
        "not-a-uuid",
        str(uuid.uuid4()),
        str(env_b["primary_doc"]["id"]),  # exists, but owned by Workspace B!
    ):
        fabricated = {
            **bad_brief_base,
            "required_facts": [{"fact": "Fato não verificável", "source_id": bad_source_id}],
        }
        fake_llm = FakeLLM(settings=FakeLLM.Settings(default_response=fabricated))
        with pytest.raises(InvalidEvidenceReferenceError):
            strategy.brief(
                opp_a,
                conn=db_conn,
                workspace_id=ws_a,
                offline=True,
                llm_adapter=fake_llm,
            )

    assert (
        db.get_latest_brief_for_opportunity(db_conn, workspace_id=ws_a, opportunity_id=opp_a)
        is None
    )


def test_brief_queries_data_and_management_sources_ac3(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F12 AC3: Enabled matching-locale data/management sources feed documents & required_facts."""
    env = _setup_approved_opportunity_env(db_conn, "ev-ac3")
    ws_id = env["workspace_id"]
    opp_id = env["opportunity"]["id"]
    pack_slug = f"brief-ev-{uuid.uuid4().hex[:6]}"
    db_conn.execute(
        "UPDATE workspaces SET industry_pack = %s WHERE id = %s",
        (pack_slug, ws_id),
    )
    db_conn.commit()

    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack=pack_slug,
        name="IBGE Dados",
        domain="ibge-test.gov.br",
        url="https://ibge-test.gov.br",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="data",
        locales=["pt-BR"],
        topics=["varejo otico"],
        enabled=True,
    )
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack=pack_slug,
        name="Sebrae Gestão",
        domain="sebrae-test.com.br",
        url="https://sebrae-test.com.br",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="management",
        locales=["pt-BR"],
        topics=["capital de giro"],
        enabled=True,
    )
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack=pack_slug,
        name="Foreign Wrong Locale",
        domain="vision-us.example.org",
        url="https://vision-us.example.org",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="data",
        locales=["en-US"],
        topics=["eyewear"],
        enabled=True,
    )
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        industry_pack=pack_slug,
        name="Disabled Data Source",
        domain="disabled-data.example.org",
        url="https://disabled-data.example.org",
        fetch_mode="site_search",
        feed_url=None,
        tier=1,
        role="data",
        locales=["pt-BR"],
        topics=["desabilitado"],
        enabled=False,
    )

    brief_row = strategy.brief(
        {"id": opp_id, "workspace_id": ws_id},
        conn=db_conn,
        offline=True,
        actor_user_id=env["user_id"],
    )
    facts = brief_row["payload"]["required_facts"]
    assert len(facts) >= 2

    ev_docs = db.list_evidence_documents(db_conn, workspace_id=ws_id)
    domains_found = {str(d.get("source_name")) for d in ev_docs}
    assert "IBGE Dados" in domains_found
    assert "Sebrae Gestão" in domains_found
    assert "Foreign Wrong Locale" not in domains_found
    assert "Disabled Data Source" not in domains_found


def test_brief_revise_increments_version_and_preserves_v1_ac4(
    db_conn: psycopg.Connection[Any],
) -> None:
    """F12 AC4: REVISION -> brief produces version=2 incorporating findings; v1 remains readable."""
    env = _setup_approved_opportunity_env(db_conn, "rev-ac4")
    ws_id = env["workspace_id"]
    user_id = env["user_id"]
    opp_id = env["opportunity"]["id"]

    with pytest.raises(ValueError, match="findings cannot be empty"):
        services.revise_brief(
            db_conn, workspace_id=ws_id, opportunity_id=opp_id, user_id=user_id, findings=[]
        )

    with pytest.raises(ValueError, match="no prior brief version exists"):
        services.revise_brief(
            db_conn,
            workspace_id=ws_id,
            opportunity_id=opp_id,
            user_id=user_id,
            findings=[{"criterion": "depth", "detail": "Aprofundar exemplo de estoque"}],
        )

    v1 = services.generate_brief(
        db_conn, workspace_id=ws_id, opportunity_id=opp_id, user_id=user_id, offline=True
    )
    assert v1["version"] == 1

    v2 = services.revise_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        findings=[{"criterion": "angle", "detail": "Incluir ROI em 90 dias"}],
        offline=True,
    )
    assert v2["version"] == 2
    assert "Incluir ROI em 90 dias" in v2["payload"]["angle"]

    all_versions = services.list_briefs(
        db_conn, workspace_id=ws_id, user_id=user_id, opportunity_id=opp_id
    )
    assert [b["version"] for b in all_versions] == [2, 1]

    v1_reloaded = db.get_brief(db_conn, workspace_id=ws_id, brief_id=v1["id"])
    assert v1_reloaded is not None
    assert "Incluir ROI em 90 dias" not in v1_reloaded["payload"]["angle"]

    rev_events = db.list_events(
        db_conn, workspace_id=ws_id, event_type="brief.created", entity_id=v2["id"]
    )
    assert len(rev_events) == 1
    assert rev_events[0]["payload"]["revision"] is True


def test_brief_keyword_cold_cache_and_warm_cache_without_provider(
    db_conn: psycopg.Connection[Any],
) -> None:
    """When keyword_provider is unset online, warm cache hits and cold terms are estimates."""
    cold_term = f"kw frio {uuid.uuid4().hex[:6]}"
    warm_term = f"kw quente {uuid.uuid4().hex[:6]}"

    env = _setup_approved_opportunity_env(
        db_conn,
        "kw-cache",
        record_payload={
            "headline": warm_term,
            "summary": "Teste de cache",
            "seed_terms": [warm_term, cold_term],
        },
    )
    ws_id = env["workspace_id"]
    user_id = env["user_id"]
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="llm",
        plugin="fake",
        updated_by=user_id,
    )
    db.upsert_keyword_cache(
        db_conn,
        provider="dataforseo",
        locale="pt-BR",
        market="BR",
        keywords_data=[
            {
                "keyword_norm": warm_term,
                "data": KeywordData(
                    keyword=warm_term,
                    search_volume=3200,
                    difficulty=31.0,
                    cpc_usd=1.10,
                    intent="commercial",
                    is_estimate=False,
                ).model_dump(mode="json"),
            }
        ],
    )

    # Configured LLM, no keyword_provider/search/extractor configured, offline=False
    brief_row = services.generate_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=env["opportunity"]["id"],
        user_id=user_id,
        offline=False,
    )
    by_kw = {k["keyword"]: k for k in brief_row["keywords"]}
    assert by_kw[warm_term]["search_volume"] == 3200
    assert by_kw[warm_term]["is_estimate"] is False
    assert by_kw[cold_term]["search_volume"] == 0
    assert by_kw[cold_term]["is_estimate"] is True


def test_brief_configured_integrations_and_explicit_adapters(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify plugin_config resolution when offline=False and explicit non-Fake KeywordProvider."""
    env = _setup_approved_opportunity_env(
        db_conn,
        "cfg-int",
        locale="en-US",
        market="US",
        primary_source_role="data",
    )
    ws_id = env["workspace_id"]
    user_id = env["user_id"]
    for port, plugin in (
        ("llm", "fake"),
        ("keyword_provider", "fake"),
        ("news_search", "fake"),
        ("extractor", "fake"),
    ):
        set_workspace_integration(
            db_conn, workspace_id=ws_id, port=port, plugin=plugin, updated_by=user_id
        )

    brief_cfg = services.generate_brief(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=env["opportunity"]["id"],
        user_id=user_id,
        offline=False,
    )
    assert brief_cfg["version"] == 1
    assert brief_cfg["payload"]["brand_version"] == 1

    # Explicit non-FakeKeywordProvider class hits provider_name="dataforseo" branch
    class CustomKWPort:
        def healthcheck(self) -> None:
            pass

        def lookup(
            self, terms: list[str], locale: str, market: str, **kwargs: Any
        ) -> list[KeywordData]:
            return [KeywordData(keyword=t, search_volume=777, is_estimate=False) for t in terms]

    brief_custom = strategy.brief(
        env["opportunity"]["id"],
        conn=db_conn,
        workspace_id=ws_id,
        llm_adapter=FakeLLM(),
        keyword_provider=CustomKWPort(),
        news_search_adapter=FakeNewsSearch(),
        extractor_adapter=FakeExtractor(),
    )
    assert brief_custom["version"] == 2
    assert brief_custom["keywords"][0]["search_volume"] == 777


def test_brief_managed_connections_and_decide_topic_auto_brief(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Exercise conn=None managed wrappers in services and brief, plus decide_topic auto_brief."""
    env = _setup_approved_opportunity_env(db_conn, "mng-conn", opp_status="scored")
    ws_id = env["workspace_id"]
    user_id = env["user_id"]
    opp_id = env["opportunity"]["id"]

    # Create second scored opp to reject with auto_brief=True (should NOT generate brief)
    doc2 = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url="https://primary-mng-conn.example.com/story-2",
        canonical_url="https://primary-mng-conn.example.com/story-2",
        title="Outro tópico rejeitado",
        date_status="verified",
        fetch_status="ok",
    )
    rec2 = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=env["run_id"],
        document_id=doc2["id"],
        status="verified",
        schema_version=1,
        payload={"headline": "Tópico rejeitado"},
    )
    opp2 = db.upsert_opportunity(
        db_conn,
        workspace_id=ws_id,
        run_id=env["run_id"],
        record_id=rec2["id"],
        scores={"novelty": {"score": 5.0, "evidence": "Fraco"}},
        total=Decimal("50.00"),
        rank=2,
        funnel_stage="bofu",
        confidence="medium",
        angle="Ângulo rejeitado",
        status="scored",
    )
    db_conn.commit()

    decided = services.decide_topic(
        workspace_id=ws_id,
        opportunity_id=opp_id,
        decision="approved",
        user_id=user_id,
        auto_brief=True,
        offline=True,
    )
    assert decided["brief"]["version"] == 1

    rejected = services.decide_topic(
        workspace_id=ws_id,
        opportunity_id=opp2["id"],
        decision="rejected",
        user_id=user_id,
        auto_brief=True,
        offline=True,
    )
    assert "brief" not in rejected

    b_direct = strategy.brief(opp_id, workspace_id=ws_id, offline=True)
    assert b_direct["version"] == 2

    b_svc = services.generate_brief(
        workspace_id=ws_id, opportunity_id=opp_id, user_id=user_id, offline=True
    )
    assert b_svc["version"] == 3

    b_rev = services.revise_brief(
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        findings=["Detalhar CTA final"],
        offline=True,
    )
    assert b_rev["version"] == 4

    latest = services.get_latest_brief(workspace_id=ws_id, opportunity_id=opp_id, user_id=user_id)
    assert latest is not None and latest["version"] == 4
    listed = services.list_briefs(workspace_id=ws_id, user_id=user_id)
    assert len(listed) == 4


def test_brief_validation_and_error_guards(db_conn: psycopg.Connection[Any]) -> None:
    """Verify guards for missing workspace_id, wrong status, missing brand, and missing opp."""
    env_scored = _setup_approved_opportunity_env(db_conn, "err-scored", opp_status="scored")
    env_nobrand = _setup_approved_opportunity_env(db_conn, "err-nobrand", with_brand=False)

    with pytest.raises(ValueError, match="workspace_id is required"):
        strategy.brief(env_scored["opportunity"]["id"], offline=True)

    with pytest.raises(ValueError, match="not found"):
        strategy.brief(
            uuid.uuid4(), conn=db_conn, workspace_id=env_scored["workspace_id"], offline=True
        )

    with pytest.raises(ValueError, match="expected 'approved'"):
        strategy.brief(
            env_scored["opportunity"]["id"],
            conn=db_conn,
            workspace_id=env_scored["workspace_id"],
            offline=True,
        )

    with pytest.raises(ValueError, match="no current brand profile"):
        strategy.brief(
            env_nobrand["opportunity"]["id"],
            conn=db_conn,
            workspace_id=env_nobrand["workspace_id"],
            offline=True,
        )

    with pytest.raises(ValueError, match="not found"):
        db.create_next_brief_version(
            db_conn,
            workspace_id=env_scored["workspace_id"],
            opportunity_id=uuid.uuid4(),
            brand_profile_id=env_scored["brand"]["id"],
            prompt_version="brief.md:v1",
            keywords=[],
            schema_version=1,
            payload={},
        )


def test_brief_helper_edge_cases_and_seed_extraction(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise helper edge branches for 100% branch/line coverage of brief synthesis."""
    # Prompt file missing branch
    brief_mod = importlib.import_module("engine.strategy.brief")
    monkeypatch.setattr(brief_mod, "PROMPT_FILE", tmp_path / "missing_brief.md")
    with pytest.raises(FileNotFoundError, match="Brief prompt template missing"):
        get_brief_prompt_and_version()
    monkeypatch.setattr(brief_mod, "PROMPT_FILE", PROMPT_FILE)

    # _coerce_mapping & pack loader fallbacks
    brand_model = BrandProfile.model_validate(_load_seed_brand())
    assert _coerce_mapping(brand_model)["voice"] == brand_model.voice
    assert _coerce_mapping({"k": "v"}) == {"k": "v"}
    assert _coerce_mapping(123) == {}
    assert _safe_load_industry_pack(None) == {}
    assert _safe_load_industry_pack("nonexistent-industry-xyz") == {
        "slug": "nonexistent-industry-xyz"
    }
    assert _safe_load_format_pack("nonexistent-format-xyz")["slug"] == "nonexistent-format-xyz"

    # synthesize_brief_with_prompt without evidence_documents + without industry_pack
    synth = synthesize_brief_with_prompt(
        opportunity={"title": "Test", "angle": "Angle", "funnel_stage": "bofu"},
        evidence_documents=[],
        keywords=[{"keyword": "ótica"}],
        brand_profile=brand_model,
        brand_profile_id=None,
        industry_pack=None,
        locale="en-US",
        llm_adapter=FakeLLM(),
    )
    assert synth.format == "blog"

    # validate_brief_evidence_references valid_document_ids branch (hit and miss)
    valid_doc_id = uuid.uuid4()
    b_obj = EditorialBrief(
        topic="T",
        target_audience="A",
        primary_keyword="k",
        angle="ang",
        outline=["1"],
        required_facts=[RequiredFact(fact="F", source_id=valid_doc_id)],
        cta_goal="CTA",
    )
    validate_brief_evidence_references(b_obj, valid_document_ids={valid_doc_id})
    with pytest.raises(InvalidEvidenceReferenceError, match="not present"):
        validate_brief_evidence_references(b_obj, valid_document_ids=set())

    # Empty seed terms + duplicates + angle fallback + FakeNewsSearch no-domain + BudgetCapReached
    assert _extract_brief_seed_terms(
        {
            "record_payload": {
                "headline": "",
                "entities": ["", "a", "a", "b", "c", "d", "e", "f"],
            },
            "angle": "Curva ABC",
        }
    ) == ["a", "b", "c", "d", "e"]
    assert _extract_brief_seed_terms(
        {"record_payload": {}, "document_title": "", "angle": "Curva ABC rapida"}
    ) == ["Curva ABC rapida"]

    env = _setup_approved_opportunity_env(
        db_conn,
        "edges",
        record_payload={
            "headline": "",
            "summary": "",
            "entities": [" ", "e1", "e1", "e2", "e3", "e4", "e5", "e6"],
        },
    )
    ws_id = env["workspace_id"]
    opp_ctx = db.get_opportunity_context(
        db_conn, workspace_id=ws_id, opportunity_id=env["opportunity"]["id"]
    )
    assert opp_ctx is not None
    brief_multi = strategy.brief(
        env["opportunity"]["id"], conn=db_conn, workspace_id=ws_id, offline=True
    )
    assert len(brief_multi["keywords"]) == 5

    opp_empty = _setup_approved_opportunity_env(
        db_conn, "empty-kw", record_payload={"headline": ""}
    )
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE documents SET title = '' WHERE id = %s", (opp_empty["primary_doc"]["id"],)
        )
        cur.execute(
            "UPDATE opportunities SET angle = '' WHERE id = %s", (opp_empty["opportunity"]["id"],)
        )
    brief_no_kw = strategy.brief(
        opp_empty["opportunity"]["id"],
        conn=db_conn,
        workspace_id=opp_empty["workspace_id"],
        offline=True,
    )
    assert brief_no_kw["keywords"] == []

    # _format_evidence_doc non-dict meta
    formatted = _format_evidence_doc({"id": valid_doc_id, "meta": "not-a-dict", "title": "DocT"})
    assert formatted["facts"] == []


def test_brief_evidence_error_handling_and_fake_llm_branches(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Exercise evidence error resilience, BudgetCapReached propagation, and FakeLLM edges."""
    env = _setup_approved_opportunity_env(db_conn, "ev-edges")
    ws_id = env["workspace_id"]
    opp_ctx = db.get_opportunity_context(
        db_conn, workspace_id=ws_id, opportunity_id=env["opportunity"]["id"]
    )
    assert opp_ctx is not None

    # _search_source_for_evidence error handling + BudgetCapReached propagation
    class BrokenSearch:
        def healthcheck(self) -> None:
            pass

        def search(
            self,
            query: str,
            locale: str,
            market: str,
            since: date | None = None,
        ) -> list[ResearchRecord]:
            raise RuntimeError("transient error")

    _search_source_for_evidence(
        db_conn,
        workspace_id=ws_id,
        source_row={"domain": ""},
        opp_title="Test",
        locale="pt-BR",
        market="BR",
        search_port=BrokenSearch(),
        extractor_port=FakeExtractor(),
    )

    class CapSearch:
        def healthcheck(self) -> None:
            pass

        def search(
            self,
            query: str,
            locale: str,
            market: str,
            since: date | None = None,
        ) -> list[ResearchRecord]:
            raise budget.BudgetCapReached(env["run_id"], Decimal("1"), Decimal("2"))

    class ItemFailExtractor:
        def healthcheck(self) -> None:
            pass

        def extract(self, url: str, html: str | None = None) -> ResearchRecord:
            raise RuntimeError("extract failed")

    class CapExtractor:
        def healthcheck(self) -> None:
            pass

        def extract(self, url: str, html: str | None = None) -> ResearchRecord:
            raise budget.BudgetCapReached(env["run_id"], Decimal("1"), Decimal("2"))

    _search_source_for_evidence(
        db_conn,
        workspace_id=ws_id,
        source_row={"domain": "ex.com"},
        opp_title="Test",
        locale="pt-BR",
        market="BR",
        search_port=FakeNewsSearch(),
        extractor_port=ItemFailExtractor(),
    )

    for search_p, ext_p in ((CapSearch(), FakeExtractor()), (FakeNewsSearch(), CapExtractor())):
        with pytest.raises(budget.BudgetCapReached):
            _search_source_for_evidence(
                db_conn,
                workspace_id=ws_id,
                source_row={"domain": "ex.com"},
                opp_title="Test",
                locale="pt-BR",
                market="BR",
                search_port=search_p,
                extractor_port=ext_p,
            )

    # _gather_evidence_documents without configured adapters falls back to primary document
    gathered = _gather_evidence_documents(db_conn, opp_ctx, offline=False, adapters=BriefAdapters())
    assert any(d["id"] == str(opp_ctx["primary_document_id"]) for d in gathered)

    # FakeLLM edge branches: invalid JSON, non-dict JSON, blank findings, and custom payload
    fake = FakeLLM()
    assert fake.complete("No input marker", schema=EditorialBrief)["format"] == "blog"
    assert fake.complete("Sys\n\nInput:\n{bad json", schema=EditorialBrief)["topic"]
    assert fake.complete('Sys\n\nInput:\n["list"]', schema=EditorialBrief)["topic"]
    en_doc_res = fake.complete(
        'Sys\n\nInput:\n{"locale":"en-US","opportunity":{"funnel_stage":"tofu"},'
        '"revision_findings":[""],'
        '"evidence_documents":["bad",{"id":"11111111-1111-4111-8111-111111111111"}]}',
        schema=EditorialBrief,
    )
    assert "Verified industry benchmark" in en_doc_res["required_facts"][0]["fact"]
    custom_fake = FakeLLM(
        settings=FakeLLM.Settings(
            default_response={
                "topic": "Custom",
                "target_audience": "Aud",
                "primary_keyword": "kw",
                "angle": "ang",
                "outline": ["1"],
                "required_facts": [],
                "cta_goal": "CTA",
            }
        )
    )
    assert custom_fake.complete("Anything", schema=EditorialBrief)["topic"] == "Custom"
