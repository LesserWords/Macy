"""CI translation completeness gate (F19 AC3, ADR-014).

Verifies that all message catalogues have 0 untranslated and 0 fuzzy entries.
Fails with exit code 1 if any catalog is incomplete or fuzzy.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from babel.messages.pofile import read_po

REQUIRED_LOCALES = ("pt_BR", "en_US")
LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


def _is_empty_translation(val: Any) -> bool:
    """Check if message translation string is empty or blank."""
    if not val:
        return True
    if isinstance(val, str):
        return not val.strip()
    if isinstance(val, (list, tuple)):
        return not any(s.strip() for s in val)
    return False


def check_catalog(po_path: Path) -> tuple[list[str], list[str]]:
    """Check a .po file for untranslated and fuzzy messages."""
    untranslated: list[str] = []
    fuzzy: list[str] = []

    with po_path.open("r", encoding="utf-8") as f:
        catalog = read_po(f)

    for message in catalog:
        # Skip header entry
        if not message.id:
            continue

        if "fuzzy" in message.flags:
            fuzzy.append(str(message.id))

        if _is_empty_translation(message.string):
            untranslated.append(str(message.id))

    return untranslated, fuzzy


def main() -> int:
    """Run translation completeness check across all required locales."""
    total_untranslated = 0
    total_fuzzy = 0

    if not LOCALES_DIR.is_dir():
        print(f"Error: locales directory '{LOCALES_DIR}' not found", file=sys.stderr)
        return 1

    for locale in REQUIRED_LOCALES:
        po_path = LOCALES_DIR / locale / "LC_MESSAGES" / "messages.po"
        if not po_path.is_file():
            print(f"Error: missing catalogue file '{po_path}'", file=sys.stderr)
            return 1

        untranslated, fuzzy = check_catalog(po_path)
        if untranslated:
            print(f"[{locale}] {len(untranslated)} untranslated string(s):", file=sys.stderr)
            for msg in untranslated:
                print(f"  - {msg}", file=sys.stderr)
            total_untranslated += len(untranslated)

        if fuzzy:
            print(f"[{locale}] {len(fuzzy)} fuzzy string(s):", file=sys.stderr)
            for msg in fuzzy:
                print(f"  - {msg}", file=sys.stderr)
            total_fuzzy += len(fuzzy)

    if total_untranslated > 0 or total_fuzzy > 0:
        print(
            f"\nTranslation gate FAILED: {total_untranslated} untranslated, {total_fuzzy} fuzzy.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Translation gate PASSED: all required locales {REQUIRED_LOCALES} "
        "have 0 untranslated and 0 fuzzy messages."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
