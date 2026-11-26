"""Tests for scripts/deploy.sh preflight and failure isolation (F20 AC2, ADR-012, ADR-019)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SH = ROOT / "scripts" / "deploy.sh"


def _sh_executable() -> str | None:
    sh = shutil.which("sh")
    if sh:
        return sh
    git_sh = Path(r"C:\Program Files\Git\usr\bin\sh.exe")
    if git_sh.is_file():
        return str(git_sh)
    return None


def _run_deploy_sh(
    args: list[str], custom_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    sh = _sh_executable()
    if not sh:
        pytest.skip("POSIX shell not available")

    env = dict(custom_env if custom_env is not None else os.environ)
    git_bin = r"C:\Program Files\Git\usr\bin"
    if os.path.isdir(git_bin) and git_bin not in env.get("PATH", ""):
        env["PATH"] = f"{git_bin};{env.get('PATH', '')}"

    return subprocess.run(  # noqa: S603
        [sh, str(DEPLOY_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_deploy_script_exists() -> None:
    assert DEPLOY_SH.is_file(), "scripts/deploy.sh must exist"
    content = DEPLOY_SH.read_text(encoding="utf-8")
    assert "#!/usr/bin/env sh" in content
    assert "preflight" in content
    assert "rollback" in content
    assert "one-shot" in content


def test_deploy_script_help() -> None:
    res = _run_deploy_sh(["--help"])
    assert res.returncode == 0
    assert "Usage" in res.stdout or "deploy:" in res.stdout or "scripts/deploy.sh" in res.stdout


def test_deploy_preflight_missing_env(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["ENV_FILE"] = str(tmp_path / "nonexistent.env")
    env["SKIP_ENV_MODE_CHECK"] = "1"

    res = _run_deploy_sh(["--preflight-only"], env)
    assert res.returncode != 0
    assert "not found" in res.stderr


def _run_preflight(
    env_file: Path,
    *,
    allow_placeholders: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["ENV_FILE"] = str(env_file)
    env["SKIP_ENV_MODE_CHECK"] = "1"
    env.pop("STREAMLIT_SECRETS_FILE", None)
    if allow_placeholders is not None:
        env["ALLOW_PLACEHOLDER_SECRETS"] = allow_placeholders
    if extra_env:
        env.update(extra_env)
    return _run_deploy_sh(["--preflight-only"], env)


def test_deploy_preflight_rejects_placeholders(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    shutil.copy(ROOT / ".env.staging.example", env_file)

    res = _run_preflight(env_file, allow_placeholders="0")
    assert res.returncode != 0
    assert "CHANGE_ME" in res.stderr


def _make_env_file(
    target: Path,
    *,
    app_pw: str = "my_strong_app_pw",
    oidc_client_credential: str = "my_google_client_value",
) -> None:
    lines = [
        "APP_ENV=staging",
        "COMPOSE_PROJECT_NAME=macy-test",
        "DOMAIN=localhost",
        "APP_IMAGE=test-image",
        "APP_TAG=v1.0",
        "POSTGRES_PASSWORD=my_strong_db_pw",
        f"APP_DB_PASSWORD={app_pw}",
        "APP_ENCRYPTION_KEY=fernet_key",
        "AUTH_COOKIE_SECRET=test_cookie_key_for_preflight_only",
        "GOOGLE_CLIENT_ID=test-client.apps.googleusercontent.com",
        f"GOOGLE_CLIENT_SECRET={oidc_client_credential}",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_deploy_preflight_rejects_default_app_password(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    _make_env_file(env_file, app_pw="app_secret")

    res = _run_preflight(env_file)
    assert res.returncode != 0
    assert "APP_DB_PASSWORD must not be the development default" in res.stderr


def test_deploy_preflight_rejects_oidc_placeholders(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    _make_env_file(env_file, oidc_client_credential="CHANGE_ME_google_client_secret")

    res = _run_preflight(env_file, allow_placeholders="0")
    assert res.returncode != 0
    assert "CHANGE_ME" in res.stderr


def test_deploy_preflight_success_with_valid_env_only(tmp_path: Path) -> None:
    """Preflight passes from .env OIDC variables without requiring any secrets.toml file."""
    env_file = tmp_path / ".env.test"
    _make_env_file(env_file)

    res = _run_preflight(env_file)
    assert res.returncode == 0
    assert "preflight ok" in res.stderr


def test_deploy_preflight_optional_secrets_file_checked(tmp_path: Path) -> None:
    """If STREAMLIT_SECRETS_FILE is explicitly provided, deploy.sh validates it."""
    env_file = tmp_path / ".env.test"
    _make_env_file(env_file)

    secrets_file = tmp_path / "secrets.toml"
    secrets_file.write_text("[auth]\ncookie_secret = 'CHANGE_ME'\n", encoding="utf-8")

    res = _run_preflight(
        env_file,
        allow_placeholders="0",
        extra_env={"STREAMLIT_SECRETS_FILE": str(secrets_file)},
    )
    assert res.returncode != 0
    assert "CHANGE_ME" in res.stderr


def test_backup_script_once_and_retention_pruning(tmp_path: Path) -> None:
    """Verify scripts/backup.sh creates a dump, syncs offsite, and prunes files > 14 days."""
    sh = _sh_executable()
    if not sh:
        pytest.skip("POSIX shell not available")

    backup_sh = ROOT / "scripts" / "backup.sh"
    assert backup_sh.is_file(), "scripts/backup.sh must exist"

    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir()
    (bin_dir / "pg_dump").write_text(
        "#!/usr/bin/env sh\n"
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-f" ]; then shift; out="$1"; fi\n'
        "  shift\n"
        "done\n"
        'printf "PGDMP-FAKE-CONTENT\\n" > "$out"\n',
        encoding="utf-8",
    )
    (bin_dir / "pg_restore").write_text(
        "#!/usr/bin/env sh\nexit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "pg_dump").chmod(0o755)
    (bin_dir / "pg_restore").chmod(0o755)

    backup_dir = tmp_path / "backups"
    offsite_dir = tmp_path / "offsite"
    backup_dir.mkdir()
    offsite_dir.mkdir()

    # Seed an old dump (20 days old) and a recent dump (2 days old) in both dirs
    old_local = backup_dir / "macy_macy_20260101T000000Z.dump"
    old_offsite = offsite_dir / "macy_macy_20260101T000000Z.dump"
    recent_local = backup_dir / "macy_macy_20261108T000000Z.dump"
    recent_offsite = offsite_dir / "macy_macy_20261108T000000Z.dump"
    for f in (old_local, old_offsite, recent_local, recent_offsite):
        f.write_text("dummy", encoding="utf-8")

    now_ts = int(time.time())
    twenty_days_ago = now_ts - (20 * 86400)
    two_days_ago = now_ts - (2 * 86400)
    os.utime(old_local, (twenty_days_ago, twenty_days_ago))
    os.utime(old_offsite, (twenty_days_ago, twenty_days_ago))
    os.utime(recent_local, (two_days_ago, two_days_ago))
    os.utime(recent_offsite, (two_days_ago, two_days_ago))

    env = {
        **os.environ,
        "PATH": os.pathsep.join(
            [str(bin_dir), r"C:\Program Files\Git\usr\bin", os.environ.get("PATH", "")]
        ),
        "POSTGRES_HOST": "db",
        "POSTGRES_DB": "macy",
        "POSTGRES_USER": "owner",
        "POSTGRES_PASSWORD": "test_pw",
        "BACKUP_DIR": str(backup_dir),
        "OFFSITE_BACKUP_DIR": str(offsite_dir),
        "BACKUP_RETENTION_DAYS": "14",
    }

    res = subprocess.run(  # noqa: S603
        [sh, str(backup_sh), "--once"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, f"backup.sh failed: {res.stderr}"

    local_dumps = sorted(p.name for p in backup_dir.glob("*.dump"))
    offsite_dumps = sorted(p.name for p in offsite_dir.glob("*.dump"))
    assert len(local_dumps) == 2
    assert local_dumps == offsite_dumps
    assert "macy_macy_20260101T000000Z.dump" not in local_dumps
    assert "macy_macy_20261108T000000Z.dump" in local_dumps
    assert (backup_dir / ".last_backup_ok").is_file()


def test_backup_script_heartbeat_and_failure_alerts(tmp_path: Path) -> None:
    """backup.sh pings BACKUP_HEARTBEAT_URL on ok and fires alerts/clears marker on fail."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sh = _sh_executable()
    curl = shutil.which("curl")
    if not sh or not curl:
        pytest.skip("POSIX shell or curl not available")

    received: list[tuple[str, str, str]] = []

    class _AlertHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            received.append(("GET", self.path, ""))
            self.send_response(200)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            received.append(("POST", self.path, body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _AlertHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        backup_sh = ROOT / "scripts" / "backup.sh"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        pg_dump_bin = bin_dir / "pg_dump"
        pg_dump_bin.write_text(
            "#!/usr/bin/env sh\n"
            'if [ "${FAKE_PG_DUMP_FAIL:-0}" = "1" ]; then exit 1; fi\n'
            'out=""\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = "-f" ]; then shift; out="$1"; fi\n'
            "  shift\n"
            "done\n"
            'printf "PGDMP-FAKE\\n" > "$out"\n',
            encoding="utf-8",
        )
        pg_restore_bin = bin_dir / "pg_restore"
        pg_restore_bin.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        pg_dump_bin.chmod(0o755)
        pg_restore_bin.chmod(0o755)

        backup_dir = tmp_path / "backups"
        offsite_dir = tmp_path / "offsite"
        backup_dir.mkdir()
        offsite_dir.mkdir()

        path_parts = [
            str(bin_dir),
            r"C:\Program Files\Git\usr\bin",
            str(Path(curl).parent),
            os.environ.get("PATH", ""),
        ]
        base_env = {
            **os.environ,
            "PATH": os.pathsep.join(path_parts),
            "POSTGRES_HOST": "db",
            "POSTGRES_DB": "macy_prod",
            "POSTGRES_USER": "owner",
            "POSTGRES_PASSWORD": "test_pw",
            "BACKUP_DIR": str(backup_dir),
            "OFFSITE_BACKUP_DIR": str(offsite_dir),
            "BACKUP_HEARTBEAT_URL": f"http://127.0.0.1:{port}/hb",
            "BACKUP_ALERT_URL": f"http://127.0.0.1:{port}/alert",
        }

        res_ok = subprocess.run(  # noqa: S603
            [sh, str(backup_sh), "--once"],
            capture_output=True,
            text=True,
            errors="replace",
            env=base_env,
            check=False,
        )
        assert res_ok.returncode == 0, res_ok.stderr
        assert (backup_dir / ".last_backup_ok").is_file()
        assert ("GET", "/hb", "") in received

        received.clear()
        fail_env = {**base_env, "FAKE_PG_DUMP_FAIL": "1"}
        res_fail = subprocess.run(  # noqa: S603
            [sh, str(backup_sh), "--once"],
            capture_output=True,
            text=True,
            errors="replace",
            env=fail_env,
            check=False,
        )
        assert res_fail.returncode == 1
        assert not (backup_dir / ".last_backup_ok").exists()
        assert ("GET", "/hb/fail", "") in received
        post_events = [item for item in received if item[0] == "POST" and item[1] == "/alert"]
        assert len(post_events) == 1
        payload = json.loads(post_events[0][2])
        assert payload["service"] == "backup"
        assert payload["db"] == "macy_prod"
        assert "pg_dump" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


def _write_fake_docker_and_curl(
    bin_dir: Path,
    log_file: Path,
    *,
    curl_behavior: str = "healthy",
) -> None:
    docker_bin = bin_dir / "docker"
    docker_bin.write_text(
        "#!/usr/bin/env sh\n"
        f'printf "%s\\n" "$*" >> "{log_file.as_posix()}"\n'
        'if [ "${1:-}" = "inspect" ]; then\n'
        '  printf "%s\\n" "ghcr.io/org/macy:0.0.9-prev"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "${1:-}" = "compose" ]; then\n'
        "  shift\n"
        '  while [ "$#" -gt 0 ]; do\n'
        '    case "$1" in\n'
        "      -p | --env-file | -f) shift 2 ;;\n"
        "      *) break ;;\n"
        "    esac\n"
        "  done\n"
        '  if [ "${1:-}" = "ps" ]; then\n'
        '    printf "fake-app-cid\\n"\n'
        "    exit 0\n"
        "  fi\n"
        '  if [ "${1:-}" = "exec" ]; then\n'
        "    cat >/dev/null\n"
        "    exit 0\n"
        "  fi\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_bin.chmod(0o755)

    curl_bin = bin_dir / "curl"
    if curl_behavior == "healthy":
        curl_script = (
            "#!/usr/bin/env sh\n"
            f'printf "curl %s\\n" "$*" >> "{log_file.as_posix()}"\n'
            'case "$*" in\n'
            '  */mcp*) printf "HTTP/1.1 401 Unauthorized\\r\\n\\r\\n"; exit 0 ;;\n'
            '  *) printf "ok"; exit 0 ;;\n'
            "esac\n"
        )
    else:
        curl_script = (
            "#!/usr/bin/env sh\n"
            f'printf "curl %s\\n" "$*" >> "{log_file.as_posix()}"\n'
            'case "$*" in\n'
            '  */mcp*) printf "HTTP/1.1 200 OK\\r\\n\\r\\n"; exit 0 ;;\n'
            '  *) printf "ok"; exit 0 ;;\n'
            "esac\n"
        )
    curl_bin.write_text(curl_script, encoding="utf-8")
    curl_bin.chmod(0o755)


def test_deploy_preflight_rejects_mcp_placeholder(tmp_path: Path) -> None:
    """Preflight rejects placeholder MCP_AUTH_TOKEN and empty MCP_ACTOR_EMAIL when MCP active."""
    env_file = tmp_path / ".env.mcp"
    _make_env_file(env_file)
    with env_file.open("a", encoding="utf-8") as fh:
        fh.write("MCP_ENABLED=true\nMCP_AUTH_TOKEN=CHANGE_ME_mcp_bearer_token\nMCP_ACTOR_EMAIL=\n")

    res_placeholder = _run_preflight(env_file, allow_placeholders="0")
    assert res_placeholder.returncode != 0
    assert "MCP_AUTH_TOKEN" in res_placeholder.stderr

    _make_env_file(env_file)
    with env_file.open("a", encoding="utf-8") as fh:
        fh.write(
            "COMPOSE_PROFILES=mcp\nMCP_AUTH_TOKEN=valid-runtime-secret-token\nMCP_ACTOR_EMAIL=\n"
        )

    res_missing_actor = _run_preflight(env_file, allow_placeholders="0")
    assert res_missing_actor.returncode != 0
    assert "MCP_ACTOR_EMAIL" in res_missing_actor.stderr

    _make_env_file(env_file)
    with env_file.open("a", encoding="utf-8") as fh:
        fh.write(
            "MCP_ENABLED=true\n"
            "MCP_AUTH_TOKEN=valid-runtime-secret-token\n"
            "MCP_ACTOR_EMAIL=owner@example.test\n"
        )

    res_ok = _run_preflight(env_file, allow_placeholders="0")
    assert res_ok.returncode == 0
    assert "preflight ok" in res_ok.stderr


def test_deploy_and_rollback_manage_mcp_without_rerunning_migrations(tmp_path: Path) -> None:
    """deploy.sh rolls out mcp with COMPOSE_PROFILES=mcp and --rollback skips migrate."""
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker_calls.log"
    _write_fake_docker_and_curl(bin_dir, log_file, curl_behavior="healthy")

    env_file = tmp_path / ".env.prod"
    _make_env_file(env_file)
    with env_file.open("a", encoding="utf-8") as fh:
        fh.write(
            "MCP_ENABLED=true\n"
            "MCP_AUTH_TOKEN=valid-runtime-secret-token\n"
            "MCP_ACTOR_EMAIL=owner@example.test\n"
            "SMOKE_URL=https://localhost:8443/_stcore/health\n"
        )

    base_env = {
        **os.environ,
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        "ENV_FILE": str(env_file),
        "SKIP_ENV_MODE_CHECK": "1",
        "SMOKE_RETRIES": "1",
        "SMOKE_DELAY": "0",
    }
    res_deploy = _run_deploy_sh([], base_env)
    assert res_deploy.returncode == 0, res_deploy.stderr
    deploy_log = log_file.read_text(encoding="utf-8")
    assert "pull app worker caddy db migrate backup mcp" in deploy_log
    assert "run --rm migrate" in deploy_log
    assert "app worker caddy backup mcp" in deploy_log

    # Explicit --rollback restores app, worker, and mcp without running migrations
    log_file.write_text("", encoding="utf-8")
    res_rb = _run_deploy_sh(["--rollback", "0.0.9-prev"], base_env)
    assert res_rb.returncode == 0, res_rb.stderr
    rb_log = log_file.read_text(encoding="utf-8")
    assert "migrate" not in rb_log
    assert "up -d --no-build --wait" in rb_log
    assert "app worker mcp" in rb_log

    # Smoke failure on unauthenticated /mcp triggers automatic rollback including mcp
    _write_fake_docker_and_curl(bin_dir, log_file, curl_behavior="mcp_open")
    log_file.write_text("", encoding="utf-8")
    res_fail = _run_deploy_sh([], base_env)
    assert res_fail.returncode != 0
    fail_log = log_file.read_text(encoding="utf-8")
    assert "rolling back" in res_fail.stderr
    assert "up -d --no-build --wait" in fail_log
    assert "app worker mcp" in fail_log


def test_deploy_real_curl_smoke_mcp_401_with_crlf_env(tmp_path: Path) -> None:
    """deploy.sh smoke passes with real curl against HTTP /_stcore/health + 401 /mcp."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sh = _sh_executable()
    mingw_curl = Path(r"C:\Program Files\Git\mingw64\bin\curl.exe")
    curl = str(mingw_curl) if mingw_curl.is_file() else shutil.which("curl")
    if not sh or not curl:
        pytest.skip("POSIX shell or curl not available")

    seen: list[tuple[str, str]] = []

    class _SmokeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(("GET", self.path))
            if self.path == "/_stcore/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            seen.append(("POST", self.path))
            if self.path == "/mcp":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"unauthorized"}')
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        bin_dir = tmp_path / "fake_docker_only"
        bin_dir.mkdir()
        log_file = tmp_path / "docker.log"
        _write_fake_docker_and_curl(bin_dir, log_file, curl_behavior="healthy")
        (bin_dir / "curl").unlink()

        env_file = tmp_path / ".env.crlf"
        _make_env_file(env_file)
        with env_file.open("a", encoding="utf-8", newline="") as fh:
            fh.write(
                "MCP_ENABLED=true\r\n"
                "MCP_AUTH_TOKEN=valid-runtime-secret-token\r\n"
                "MCP_ACTOR_EMAIL=owner@example.test\r\n"
                f"SMOKE_URL=http://127.0.0.1:{port}/_stcore/health\r\n"
            )

        env = {
            **os.environ,
            "PATH": os.pathsep.join(
                [str(bin_dir), str(Path(curl).parent), os.environ.get("PATH", "")]
            ),
            "ENV_FILE": str(env_file),
            "SKIP_ENV_MODE_CHECK": "1",
            "SKIP_PULL": "1",
            "SMOKE_RETRIES": "1",
            "SMOKE_DELAY": "0",
        }
        res = _run_deploy_sh([], env)
        assert res.returncode == 0, res.stderr
        assert "smoke ok" in res.stderr
        assert ("GET", "/_stcore/health") in seen
        assert ("POST", "/mcp") in seen
        assert "app worker caddy backup mcp" in log_file.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()
