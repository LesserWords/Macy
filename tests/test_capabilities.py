"""Tests for M7-01 capability plugin registry, switchboard, and packaging extras."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_discover_capabilities_returns_builtin_plugins() -> None:
    """D1: All 4 built-in capability plugins are discovered with valid metadata."""
    from engine.capabilities import discover_capabilities, get_capability

    caps = discover_capabilities()
    assert set(caps.keys()) >= {
        "core-compliance",
        "seo-audit",
        "editorial-pipeline",
        "route-monitors",
    }

    core = get_capability("core-compliance")
    assert core.always_enabled is True
    assert core.required_ports == ()
    assert "macy_evaluate_draft" in core.mcp_tools
    assert "macy_import_draft" in core.mcp_tools

    seo = get_capability("seo-audit")
    assert seo.always_enabled is False
    assert "audit" in seo.ui_screens
    assert "audit" in seo.cli_commands
    assert "macy_audit_url" in seo.mcp_tools
    assert "macy_crawl_site" in seo.mcp_tools

    editorial = get_capability("editorial-pipeline")
    assert editorial.always_enabled is False
    assert set(editorial.required_ports) >= {"llm", "feed_source", "extractor"}
    assert "report" in editorial.ui_screens
    assert "macy_generate_draft" in editorial.mcp_tools

    monitors = get_capability("route-monitors")
    assert monitors.always_enabled is False
    assert "analytics_source" in monitors.optional_ports


def test_pyproject_extras_cover_composable_profiles() -> None:
    """D1: pyproject.toml defines core, editorial, ui, audit-browser, and full extras."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data.get("project", {}).get("optional-dependencies", {})
    for name in ("core", "editorial", "ui", "audit-browser", "full"):
        assert name in extras, f"Missing optional dependency extra '{name}'"
    assert "content_engine.capabilities" in data.get("project", {}).get("entry-points", {})


def test_switchboard_persists_capabilities_and_protects_core(db_conn: object) -> None:
    """D2: Workspace switchboard persists selections, keeps core-compliance, and logs event."""
    import uuid
    from typing import Any

    import psycopg
    import pytest

    from engine import db, services

    conn: psycopg.Connection[Any] = db_conn  # type: ignore[assignment]
    slug = f"ws-sw-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(conn, slug=slug, name="Switchboard WS")
    ws_id = ws["workspace_id"]
    owner_res = services.add_workspace_owner(
        conn, workspace_slug=slug, email=f"sw-owner-{uuid.uuid4().hex[:6]}@example.com"
    )
    owner_id = owner_res["user"]["user_id"]

    # Default when unset in workspaces.config: all 4 capabilities enabled
    defaults = services.get_enabled_capabilities(conn, workspace_id=ws_id, user_id=owner_id)
    assert set(defaults) >= {
        "core-compliance",
        "seo-audit",
        "editorial-pipeline",
        "route-monitors",
    }

    # Updating to ["seo-audit"] automatically retains core-compliance
    updated = services.set_enabled_capabilities(
        conn,
        workspace_id=ws_id,
        user_id=owner_id,
        enabled_capabilities=["seo-audit"],
    )
    assert updated == ["core-compliance", "seo-audit"]
    assert services.get_enabled_capabilities(conn, workspace_id=ws_id, user_id=owner_id) == [
        "core-compliance",
        "seo-audit",
    ]

    # Audit event emitted
    evts = db.list_events(conn, workspace_id=ws_id, event_type="workspace.capabilities_updated")
    assert len(evts) == 1
    assert evts[0]["payload"]["enabled_capabilities"] == ["core-compliance", "seo-audit"]

    # Unknown slug rejected
    with pytest.raises(ValueError, match="Unknown capability"):
        services.set_enabled_capabilities(
            conn,
            workspace_id=ws_id,
            user_id=owner_id,
            enabled_capabilities=["nonexistent-cap"],
        )

    # Non-owner / non-member rejected
    outsider_id = db.create_or_get_user(
        conn, email=f"sw-outsider-{uuid.uuid4().hex[:6]}@example.com"
    )["user_id"]
    with pytest.raises(PermissionError):
        services.set_enabled_capabilities(
            conn,
            workspace_id=ws_id,
            user_id=outsider_id,
            enabled_capabilities=["seo-audit"],
        )

    # Non-owner role also rejected
    import unittest.mock

    with (
        unittest.mock.patch("engine.services._execute_require_member", return_value="viewer"),
        pytest.raises(PermissionError, match="forbidden from updating capabilities"),
    ):
        services.set_enabled_capabilities(
            conn,
            workspace_id=ws_id,
            user_id=outsider_id,
            enabled_capabilities=["seo-audit"],
        )

    # Managed connection (conn=None) and missing workspace guards
    assert services.get_enabled_capabilities(workspace_id=ws_id, user_id=owner_id) == [
        "core-compliance",
        "seo-audit",
    ]
    assert services.set_enabled_capabilities(
        workspace_id=ws_id,
        user_id=owner_id,
        enabled_capabilities=["core-compliance", "seo-audit", "editorial-pipeline"],
    ) == ["core-compliance", "seo-audit", "editorial-pipeline"]
    with pytest.raises(ValueError, match="not found"):
        services.get_enabled_capabilities(conn, workspace_id=uuid.uuid4())
    with pytest.raises(ValueError, match="not found"):
        db.update_workspace_config(conn, workspace_id=uuid.uuid4(), config={})


def test_app_sidebar_filters_screens_by_enabled_capabilities() -> None:
    """D3: Admin UI sidebar omits editorial screens when editorial-pipeline is disabled."""
    import app as macy_app

    full_nav = {
        "dashboard": "Dashboard",
        "report": "Weekly Report",
        "draft_review": "Draft Review",
        "campaigns": "Campaigns",
        "brand_profile": "Brand Profile",
        "sources": "Sources",
        "integrations": "Integrations",
        "webhooks": "Webhooks",
        "runs": "Runs",
        "audit": "SEO & GEO Audit",
    }
    filtered = macy_app.filter_nav_labels(
        full_nav,
        enabled_capabilities=["core-compliance", "seo-audit"],
    )
    assert set(filtered.keys()) == {
        "dashboard",
        "brand_profile",
        "integrations",
        "webhooks",
        "audit",
    }
    assert "report" not in filtered
    assert "draft_review" not in filtered
    assert "campaigns" not in filtered
    assert "sources" not in filtered
    assert "runs" not in filtered


def test_cli_capabilities_list_enable_disable(
    db_conn: object,
    capsys: object,
) -> None:
    """D3: CLI capabilities list/enable/disable updates workspace switchboard."""
    import uuid
    from typing import Any

    import psycopg

    from engine import cli, services

    conn: psycopg.Connection[Any] = db_conn  # type: ignore[assignment]
    slug = f"ws-clicap-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(conn, slug=slug, name="CLI Cap WS")
    services.add_workspace_owner(
        conn, workspace_slug=slug, email=f"clicap-{uuid.uuid4().hex[:6]}@example.com"
    )

    assert cli.main(["capabilities", "list"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "core-compliance" in captured.out
    assert "seo-audit" in captured.out

    assert cli.main(["capabilities", "disable", "editorial-pipeline", "--workspace", slug]) == 0
    caps = services.get_enabled_capabilities(conn, workspace_id=ws["workspace_id"])
    assert "editorial-pipeline" not in caps

    assert cli.main(["capabilities", "enable", "editorial-pipeline", "--workspace", slug]) == 0
    caps_after = services.get_enabled_capabilities(conn, workspace_id=ws["workspace_id"])
    assert "editorial-pipeline" in caps_after

    # Cannot disable core-compliance via CLI
    assert cli.main(["capabilities", "disable", "core-compliance", "--workspace", slug]) == 1

    # CLI list --workspace and error paths
    assert cli.main(["capabilities", "list", "--workspace", slug]) == 0
    assert cli.main(["capabilities", "enable", "unknown-cap", "--workspace", slug]) == 1

    no_owner_slug = f"ws-noowner-{uuid.uuid4().hex[:8]}"
    services.create_workspace(conn, slug=no_owner_slug, name="No Owner WS")
    assert cli.main(["capabilities", "enable", "seo-audit", "--workspace", no_owner_slug]) == 1


def test_capability_registry_edge_cases(monkeypatch: object) -> None:
    """D1/D3: Registry handles missing capabilities, empty slugs, ports, and entry points."""
    from types import SimpleNamespace
    from typing import Any

    import pytest

    from engine.capabilities import (
        capability_for_mcp_tool,
        discover_capabilities,
        get_capability,
        normalize_enabled_capabilities,
        required_ports_for_capabilities,
    )
    from engine.capabilities.builtins import CoreComplianceCapability

    with pytest.raises(KeyError, match="Unknown capability"):
        get_capability("does-not-exist")

    assert normalize_enabled_capabilities(["", "seo-audit"]) == ["core-compliance", "seo-audit"]
    assert required_ports_for_capabilities(["editorial-pipeline"]) == {
        "llm",
        "feed_source",
        "extractor",
    }
    assert capability_for_mcp_tool("nonexistent_tool") is None

    def _broken_loader() -> Any:
        raise RuntimeError("boom")

    fake_eps = [
        SimpleNamespace(name="core-compliance", load=lambda: CoreComplianceCapability),
        SimpleNamespace(name="broken-cap", load=_broken_loader),
    ]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "importlib.metadata.entry_points",
        lambda group: fake_eps,
    )
    caps = discover_capabilities()
    assert "core-compliance" in caps
    assert "broken-cap" not in caps


def test_switchboard_cross_workspace_isolation(db_conn: object) -> None:
    """D2: Updating switchboard in workspace A does not affect workspace B."""
    import uuid
    from typing import Any

    import psycopg

    from engine import services

    conn: psycopg.Connection[Any] = db_conn  # type: ignore[assignment]
    ws_a = services.create_workspace(conn, slug=f"ws-swa-{uuid.uuid4().hex[:8]}", name="WS A")
    ws_b = services.create_workspace(conn, slug=f"ws-swb-{uuid.uuid4().hex[:8]}", name="WS B")
    owner_a = services.add_workspace_owner(
        conn, workspace_slug=ws_a["slug"], email=f"swa-{uuid.uuid4().hex[:6]}@example.com"
    )["user"]["user_id"]
    owner_b = services.add_workspace_owner(
        conn, workspace_slug=ws_b["slug"], email=f"swb-{uuid.uuid4().hex[:6]}@example.com"
    )["user"]["user_id"]

    services.set_enabled_capabilities(
        conn,
        workspace_id=ws_a["workspace_id"],
        user_id=owner_a,
        enabled_capabilities=["seo-audit"],
    )
    caps_b = services.get_enabled_capabilities(
        conn, workspace_id=ws_b["workspace_id"], user_id=owner_b
    )
    assert "editorial-pipeline" in caps_b
