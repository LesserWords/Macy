"""Tests for deterministic JSON-LD schema generation and validation (M6-01 D3)."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from engine.contracts import BrandProfile, ContentDraft, PublishRequest
from engine.integrations.markdown_export import MarkdownExportPublisher
from engine.production import schema


def _sample_draft() -> ContentDraft:
    return ContentDraft(
        title="Guia completo de gestao de estoque para oticas",
        meta_description=(
            "Aprenda a controlar o estoque da sua otica com curva ABC e indicadores "
            "operacionais claros para aumentar margem e giro mensal."
        ),
        slug="gestao-estoque-oticas",
        body_md=(
            "# Guia completo de gestao de estoque para oticas\n\n"
            "## Passo 1: Classificar armacoes por giro\n\n"
            "Separe os modelos por curva ABC semanalmente.\n\n"
            "## Passo 2: Ajustar reposicao automatica\n\n"
            "Defina estoque minimo por categoria de lente e armacao."
        ),
        faq=[
            {
                "question": "Como iniciar o controle de estoque na otica?",
                "answer": "Realize inventario inicial por categoria e defina giro minimo.",
            }
        ],
    )


def test_generate_and_validate_all_five_schema_types() -> None:
    """Verify M6-01 D3: Article, FAQPage, Product, LocalBusiness, and HowTo JSON-LD."""
    draft = _sample_draft()
    brand = BrandProfile(voice="Consultivo", positioning="Acme Content OS para oticas")

    article = schema.generate_schema("Article", draft=draft, brand=brand, locale="pt-BR")
    assert article["@context"] == "https://schema.org"
    assert article["@type"] == "Article"
    assert article["inLanguage"] == "pt-BR"
    assert schema.validate_schema(article, expected_locale="pt-BR").valid is True

    faq = schema.generate_schema("FAQPage", draft=draft, locale="pt-BR")
    assert faq["@type"] == "FAQPage"
    assert len(faq["mainEntity"]) == 1
    assert schema.validate_schema(faq, expected_locale="pt-BR").valid is True

    product = schema.generate_schema(
        "Product",
        draft=draft,
        brand=brand,
        locale="pt-BR",
        extra={"name": "Acme Content OS", "price": "299.00", "priceCurrency": "BRL"},
    )
    assert product["@type"] == "Product"
    assert product["offers"]["price"] == "299.00"
    assert schema.validate_schema(product, expected_locale="pt-BR").valid is True

    local_biz = schema.generate_schema(
        "LocalBusiness",
        brand=brand,
        locale="pt-BR",
        extra={
            "name": "Otica Modelo",
            "address": "Av Paulista 1000, Sao Paulo - SP",
            "telephone": "+55-11-99999-0000",
        },
    )
    assert local_biz["@type"] == "LocalBusiness"
    assert schema.validate_schema(local_biz, expected_locale="pt-BR").valid is True

    howto = schema.generate_schema("HowTo", draft=draft, locale="pt-BR")
    assert howto["@type"] == "HowTo"
    assert len(howto["step"]) == 2
    assert schema.validate_schema(howto, expected_locale="pt-BR").valid is True

    with pytest.raises(ValueError, match="Unsupported schema_type"):
        schema.generate_schema("Recipe", draft=draft)


def test_validate_schema_graph_missing_fields_nested_and_locale() -> None:
    """Verify @graph flattening, missing required/nested fields, invalid JSON, and locale."""
    graph_payload = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": "Clinical myopia guide",
                "inLanguage": "en-US",
                "author": {"@type": "Person", "name": "Dr. Jane"},
                "publisher": {"@type": "Organization", "name": "Vision Clinic"},
            }
        ],
    }
    graph_res = schema.validate_schema(graph_payload, expected_locale="en-US")
    assert graph_res.valid is True
    assert graph_res.types == ["Article"]

    # Locale mismatch
    mismatch = schema.validate_schema(graph_payload, expected_locale="pt-BR")
    assert mismatch.valid is False
    assert any("inLanguage" in err for err in mismatch.errors)

    # Missing required & nested fields, plus missing inLanguage still passes locale check
    no_lang_product = {
        "@type": "Product",
        "name": "Lens",
        "offers": {"@type": "Offer", "price": "99.90", "priceCurrency": "BRL"},
    }
    assert schema.validate_schema(no_lang_product, expected_locale="pt-BR").valid is True
    broken_product = {"@type": "Product", "name": "Lens", "offers": {"@type": "Offer"}}
    broken_res = schema.validate_schema(broken_product)
    assert broken_res.valid is False
    assert "offers.price" in broken_res.nested_issues
    assert broken_res.recommended_missing

    # Invalid JSON string, non-object JSON, list JSON-LD, & missing @type
    bad_json = schema.validate_schema("{not-json")
    assert bad_json.valid is False
    assert bad_json.errors
    assert schema.validate_schema("123").valid is False
    assert schema.validate_schema(graph_payload["@graph"]).valid is True
    assert schema.validate_schema({"@type": "FAQPage", "mainEntity": []}).valid is False
    assert schema.validate_schema({"@type": "HowTo", "name": "  ", "step": [1]}).valid is False
    assert schema.validate_schema({"@type": "Article", "headline": "X", "author": 1}).valid is False
    no_type = schema.validate_schema({"headline": "Missing type"})
    assert no_type.valid is False

    # Mapping brand, None draft HowTo, explicit steps, LocalBusiness optional keys
    dict_prod = schema.generate_schema(
        "Product", brand={"name": "OptiTech"}, locale="en-US", extra={"name": "Lens"}
    )
    assert dict_prod["brand"]["name"] == "OptiTech"
    assert dict_prod["offers"]["priceCurrency"] == "USD"
    assert schema.generate_schema("HowTo", extra={"steps": ["Step 1"]})["step"][0]["position"] == 1
    assert schema.generate_schema("HowTo")["step"] == []
    lb_opt = schema.generate_schema(
        "LocalBusiness",
        extra={"name": "Shop", "address": "Main St", "openingHours": "Mo-Fr 09:00-18:00"},
    )
    assert lb_opt["openingHours"] == "Mo-Fr 09:00-18:00"
    assert (
        schema.generate_schema("FAQPage", extra={"faq": [{"question": "Q", "answer": ""}]})[
            "mainEntity"
        ]
        == []
    )


def test_schema_module_has_zero_llm_or_quality_imports_and_exports_in_markdown(
    tmp_path: Path,
) -> None:
    """Verify ADR-028 modularity and optional markdown_export schema_json_ld frontmatter."""
    src = Path("engine/production/schema.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(mod.startswith(("engine.quality", "engine.llm")) for mod in imported)

    draft = _sample_draft()
    article = schema.generate_schema("Article", draft=draft, locale="pt-BR")
    pub = MarkdownExportPublisher(
        settings=MarkdownExportPublisher.Settings(export_dir=str(tmp_path))
    )
    res = pub.publish(
        PublishRequest(
            draft_id=uuid.uuid4(),
            draft=draft,
            locale="pt-BR",
            destination="blog",
            schema_json_ld=article,
        )
    )
    assert res.raw is not None
    exported_text = Path(res.raw["path"]).read_text(encoding="utf-8")
    assert "schema_json_ld:" in exported_text
    assert "Article" in exported_text
