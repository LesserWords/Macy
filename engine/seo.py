"""SEO and keyword research module (F11, M3-01, ADR-004, ADR-005, ADR-015).

Provides:
- `normalize_keyword`: idempotent, lowercased, unaccented, trimmed string normalization.
- `extract_seed_terms`: extracts target keyword terms from an opportunity candidate.
- `keywords_for`: resolves keyword metrics using 30-day cache before invoking provider.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any

from engine import db
from engine.contracts import KeywordData, Opportunity
from engine.integrations.dataforseo import DataForSEOKeywordProvider
from engine.integrations.fakes import FakeKeywordProvider
from engine.integrations.ports import KeywordProviderPort

if TYPE_CHECKING:
    import uuid

    import psycopg

__all__ = [
    "extract_seed_terms",
    "keywords_for",
    "normalize_keyword",
]


def normalize_keyword(term: str) -> str:
    """Normalize a keyword: trimmed, lowercased, unaccented, collapsed whitespace (idempotent)."""
    if not term:
        return ""
    # NFKD decomposes unicode characters (including math/compatibility symbols to base characters)
    nfkd = unicodedata.normalize("NFKD", term.strip())
    # Strip combining marks (accents, tildes, cedillas, etc.)
    unaccented = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    # Lowercase after decomposition to ensure idempotent normalization
    lowered = unaccented.lower()
    return " ".join(lowered.split())


def _clean_terms(terms: list[Any]) -> list[str]:
    return [str(t).strip() for t in terms if str(t).strip()]


def _extract_from_dict(opp: dict[str, Any]) -> list[str]:
    for key in ("seed_terms", "keywords"):
        val = opp.get(key)
        if isinstance(val, list):
            return _clean_terms(val)
    title = str(opp.get("title", "")).strip()
    angle = str(opp.get("angle", "")).strip()
    return [t for t in (title, angle) if t]


def extract_seed_terms(opportunity: Opportunity | dict[str, Any] | list[str] | str) -> list[str]:
    """Extract candidate keyword search terms from opportunity or terms argument."""
    if isinstance(opportunity, str):
        cleaned = opportunity.strip()
        return [cleaned] if cleaned else []
    if isinstance(opportunity, list):
        return _clean_terms(opportunity)
    if isinstance(opportunity, dict):
        return _extract_from_dict(opportunity)
    if hasattr(opportunity, "title"):
        title = str(getattr(opportunity, "title", "")).strip()
        angle = str(getattr(opportunity, "angle", "")).strip()
        return [t for t in (title, angle) if t]
    return []


def _resolve_provider(
    provider: KeywordProviderPort | None,
    provider_name: str,
) -> KeywordProviderPort:
    if provider is not None:
        return provider
    if provider_name == "fake":
        return FakeKeywordProvider()
    return DataForSEOKeywordProvider()


def keywords_for(  # noqa: PLR0913
    opportunity: Opportunity | dict[str, Any] | list[str] | str,
    *,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    locale: str = "pt-BR",
    market: str = "br",
    provider: KeywordProviderPort | None = None,
    provider_name: str = "dataforseo",
    max_age_days: int = 30,
) -> list[KeywordData]:
    """Retrieve search metrics for an opportunity, checking 30-day cache first (F11 AC1-AC4)."""
    raw_terms = extract_seed_terms(opportunity)
    if not raw_terms:
        return []

    norm_map = {term: normalize_keyword(term) for term in raw_terms}
    unique_norms = list(dict.fromkeys(norm_map.values()))

    cached_data: dict[str, dict[str, Any]] = {}
    if conn is not None:
        cached_data = db.get_fresh_keywords_from_cache(
            conn,
            provider=provider_name,
            locale=locale,
            market=market,
            normalized_keywords=unique_norms,
            max_age_days=max_age_days,
        )

    # Determine terms needing live provider lookup
    terms_to_fetch = [term for term in raw_terms if norm_map[term] not in cached_data]

    if terms_to_fetch:
        active_provider = _resolve_provider(provider, provider_name)
        fetched = active_provider.lookup(
            terms_to_fetch,
            locale=locale,
            market=market,
            conn=conn,
            workspace_id=workspace_id,
            run_id=run_id,
        )

        to_upsert: list[dict[str, Any]] = []
        for kw_item in fetched:
            kw_norm = normalize_keyword(kw_item.keyword)
            kw_dict = kw_item.model_dump()
            cached_data[kw_norm] = kw_dict
            to_upsert.append({"keyword_norm": kw_norm, "data": kw_dict})

        if conn is not None and to_upsert:
            db.upsert_keyword_cache(
                conn,
                provider=provider_name,
                locale=locale,
                market=market,
                keywords_data=to_upsert,
            )

    # Reconstruct output preserving original term order and requested casing
    output: list[KeywordData] = []
    for term in raw_terms:
        norm = norm_map[term]
        data = cached_data.get(norm)
        if data is not None:
            kw_dict = dict(data)
            kw_dict["keyword"] = term
            output.append(KeywordData(**kw_dict))
        else:
            output.append(
                KeywordData(
                    keyword=term,
                    search_volume=0,
                    difficulty=0.0,
                    cpc_usd=0.0,
                    intent="informational",
                    is_estimate=True,
                )
            )
    return output
