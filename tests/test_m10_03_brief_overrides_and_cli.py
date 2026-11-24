"""M10-03: MCP brief format_slug/funnel_stage overrides, CLI argv[0] guard, CLI editorial verbs."""

from __future__ import annotations

import importlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from engine import cli, db, services
from engine.contracts import EditorialBrief, QualityReport
from engine.mcp.auth import BriefRequest
from engine.mcp.server import ServiceStoreAdapter

brief_mod = importlib.import_module("engine.strategy.brief")


def test_service_store_adapter_forwards_format_slug_and_funnel_stage() -> None:
    adapter = ServiceStoreAdapter()
    ws_id = uuid.uuid4()
    opp_id = uuid.uuid4()
    user_id = uuid.uuid4()
    req = BriefRequest(
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        force_regenerate=False,
        offline=True,
        format_slug="linkedin_post",
        funnel_stage="bofu",
    )
    with patch.object(services, "get_or_create_mcp_brief", return_value={"id": "b1"}) as mock_fn:
        res = adapter.get_or_create_brief(req)
    assert res == {"id": "b1"}
    mock_fn.assert_called_once_with(
        workspace_id=ws_id,
        opportunity_id=opp_id,
        user_id=user_id,
        force_regenerate=False,
        offline=True,
        format_slug="linkedin_post",
        funnel_stage="bofu",
    )


def test_execute_get_or_create_mcp_brief_regenerates_when_format_or_funnel_differs() -> None:
    ws_id = uuid.uuid4()
    opp_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run_id = uuid.uuid4()
    existing_row = {
        "id": uuid.uuid4(),
        "version": 1,
        "payload": {"format": "blog", "primary_keyword": "kw", "outline": ["a"]},
        "funnel_stage": "tofu",
    }
    new_row = {
        "id": uuid.uuid4(),
        "version": 2,
        "payload": {"format": "linkedin_post", "primary_keyword": "kw2", "outline": ["b"]},
        "funnel_stage": "mofu",
    }
    captured_gen: dict[str, Any] = {}

    def _fake_gen(_conn: Any, params: dict[str, Any]) -> dict[str, Any]:
        captured_gen.update(params)
        return new_row

    with (
        patch.object(services, "_ensure_mcp_offline_allowed"),
        patch.object(services, "require_member"),
        patch.object(
            db,
            "get_opportunity_context",
            return_value={"run_id": str(run_id), "funnel_stage": "tofu"},
        ),
        patch.object(db, "get_latest_brief_for_opportunity", return_value=existing_row),
        patch.object(services, "_execute_preflight_mcp_spend"),
        patch.object(services, "_resolve_mcp_llm", return_value=(MagicMock(), True)),
        patch.object(services, "_execute_generate_brief", side_effect=_fake_gen),
        patch.object(services, "_resolve_brief_cited_documents", return_value=[]),
    ):
        out = services.get_or_create_mcp_brief(
            MagicMock(),
            workspace_id=ws_id,
            opportunity_id=opp_id,
            user_id=user_id,
            offline=True,
            format_slug="linkedin_post",
            funnel_stage="mofu",
        )
    assert out["created"] is True
    assert captured_gen["format_slug"] == "linkedin_post"
    assert captured_gen["funnel_stage"] == "mofu"

    # Also test when format matches ("blog"), only funnel_stage differs ("tofu" vs "bofu"),
    # and when both format and funnel_stage match (reuses existing without regenerating).
    assert (
        services._brief_needs_regeneration(
            existing_row,
            {"funnel_stage": "tofu"},
            {"format_slug": "blog", "funnel_stage": "bofu"},
        )
        is True
    )
    assert (
        services._brief_needs_regeneration(
            existing_row,
            {"funnel_stage": "tofu"},
            {"format_slug": "blog", "funnel_stage": "tofu"},
        )
        is False
    )
    assert (
        services._brief_needs_regeneration(
            None,
            {"funnel_stage": "tofu"},
            {"format_slug": "blog"},
        )
        is True
    )


def test_strategy_brief_applies_format_slug_and_funnel_stage_overrides() -> None:
    ws_id = uuid.uuid4()
    opp_id = uuid.uuid4()
    run_id = uuid.uuid4()
    brand_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    opp_ctx = {
        "workspace_id": str(ws_id),
        "run_id": str(run_id),
        "status": "approved",
        "funnel_stage": "tofu",
        "angle": "Angle",
        "primary_document_id": str(doc_id),
        "record_payload": {"headline": "Headline", "summary": "Summary"},
    }
    brand_row = {
        "id": str(brand_id),
        "version": 1,
        "payload": {
            "voice": "Direct and evidence-backed",
            "positioning": "B2B operational platform",
        },
    }
    fake_brief = EditorialBrief(
        topic="Headline",
        target_audience="Ops",
        primary_keyword="headline",
        format="blog",
        angle="Angle",
        outline=["H2"],
        required_facts=[],
        cta_goal="CTA",
    )
    captured_input: dict[str, Any] = {}

    def _fake_call_llm(**kwargs: Any) -> EditorialBrief:
        captured_input.update(kwargs["input"])
        return fake_brief

    with (
        patch.object(brief_mod.db, "get_opportunity_context", return_value=opp_ctx),
        patch.object(brief_mod.db, "get_current_brand_profile", return_value=brand_row),
        patch.object(brief_mod, "_gather_evidence_documents", return_value=[]),
        patch.object(brief_mod, "_resolve_brief_keywords", return_value=[]),
        patch.object(
            brief_mod,
            "_safe_load_format_pack",
            side_effect=lambda slug: {"slug": slug, "name": slug},
        ) as fmt_loader,
        patch.object(brief_mod, "_call_brief_llm", side_effect=_fake_call_llm),
        patch.object(
            brief_mod.db,
            "create_next_brief_version",
            side_effect=lambda _c, **kw: {"id": str(uuid.uuid4()), "version": 1, **kw},
        ),
        patch.object(brief_mod.events, "emit"),
    ):
        row = brief_mod.brief(
            opp_id,
            conn=MagicMock(),
            workspace_id=ws_id,
            offline=True,
            format_slug="newsletter",
            funnel_stage="BOFU",
        )
    fmt_loader.assert_called_with("newsletter")
    assert captured_input["opportunity"]["funnel_stage"] == "bofu"
    assert captured_input["format"] == "newsletter"
    assert row["payload"]["format"] == "newsletter"


def test_cli_main_executes_when_file_arg_contains_pytest_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    draft_path = tmp_path / "pytest-nested-draft.md"
    draft_path.write_text("# Sample Title\n\nBody paragraph.", encoding="utf-8")
    assert "pytest" in str(draft_path).lower()

    fake_res = {
        "title": "Sample Title",
        "verdict": "PASS",
        "findings": [],
        "stateless": True,
    }
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "macy",
            "import-draft",
            "--stateless",
            "--file",
            str(draft_path),
            "--primary-keyword",
            "sample",
            "--json",
        ],
    )
    with patch.object(services, "check_draft_stateless", return_value=fake_res) as mock_chk:
        rc = cli.main(None)
    assert rc == 0
    mock_chk.assert_called_once()
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "PASS"

    monkeypatch.setattr(sys, "argv", ["pytest", "-q"])
    assert cli.main(None) == 0
    assert "usage: macy" in capsys.readouterr().out.lower()


def test_cli_editorial_stage_subcommands_dispatch_to_services(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    brief_id = uuid.uuid4()
    draft_id = uuid.uuid4()

    report_obj = QualityReport(verdict="PASS", findings=[], score=92.0)

    with (
        patch.object(cli, "_resolve_workspace_and_owner", return_value=(ws_id, owner_id)),
        patch.object(
            services,
            "decide_topic",
            return_value={"id": str(topic_id), "status": "approved"},
        ) as m_decide_topic,
        patch.object(
            services,
            "get_or_create_mcp_brief",
            return_value={"id": str(brief_id), "version": 1, "primary_keyword": "kw"},
        ) as m_brief,
        patch.object(
            services,
            "produce_and_evaluate_draft",
            return_value={
                "draft": {"id": str(draft_id), "revision": 0, "status": "evaluated"},
                "report": report_obj,
                "verdict": "PASS",
            },
        ) as m_write,
        patch.object(
            services,
            "evaluate_draft",
            return_value={
                "draft": {"id": str(draft_id), "status": "evaluated"},
                "report": report_obj,
            },
        ) as m_eval,
        patch.object(
            services,
            "decide_draft",
            return_value={"id": str(draft_id), "status": "approved"},
        ) as m_decide_draft,
        patch.object(
            services,
            "publish_draft",
            return_value={"id": str(uuid.uuid4()), "status": "published"},
        ) as m_pub,
    ):
        assert (
            cli.main(
                [
                    "decide-topic",
                    "--workspace",
                    "acme",
                    "--topic-id",
                    str(topic_id),
                    "--decision",
                    "approved",
                    "--note",
                    "Go ahead",
                    "--json",
                ]
            )
            == 0
        )
        m_decide_topic.assert_called_once()

        assert (
            cli.main(
                [
                    "brief",
                    "--workspace",
                    "acme",
                    "--topic-id",
                    str(topic_id),
                    "--format-slug",
                    "blog",
                    "--funnel-stage",
                    "mofu",
                    "--offline",
                    "--json",
                ]
            )
            == 0
        )
        m_brief.assert_called_once()

        assert (
            cli.main(
                [
                    "write",
                    "--workspace",
                    "acme",
                    "--brief-id",
                    str(brief_id),
                    "--offline",
                    "--json",
                ]
            )
            == 0
        )
        m_write.assert_called_once()

        assert (
            cli.main(
                [
                    "evaluate",
                    "--workspace",
                    "acme",
                    "--draft-id",
                    str(draft_id),
                    "--deterministic-only",
                    "--offline",
                    "--json",
                ]
            )
            == 0
        )
        m_eval.assert_called_once()

        assert (
            cli.main(
                [
                    "decide-draft",
                    "--workspace",
                    "acme",
                    "--draft-id",
                    str(draft_id),
                    "--decision",
                    "approved",
                    "--note",
                    "Ship it",
                    "--json",
                ]
            )
            == 0
        )
        m_decide_draft.assert_called_once()

        assert (
            cli.main(
                [
                    "publish",
                    "--workspace",
                    "acme",
                    "--draft-id",
                    str(draft_id),
                    "--channel",
                    "markdown_export",
                    "--json",
                ]
            )
            == 0
        )
        m_pub.assert_called_once()

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip().startswith("{")]
    assert len(lines) >= 1

    # Verify CLI error paths return exit code 1 cleanly
    with patch.object(cli, "_resolve_workspace_and_owner", side_effect=RuntimeError("db down")):
        assert (
            cli.main(
                [
                    "decide-topic",
                    "--workspace",
                    "acme",
                    "--topic-id",
                    str(topic_id),
                    "--decision",
                    "approved",
                ]
            )
            == 1
        )
        assert cli.main(["brief", "--workspace", "acme", "--topic-id", str(topic_id)]) == 1
        assert cli.main(["write", "--workspace", "acme", "--brief-id", str(brief_id)]) == 1
        assert cli.main(["evaluate", "--workspace", "acme", "--draft-id", str(draft_id)]) == 1
        assert (
            cli.main(
                [
                    "decide-draft",
                    "--workspace",
                    "acme",
                    "--draft-id",
                    str(draft_id),
                    "--decision",
                    "approved",
                ]
            )
            == 1
        )
        assert cli.main(["publish", "--workspace", "acme", "--draft-id", str(draft_id)]) == 1
