"""Tests for modular Seovela-style LLM draft rewrite modes (M6-01 D4, ADR-028)."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engine.contracts import ContentDraft, DraftClaim
from engine.integrations.fakes import FakeLLM
from engine.production import rewrite


def _sample_draft() -> ContentDraft:
    doc_id = str(uuid.uuid4())
    return ContentDraft(
        title="Gestao de estoque em oticas independentes",
        meta_description=(
            "Aprenda praticas essenciais de gestao de estoque em oticas para reduzir "
            "perdas financeiras e aumentar giro mensal de armacoes."
        ),
        slug="gestao-estoque-oticas",
        body_md=(
            "# Gestao de estoque em oticas independentes\n\n"
            "## Diagnostico semanal\n\n"
            "Controlar giro de armacoes melhora a liquidez da loja."
        ),
        faq=[
            {
                "question": "Qual o primeiro passo na gestao de estoque?",
                "answer": "Categorizar armacoes por faixa de giro.",
            }
        ],
        cta={
            "headline": "Baixe nosso guia",
            "body": "Checklist gratuito de inventario.",
            "button_text": "Baixar guia",
        },
        claims=[
            DraftClaim(
                text="Vendas do setor optico cresceram 6,4% no ultimo ano.",
                kind="fact",
                source_id=doc_id,
            )
        ],
    )


def test_rewrite_modes_preserve_provenance_and_reject_unknown_mode() -> None:
    """Verify improve, simplify, and seo_optimize modes return ContentDraft with provenance."""
    draft = _sample_draft()
    source_id = draft.claims[0].source_id

    for mode in ("improve", "simplify", "seo_optimize"):
        out = rewrite.rewrite_draft(
            draft,
            mode=mode,
            primary_keyword="gestao de estoque",
            locale="pt-BR",
        )
        assert isinstance(out, ContentDraft)
        assert any(c.kind == "fact" and str(c.source_id) == str(source_id) for c in out.claims)

    # Also accepts raw dict draft and explicit LLMPort
    spy = MagicMock()
    fake = FakeLLM()
    spy.complete = MagicMock(side_effect=fake.complete)
    out_dict = rewrite.rewrite_draft(
        draft.model_dump(),
        mode="simplify",
        locale="en-US",
        llm_adapter=spy,
    )
    assert isinstance(out_dict, ContentDraft)
    assert spy.complete.call_count == 1

    with pytest.raises(ValueError, match="Unsupported rewrite mode"):
        rewrite.rewrite_draft(draft, mode="translate")

    # Persisted conn path forwards plugin=None to llm.structured_call
    fake_conn = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        captured: dict[str, object] = {}

        def _fake_structured_call(**kwargs: object) -> ContentDraft:
            captured.update(kwargs)
            return draft

        mp.setattr("engine.llm.structured_call", _fake_structured_call)
        res_conn = rewrite.rewrite_draft(draft, mode="seo_optimize", conn=fake_conn)
        assert res_conn == draft
        assert captured["plugin"] is None
        assert captured["conn"] is fake_conn


def test_rewrite_module_is_decoupled_from_quality_checks() -> None:
    """Verify ADR-028: rewrite.py and quality/checks.py have zero cross-imports."""
    rewrite_tree = ast.parse(Path("engine/production/rewrite.py").read_text(encoding="utf-8"))
    checks_tree = ast.parse(Path("engine/quality/checks.py").read_text(encoding="utf-8"))

    def _imports(tree: ast.Module) -> set[str]:
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        return mods

    assert not any(m.startswith("engine.quality") for m in _imports(rewrite_tree))
    assert not any(m.startswith("engine.production.rewrite") for m in _imports(checks_tree))
