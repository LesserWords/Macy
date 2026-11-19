"""Tests for M7-04 D1-D3 first-boot onboarding, URL auto-seed, and `macy init`."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest


def _swap_db_name(db_url: str, db_name: str) -> str:
    parsed = urlsplit(db_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.query, parsed.fragment))


@pytest.fixture
def isolated_empty_db(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Create a fresh migrated PostgreSQL database with zero workspaces for onboarding tests."""
    from engine.migrations import run_startup_migrations

    base_owner_url = os.getenv(
        "OWNER_DATABASE_URL",
        "postgres://owner:secret@localhost:5432/macy?sslmode=disable",
    )
    tmp_db_name = f"macy_onb_{uuid.uuid4().hex[:8]}"
    tmp_url = _swap_db_name(base_owner_url, tmp_db_name)

    from psycopg import sql

    with psycopg.connect(base_owner_url, autocommit=True) as admin_conn:
        admin_conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(tmp_db_name)))

    try:
        applied = run_startup_migrations(
            env={"AUTO_MIGRATE": "true", "MIGRATION_DATABASE_URL": tmp_url}
        )
        assert len(applied) >= 1
        monkeypatch.setenv("DATABASE_URL", tmp_url)
        yield tmp_url
    finally:
        with psycopg.connect(base_owner_url, autocommit=True) as admin_conn:
            admin_conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(tmp_db_name)
                )
            )


def test_bootstrap_onboarding_atomic_and_single_use(isolated_empty_db: str) -> None:
    """D1: bootstrap_onboarding commits atomically on empty DB, rolls back on error."""
    from engine import db, services

    with db.get_connection(isolated_empty_db) as conn:
        assert services.needs_first_boot_wizard(conn) is True

        secret_pw = "pocket-admin-pass-123"
        # Invalid preset MUST roll back workspace and user completely
        with pytest.raises(ValueError, match="Unknown integration preset"):
            services.bootstrap_onboarding(
                conn,
                email="fail@example.com",
                password=secret_pw,
                workspace_slug="fail-ws",
                workspace_name="Fail WS",
                enabled_capabilities=["core-compliance", "seo-audit"],
                preset_name="invalid-preset",
            )
        assert db.list_workspaces(conn) == []
        assert db.get_user_by_email(conn, email="fail@example.com") is None

        # Valid bootstrap creates workspace, owner + PBKDF2 password, capabilities, and preset
        res = services.bootstrap_onboarding(
            conn,
            email="admin@example.com",
            password=secret_pw,
            workspace_slug="pocket-home",
            workspace_name="Pocket Home",
            enabled_capabilities=["core-compliance", "seo-audit"],
            preset_name="pocket_free",
        )
        ws_id = res["workspace"]["workspace_id"]
        assert res["enabled_capabilities"] == ["core-compliance", "seo-audit"]
        assert {r["port"]: r["plugin"] for r in res["integrations"]}["news_search"] == "gnews_rss"
        assert services.authenticate_local_user(conn, email="admin@example.com", password=secret_pw)
        assert services.needs_first_boot_wizard(conn) is False

        events = db.list_events(conn, workspace_id=ws_id, event_type="onboarding.bootstrapped")
        assert len(events) == 1

        # Second unauthenticated bootstrap attempt MUST be blocked once workspaces exist
        with pytest.raises(PermissionError, match="already exists"):
            services.bootstrap_onboarding(
                conn,
                email="attacker@example.com",
                password=secret_pw,
                workspace_slug="hijack-ws",
                workspace_name="Hijack WS",
            )


def test_bootstrap_onboarding_managed_conn_and_cli_init(
    isolated_empty_db: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D1 & D3: bootstrap_onboarding(conn=None) and `macy init` work on empty DB."""
    from engine import cli, services

    assert services.needs_first_boot_wizard() is True
    secret_pw = "cli-init-pass-123"
    exit_code = cli.main(
        [
            "init",
            "--email",
            "cli-admin@example.com",
            "--password",
            secret_pw,
            "--slug",
            "cli-pocket",
            "--name",
            "CLI Pocket WS",
            "--capabilities",
            "core-compliance,seo-audit",
            "--preset",
            "pocket_free",
            "--non-interactive",
            "--json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"]["slug"] == "cli-pocket"
    assert payload["enabled_capabilities"] == ["core-compliance", "seo-audit"]
    assert services.needs_first_boot_wizard() is False


def test_cli_init_plain_output_and_site_url(
    isolated_empty_db: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D1-D3: CLI init supports --site-url autoseed and human-readable stdout without --json."""
    from engine import cli
    from engine.safe_http import SafeResponse

    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda _host: ["93.184.216.34"],
    )
    html = b"<html><head><title>Plain CLI Brand</title></head><body><h1>Main</h1></body></html>"
    monkeypatch.setattr(
        "engine.onboarding.safe_request",
        lambda _m, url, **_kw: SafeResponse(
            status_code=404 if url.endswith("/robots.txt") else 200,
            headers={},
            content=b"" if url.endswith("/robots.txt") else html,
        ),
    )
    monkeypatch.setattr(
        "engine.safe_http.safe_request",
        lambda *_a, **_kw: SafeResponse(status_code=404, headers={}, content=b""),
    )
    secret_pw = "plain-cli-pass-123"
    exit_code = cli.main(
        [
            "init",
            "--password",
            secret_pw,
            "--site-url",
            "https://plain.example.com",
            "--non-interactive",
        ]
    )
    assert exit_code == 0
    assert "Initialized workspace 'pocket-ws'" in capsys.readouterr().out


def test_autoseed_from_url_populates_brand_source_and_sitemap(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: autoseed_from_url extracts BrandProfile, saves company source, and imports sitemap."""
    from engine import db, services
    from engine.safe_http import SafeResponse

    slug = f"ws-seed-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="Autoseed WS")
    owner = services.add_workspace_owner(
        db_conn, workspace_slug=slug, email=f"s-{slug}@example.com"
    )
    ws_id = ws["workspace_id"]
    user_id = owner["user"]["user_id"]

    with pytest.raises(ValueError, match="Unsafe"):
        services.autoseed_from_url(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            site_url="http://127.0.0.1/internal",
        )

    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda _host: ["93.184.216.34"],
    )
    html_doc = b"""<!doctype html>
<html lang="pt-BR">
<head>
  <title>Otica Exemplo | Saude Ocular</title>
  <meta name="description" content="Consultoria em lentes multifocais e visao preventiva.">
</head>
<body>
  <h1>Especialistas em Saude Visual</h1>
  <p>Atendimento personalizado em lentes de contato e armacoes.</p>
</body>
</html>"""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://oticaexemplo.com.br/lentes-multifocais</loc>
    <lastmod>2026-11-01</lastmod>
  </url>
</urlset>"""

    def fake_request(_method: str, url: str, **_kwargs: object) -> SafeResponse:
        if url.endswith("/robots.txt"):
            return SafeResponse(
                status_code=200,
                headers={},
                content=b"User-agent: *\nSitemap: https://oticaexemplo.com.br/sitemap.xml\n",
            )
        if url.endswith("/sitemap.xml"):
            return SafeResponse(status_code=200, headers={}, content=sitemap_xml)
        return SafeResponse(status_code=200, headers={}, content=html_doc)

    monkeypatch.setattr("engine.onboarding.safe_request", fake_request)
    monkeypatch.setattr("engine.safe_http.safe_request", fake_request)

    seeded = services.autoseed_from_url(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        site_url="https://oticaexemplo.com.br",
    )
    assert seeded["brand_profile"]["id"] is not None
    assert seeded["source"]["domain"] == "oticaexemplo.com.br"
    assert seeded["sitemap_imported"] == 1
    assert db.get_current_brand_profile(db_conn, workspace_id=ws_id) is not None
    assert len(db.list_publications(db_conn, workspace_id=ws_id)) == 1


def test_autoseed_from_url_handles_missing_sitemap_and_http_errors(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: autoseed_from_url tolerates missing sitemaps (404) while propagating homepage errors."""
    from engine import services
    from engine.safe_http import SafeResponse

    slug = f"ws-seed2-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="Autoseed Fallback WS")
    owner = services.add_workspace_owner(
        db_conn, workspace_slug=slug, email=f"s2-{slug}@example.com"
    )
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda _host: ["93.184.216.34"],
    )

    monkeypatch.setattr(
        "engine.onboarding.safe_request",
        lambda *_args, **_kwargs: SafeResponse(status_code=500, headers={}, content=b"err"),
    )
    with pytest.raises(ValueError, match="HTTP 500"):
        services.autoseed_from_url(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=owner["user"]["user_id"],
            site_url="https://nositemap.example.com",
        )

    from engine.safe_http import SafeHTTPError

    def fake_get(_method: str, url: str, **_kwargs: object) -> SafeResponse:
        if url.endswith("/robots.txt"):
            raise SafeHTTPError("robots fetch timeout")
        if url.endswith("/sitemap.xml"):
            return SafeResponse(status_code=404, headers={}, content=b"Not Found")
        html = b"<html><head><title>Brand Studio</title></head><body><h1>Hi</h1></body></html>"
        return SafeResponse(status_code=200, headers={}, content=html)

    monkeypatch.setattr("engine.onboarding.safe_request", fake_get)
    monkeypatch.setattr("engine.safe_http.safe_request", fake_get)
    res = services.autoseed_from_url(
        workspace_id=ws["workspace_id"],
        user_id=owner["user"]["user_id"],
        site_url="https://nositemap.example.com",
    )
    assert res["sitemap_imported"] == 0
    assert res["brand_profile"]["id"] is not None


def test_setup_wizard_renders_and_provisions_on_empty_db(
    isolated_empty_db: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D3: Empty DB triggers 3-step Setup Wizard, creates owner/workspace, and auto-seeds URL."""
    from streamlit.testing.v1 import AppTest

    from engine import db
    from engine.safe_http import SafeResponse

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POCKET_MODE", "true")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("MACY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MACY_TEST_USER_EMAIL", raising=False)
    monkeypatch.setattr(
        "engine.safe_http.resolve_and_validate_host",
        lambda _host: ["93.184.216.34"],
    )

    def fake_request(_method: str, url: str, **_kwargs: object) -> SafeResponse:
        if url.endswith("/robots.txt"):
            return SafeResponse(status_code=404, headers={}, content=b"")
        return SafeResponse(
            status_code=200,
            headers={},
            content=(
                b"<html lang='en-US'><head><title>Wizard Brand</title>"
                b"<meta name='description' content='Guided setup optical shop'></head>"
                b"<body><h1>Optical Leaders</h1></body></html>"
            ),
        )

    monkeypatch.setattr("engine.onboarding.safe_request", fake_request)
    monkeypatch.setattr("engine.safe_http.safe_request", fake_request)

    app_file = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app_file, default_timeout=20)
    at.run()
    assert not at.exception
    assert any("Wizard" in t.value or "Configuração" in t.value for t in at.title)

    # Step 1: Account & Capabilities
    at.text_input(key="wizard_email").input("wizard@example.com").run()
    pw_val = "wizard-pass-123"
    at.text_input(key="wizard_password").input(pw_val).run()
    at.text_input(key="wizard_ws_name").input("Wizard Optical").run()
    at.text_input(key="wizard_ws_slug").input("wizard-optical").run()
    at.multiselect(key="wizard_capabilities").select("core-compliance").select("seo-audit").run()
    at.button(key="wizard_next_1").click().run()
    assert not at.exception

    # Step 2: Stack Preset
    at.selectbox(key="wizard_preset").select("pocket_free").run()
    at.button(key="wizard_next_2").click().run()
    assert not at.exception

    # Step 3: Website URL Auto-Seed + Complete Setup
    at.text_input(key="wizard_site_url").input("https://wizardoptical.example.com").run()
    at.button(key="wizard_complete").click().run()
    assert not at.exception

    with db.get_connection(isolated_empty_db) as conn:
        workspaces = db.list_workspaces(conn)
        assert len(workspaces) == 1
        assert workspaces[0]["slug"] == "wizard-optical"
        brand = db.get_current_brand_profile(conn, workspace_id=workspaces[0]["workspace_id"])
        assert brand is not None

    assert any("Wizard Optical" in t.value for t in at.title)
