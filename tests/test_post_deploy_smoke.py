"""Tests for scripts/post_deploy_smoke.py (F20 AC3, M5-01 Step 1)."""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import post_deploy_smoke


class _SmokeHandler(BaseHTTPRequestHandler):
    health_status: int = 200
    health_body: str = "ok"
    root_status: int = 200
    root_html: str = "<html><body><h1>Sign in with Google</h1></body></html>"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/_stcore/health"):
            self.send_response(self.health_status)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(self.health_body.encode("utf-8"))
            return

        self.send_response(self.root_status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.root_html.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


@pytest.fixture
def smoke_server() -> Generator[tuple[str, type[_SmokeHandler]], None, None]:
    class CustomHandler(_SmokeHandler):
        pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), CustomHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", CustomHandler
    finally:
        server.shutdown()
        server.server_close()


def test_check_health_succeeds_and_fails(
    smoke_server: tuple[str, type[_SmokeHandler]],
) -> None:
    base_url, handler_cls = smoke_server
    post_deploy_smoke.check_health(base_url, timeout=5.0)

    handler_cls.health_body = "unhealthy"
    with pytest.raises(post_deploy_smoke.SmokeCheckError, match="Unexpected health body"):
        post_deploy_smoke.check_health(base_url, timeout=5.0)

    handler_cls.health_status = 503
    with pytest.raises(post_deploy_smoke.SmokeCheckError, match="Health probe failed"):
        post_deploy_smoke.check_health(base_url, timeout=5.0)


def _mock_playwright_page(
    *,
    status: int = 200,
    exception_count: int = 0,
    authenticated: bool = True,
    has_cost_marker: bool = True,
    fail_on_save_click: bool = True,
) -> tuple[MagicMock, MagicMock]:
    page = MagicMock()
    resp = MagicMock()
    resp.status = status
    page.goto.return_value = resp

    def _locator(selector: str) -> MagicMock:
        loc = MagicMock()
        if "stException" in selector:
            loc.count.return_value = exception_count
        else:
            loc.count.return_value = 0
        return loc

    page.locator.side_effect = _locator

    def _get_by_text(text: str) -> MagicMock:
        loc = MagicMock()
        loc.or_.side_effect = functools.partial(
            lambda first, other: first if first.count.return_value else other,
            loc,
        )
        if text in (
            "Campanhas",
            "Campaigns",
            "Configurações da Campanha",
            "Campaign Settings",
        ):
            loc.count.return_value = 1 if authenticated else 0
        elif text in ("Custo Mensal Estimado", "Estimated Monthly Cost"):
            loc.count.return_value = 1 if (authenticated and has_cost_marker) else 0
        elif "Save" in text or "Salvar" in text:
            loc.count.return_value = 1
            if fail_on_save_click:
                loc.click.side_effect = AssertionError("Read-only smoke must never click Save")
        else:
            loc.count.return_value = 0
        return loc

    page.get_by_text.side_effect = _get_by_text

    pw_cm = MagicMock()
    pw_obj = pw_cm.__enter__.return_value
    browser = pw_obj.chromium.launch.return_value
    context = browser.new_context.return_value
    context.new_page.return_value = page
    return pw_cm, page


def test_run_post_deploy_smoke_j1_readonly_and_auth_enforcement(
    smoke_server: tuple[str, type[_SmokeHandler]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_url, _handler = smoke_server
    pw_cm, page = _mock_playwright_page(authenticated=True, has_cost_marker=True)
    monkeypatch.setattr(post_deploy_smoke, "_sync_playwright", lambda: pw_cm)

    storage_file = tmp_path / "storage.json"
    storage_file.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    res = post_deploy_smoke.run_post_deploy_smoke(
        base_url,
        storage_state_json=storage_file.read_text(encoding="utf-8"),
        require_auth=True,
    )
    assert res["health"] == "ok"
    assert res["j1_checked"] is True
    assert page.goto.called

    # Fail when stException appears on UI
    pw_err, _ = _mock_playwright_page(exception_count=1)
    monkeypatch.setattr(post_deploy_smoke, "_sync_playwright", lambda: pw_err)
    with pytest.raises(post_deploy_smoke.SmokeCheckError, match="Streamlit exception"):
        post_deploy_smoke.run_post_deploy_smoke(base_url, require_auth=False)

    # Fail when --require-auth is true but Campaign nav is absent
    pw_unauth, _ = _mock_playwright_page(authenticated=False)
    monkeypatch.setattr(post_deploy_smoke, "_sync_playwright", lambda: pw_unauth)
    with pytest.raises(post_deploy_smoke.SmokeCheckError, match="Authenticated J1 navigation"):
        post_deploy_smoke.run_post_deploy_smoke(base_url, require_auth=True)

    # CLI exit codes (0 on success, 1 on failure)
    pw_ok, _ = _mock_playwright_page(authenticated=True)
    monkeypatch.setattr(post_deploy_smoke, "_sync_playwright", lambda: pw_ok)
    assert post_deploy_smoke.main(["--url", base_url]) == 0
    assert post_deploy_smoke.main(["--url", "http://127.0.0.1:9", "--timeout", "1"]) == 1


def test_post_deploy_smoke_mcp_auth_and_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --check-mcp enforces HTTP 401 unauth and 200 on Bearer initialize + workspace call."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    expected_token = "smoke-mcp-secret-token-value"
    sid = "sess-smoke-001"
    unauth_status_override: dict[str, int] = {"status": 401}

    class _McpSmokeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            ok = b"ok"
            html = b"<html><body>OK</body></html>"
            body = ok if self.path.startswith("/_stcore/health") else html
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw_req = self.rfile.read(length) if length > 0 else b"{}"
            if not self.path.startswith("/mcp"):
                self.send_response(404)
                self.end_headers()
                return
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {expected_token}":
                err_body = b'{"error":"unauthorized"}'
                self.send_response(unauth_status_override["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                return

            req = json.loads(raw_req.decode("utf-8"))
            method = req.get("method")
            if method == "initialize":
                payload = {
                    "jsonrpc": "2.0",
                    "id": req["id"],
                    "result": {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}},
                }
                sse = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("mcp-session-id", sid)
                self.send_header("Content-Length", str(len(sse)))
                self.end_headers()
                self.wfile.write(sse)
                return
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            assert self.headers.get("mcp-session-id") == sid
            tool_json = json.dumps({"ok": True, "count": 1, "workspaces": [{"slug": "acme"}]})
            payload = {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"content": [{"type": "text", "text": tool_json}], "isError": False},
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpSmokeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        pw_ok, _ = _mock_playwright_page(authenticated=True)
        monkeypatch.setattr(post_deploy_smoke, "_sync_playwright", lambda: pw_ok)
        monkeypatch.setenv("MCP_AUTH_TOKEN", expected_token)

        res = post_deploy_smoke.check_mcp_smoke(
            base_url,
            timeout=5.0,
            mcp_token=expected_token,
        )
        assert res["unauth_401"] is True
        assert res["auth_verified"] is True

        assert post_deploy_smoke.main(["--url", base_url, "--check-mcp"]) == 0

        # Without token: still checks 401 on /mcp
        res_no_tok = post_deploy_smoke.check_mcp_smoke(base_url, timeout=5.0, mcp_token=None)
        assert res_no_tok == {"unauth_401": True, "auth_verified": False}

        # Wrong token fails closed
        bad_token = "wrong-token"  # noqa: S105
        with pytest.raises(post_deploy_smoke.SmokeCheckError, match="HTTP 401|HTTP 200"):
            post_deploy_smoke.check_mcp_smoke(base_url, timeout=5.0, mcp_token=bad_token)

        # Open /mcp (returning 200 without auth) fails loudly
        unauth_status_override["status"] = 200
        with pytest.raises(post_deploy_smoke.SmokeCheckError, match="401"):
            post_deploy_smoke.check_mcp_smoke(base_url, timeout=5.0)
        assert post_deploy_smoke.main(["--url", base_url, "--check-mcp"]) == 1
    finally:
        server.shutdown()
        server.server_close()
