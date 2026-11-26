"""Tests for CLI verify-sources and source verification commands (M1-12, F04 AC6)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from engine import cli, services


def test_cli_verify_sources_offline(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI verify-sources runs in offline mode and prints default general pack report."""
    exit_code = cli.main(["verify-sources", "--offline"])
    assert exit_code == 0
    captured = capsys.readouterr()
    out = captured.out

    assert "Seed Source Verification Report (general, last 30 days):" in out
    assert "Google Search Central" in out
    assert "W3C" in out
    assert "Sebrae" in out
    assert "IBGE" in out
    assert "Total: 16 | Active OK: 14 | Disabled: 2 | No Records: 0 | Errors: 0" in out


def test_cli_verify_sources_custom_days_and_pack(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI verify-sources accepts --days and --industry-pack options."""
    exit_code = cli.main(
        ["verify-sources", "--days", "14", "--industry-pack", "b2b-saas", "--offline"]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    out = captured.out

    assert "Seed Source Verification Report (b2b-saas, last 14 days):" in out
    assert "Total: 6 | Active OK: 6 | Disabled: 0 | No Records: 0 | Errors: 0" in out


def test_cli_verify_sources_invalid_pack(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI verify-sources exits with code 1 when given a nonexistent pack."""
    exit_code = cli.main(["verify-sources", "--industry-pack", "nonexistent-pack-xyz", "--offline"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_cli_webhooks_deliver(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI webhooks-deliver processes due deliveries."""
    from engine import services

    monkeypatch.setattr(
        services, "deliver_webhooks", lambda batch_size=10: [{"status": "delivered"}]
    )
    exit_code = cli.main(["webhooks-deliver", "--batch-size", "5"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Processed 1 webhook deliveries" in captured.out


def test_cli_webhooks_deliver_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI webhooks-deliver handles exceptions and returns code 1."""
    from engine import services

    def _fail_deliver(**kwargs: object) -> list[object]:
        raise RuntimeError("Database unreachable")

    monkeypatch.setattr(services, "deliver_webhooks", _fail_deliver)
    exit_code = cli.main(["webhooks-deliver"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: Database unreachable" in captured.err


def test_cli_import_sitemap_success(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI import-sitemap imports articles and returns code 0."""
    fake_ws = uuid.uuid4()
    fake_user = uuid.uuid4()
    monkeypatch.setattr(
        services,
        "resolve_workspace",
        lambda workspace_identifier=None: {
            "workspace_id": fake_ws,
            "owner_user_id": fake_user,
        },
    )
    monkeypatch.setattr(
        services,
        "import_sitemap",
        lambda **kwargs: {"total": 5, "inserted": 5, "updated": 0},
    )

    exit_code = cli.main(["import-sitemap", "https://example.com/sitemap.xml"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Imported sitemap" in captured.out
    assert "5 inserted" in captured.out


def test_cli_import_sitemap_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI import-sitemap reports errors and returns code 1."""

    def _fail(workspace_identifier: str | None = None) -> dict[str, Any]:
        raise ValueError("No workspaces found")

    monkeypatch.setattr(services, "resolve_workspace", _fail)
    exit_code = cli.main(["import-sitemap", "https://example.com/sitemap.xml"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: No workspaces found" in captured.err


def test_cli_import_sitemap_no_owner(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI import-sitemap reports error when workspace has no owner."""
    fake_ws = uuid.uuid4()
    monkeypatch.setattr(
        services,
        "resolve_workspace",
        lambda workspace_identifier=None: {
            "workspace_id": fake_ws,
            "owner_user_id": None,
        },
    )
    exit_code = cli.main(["import-sitemap", "https://example.com/sitemap.xml"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: Workspace has no owner" in captured.err


def test_cli_test_sentry(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI test-sentry exits non-zero when SENTRY_DSN is unset and captures event when set."""
    from unittest.mock import patch

    monkeypatch.delenv("SENTRY_DSN", raising=False)
    with patch("sentry_sdk.is_initialized", return_value=False):
        exit_fail = cli.main(["test-sentry"])
    assert exit_fail == 1
    assert "SENTRY_DSN" in capsys.readouterr().err

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    with (
        patch("sentry_sdk.is_initialized", return_value=False),
        patch("sentry_sdk.init"),
        patch("sentry_sdk.new_scope"),
        patch("sentry_sdk.capture_exception", return_value="evt-sentry-ok") as mock_cap,
        patch("sentry_sdk.flush") as mock_flush,
    ):
        exit_ok = cli.main(["test-sentry"])
    assert exit_ok == 0
    assert "evt-sentry-ok" in capsys.readouterr().out
    mock_cap.assert_called_once()
    mock_flush.assert_called_once()


def test_cli_create_workspace_and_validation(
    db_conn: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI create-workspace creates a workspace and validates duplicates, slugs, packs, and tz."""
    from engine import db

    slug = f"acme-{uuid.uuid4().hex[:8]}"
    exit_ok = cli.main(
        [
            "create-workspace",
            slug,
            "--name",
            "Acme Content OS",
            "--pack",
            "general",
            "--timezone",
            "America/Sao_Paulo",
        ]
    )
    assert exit_ok == 0
    out = capsys.readouterr().out
    assert slug in out

    ws = db.get_workspace_by_slug(db_conn, slug=slug)
    assert ws is not None
    assert ws["name"] == "Acme Content OS"
    assert ws["industry_pack"] == "general"
    assert ws["timezone"] == "America/Sao_Paulo"

    exit_dup = cli.main(["create-workspace", slug, "--name", "Acme Dup"])
    assert exit_dup == 1
    assert "already exists" in capsys.readouterr().err

    exit_bad_slug = cli.main(["create-workspace", "BAD_SLUG", "--name", "Bad"])
    assert exit_bad_slug == 1
    assert "Error:" in capsys.readouterr().err

    exit_bad_tz = cli.main(
        [
            "create-workspace",
            f"tz-{uuid.uuid4().hex[:6]}",
            "--name",
            "Bad TZ",
            "--timezone",
            "Mars/Olympus",
        ]
    )
    assert exit_bad_tz == 1
    assert "timezone" in capsys.readouterr().err.lower()

    exit_bad_pack = cli.main(
        [
            "create-workspace",
            f"pk-{uuid.uuid4().hex[:6]}",
            "--name",
            "Bad Pack",
            "--pack",
            "nonexistent-pack-xyz",
        ]
    )
    assert exit_bad_pack == 1
    assert "Error:" in capsys.readouterr().err


def test_cli_run_week_accepts_workspace_flag_from_crontab(
    db_conn: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI run-week accepts --workspace flag generated by scheduler.generate_crontab."""
    import shlex

    slug = f"cron-flag-{uuid.uuid4().hex[:8]}"
    created = services.create_workspace(
        db_conn,
        slug=slug,
        name="Crontab Flag WS",
        industry_pack="general",
        timezone="America/Sao_Paulo",
    )
    ws_id = created["workspace_id"]
    owner_res = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"cron-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = owner_res["user"]["user_id"]
    camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=100,
        run_cap_usd=10,
        name="Weekly Flag Campaign",
    )
    crontab_txt = services.generate_crontab(db_conn, workspace_id=ws_id)
    run_line = next(line for line in crontab_txt.splitlines() if "macy run-week" in line)
    tokens = shlex.split(run_line.split("macy ", 1)[1])

    assert cli.main([*tokens, "--offline"]) == 0
    assert str(camp["id"]) in run_line

    assert cli.main(["run-week"]) == 1
    assert "workspace" in capsys.readouterr().err.lower()

    assert cli.main(["run-now", slug, "--offline"]) == 0
    assert "executed with status" in capsys.readouterr().out.lower()

    with pytest.raises(ValueError, match="Workspace name"):
        services.create_workspace(db_conn, slug="valid-slug-name", name="   ")


def test_mcp_help_and_check_mock(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `mcp --help` lists required flags and `mcp --check --mock` exits 0 in dev."""
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["mcp", "--help"])
    assert help_exit.value.code == 0
    help_out = capsys.readouterr().out
    for flag in (
        "--transport",
        "--host",
        "--port",
        "--mock",
        "--allow-owner-fallback",
        "--check",
    ):
        assert flag in help_out

    monkeypatch.setenv("APP_ENV", "development")
    exit_ok = cli.main(["mcp", "--check", "--mock"])
    assert exit_ok == 0
    captured = capsys.readouterr()
    assert "MCP check OK" in captured.out or "MCP check OK" in captured.err


def test_mcp_check_fails_when_db_down_without_mock(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `mcp` refuses DB fallback to mock, bans mock in prod, and dispatches transports."""
    from engine.mcp import server as mcp_server

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("MACY_MCP_STANDALONE", raising=False)
    monkeypatch.setattr(
        services,
        "check_database_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("postgres offline")),
    )

    assert cli.main(["mcp", "--check"]) == 1
    assert "Error:" in capsys.readouterr().err

    monkeypatch.setenv("APP_ENV", "production")
    assert cli.main(["mcp", "--check", "--mock"]) == 1
    assert "forbidden" in capsys.readouterr().err.lower()

    assert cli.main(["mcp", "--transport", "streamable-http", "--check"]) == 1
    assert "MCP_AUTH_TOKEN" in capsys.readouterr().err

    # Transport runtime dispatch paths (mocked blocking loops)
    monkeypatch.setenv("APP_ENV", "development")
    called: list[str] = []
    monkeypatch.setattr(
        mcp_server,
        "run_stdio_server",
        lambda _bundle: called.append("stdio"),
    )
    monkeypatch.setattr(
        mcp_server,
        "run_streamable_http_server",
        lambda _bundle, *, host, port: called.append(f"http:{host}:{port}"),
    )
    assert cli.main(["mcp", "--mock", "--transport", "stdio"]) == 0
    assert (
        cli.main(
            [
                "mcp",
                "--mock",
                "--transport",
                "streamable-http",
                "--host",
                "127.0.0.1",
                "--port",
                "8766",
            ]
        )
        == 0
    )
    assert called == ["stdio", "http:127.0.0.1:8766"]

    assert cli.main(["mcp", "--mock", "--transport", "streamable-http", "--check"]) == 0
    assert "mode=mock, transport=streamable-http" in capsys.readouterr().out

    monkeypatch.setattr(services, "check_database_connection", lambda: "app")
    assert cli.main(["mcp", "--check"]) == 0
    assert "mode=db, transport=stdio" in capsys.readouterr().err


def test_cli_import_draft_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Verify M5-07: CLI `import-draft` dispatches to services.import_external_draft."""
    md_file = tmp_path / "post.md"
    md_file.write_text("# Post\n\nTexto curto.", encoding="utf-8")
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()

    monkeypatch.setattr(
        services,
        "resolve_workspace",
        lambda workspace_identifier=None: {"workspace_id": ws_id, "owner_user_id": owner_id},
    )
    captured: dict[str, Any] = {}

    def _fake_import_external_draft(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "draft_id": uuid.uuid4(),
            "brief_id": uuid.uuid4(),
            "run_id": uuid.uuid4(),
            "revision": 0,
            "verdict": "PASS",
            "score": None,
            "findings": [],
        }

    monkeypatch.setattr(services, "import_external_draft", _fake_import_external_draft)

    code = cli.main(
        [
            "import-draft",
            "--workspace",
            "acme",
            "--file",
            str(md_file),
            "--format",
            "blog-short",
            "--deterministic-only",
        ]
    )
    assert code == 0
    assert captured["workspace_id"] == ws_id
    assert captured["user_id"] == owner_id
    assert captured["format_slug"] == "blog-short"
    assert captured["deterministic_only"] is True
    assert "PASS" in capsys.readouterr().out


def test_cli_audit_single_url_and_crawl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify M6-02 D4: `macy audit` supports single-URL and bounded crawl modes."""
    from engine.contracts import CrawlConfig, PageAuditReport, SiteAuditReport

    page = PageAuditReport(
        url="https://example.com/",
        status_code=200,
        overall_score=88.5,
        grade="A-",
        categories={},
        checks=[],
        recommendations=["Add /llms.txt"],
    )
    site = SiteAuditReport(
        start_url="https://example.com/",
        pages_crawled=2,
        max_pages=20,
        overall_score=84.0,
        grade="B+",
        pages=[page],
    )
    seen: dict[str, Any] = {}

    def _fake_url_audit(**kwargs: Any) -> dict[str, Any]:
        seen["single"] = kwargs
        return page.model_dump(mode="json")

    def _fake_site_crawl(**kwargs: Any) -> dict[str, Any]:
        seen["crawl"] = kwargs
        return site.model_dump(mode="json")

    monkeypatch.setattr(services, "run_url_audit", _fake_url_audit)
    monkeypatch.setattr(services, "run_site_crawl", _fake_site_crawl)

    code_single = cli.main(["audit", "https://example.com/", "--json"])
    assert code_single == 0
    assert seen["single"]["url"] == "https://example.com/"
    assert '"overall_score": 88.5' in capsys.readouterr().out

    code_crawl = cli.main(
        ["audit", "https://example.com/", "--crawl", "--max-pages", "5", "--plan", "paid"]
    )
    assert code_crawl == 0
    cfg: CrawlConfig = seen["crawl"]["config"]
    assert cfg.plan == "paid"
    assert cfg.effective_max_pages == 5
    out = capsys.readouterr().out
    assert "B+" in out
    assert "2 pages" in out


def test_cli_parser_uses_macy_prog() -> None:
    """Verify M9-01 D1: CLI ArgumentParser reports prog='macy'."""
    assert cli._build_parser().prog == "macy"
