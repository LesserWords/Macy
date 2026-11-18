"""Internationalization and localization helpers (F19, ADR-014).

Provides bilingual support for Portuguese (pt-BR) and English (en-US), gettext _()
helper, catalog loading, locale-aware date and currency formatting, and browser
Accept-Language negotiation.
"""

from __future__ import annotations

import gettext
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

DEFAULT_LOCALE = "pt-BR"
SUPPORTED_LOCALES = ("pt-BR", "en-US")
LOCALE_CATALOG_MAP = {
    "pt-BR": "pt_BR",
    "en-US": "en_US",
}

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
_active_locale_ctx: ContextVar[str] = ContextVar("active_locale", default=DEFAULT_LOCALE)


def get_current_locale() -> str:
    """Retrieve active UI locale from Streamlit session or context variable."""
    import streamlit as st

    if hasattr(st, "session_state") and hasattr(st.session_state, "ui_locale"):
        val = str(st.session_state.ui_locale)
        if val in SUPPORTED_LOCALES:
            return val
    return _active_locale_ctx.get()


def set_current_locale(locale: str) -> None:
    """Set active UI locale in context variable and Streamlit session state if available."""
    if locale not in SUPPORTED_LOCALES:
        msg = f"Unsupported locale '{locale}'. Supported locales: {SUPPORTED_LOCALES}"
        raise ValueError(msg)
    _active_locale_ctx.set(locale)
    import streamlit as st

    if hasattr(st, "session_state"):
        st.session_state.ui_locale = locale


@lru_cache(maxsize=4)
def _get_catalog_translation(catalog_lang: str) -> gettext.NullTranslations:
    """Load compiled gettext translation catalog from disk with fallback."""
    return gettext.translation(
        domain="messages",
        localedir=str(_LOCALES_DIR),
        languages=[catalog_lang],
        fallback=True,
    )


def gettext_fn(message: str) -> str:
    """Translate user-facing string using active UI locale catalog."""
    current_loc = get_current_locale()
    catalog_lang = LOCALE_CATALOG_MAP.get(current_loc, "pt_BR")
    trans = _get_catalog_translation(catalog_lang)
    return trans.gettext(message)


# Standard gettext shorthand
_ = gettext_fn


def format_date(value: datetime | date, locale: str | None = None) -> str:
    """Format date according to target UI locale (AC4).

    pt-BR: DD/MM/YYYY
    en-US: MM/DD/YYYY
    """
    loc = locale or get_current_locale()
    if loc == "en-US":
        return value.strftime("%m/%d/%Y")
    return value.strftime("%d/%m/%Y")


def format_currency_usd(amount: Decimal | float | int, locale: str | None = None) -> str:
    """Format USD monetary amount with locale-aware number separators (AC4).

    pt-BR: R$-style separators (period for thousands, comma for decimals) while currency is USD.
    en-US: Standard US formatting (comma for thousands, period for decimals).
    """
    loc = locale or get_current_locale()
    dec = Decimal(str(amount))
    rounded = f"{dec:.2f}"
    whole, frac = rounded.split(".")

    if loc == "en-US":
        whole_formatted = f"{int(whole):,}"
        return f"$ {whole_formatted}.{frac} USD"

    # pt-BR formatting
    whole_formatted = f"{int(whole):,}".replace(",", ".")
    return f"$ {whole_formatted},{frac} USD"


def _parse_quality(params: list[str]) -> float:
    """Extract quality factor q from header parameters."""
    for param in params:
        param_clean = param.strip()
        if param_clean.startswith("q="):
            try:
                return float(param_clean[2:])
            except ValueError:
                return 0.0
    return 1.0


def resolve_initial_locale(
    accept_language: str | None = None,
    fallback: str = DEFAULT_LOCALE,
) -> str:
    """Resolve initial UI locale from HTTP Accept-Language header (F19 AC1)."""
    if not accept_language:
        return fallback

    pt_q = 0.0
    en_q = 0.0

    for item in accept_language.split(","):
        part = item.strip()
        if not part:
            continue
        subparts = part.split(";")
        lang_tag = subparts[0].strip().lower()
        q = _parse_quality(subparts[1:])

        if lang_tag.startswith("pt"):
            pt_q = max(pt_q, q)
        elif lang_tag.startswith("en"):
            en_q = max(en_q, q)

    if pt_q == 0.0 and en_q == 0.0:
        return fallback

    return "pt-BR" if pt_q >= en_q else "en-US"
