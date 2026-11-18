"""Tests for M7-03 D3 zero-key Autocomplete keyword suggestion provider."""

from __future__ import annotations

import json

import pytest

from engine.safe_http import SafeResponse


def test_autocomplete_discovered_and_maps_locales() -> None:
    """D3: autocomplete entry point implements KeywordProviderPort and validates locales."""
    from engine.integrations.autocomplete_keywords import (
        AutocompleteKeywordProvider,
        map_autocomplete_locale,
    )
    from engine.integrations.ports import KeywordProviderPort
    from engine.integrations.registry import UnsupportedLocale, discover_plugins

    discovered = discover_plugins("keyword_provider")["keyword_provider"]
    assert discovered["autocomplete"] is AutocompleteKeywordProvider

    provider = AutocompleteKeywordProvider()
    assert isinstance(provider, KeywordProviderPort)
    assert map_autocomplete_locale("pt-BR", "br") == ("pt", "BR")
    assert map_autocomplete_locale("en-US", "us") == ("en", "US")

    with pytest.raises(UnsupportedLocale, match="fr-FR"):
        map_autocomplete_locale("fr-FR", "fr")
    with pytest.raises(UnsupportedLocale, match="us"):
        map_autocomplete_locale("pt-BR", "us")


def test_autocomplete_lookup_returns_estimated_metrics_and_handles_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: Autocomplete lookup estimates volume, sets is_estimate=True, and checks errors."""
    from engine.integrations.autocomplete_keywords import AutocompleteKeywordProvider

    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda _host: ["142.250.190.46"],
    )
    provider = AutocompleteKeywordProvider()
    provider.healthcheck()

    assert provider.lookup([], locale="pt-BR", market="br") == []

    def fake_get(_method: str, url: str, **_kwargs: object) -> SafeResponse:
        if "comprar+lentes" in url:
            body = json.dumps(
                ["comprar lentes", ["comprar lentes multifocais", "comprar lentes online"]]
            )
            return SafeResponse(status_code=200, headers={}, content=body.encode("utf-8"))
        if "empty" in url:
            return SafeResponse(status_code=200, headers={}, content=b"not-valid-json")
        if "melhor" in url:
            body = json.dumps(["melhor oculos", ["melhor oculos de grau"]])
            return SafeResponse(status_code=200, headers={}, content=body.encode("utf-8"))
        if "nonlist" in url:
            return SafeResponse(status_code=200, headers={}, content=b'{"not": "a-list"}')
        return SafeResponse(status_code=200, headers={}, content=b'["other", []]')

    monkeypatch.setattr("engine.integrations.autocomplete_keywords.safe_request", fake_get)

    results = provider.lookup(
        ["comprar lentes", "empty", "unknown", "melhor oculos", "nonlist"],
        locale="pt-BR",
        market="br",
    )
    assert len(results) == 5
    assert results[0].keyword == "comprar lentes"
    assert results[0].is_estimate is True
    assert results[0].search_volume > 0
    assert results[0].intent == "transactional"
    assert results[0].cpc_usd == 0.0

    assert results[1].keyword == "empty"
    assert results[1].is_estimate is True
    assert results[1].search_volume == 0
    assert results[1].intent == "informational"

    assert results[2].search_volume == 0
    assert results[3].intent == "commercial"
    assert results[4].search_volume == 0

    monkeypatch.setattr(
        "engine.integrations.autocomplete_keywords.safe_request",
        lambda *_args, **_kwargs: SafeResponse(status_code=500, headers={}, content=b"error"),
    )
    with pytest.raises(ValueError, match="HTTP 500"):
        provider.lookup(["oculos"], locale="en-US", market="us")
