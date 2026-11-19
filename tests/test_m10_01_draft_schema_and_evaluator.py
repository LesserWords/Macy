"""M10-01: typed draft FAQ/CTA schema, evaluator model label, live external draft evaluation."""

from __future__ import annotations

import importlib
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from engine import db, services
from engine.contracts import ContentDraft, DraftCTA, FAQItem, QualityReport

evaluate_mod = importlib.import_module("engine.quality.evaluate")


def _propertyless_objects(node: Any, path: str = "$") -> list[str]:
    """Return JSON-schema paths of object nodes that declare no explicit properties."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and not node.get("properties"):
            found.append(path)
        for key, value in node.items():
            found.extend(_propertyless_objects(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            found.extend(_propertyless_objects(value, f"{path}[{idx}]"))
    return found


def test_content_draft_schema_declares_explicit_faq_and_cta_properties() -> None:
    schema = ContentDraft.model_json_schema()
    defs = schema["$defs"]
    assert {"question", "answer"} <= set(defs["FAQItem"]["properties"])
    assert {"headline", "body", "button_text"} <= set(defs["DraftCTA"]["properties"])
    assert _propertyless_objects(schema) == []


def test_content_draft_coerces_legacy_faq_and_cta_dicts() -> None:
    draft = ContentDraft(
        title="t",
        meta_description="m",
        slug="s",
        body_md="b",
        faq=[{"q": "Why?", "a": "Because."}, {"question": "How?", "answer": "Like so."}],
        cta={"text": "Book a demo", "url": "https://example.com"},
    )
    assert draft.faq[0] == FAQItem(question="Why?", answer="Because.")
    assert draft.faq[1].get("question") == "How?"
    assert isinstance(draft.cta, DraftCTA)
    assert draft.cta.get("body") == "Book a demo"
    assert draft.cta.get("url") == "https://example.com"
    assert draft.cta.get("intensity") is None


def test_content_draft_accepts_empty_defaults_and_cta_intensity() -> None:
    draft = ContentDraft(title="t", meta_description="m", slug="s", body_md="b")
    assert draft.faq == []
    assert not any(draft.cta.values())
    with_intensity = ContentDraft(
        title="t",
        meta_description="m",
        slug="s",
        body_md="b",
        cta={"headline": "Go", "intensity": "soft"},
    )
    assert with_intensity.cta.get("intensity") == "soft"
    assert "intensity" in dict(with_intensity.cta.items())


def test_llm_rubric_call_records_workspace_adapter_model_not_fake_label() -> None:
    adapter = MagicMock()
    adapter.settings.models_by_stage = {"evaluate": "anthropic/claude-sonnet-5.5"}
    draft = ContentDraft(title="t", meta_description="m", slug="s", body_md="b")
    raw = QualityReport(verdict="PASS", findings=[])
    with (
        patch("engine.llm._resolve_llm_adapter", return_value=adapter) as resolver,
        patch("engine.llm.structured_call", return_value=raw) as call,
    ):
        _, label, _ = evaluate_mod._run_llm_rubric_call(
            draft_model=draft,
            brief=None,
            evidence=None,
            rubric_list=[],
            det_report=MagicMock(),
            brand_profile=None,
            brand_version=1,
            locale="en-US",
            funnel_stage="mofu",
            format_slug="blog",
            industry_slug=None,
            conn=MagicMock(),
            workspace_id=uuid.uuid4(),
        )
    assert label == "anthropic/claude-sonnet-5.5"
    resolver.assert_called_once()
    assert call.call_args.kwargs["plugin"] is adapter


@pytest.mark.parametrize(
    ("kwargs", "expected_offline"),
    [({}, False), ({"offline": True}, True)],
)
def test_import_external_draft_forwards_offline_flag(
    kwargs: dict[str, Any], expected_offline: bool
) -> None:
    captured: dict[str, Any] = {}

    def _fake_execute(_conn: Any, params: dict[str, Any]) -> dict[str, Any]:
        captured.update(params)
        return {"ok": True}

    with patch.object(services, "_execute_import_external_draft", _fake_execute):
        services.import_external_draft(
            MagicMock(),
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            markdown_text="# Title\n\nBody text here.",
            topic="Topic",
            deterministic_only=False,
            **kwargs,
        )
    assert captured["offline"] is expected_offline


def test_execute_import_external_draft_passes_offline_to_evaluate() -> None:
    seen: dict[str, Any] = {}

    def _fake_eval(_conn: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.update(params)
        report = QualityReport(verdict="PASS", findings=[])
        return {"report": report, "draft": {"status": "approved"}}

    draft_row = {"id": str(uuid.uuid4())}
    draft = ContentDraft(title="t", meta_description="m", slug="s", body_md="b")
    brief_id = uuid.uuid4()
    with (
        patch.object(services, "require_member"),
        patch.object(db, "get_brief_writing_context", return_value={"run_id": str(uuid.uuid4())}),
        patch.object(db, "get_latest_draft_for_brief", return_value=None),
        patch.object(db, "create_next_draft_revision", return_value=draft_row),
        patch("engine.production.writer.validate_draft_claim_provenance", return_value=[]),
        patch.object(services, "_execute_evaluate_draft", _fake_eval),
    ):
        services._execute_import_external_draft(
            MagicMock(),
            {
                "workspace_id": uuid.uuid4(),
                "user_id": uuid.uuid4(),
                "draft_model": draft,
                "brief_id": brief_id,
                "deterministic_only": False,
                "offline": False,
            },
        )
    assert seen["offline"] is False
    assert seen["deterministic_only"] is False
