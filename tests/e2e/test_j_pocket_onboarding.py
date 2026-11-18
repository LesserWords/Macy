"""Offline Playwright E2E journey J-POCKET (Path A Setup Wizard + Path B stateless smoke)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _swap_db_name(db_url: str, db_name: str) -> str:
    parsed = urlsplit(db_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.query, parsed.fragment))


@pytest.fixture
def isolated_e2e_db() -> Generator[str, None, None]:
    """Create a blank migrated database for full-stack J-POCKET verification."""
    from engine.migrations import run_startup_migrations

    base_owner_url = os.getenv(
        "OWNER_DATABASE_URL",
        "postgres://owner:secret@localhost:5432/macy?sslmode=disable",
    )
    tmp_db_name = f"macy_jpock_{uuid.uuid4().hex[:8]}"
    tmp_url = _swap_db_name(base_owner_url, tmp_db_name)

    from psycopg import sql

    with psycopg.connect(base_owner_url, autocommit=True) as admin_conn:
        admin_conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(tmp_db_name)))

    try:
        run_startup_migrations(env={"AUTO_MIGRATE": "true", "MIGRATION_DATABASE_URL": tmp_url})
        yield tmp_url
    finally:
        with psycopg.connect(base_owner_url, autocommit=True) as admin_conn:
            admin_conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(tmp_db_name)
                )
            )


def _wait_for_server(url: str, *, timeout_sec: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:  # noqa: S310
                if resp.status < 500:
                    return
        except Exception:
            time.sleep(0.4)
    msg = f"Streamlit server did not become ready at {url} within {timeout_sec}s"
    raise TimeoutError(msg)


def _step_through_wizard(page: Any) -> None:
    page.get_by_text(re.compile(r"Setup Wizard|Configuração Macy")).wait_for(timeout=20000)

    email_in = page.get_by_label(re.compile(r"Owner Email|E-mail do Proprietário"))
    email_in.fill("jpocket@example.com")
    admin_secret = "pocket-e2e-pass-123"
    pw_in = page.get_by_label(re.compile(r"Admin Password|Senha do Administrador"))
    pw_in.fill(admin_secret)
    ws_name_in = page.get_by_label(re.compile(r"Workspace Name|Nome do Espaço de Trabalho"))
    ws_name_in.fill("Pocket E2E")
    ws_slug_in = page.get_by_label(re.compile(r"Workspace Slug|Slug do Espaço de Trabalho"))
    ws_slug_in.fill("pocket-e2e")
    ws_slug_in.press("Tab")
    page.get_by_role("button", name=re.compile(r"Next|Próximo")).click()

    page.get_by_text(re.compile(r"Step 2|Etapa 2")).wait_for(timeout=15000)
    email_in.wait_for(state="detached", timeout=10000)
    page.get_by_role("button", name=re.compile(r"Back|Voltar")).wait_for(
        state="visible", timeout=10000
    )
    next_btn = page.get_by_role("button", name=re.compile(r"Next|Próximo"))
    next_btn.wait_for(state="visible", timeout=5000)
    next_btn.click()

    try:
        page.get_by_text(re.compile(r"Step 3|Etapa 3")).wait_for(timeout=15000)
    except Exception as exc:
        body_snippet = page.locator("body").inner_text()[:800]
        msg = f"Failed to reach Step 3; body={body_snippet!r}"
        raise AssertionError(msg) from exc
    page.get_by_role("button", name=re.compile(r"Complete Setup|Concluir Configuração")).click()


def test_j_pocket_setup_wizard_and_path_b_stateless_smoke(
    isolated_e2e_db: str,
    tmp_path: Path,
) -> None:
    """D4: Offline Playwright E2E J-POCKET onboarding + Path B zero-DB CLI smoke check."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("MACY_TEST_USER_EMAIL", None)
    env.pop("APP_ENCRYPTION_KEY", None)
    env.pop("AUTH_COOKIE_SECRET", None)
    env.update(
        {
            "APP_ENV": "test",
            "POCKET_MODE": "true",
            "AUTH_MODE": "local",
            "AUTO_MIGRATE": "false",
            "EMBEDDED_WORKER": "false",
            "DATABASE_URL": isolated_e2e_db,
            "MACY_DATA_DIR": str(data_dir),
        }
    )

    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_server(base_url)
        page_errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.goto(base_url, wait_until="networkidle")

            _step_through_wizard(page)

            page.get_by_text(re.compile(r"Pocket E2E")).first.wait_for(timeout=20000)
            sidebar = page.locator('[data-testid="stSidebar"]')
            sidebar_text = sidebar.inner_text()
            assert "Audit" in sidebar_text or "Auditoria" in sidebar_text
            assert "Brand" in sidebar_text or "Marca" in sidebar_text
            assert "Runs" not in sidebar_text and "Execuções" not in sidebar_text
            browser.close()

        assert page_errors == []
        assert (data_dir / ".macy_secrets.json").is_file()
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)

    # Path B smoke check with DATABASE_URL removed completely
    import tempfile

    stateless_env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    with tempfile.TemporaryDirectory(prefix="macy_pb_") as pb_dir:
        draft_md = Path(pb_dir) / "sample.md"
        draft_md.write_text(
            "# Lentes Multifocais\n\nConteudo educativo com boas praticas de visao preventiva.",
            encoding="utf-8",
        )
        cli_res = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "engine.cli",
                "import-draft",
                "--file",
                str(draft_md),
                "--stateless",
                "--json",
            ],
            cwd=str(ROOT_DIR),
            env=stateless_env,
            capture_output=True,
            text=True,
            check=True,
        )
    payload = json.loads(cli_res.stdout)
    assert payload["stateless"] is True
    assert payload["verdict"] in {"PASS", "REVISION", "REJECT"}

    mcp_res = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "engine.cli",
            "mcp",
            "--transport",
            "stdio",
            "--stateless",
            "--check",
        ],
        cwd=str(ROOT_DIR),
        env=stateless_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "mode=stateless" in mcp_res.stderr or "mode=stateless" in mcp_res.stdout
