"""Tests for M7-03 D2 Google News RSS zero-key NewsSearchPort adapter."""

from __future__ import annotations

from datetime import date

import pytest

from engine.safe_http import SafeResponse


def test_gnews_rss_discovered_and_maps_locales() -> None:
    """D2: gnews_rss entry point satisfies NewsSearchPort and validates locales."""
    from engine.integrations.gnews_rss import GoogleNewsRSSSearch, map_gnews_locale
    from engine.integrations.ports import NewsSearchPort
    from engine.integrations.registry import UnsupportedLocale, discover_plugins

    discovered = discover_plugins("news_search")["news_search"]
    assert discovered["gnews_rss"] is GoogleNewsRSSSearch

    adapter = GoogleNewsRSSSearch()
    assert isinstance(adapter, NewsSearchPort)
    assert map_gnews_locale("pt-BR", "br") == ("pt-BR", "BR", "BR:pt-419")
    assert map_gnews_locale("en-US", "us") == ("en-US", "US", "US:en")

    with pytest.raises(UnsupportedLocale, match="fr-FR"):
        map_gnews_locale("fr-FR", "fr")
    with pytest.raises(UnsupportedLocale, match="us"):
        map_gnews_locale("pt-BR", "us")


def test_gnews_rss_search_parses_filters_and_handles_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: Zero-key Google News RSS search parses items, filters since/limit, and checks errors."""
    from engine.integrations.gnews_rss import GoogleNewsRSSSearch

    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda _host: ["142.250.190.46"],
    )
    adapter = GoogleNewsRSSSearch(settings=GoogleNewsRSSSearch.Settings(max_results=2))
    adapter.healthcheck()

    rss_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>optometry - Google News</title>
    <item>
      <title>Old Study on Vision</title>
      <link>https://example.com/old</link>
      <pubDate>Tue, 01 Jan 2020 10:00:00 GMT</pubDate>
      <description>Old article</description>
    </item>
    <item>
      <title>Missing Link Entry</title>
      <description>Ignored</description>
    </item>
    <item>
      <title>Mercado Optico em Alta - Opticanet</title>
      <link>https://opticanet.com.br/noticia-1?utm_source=gnews</link>
      <pubDate>Sun, 01 Nov 2026 12:00:00 GMT</pubDate>
      <description>Crescimento de vendas em 2026</description>
      <source url="https://opticanet.com.br">Opticanet</source>
    </item>
    <item>
      <title>Novas Lentes de Controle</title>
      <link>https://abioptica.com.br/lentes-2</link>
      <pubDate>Mon, 02 Nov 2026 09:30:00 GMT</pubDate>
      <description>Artigo tecnico</description>
    </item>
    <item>
      <title>Third Recent Item Should Be Truncated</title>
      <link>https://example.com/third</link>
      <pubDate>Tue, 03 Nov 2026 09:30:00 GMT</pubDate>
      <description>Should exceed max_results=2</description>
    </item>
  </channel>
</rss>"""

    captured_urls: list[str] = []

    def fake_get(_method: str, url: str, **_kwargs: object) -> SafeResponse:
        captured_urls.append(url)
        return SafeResponse(status_code=200, headers={}, content=rss_xml)

    monkeypatch.setattr("engine.integrations.gnews_rss.safe_request", fake_get)
    records = adapter.search(
        query="mercado optico",
        locale="pt-BR",
        market="br",
        since=date(2026, 10, 1),
        domain="opticanet.com.br",
    )
    assert len(records) == 2
    assert "site%3Aopticanet.com.br" in captured_urls[0]
    assert "hl=pt-BR" in captured_urls[0]
    assert records[0].headline == "Mercado Optico em Alta - Opticanet"
    assert records[0].canonical_url == "https://opticanet.com.br/noticia-1"
    assert records[0].source == "Opticanet"
    assert records[0].verification_status == "verified"

    monkeypatch.setattr(
        "engine.integrations.gnews_rss.safe_request",
        lambda *_args, **_kwargs: SafeResponse(status_code=503, headers={}, content=b"down"),
    )
    with pytest.raises(ValueError, match="HTTP 503"):
        adapter.search("optometry", locale="en-US", market="us")
