"""Deterministic JSON-LD structured data generator and validator (M6-01 D3, ADR-028)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from engine.contracts import BrandProfile, ContentDraft

SCHEMA_CONTEXT = "https://schema.org"
SUPPORTED_SCHEMA_TYPES: tuple[str, ...] = (
    "Article",
    "FAQPage",
    "Product",
    "LocalBusiness",
    "HowTo",
)

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "Article": ("headline", "author", "publisher"),
    "FAQPage": ("mainEntity",),
    "Product": ("name", "offers"),
    "LocalBusiness": ("name", "address"),
    "HowTo": ("name", "step"),
}

_RECOMMENDED_FIELDS: dict[str, tuple[str, ...]] = {
    "Article": ("description", "inLanguage"),
    "FAQPage": ("inLanguage",),
    "Product": ("description", "brand"),
    "LocalBusiness": ("telephone", "url"),
    "HowTo": ("description", "inLanguage"),
}

_NESTED_REQUIREMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "Article": {"author": ("name",), "publisher": ("name",)},
    "Product": {"offers": ("price", "priceCurrency")},
}

_STEP_HEADING_RE = re.compile(r"^\s*#{2,3}\s+(.+?)\s*#*\s*$")


class SchemaValidationResult(BaseModel):
    """Structured validation outcome for one or more JSON-LD blocks."""

    valid: bool
    types: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    recommended_missing: list[str] = Field(default_factory=list)
    nested_issues: list[str] = Field(default_factory=list)


def _coerce_brand_name(brand: BrandProfile | Mapping[str, Any] | None) -> str:
    if isinstance(brand, BrandProfile):
        return brand.positioning.strip() or "Publisher"
    if isinstance(brand, Mapping):
        return str(brand.get("name") or brand.get("positioning") or "Publisher").strip()
    return "Publisher"


def _build_article(
    draft: ContentDraft | None,
    brand: BrandProfile | Mapping[str, Any] | None,
    locale: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    brand_name = _coerce_brand_name(brand)
    headline = str(extra.get("headline") or (draft.title if draft else "")).strip()
    description = str(extra.get("description") or (draft.meta_description if draft else "")).strip()
    author_name = str(extra.get("author") or brand_name).strip()
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": "Article",
        "headline": headline,
        "description": description,
        "inLanguage": locale,
        "author": {"@type": "Organization", "name": author_name},
        "publisher": {"@type": "Organization", "name": brand_name},
    }


def _build_faq(
    draft: ContentDraft | None,
    brand: BrandProfile | Mapping[str, Any] | None,
    locale: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    _ = brand
    raw_faq = extra.get("faq") or (draft.faq if draft else [])
    entities = []
    for item in raw_faq:
        q = str(item.get("question") or item.get("q") or "").strip()
        a = str(item.get("answer") or item.get("a") or "").strip()
        if q and a:
            entities.append(
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
            )
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": "FAQPage",
        "inLanguage": locale,
        "mainEntity": entities,
    }


def _build_product(
    draft: ContentDraft | None,
    brand: BrandProfile | Mapping[str, Any] | None,
    locale: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    brand_name = _coerce_brand_name(brand)
    name = str(extra.get("name") or (draft.title if draft else "")).strip()
    description = str(extra.get("description") or (draft.meta_description if draft else "")).strip()
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": "Product",
        "name": name,
        "description": description,
        "inLanguage": locale,
        "brand": {"@type": "Brand", "name": brand_name},
        "offers": {
            "@type": "Offer",
            "price": str(extra.get("price") or "0.00"),
            "priceCurrency": str(
                extra.get("priceCurrency") or ("BRL" if locale.startswith("pt") else "USD")
            ),
        },
    }


def _build_local_business(
    draft: ContentDraft | None,
    brand: BrandProfile | Mapping[str, Any] | None,
    locale: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    _ = draft
    name = str(extra.get("name") or _coerce_brand_name(brand)).strip()
    payload: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "LocalBusiness",
        "name": name,
        "address": extra.get("address") or "",
        "inLanguage": locale,
    }
    for opt_key in ("telephone", "url", "openingHours"):
        if extra.get(opt_key):
            payload[opt_key] = extra[opt_key]
    return payload


def _extract_howto_steps(
    draft: ContentDraft | None, extra: Mapping[str, Any]
) -> list[dict[str, Any]]:
    raw_steps = extra.get("steps")
    if isinstance(raw_steps, list):
        return [
            {"@type": "HowToStep", "position": idx, "name": str(step).strip()}
            for idx, step in enumerate(raw_steps, start=1)
            if str(step).strip()
        ]
    if draft is None:
        return []
    steps: list[dict[str, Any]] = []
    for line in draft.body_md.splitlines():
        match = _STEP_HEADING_RE.match(line)
        if match:
            steps.append(
                {"@type": "HowToStep", "position": len(steps) + 1, "name": match.group(1).strip()}
            )
    return steps


def _build_howto(
    draft: ContentDraft | None,
    brand: BrandProfile | Mapping[str, Any] | None,
    locale: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    _ = brand
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": "HowTo",
        "name": str(extra.get("name") or (draft.title if draft else "")).strip(),
        "description": str(
            extra.get("description") or (draft.meta_description if draft else "")
        ).strip(),
        "inLanguage": locale,
        "step": _extract_howto_steps(draft, extra),
    }


_BUILDERS: dict[
    str,
    Callable[
        [ContentDraft | None, BrandProfile | Mapping[str, Any] | None, str, Mapping[str, Any]],
        dict[str, Any],
    ],
] = {
    "Article": _build_article,
    "FAQPage": _build_faq,
    "Product": _build_product,
    "LocalBusiness": _build_local_business,
    "HowTo": _build_howto,
}


def generate_schema(
    schema_type: str,
    *,
    draft: ContentDraft | None = None,
    brand: BrandProfile | Mapping[str, Any] | None = None,
    locale: str = "pt-BR",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a Schema.org JSON-LD dictionary for supported content/site types."""
    builder = _BUILDERS.get(schema_type)
    if builder is None:
        supported = ", ".join(SUPPORTED_SCHEMA_TYPES)
        msg = f"Unsupported schema_type '{schema_type}'. Supported: {supported}"
        raise ValueError(msg)
    return builder(draft, brand, locale, extra or {})


def _field_present(block: Mapping[str, Any], field: str) -> bool:
    val = block.get(field)
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, list | dict):
        return len(val) > 0
    return True


def _flatten_jsonld(
    raw: Mapping[str, Any] | Sequence[Any] | str,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    if isinstance(raw, str):
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            return [], [f"Invalid JSON-LD: {exc.msg} at line {exc.lineno}"]
    else:
        parsed = raw

    if isinstance(parsed, Sequence) and not isinstance(parsed, str | bytes):
        return [item for item in parsed if isinstance(item, Mapping)], []
    if isinstance(parsed, Mapping) and isinstance(parsed.get("@graph"), list):
        return [item for item in parsed["@graph"] if isinstance(item, Mapping)], []
    if isinstance(parsed, Mapping):
        return [parsed], []
    return [], ["JSON-LD payload must be a JSON object or array."]


def _lang_matches(expected_locale: str, schema_lang: Any) -> bool:
    if not isinstance(schema_lang, str) or not schema_lang.strip():
        return True
    exp = expected_locale.strip().lower().replace("_", "-")
    act = schema_lang.strip().lower().replace("_", "-")
    return exp == act or exp.split("-")[0] == act.split("-")[0]


def _check_nested(block: Mapping[str, Any], schema_type: str) -> list[str]:
    issues: list[str] = []
    for parent, subfields in _NESTED_REQUIREMENTS.get(schema_type, {}).items():
        obj = block.get(parent)
        first = obj[0] if isinstance(obj, list) and obj else obj
        if isinstance(first, Mapping):
            for sf in subfields:
                if not _field_present(first, sf):
                    issues.append(f"{parent}.{sf}")
    return issues


def _validate_single_block(
    block: Mapping[str, Any],
    expected_locale: str | None,
) -> tuple[str | None, list[str], list[str], list[str]]:
    raw_type = block.get("@type")
    primary = raw_type[0] if isinstance(raw_type, list) and raw_type else raw_type
    if not isinstance(primary, str) or not primary.strip():
        return None, ["JSON-LD block is missing @type."], [], []

    block_errors = [
        f"{primary} missing required field '{req}'."
        for req in _REQUIRED_FIELDS.get(primary, ())
        if not _field_present(block, req)
    ]
    rec_missing = [
        f"{primary}.{rec}"
        for rec in _RECOMMENDED_FIELDS.get(primary, ())
        if not _field_present(block, rec)
    ]
    nested_issues = _check_nested(block, primary)
    block_errors.extend(f"{primary} missing nested field '{item}'." for item in nested_issues)
    if expected_locale and not _lang_matches(expected_locale, block.get("inLanguage")):
        in_lang = block.get("inLanguage")
        block_errors.append(f"{primary} inLanguage '{in_lang}' mismatches '{expected_locale}'.")
    return primary, block_errors, rec_missing, nested_issues


def validate_schema(
    payload: Mapping[str, Any] | Sequence[Any] | str,
    *,
    expected_locale: str | None = None,
) -> SchemaValidationResult:
    """Validate JSON-LD structure, required/recommended fields, nested objects, and locale."""
    blocks, errors = _flatten_jsonld(payload)
    types: list[str] = []
    rec_missing: list[str] = []
    nested_issues: list[str] = []

    for block in blocks:
        primary, b_errors, b_rec, b_nested = _validate_single_block(block, expected_locale)
        if primary is not None:
            types.append(primary)
        errors.extend(b_errors)
        rec_missing.extend(b_rec)
        nested_issues.extend(b_nested)

    return SchemaValidationResult(
        valid=not errors and bool(blocks),
        types=types,
        errors=errors,
        recommended_missing=rec_missing,
        nested_issues=nested_issues,
    )
