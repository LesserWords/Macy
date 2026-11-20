"""M10-02: provenance UUID validation, keyword fallback, per-source research isolation."""

from __future__ import annotations

import importlib
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from engine import research
from engine.contracts import DraftClaim, EditorialBrief, KeywordData, RequiredFact
from engine.integrations.autocomplete_keywords import AutocompleteKeywordProvider

brief_mod = importlib.import_module("engine.strategy.brief")

# --- BUG-04: UUID validation so instructor retries on source titles ---------


def test_required_fact_rejects_non_uuid_source_id() -> None:
    with pytest.raises(ValidationError, match="evidence_documents"):
        RequiredFact(fact="x", source_id="Blog Name")
    ok = RequiredFact(fact="x", source_id=str(uuid.uuid4()))
    assert uuid.UUID(str(ok.source_id))


def test_fact_claim_rejects_non_uuid_source_id_but_analysis_is_free() -> None:
    with pytest.raises(ValidationError, match="evidence_documents"):
        DraftClaim(text="c", kind="fact", source_id="Some Title")
    analysis = DraftClaim(text="c", kind="analysis", source_id=None)
    assert analysis.source_id is None
    good = DraftClaim(text="c", kind="fact", source_id=str(uuid.uuid4()))
    assert good.source_id

    writer_mod = importlib.import_module("engine.production.writer")
    try:
        DraftClaim(text="c", kind="fact", source_id="Some Title")
    except ValidationError as exc:
        val_err = exc
    with (
        patch.object(brief_mod.llm, "structured_call", side_effect=val_err),
        pytest.raises(brief_mod.InvalidEvidenceReferenceError),
    ):
        brief_mod._call_brief_llm(prompt="p", input={})
    with (
        patch.object(writer_mod.llm, "structured_call", side_effect=val_err),
        pytest.raises(writer_mod.InvalidDraftProvenanceError),
    ):
        writer_mod._call_write_llm(prompt="p", input={})

    try:
        EditorialBrief.model_validate({})
    except ValidationError as exc:
        other_err = exc
    with (
        patch.object(brief_mod.llm, "structured_call", side_effect=other_err),
        pytest.raises(ValidationError),
    ):
        brief_mod._call_brief_llm(prompt="p", input={})
    with (
        patch.object(writer_mod.llm, "structured_call", side_effect=other_err),
        pytest.raises(ValidationError),
    ):
        writer_mod._call_write_llm(prompt="p", input={})


# --- BUG-05: seed term sanitization and keyword fallback --------------------


def test_candidate_seed_terms_are_sanitized_and_capped() -> None:
    long_entity = "palavra " * 30
    ctx = {
        "record_payload": {
            "headline": 'Black Friday: fim da escala 6x1 — "o que muda"? sim!',
            "entities": [long_entity, "Rede: Varejo"],
        }
    }
    terms = brief_mod._collect_candidate_seed_terms(ctx)
    assert terms
    for term in terms:
        assert len(term) <= 60
        assert not any(ch in term for ch in ':—–"?!')
        assert "  " not in term


def test_autocomplete_lookup_tolerates_single_term_failure() -> None:
    provider = AutocompleteKeywordProvider()

    def _suggest(term: str, locale: str, market: str) -> list[str]:
        if term == "bad":
            raise ValueError("Autocomplete keyword request failed with HTTP 400")
        return ["a", "b"]

    with patch.object(provider, "suggest_related", side_effect=_suggest):
        out = provider.lookup(["good", "bad"], "pt-BR", "BR")
    by_kw = {item.keyword: item for item in out}
    assert by_kw["good"].search_volume > 0
    assert by_kw["bad"].search_volume == 0
    assert by_kw["bad"].is_estimate is True


def test_autocomplete_lookup_raises_when_every_term_fails() -> None:
    provider = AutocompleteKeywordProvider()
    with (
        patch.object(provider, "suggest_related", side_effect=ValueError("HTTP 429")),
        pytest.raises(ValueError, match="HTTP 429"),
    ):
        provider.lookup(["a", "b"], "pt-BR", "BR")


def test_resolve_brief_keywords_falls_back_when_live_provider_errors() -> None:
    ctx: dict[str, Any] = {
        "workspace_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "campaign_locale": "pt-BR",
        "campaign_market": "BR",
        "record_payload": {"headline": "Escala 6x1 nas oticas"},
    }
    estimate = [KeywordData(keyword="x", search_volume=0, difficulty=0.0, cpc_usd=0.0)]
    with (
        patch.object(brief_mod, "plugin_config", return_value=(MagicMock(), MagicMock())),
        patch.object(brief_mod.seo, "keywords_for", side_effect=ValueError("HTTP 400")),
        patch.object(brief_mod, "_estimate_keywords_with_cache", return_value=estimate) as est,
    ):
        result = brief_mod._resolve_brief_keywords(
            MagicMock(), ctx, offline=False, keyword_provider=None
        )
    assert result == estimate
    est.assert_called_once()


# --- BUG-06: per-source fault isolation -------------------------------------


def test_collect_feed_sources_isolates_failing_source() -> None:
    feed = MagicMock()

    def _fetch(url: str, since: Any = None) -> list[dict[str, Any]]:
        if "bad" in url:
            raise RuntimeError("HTTP 429")
        return [{"url": url}]

    feed.fetch_feed.side_effect = _fetch
    sources = [
        {"feed_url": ""},
        {"feed_url": "https://bad.example/rss"},
        {"feed_url": "https://ok.example/rss"},
    ]
    with patch.object(
        research, "_process_collected_items", side_effect=lambda *_a, **kw: kw["items"]
    ):
        out = research.collect_feed_sources(
            MagicMock(),
            workspace_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            since=None,
            feed_adapter=feed,
            extractor_adapter=MagicMock(),
            sources=sources,
        )
    assert out == [{"url": "https://ok.example/rss"}]
    with pytest.raises(RuntimeError, match="HTTP 429"):
        research.collect_feed_sources(
            MagicMock(),
            workspace_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            since=None,
            feed_adapter=feed,
            extractor_adapter=MagicMock(),
            sources=[{"feed_url": "https://bad.example/rss"}],
        )


def test_collect_site_search_sources_isolates_failing_source() -> None:
    news = MagicMock()

    def _search(query: str, **_kw: Any) -> list[dict[str, Any]]:
        if "bad.example" in query:
            raise RuntimeError("HTTP 429")
        return [{"query": query}]

    news.search.side_effect = _search
    sources = [{"domain": ""}, {"domain": "bad.example"}, {"domain": "ok.example"}]
    with patch.object(
        research, "_process_collected_items", side_effect=lambda *_a, **kw: kw["items"]
    ):
        out = research.collect_site_search_sources(
            MagicMock(),
            locale="pt-BR",
            market="BR",
            since=None,
            news_search_adapter=news,
            extractor_adapter=MagicMock(),
            workspace_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            sources=sources,
        )
    assert out == [{"query": "site:ok.example"}]
    with pytest.raises(RuntimeError, match="HTTP 429"):
        research.collect_site_search_sources(
            MagicMock(),
            locale="pt-BR",
            market="BR",
            since=None,
            news_search_adapter=news,
            extractor_adapter=MagicMock(),
            workspace_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            sources=[{"domain": "bad.example"}],
        )
