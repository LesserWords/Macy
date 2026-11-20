"""Zero-key autocomplete keyword provider implementing KeywordProviderPort (ADR-031)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field

from engine.contracts import KeywordData
from engine.integrations.registry import UnsupportedLocale
from engine.safe_http import SafeHTTPError, safe_request, validate_safe_url

AUTOCOMPLETE_LOCALE_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("pt-BR", "br"): ("pt", "BR"),
    ("en-US", "us"): ("en", "US"),
}

_TRANSACTIONAL_MARKERS = ("buy", "price", "shop", "comprar", "preco", "preço", "loja")
_COMMERCIAL_MARKERS = ("best", "review", "vs", "melhor", "melhores", "comparativo")


def map_autocomplete_locale(locale: str, market: str) -> tuple[str, str]:
    """Map campaign (locale, market) to autocomplete (hl, gl) parameters."""
    norm_locale = locale.strip()
    norm_market = market.strip().lower()
    mapped = AUTOCOMPLETE_LOCALE_MAP.get((norm_locale, norm_market))
    if mapped is not None:
        return mapped
    if norm_locale not in ("pt-BR", "en-US"):
        raise UnsupportedLocale("autocomplete", locale)
    raise UnsupportedLocale("autocomplete", locale, market)


def _infer_intent(term: str, suggestions: list[str]) -> str:
    combined = f"{term} {' '.join(suggestions)}".lower()
    if any(token in combined for token in _TRANSACTIONAL_MARKERS):
        return "transactional"
    if any(token in combined for token in _COMMERCIAL_MARKERS):
        return "commercial"
    return "informational"


def _parse_autocomplete_payload(response_text: str) -> list[str]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return []
    return [str(item).strip() for item in payload[1] if str(item).strip()]


class AutocompleteKeywordProvider:
    """Zero-key KeywordProviderPort adapter deriving estimated demand from public suggestions."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        base_url: str = "https://suggestqueries.google.com/complete/search"
        timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify autocomplete endpoint URL against SSRF policy."""
        validate_safe_url(self.settings.base_url)

    def suggest_related(self, term: str, locale: str, market: str) -> list[str]:
        """Fetch raw autocomplete suggestion phrases for a query term."""
        hl, gl = map_autocomplete_locale(locale, market)
        params = urlencode({"client": "firefox", "hl": hl, "gl": gl, "q": term})
        url = f"{self.settings.base_url}?{params}"
        resp = safe_request("GET", url, timeout=self.settings.timeout_seconds)
        if resp.status_code >= 400:
            msg = f"Autocomplete keyword request failed with HTTP {resp.status_code}"
            raise ValueError(msg)
        return _parse_autocomplete_payload(resp.text)

    def lookup(
        self,
        terms: list[str],
        locale: str,
        market: str,
        **kwargs: Any,
    ) -> list[KeywordData]:
        """Return estimated KeywordData metrics per requested term using autocomplete signals."""
        if not terms:
            return []
        map_autocomplete_locale(locale, market)
        _ = kwargs

        results: list[KeywordData] = []
        failures: list[Exception] = []
        for term in terms:
            try:
                suggestions = self.suggest_related(term, locale, market)
            except (ValueError, SafeHTTPError, OSError) as exc:
                failures.append(exc)
                results.append(
                    KeywordData(
                        keyword=term,
                        search_volume=0,
                        difficulty=0.0,
                        cpc_usd=0.0,
                        intent="informational",
                        is_estimate=True,
                    )
                )
                continue
            count = len(suggestions)
            results.append(
                KeywordData(
                    keyword=term,
                    search_volume=min(10000, count * 150),
                    difficulty=round(min(100.0, count * 6.5), 1),
                    cpc_usd=0.0,
                    intent=_infer_intent(term, suggestions),
                    is_estimate=True,
                )
            )
        if failures and len(failures) == len(terms):
            raise failures[0]
        return results
