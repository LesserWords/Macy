"""Tests for engine.i18n: gettext catalog loading, date/currency formatting,
Accept-Language negotiation, and translation CI gate (F19, ADR-014).
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import write_po

from engine import i18n
from engine.i18n import _
from scripts.check_translations import check_catalog
from scripts.check_translations import main as check_translations_main


def test_i18n_translation_pt_br_and_en_us() -> None:
    i18n.set_current_locale("pt-BR")
    assert i18n.get_current_locale() == "pt-BR"
    assert _("Dashboard") == "Painel"
    assert _("Campaigns") == "Campanhas"
    assert _("Language") == "Idioma"

    i18n.set_current_locale("en-US")
    assert i18n.get_current_locale() == "en-US"
    assert _("Dashboard") == "Dashboard"
    assert _("Campaigns") == "Campaigns"
    assert _("Language") == "Language"


def test_i18n_invalid_locale_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported locale"):
        i18n.set_current_locale("fr-FR")


def test_format_date() -> None:
    test_d = date(2026, 11, 4)
    test_dt = datetime(2026, 11, 4, 15, 30)

    # pt-BR: DD/MM/YYYY
    assert i18n.format_date(test_d, locale="pt-BR") == "04/11/2026"
    assert i18n.format_date(test_dt, locale="pt-BR") == "04/11/2026"

    # en-US: MM/DD/YYYY
    assert i18n.format_date(test_d, locale="en-US") == "11/04/2026"
    assert i18n.format_date(test_dt, locale="en-US") == "11/04/2026"


def test_format_currency_usd() -> None:
    amount = Decimal("1234567.89")

    # pt-BR: period for thousands, comma for decimals, currency stays USD
    assert i18n.format_currency_usd(amount, locale="pt-BR") == "$ 1.234.567,89 USD"
    assert i18n.format_currency_usd(150, locale="pt-BR") == "$ 150,00 USD"

    # en-US: comma for thousands, period for decimals
    assert i18n.format_currency_usd(amount, locale="en-US") == "$ 1,234,567.89 USD"
    assert i18n.format_currency_usd(150, locale="en-US") == "$ 150.00 USD"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "pt-BR"),
        ("", "pt-BR"),
        ("pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7", "pt-BR"),
        ("en-US,en;q=0.9,pt-BR;q=0.8", "en-US"),
        ("en;q=0.9,pt;q=0.95", "pt-BR"),
        ("fr-FR,fr;q=0.9,es;q=0.8", "pt-BR"),
        ("invalid;header;;", "pt-BR"),
        ("pt-BR,,en;q=invalid", "pt-BR"),
    ],
)
def test_resolve_initial_locale(header: str | None, expected: str) -> None:
    assert i18n.resolve_initial_locale(header) == expected


def test_i18n_session_state_integration() -> None:
    import streamlit as st

    with patch.object(st, "session_state", create=True) as mock_state:
        mock_state.ui_locale = "en-US"
        assert i18n.get_current_locale() == "en-US"

        # Invalid locale in session state falls back to context var
        mock_state.ui_locale = "de-DE"
        i18n.set_current_locale("pt-BR")
        assert i18n.get_current_locale() == "pt-BR"

        # Setting locale updates session state
        i18n.set_current_locale("en-US")
        assert mock_state.ui_locale == "en-US"

    # Outside Streamlit (no session_state attribute)
    with patch("streamlit.session_state", create=True):
        del st.session_state
        i18n.set_current_locale("pt-BR")
        assert i18n.get_current_locale() == "pt-BR"
        i18n.set_current_locale("en-US")
        assert i18n.get_current_locale() == "en-US"


def test_ci_translation_gate_success() -> None:
    assert check_translations_main() == 0


def test_ci_translation_gate_detects_untranslated_and_fuzzy(tmp_path: Path) -> None:
    cat = Catalog(locale="pt_BR")
    cat.add("Hello", string="")  # untranslated
    cat.add("World", string="Mundo", flags=["fuzzy"])  # fuzzy
    cat.add("Valid", string="Valido")

    po_file = tmp_path / "messages.po"
    with po_file.open("wb") as f:
        write_po(f, cat)

    untranslated, fuzzy = check_catalog(po_file)
    assert "Hello" in untranslated
    assert "World" in fuzzy
    assert "Valid" not in untranslated and "Valid" not in fuzzy


def test_ci_translation_gate_missing_directory(tmp_path: Path) -> None:
    non_existent = tmp_path / "non_existent_locales"
    with patch("scripts.check_translations.LOCALES_DIR", non_existent):
        assert check_translations_main() == 1


def test_ci_translation_gate_missing_catalog(tmp_path: Path) -> None:
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    with patch("scripts.check_translations.LOCALES_DIR", locales_dir):
        assert check_translations_main() == 1
