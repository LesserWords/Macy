"""Tests for SEO and Keyword Research subsystem (F11, M3-01, ADR-004, ADR-015)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import budget, db
from engine.contracts import KeywordData, Opportunity, SubScore
from engine.integrations.dataforseo import (
    DataForSEOKeywordProvider,
    map_dataforseo_locale,
)
from engine.integrations.ports import KeywordProviderPort
from engine.integrations.registry import UnsupportedLocale
from engine.safe_http import SafeResponse
from engine.seo import extract_seed_terms, keywords_for, normalize_keyword


def test_normalize_keyword_pt_br_literal_cases() -> None:
    """Verify keyword normalization with pt-BR diacritics, casing and whitespace."""
    assert normalize_keyword("Óculos") == "oculos"
    assert normalize_keyword("AÇÃO") == "acao"
    assert normalize_keyword("  Lentes   de Contato  ") == "lentes de contato"
    assert normalize_keyword("preço de armação") == "preco de armacao"
    assert normalize_keyword("Visão Subnormal") == "visao subnormal"
    assert normalize_keyword("Óculos de Sol Infantil") == "oculos de sol infantil"
    assert normalize_keyword("   ") == ""
    assert normalize_keyword("") == ""


@given(st.text())
def test_normalize_keyword_is_idempotent(text: str) -> None:
    """Hypothesis property: keyword normalization is idempotent across arbitrary unicode."""
    norm_once = normalize_keyword(text)
    norm_twice = normalize_keyword(norm_once)
    assert norm_once == norm_twice


def test_keyword_cache_db_roundtrip_and_expiration(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify storing, retrieving fresh keywords, and ignoring expired ones (> 30 days)."""
    provider = "test_provider"
    locale = "pt-BR"
    market = "br"

    kw1 = KeywordData(
        keyword="oculos",
        search_volume=12000,
        difficulty=45.0,
        cpc_usd=1.20,
        intent="commercial",
        is_estimate=False,
    )
    kw2 = KeywordData(
        keyword="lentes de contato",
        search_volume=45000,
        difficulty=60.0,
        cpc_usd=2.50,
        intent="transactional",
        is_estimate=False,
    )

    # Upsert both
    db.upsert_keyword_cache(
        db_conn,
        provider=provider,
        locale=locale,
        market=market,
        keywords_data=[
            {"keyword_norm": "oculos", "data": kw1.model_dump()},
            {"keyword_norm": "lentes de contato", "data": kw2.model_dump()},
        ],
    )

    # Retrieve fresh
    fresh = db.get_fresh_keywords_from_cache(
        db_conn,
        provider=provider,
        locale=locale,
        market=market,
        normalized_keywords=["oculos", "lentes de contato", "armação"],
        max_age_days=30,
    )

    assert "oculos" in fresh
    assert fresh["oculos"]["search_volume"] == 12000
    assert "lentes de contato" in fresh
    assert fresh["lentes de contato"]["search_volume"] == 45000
    assert "armação" not in fresh  # missing term

    # Simulate an expired entry (> 30 days old, e.g. 35 days ago)
    with db_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE keyword_cache
            SET fetched_at = NOW() - interval '35 days'
            WHERE provider = %s AND keyword_norm = 'oculos'
            """,
            (provider,),
        )
    db_conn.commit()

    # Now 'oculos' must NOT be returned when max_age_days=30
    fresh_after_expiry = db.get_fresh_keywords_from_cache(
        db_conn,
        provider=provider,
        locale=locale,
        market=market,
        normalized_keywords=["oculos", "lentes de contato"],
        max_age_days=30,
    )
    assert "oculos" not in fresh_after_expiry
    assert "lentes de contato" in fresh_after_expiry


def test_dataforseo_locale_mapping_and_unsupported_locale() -> None:
    """Verify DataForSEO locale and market code mappings and error on unsupported (F11 AC5)."""
    assert map_dataforseo_locale("pt-BR", "br") == (2076, "pt")
    assert map_dataforseo_locale("en-US", "us") == (2840, "en")

    with pytest.raises(UnsupportedLocale, match="fr-FR"):
        map_dataforseo_locale("fr-FR", "fr")

    with pytest.raises(UnsupportedLocale, match="pt-BR"):
        map_dataforseo_locale("pt-BR", "ar")


def _seed_test_workspace_and_campaign(
    conn: psycopg.Connection[Any],
    *,
    run_cap_usd: Decimal = Decimal("5.00"),
    monthly_cap_usd: Decimal = Decimal("20.00"),
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    slug = f"ws-seo-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(conn, slug=slug, name="SEO Test WS", industry_pack="general")
    user = db.create_or_get_user(conn, email=f"user-{uuid.uuid4().hex[:8]}@example.com")
    camp_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO campaigns (
                id, workspace_id, name, locale, market, articles_per_week, schedule_cron,
                monthly_cap_usd, run_cap_usd, config, config_schema_version, created_by
            )
            VALUES (%s, %s, 'Campaign', 'pt-BR', 'BR', 1, '0 9 * * 1', %s, %s, '{}', 1, %s)
            """,
            (camp_id, ws_id, monthly_cap_usd, run_cap_usd, user["user_id"]),
        )
    conn.commit()
    return ws_id, camp_id, user["user_id"]


def _seed_test_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    status: str = "running",
    run_cap_usd: Decimal = Decimal("5.00"),
    cost_usd: Decimal = Decimal("0.00"),
) -> uuid.UUID:
    run_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (
                id, workspace_id, campaign_id, trigger, status, window_start, window_end,
                run_cap_usd, code_version, pack_version, cost_usd
            )
            VALUES (
                %s, %s, %s, 'manual', %s, NOW(), NOW() + interval '1 day',
                %s, 'v1', 'v1', %s
            )
            """,
            (run_id, workspace_id, campaign_id, status, run_cap_usd, cost_usd),
        )
    conn.commit()
    return run_id


def _mock_dataforseo_http(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    status_code: int = 200,
) -> None:
    fake_response = SafeResponse(
        status_code=status_code,
        content=content,
        headers={"content-type": "application/json"},
    )
    monkeypatch.setattr(
        "engine.integrations.dataforseo.safe_request", lambda *args, **kwargs: fake_response
    )
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host", lambda host: ["93.184.216.34"]
    )


def test_dataforseo_budget_guard_blocks_call(
    db_conn: psycopg.Connection[Any],
) -> None:
    """DataForSEO lookup verifies budget guard and raises BudgetCapReached before call (F11 AC4)."""
    ws_id, camp_id, _ = _seed_test_workspace_and_campaign(
        db_conn,
        run_cap_usd=Decimal("1.00"),
        monthly_cap_usd=Decimal("10.00"),
    )
    run_id = _seed_test_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        run_cap_usd=Decimal("1.00"),
        cost_usd=Decimal("1.00"),
    )
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        kind="api",
        vendor="dataforseo",
        stage="keywords",
        units=Decimal("1.000"),
        cost_usd=Decimal("1.000000"),
    )
    db_conn.commit()

    provider = DataForSEOKeywordProvider()
    with pytest.raises(budget.BudgetCapReached):
        provider.lookup(
            ["termo1", "termo2"],
            locale="pt-BR",
            market="br",
            conn=db_conn,
            workspace_id=ws_id,
            run_id=run_id,
        )


def test_dataforseo_usage_ledger_recorded(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataForSEO lookup records units and cost in usage_ledger (F11 AC4)."""
    ws_id, camp_id, _ = _seed_test_workspace_and_campaign(db_conn)
    _mock_dataforseo_http(
        monkeypatch,
        b"""
        {
          "tasks": [
            {
              "result": [
                {"keyword": "lentes", "search_volume": 1200, "competition_index": 30, "cpc": 0.50}
              ]
            }
          ]
        }
        """,
    )

    provider = DataForSEOKeywordProvider()
    results = provider.lookup(
        ["lentes"],
        locale="pt-BR",
        market="br",
        conn=db_conn,
        workspace_id=ws_id,
    )
    assert len(results) == 1
    assert results[0].search_volume == 1200
    assert not results[0].is_estimate

    # Check usage_ledger
    ledger = db.list_usage(db_conn, workspace_id=ws_id)
    assert len(ledger) >= 1
    row = ledger[0]
    assert row["vendor"] == "dataforseo"
    assert row["stage"] == "keywords"
    assert row["units"] == Decimal("1.000")
    assert row["cost_usd"] == Decimal("0.000500")


def test_keywords_for_cache_hit_bypasses_paid_call(
    db_conn: psycopg.Connection[Any],
) -> None:
    """When keyword is in cache within 30 days, provider is not called (F11 AC2)."""
    cached_kw = KeywordData(
        keyword="oculos",
        search_volume=33000,
        difficulty=50.0,
        cpc_usd=1.50,
        intent="commercial",
        is_estimate=False,
    )
    db.upsert_keyword_cache(
        db_conn,
        provider="dataforseo",
        locale="pt-BR",
        market="br",
        keywords_data=[{"keyword_norm": "oculos", "data": cached_kw.model_dump()}],
    )

    mock_provider = MagicMock(spec=KeywordProviderPort)
    results = keywords_for(
        ["Óculos"],
        conn=db_conn,
        locale="pt-BR",
        market="br",
        provider=mock_provider,
        provider_name="dataforseo",
    )

    assert len(results) == 1
    assert results[0].keyword == "Óculos"
    assert results[0].search_volume == 33000
    assert not results[0].is_estimate
    # Provider must NOT have been called
    mock_provider.lookup.assert_not_called()


def test_keywords_for_uncached_calls_provider_and_persists(
    db_conn: psycopg.Connection[Any],
) -> None:
    """When keyword is uncached, provider is called and result stored in cache (F11 AC1, AC2)."""
    unique_suffix = uuid.uuid4().hex[:6]
    term = f"Lentes de Grau {unique_suffix}"
    mock_provider = MagicMock(spec=KeywordProviderPort)
    mock_provider.lookup.return_value = [
        KeywordData(
            keyword=term,
            search_volume=8000,
            difficulty=40.0,
            cpc_usd=0.90,
            intent="transactional",
            is_estimate=False,
        )
    ]

    results = keywords_for(
        [term],
        conn=db_conn,
        locale="pt-BR",
        market="br",
        provider=mock_provider,
        provider_name="dataforseo",
    )

    assert len(results) == 1
    assert results[0].search_volume == 8000
    mock_provider.lookup.assert_called_once()

    # Second call for the same term -> must be a cache hit, provider NOT called again!
    mock_provider.reset_mock()
    results_second = keywords_for(
        [term.lower()],
        conn=db_conn,
        locale="pt-BR",
        market="br",
        provider=mock_provider,
        provider_name="dataforseo",
    )
    assert len(results_second) == 1
    assert results_second[0].search_volume == 8000
    mock_provider.lookup.assert_not_called()


def test_keywords_for_missing_data_marked_as_estimate() -> None:
    """When provider has no volume for term, result is marked is_estimate=True (F11 AC3)."""
    mock_provider = MagicMock(spec=KeywordProviderPort)
    # Provider returns item with 0 volume and is_estimate=True, or empty list
    mock_provider.lookup.return_value = []

    results = keywords_for(
        ["termo obscuro sem volume"],
        provider=mock_provider,
        provider_name="dataforseo",
    )
    assert len(results) == 1
    assert results[0].keyword == "termo obscuro sem volume"
    assert results[0].search_volume == 0
    assert results[0].is_estimate is True


def test_extract_seed_terms_from_opportunity() -> None:
    """Verify seed term extraction from Opportunity model, dicts and lists."""
    dummy_subscore = SubScore(score=8, evidence="Strong", is_estimate=False)
    opp = Opportunity(
        title="Benefícios do Óculos Anti-Reflexo",
        angle="Guia prático para motoristas noturnos",
        funnel_stage="tofu",
        confidence="high",
        novelty=dummy_subscore,
        audience_fit=dummy_subscore,
        brand_fit=dummy_subscore,
        competitor_gap=dummy_subscore,
        trend=dummy_subscore,
        urgency=dummy_subscore,
    )
    terms_from_opp = extract_seed_terms(opp)
    assert "Benefícios do Óculos Anti-Reflexo" in terms_from_opp
    assert "Guia prático para motoristas noturnos" in terms_from_opp

    terms_from_dict = extract_seed_terms({"seed_terms": ["lentes", "armação"]})
    assert terms_from_dict == ["lentes", "armação"]

    terms_from_list = extract_seed_terms(["oculos", "lente"])
    assert terms_from_list == ["oculos", "lente"]

    assert extract_seed_terms("") == []
    assert extract_seed_terms("   ") == []
    assert extract_seed_terms(12345) == []  # type: ignore[arg-type]
    assert extract_seed_terms({"keywords": ["k1", "k2"]}) == ["k1", "k2"]
    assert extract_seed_terms({"title": "  ", "angle": "  "}) == []


def test_keywords_for_empty_returns_empty() -> None:
    assert keywords_for([]) == []
    assert keywords_for("") == []


def test_dataforseo_healthcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host", lambda host: ["93.184.216.34"]
    )
    provider = DataForSEOKeywordProvider()
    provider.healthcheck()


def test_dataforseo_lookup_empty_terms() -> None:
    provider = DataForSEOKeywordProvider()
    assert provider.lookup([], locale="pt-BR", market="br") == []


def test_dataforseo_api_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dataforseo_http(
        monkeypatch,
        b'{"error": "Internal Server Error"}',
        status_code=500,
    )
    provider = DataForSEOKeywordProvider()
    with pytest.raises(ValueError, match="DataForSEO API request failed with HTTP 500"):
        provider.lookup(["lentes"], locale="pt-BR", market="br")


def test_dataforseo_malformed_json_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dataforseo_http(monkeypatch, b"not json")
    provider = DataForSEOKeywordProvider()
    res = provider.lookup(["lentes"], locale="pt-BR", market="br")
    assert len(res) == 1
    assert res[0].is_estimate is True
    assert res[0].search_volume == 0


def test_dataforseo_empty_or_non_dict_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dataforseo_http(
        monkeypatch,
        b'{"tasks": ["not-a-dict", {"result": [{"keyword": "lentes", "search_volume": null}]}]}',
    )
    provider = DataForSEOKeywordProvider()
    res = provider.lookup(["lentes", "oculos"], locale="pt-BR", market="br")
    assert len(res) == 2
    assert res[0].is_estimate is True
    assert res[1].is_estimate is True


def test_dataforseo_monthly_cap_with_workspace_only(
    db_conn: psycopg.Connection[Any],
) -> None:
    ws_id, _, _ = _seed_test_workspace_and_campaign(
        db_conn,
        monthly_cap_usd=Decimal("5.00"),
    )
    # Verify cross-vendor prior usage trips the monthly cap
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        kind="llm",
        vendor="openrouter",
        stage="research",
        units=Decimal("50.000"),
        cost_usd=Decimal("5.000000"),
    )
    db_conn.commit()

    provider = DataForSEOKeywordProvider()
    with pytest.raises(budget.MonthlyCapReached):
        provider.lookup(
            ["termo"],
            locale="pt-BR",
            market="br",
            conn=db_conn,
            workspace_id=ws_id,
        )


def test_db_keyword_cache_edge_cases(db_conn: psycopg.Connection[Any]) -> None:
    empty_result = db.get_fresh_keywords_from_cache(
        db_conn,
        provider="fake",
        locale="pt-BR",
        market="br",
        normalized_keywords=[],
    )
    assert empty_result == {}
    db.upsert_keyword_cache(db_conn, provider="fake", locale="pt-BR", market="br", keywords_data=[])
    db.upsert_keyword_cache(
        db_conn,
        provider="fake",
        locale="pt-BR",
        market="br",
        keywords_data=[{"keyword": ""}],
    )


def test_resolve_provider_defaults() -> None:
    from engine.integrations.fakes import FakeKeywordProvider
    from engine.seo import _resolve_provider

    fake_p = _resolve_provider(None, "fake")
    assert isinstance(fake_p, FakeKeywordProvider)
    dataforseo_p = _resolve_provider(None, "dataforseo")
    assert isinstance(dataforseo_p, DataForSEOKeywordProvider)


def test_dataforseo_monthly_cap_with_run(
    db_conn: psycopg.Connection[Any],
) -> None:
    ws_id, camp_id, _ = _seed_test_workspace_and_campaign(
        db_conn,
        run_cap_usd=Decimal("5.00"),
        monthly_cap_usd=Decimal("5.00"),
    )
    earlier_run_id = _seed_test_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        status="completed",
        run_cap_usd=Decimal("5.00"),
        cost_usd=Decimal("5.00"),
    )
    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=earlier_run_id,
        kind="api",
        vendor="dataforseo",
        stage="keywords",
        units=Decimal("100.000"),
        cost_usd=Decimal("5.000000"),
    )
    current_run_id = _seed_test_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        run_cap_usd=Decimal("5.00"),
        cost_usd=Decimal("0.00"),
    )
    db_conn.commit()

    provider = DataForSEOKeywordProvider()
    with pytest.raises(budget.MonthlyCapReached) as exc_info:
        provider.lookup(
            ["seed_monthly_cap_with_run"],
            locale="pt-BR",
            market="br",
            conn=db_conn,
            workspace_id=ws_id,
            run_id=current_run_id,
        )
    assert exc_info.value.workspace_id == ws_id
    assert exc_info.value.monthly_cap_usd == Decimal("5.00")


def test_dataforseo_lookup_with_active_run_success(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_id, camp_id, _ = _seed_test_workspace_and_campaign(db_conn)
    run_id = _seed_test_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        run_cap_usd=Decimal("5.00"),
        cost_usd=Decimal("0.00"),
    )
    _mock_dataforseo_http(
        monkeypatch,
        b'{"tasks": [{"result": [{"keyword": "kw", "search_volume": 100}]}]}',
    )

    provider = DataForSEOKeywordProvider()
    res = provider.lookup(
        ["kw"],
        locale="pt-BR",
        market="br",
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
    )
    assert len(res) == 1
    assert res[0].search_volume == 100


def test_dataforseo_lookup_workspace_without_campaigns(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-nocamp-{uuid.uuid4().hex[:6]}",
        name="No Camp",
        industry_pack="general",
    )
    _mock_dataforseo_http(
        monkeypatch,
        (
            b'{"tasks": [{"result": ["not-a-dict", {"no_keyword": 1}, '
            b'{"keyword": "kw", "search_volume": 100}]}]}'
        ),
    )

    provider = DataForSEOKeywordProvider()
    res = provider.lookup(["kw"], locale="pt-BR", market="br", conn=db_conn, workspace_id=ws_id)
    assert len(res) == 1
    assert res[0].search_volume == 100
