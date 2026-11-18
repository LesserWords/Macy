"""Tests for MCP dual-transport server, stdio framing safety, /healthz, and mock/DB parity."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from starlette.testclient import TestClient


def test_stdio_handshake_and_clean_stdout_framing() -> None:
    """Real subprocess stdio initialize emits pure JSON-RPC on stdout and prints/logs on stderr."""
    script = (
        "import logging, sys\n"
        "from engine.mcp.server import _protect_stdio_stream, create_mcp_server, run_stdio_server\n"
        "logging.basicConfig(level=logging.INFO, stream=sys.stdout)\n"
        "with _protect_stdio_stream() as protocol_stdout:\n"
        "    bundle = create_mcp_server(mock=True, transport='stdio')\n"
        "    print('STRAY_STDOUT_MUST_NOT_CORRUPT_JSONRPC')\n"
        "    logging.getLogger('mcp-test').info('LOG_LINE_ON_STDERR')\n"
        "    run_stdio_server(bundle, protocol_stdout=protocol_stdout)\n"
    )
    env = dict(os.environ)
    env["APP_ENV"] = "development"
    env.pop("MACY_MCP_STANDALONE", None)

    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "stdio-test", "version": "1.0"},
        },
    }
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.flush()

    response_line = proc.stdout.readline().strip()
    proc.stdin.close()
    stderr_text = proc.stderr.read()
    exit_code = proc.wait(timeout=10)

    assert exit_code == 0, f"stderr={stderr_text}"
    assert "STRAY_STDOUT_MUST_NOT_CORRUPT_JSONRPC" in stderr_text
    assert "STRAY_STDOUT_MUST_NOT_CORRUPT_JSONRPC" not in response_line
    payload = json.loads(response_line)
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    caps = payload["result"]["capabilities"]
    assert "tools" in caps
    assert "resources" in caps
    assert "prompts" in caps


def test_http_healthz_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.mcp.server import create_mcp_server

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "healthz-test-token")
    monkeypatch.setenv("MCP_ACTOR_EMAIL", "owner@example.com")

    bundle = create_mcp_server(mock=True, transport="streamable-http")
    app = bundle.create_http_app(host="127.0.0.1")

    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_mock_mode_initializes_demo_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from engine.mcp.mock_store import MockStore
    from engine.mcp.server import create_mcp_server

    monkeypatch.setenv("APP_ENV", "development")
    bundle = create_mcp_server(mock=True, transport="stdio")
    assert isinstance(bundle.store, MockStore)

    workspaces = bundle.store.list_accessible_workspaces(
        user_id=bundle.store.get_user_by_email("owner@example.com")["user_id"]  # type: ignore[index]
    )
    slugs = [w["slug"] for w in workspaces]
    assert slugs == ["acme-demo"]

    status = bundle.store.get_workspace_status(
        workspace_id=bundle.store.get_workspace_id_by_slug("acme-demo"),
        user_id=bundle.store.get_user_by_email("owner@example.com")["user_id"],  # type: ignore[index]
    )
    assert status["gate1_awaiting_count"] >= 1
    assert status["gate2_awaiting_count"] >= 1
    assert bundle.store.llm is not None
    assert bundle.store.feed_source is not None
    assert bundle.store.keyword_provider is not None
    assert bundle.store.publisher is not None

    captured = capsys.readouterr()
    assert "MOCK" in captured.err.upper()


def test_run_streamable_http_server_delegates_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from engine.mcp.server import create_mcp_server, run_streamable_http_server

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "uvicorn-test-token")
    monkeypatch.setenv("MCP_ACTOR_EMAIL", "owner@example.com")
    bundle = create_mcp_server(mock=True, transport="streamable-http")

    recorded: dict[str, object] = {}

    def _fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        recorded.update({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    run_streamable_http_server(bundle, host="127.0.0.1", port=8877)
    assert recorded["host"] == "127.0.0.1"
    assert recorded["port"] == 8877
    assert recorded["log_level"] == "info"


def test_db_outage_fails_cleanly_without_mock_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import services
    from engine.mcp.errors import MCPError, MCPErrorCode
    from engine.mcp.server import create_mcp_server

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("MACY_MCP_STANDALONE", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgres://app:wrong@127.0.0.1:59999/macy?connect_timeout=1"
    )

    def _boom() -> str:
        raise RuntimeError("connection refused postgres://app:pw@127.0.0.1:59999/macy")

    monkeypatch.setattr(services, "check_database_connection", _boom)

    with pytest.raises(MCPError) as exc_info:
        create_mcp_server(mock=False, transport="stdio")
    assert exc_info.value.code == MCPErrorCode.INTERNAL_ERROR
    assert "pw@" not in exc_info.value.message


def test_protect_stdio_stream_and_run_stdio_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process stdio protection switches logging StreamHandlers to stderr and restores them."""
    import logging

    from engine.mcp.server import _protect_stdio_stream, create_mcp_server, run_stdio_server

    monkeypatch.setenv("APP_ENV", "development")
    bundle = create_mcp_server(mock=True, transport="stdio")
    root_logger = logging.getLogger()
    stdout_handler = logging.StreamHandler(sys.stdout)
    root_logger.addHandler(stdout_handler)
    original_stdout = sys.stdout
    recorded_transports: list[str] = []

    def _fake_run(*, transport: str = "stdio") -> None:
        recorded_transports.append(transport)

    monkeypatch.setattr(bundle.mcp, "run", _fake_run)
    try:
        with _protect_stdio_stream() as captured_stdout:
            assert captured_stdout is original_stdout
            assert stdout_handler.stream is sys.stderr
        assert stdout_handler.stream is original_stdout

        run_stdio_server(bundle)
        assert recorded_transports == ["stdio"]
        assert sys.stdout is original_stdout
        assert stdout_handler.stream is original_stdout
    finally:
        root_logger.removeHandler(stdout_handler)
