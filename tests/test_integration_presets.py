"""Tests for M7-03 D4 stack presets across services, CLI, and Integrations UI."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import patch

import psycopg
import pytest


def test_presets_list_and_apply_across_ports_and_ssrf_guards(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4: apply_integration_preset configures pocket_free/local_ollama/pro_cloud cleanly."""
    from cryptography.fernet import Fernet

    from engine import db, services

    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    presets = {p["name"]: p for p in services.list_integration_presets()}
    assert set(presets.keys()) == {"pocket_free", "local_ollama", "pro_cloud"}
    assert presets["pocket_free"]["ports"]["news_search"] == "gnews_rss"
    assert presets["pocket_free"]["ports"]["keyword_provider"] == "autocomplete"
    assert presets["pocket_free"]["ports"]["publisher"] == "markdown_export"
    assert presets["local_ollama"]["ports"]["llm"] == "ollama"
    assert presets["pro_cloud"]["ports"]["llm"] == "openrouter"

    slug = f"ws-preset-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="Preset WS")
    owner = services.add_workspace_owner(
        db_conn, workspace_slug=slug, email=f"o-{slug}@example.com"
    )
    outsider = services.add_workspace_owner(
        db_conn,
        workspace_slug=services.create_workspace(db_conn, slug=f"other-{slug}", name="Other")[
            "slug"
        ],
        email=f"x-{slug}@example.com",
    )

    with pytest.raises(PermissionError):
        services.apply_integration_preset(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=outsider["user"]["user_id"],
            preset_name="pocket_free",
        )

    with pytest.raises(ValueError, match="Unknown integration preset"):
        services.apply_integration_preset(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=owner["user"]["user_id"],
            preset_name="nonexistent-preset",
        )

    # Localhost Ollama endpoint MUST be permitted for local_ollama
    applied = services.apply_integration_preset(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=owner["user"]["user_id"],
        preset_name="local_ollama",
        port_overrides={"llm": {"settings": {"base_url": "http://localhost:11434/v1"}}},
    )
    by_port = {row["port"]: row["plugin"] for row in applied}
    assert by_port == presets["local_ollama"]["ports"]

    ws_events = db.list_events(
        db_conn, workspace_id=ws["workspace_id"], event_type="integration.preset_applied"
    )
    assert len(ws_events) == 1
    assert ws_events[0]["payload"]["preset"] == "local_ollama"

    # Cloud metadata endpoint MUST reject and roll back the entire preset cleanly
    with pytest.raises(ValueError, match="Unsafe"):
        services.apply_integration_preset(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=owner["user"]["user_id"],
            preset_name="local_ollama",
            port_overrides={"llm": {"settings": {"base_url": "http://169.254.169.254/latest"}}},
        )

    # Managed connection wrapper (conn=None) works cleanly with pro_cloud + secrets
    token = "sk-or-test-token"
    applied_pro = services.apply_integration_preset(
        workspace_id=ws["workspace_id"],
        user_id=owner["user"]["user_id"],
        preset_name="pro_cloud",
        port_overrides={"llm": {"secrets": {"api_key": token}}},
    )
    assert {r["port"]: r["plugin"] for r in applied_pro} == presets["pro_cloud"]["ports"]


def test_cli_and_ui_preset_flows(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D4: CLI `integrations preset` and UI `_render_integration_presets` apply pocket_free."""
    import app
    from engine import cli, services

    slug = f"ws-cli-pre-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="CLI Preset WS")
    owner = services.add_workspace_owner(
        db_conn, workspace_slug=slug, email=f"c-{slug}@example.com"
    )

    assert cli.main(["integrations", "preset", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert any(p["name"] == "pocket_free" for p in listed)

    assert cli.main(["integrations", "preset", "list"]) == 0
    assert "pocket_free" in capsys.readouterr().out

    exit_code = cli.main(
        [
            "integrations",
            "preset",
            "apply",
            "pocket_free",
            "--workspace-id",
            str(ws["workspace_id"]),
            "--user-id",
            str(owner["user"]["user_id"]),
            "--json",
        ]
    )
    assert exit_code == 0
    applied = json.loads(capsys.readouterr().out)
    assert {row["port"]: row["plugin"] for row in applied}["news_search"] == "gnews_rss"

    assert (
        cli.main(
            [
                "integrations",
                "preset",
                "apply",
                "pocket_free",
                "--workspace-id",
                str(ws["workspace_id"]),
                "--user-id",
                str(owner["user"]["user_id"]),
            ]
        )
        == 0
    )
    assert "Applied preset 'pocket_free'" in capsys.readouterr().out

    monkeypatch.delenv("APP_ENCRYPTION_KEY", raising=False)
    with (
        patch("streamlit.markdown"),
        patch("streamlit.selectbox", return_value="pocket_free"),
        patch("streamlit.caption"),
        patch("streamlit.button", return_value=True),
        patch("streamlit.success") as mock_success,
    ):
        app._render_integration_presets(owner["user"], ws)
        mock_success.assert_called_once()

    # Cover missing brand / missing brief error branches in import_external_draft
    from decimal import Decimal

    services.save_campaign(
        db_conn,
        workspace_id=ws["workspace_id"],
        user_id=owner["user"]["user_id"],
        name="C",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("10.00"),
    )
    md = "# Sample Title\n\nBody paragraph about optical store operations."
    with pytest.raises(ValueError, match="no active brand profile"):
        services.import_external_draft(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=owner["user"]["user_id"],
            markdown_text=md,
        )
    with pytest.raises(ValueError, match="not found"):
        services.import_external_draft(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=owner["user"]["user_id"],
            markdown_text=md,
            brief_id=uuid.uuid4(),
        )
