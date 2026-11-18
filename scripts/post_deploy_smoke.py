"""Post-deploy production smoke check: /_stcore/health + read-only J1 UI + MCP (F20 AC3)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class SmokeCheckError(RuntimeError):
    """Raised when a post-deploy health, J1 read-only, or MCP smoke verification fails."""


def _sync_playwright() -> Any:
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _validate_http_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        msg = f"Invalid smoke target URL: {url}"
        raise SmokeCheckError(msg)
    return cleaned


def _ssl_context(*, insecure: bool = False) -> ssl.SSLContext:
    ssl_ctx = ssl.create_default_context()
    if insecure:
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    return ssl_ctx


def check_health(base_url: str, *, timeout: float = 10.0, insecure: bool = False) -> str:
    """Probe `<base_url>/_stcore/health` and require HTTP 200 with body `ok`."""
    normalized = _validate_http_url(base_url)
    health_url = f"{normalized}/_stcore/health"
    ssl_ctx = _ssl_context(insecure=insecure)

    try:
        with urllib.request.urlopen(health_url, timeout=timeout, context=ssl_ctx) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            body = str(resp.read().decode("utf-8", errors="replace").strip())
    except (urllib.error.URLError, OSError) as exc:
        msg = f"Health probe failed for {health_url}: {exc}"
        raise SmokeCheckError(msg) from exc

    if status != 200:
        msg = f"Health probe failed with status {status} at {health_url}"
        raise SmokeCheckError(msg)
    if body != "ok":
        msg = f"Unexpected health body '{body}' from {health_url} (expected 'ok')"
        raise SmokeCheckError(msg)
    return body


def _post_mcp_json(
    mcp_url: str,
    *,
    payload: dict[str, Any],
    insecure: bool = False,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], str]:
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **(headers or {}),
    }
    req = urllib.request.Request(  # noqa: S310
        mcp_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    ssl_ctx = _ssl_context(insecure=insecure)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200))
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read().decode("utf-8", errors="replace")
            return status, hdrs, body
    except urllib.error.HTTPError as exc:
        hdrs = {k.lower(): v for k, v in exc.headers.items()}
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, hdrs, body
    except (urllib.error.URLError, OSError) as exc:
        msg = f"MCP probe request failed for {mcp_url}: {exc}"
        raise SmokeCheckError(msg) from exc


def _parse_mcp_rpc_body(raw: str) -> dict[str, Any]:
    text = raw.strip()
    parsed: dict[str, Any] | None = None
    if text.startswith("{"):
        parsed = json.loads(text)
    else:
        for line in text.splitlines():
            if line.startswith("data:"):
                candidate = line.partition(":")[2].strip()
                if candidate.startswith("{"):
                    parsed = json.loads(candidate)
                    break
    if not isinstance(parsed, dict):
        msg = f"Invalid MCP JSON-RPC response body: {raw[:200]}"
        raise SmokeCheckError(msg)
    if "error" in parsed:
        msg = f"MCP JSON-RPC error returned: {parsed['error']}"
        raise SmokeCheckError(msg)
    return parsed


def _extract_mcp_tool_payload(rpc_payload: dict[str, Any]) -> dict[str, Any]:
    result = rpc_payload.get("result")
    if not isinstance(result, dict):
        msg = "MCP tools/call missing result object"
        raise SmokeCheckError(msg)
    if result.get("isError"):
        msg = f"MCP tools/call reported isError: {result}"
        raise SmokeCheckError(msg)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text_val = content[0].get("text")
        if isinstance(text_val, str):
            loaded = json.loads(text_val)
            if isinstance(loaded, dict):
                return loaded
    msg = "MCP tools/call response missing structured JSON payload"
    raise SmokeCheckError(msg)


def _init_mcp_session(
    mcp_url: str,
    *,
    token: str,
    timeout: float,
    insecure: bool,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    status, resp_headers, body = _post_mcp_json(
        mcp_url,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "macy-post-deploy-smoke", "version": "1.0"},
            },
        },
        headers=headers,
        timeout=timeout,
        insecure=insecure,
    )
    if status != 200:
        msg = f"Authenticated MCP initialize failed with HTTP {status} at {mcp_url}"
        raise SmokeCheckError(msg)
    rpc = _parse_mcp_rpc_body(body)
    if "result" not in rpc:
        msg = f"MCP initialize response missing result at {mcp_url}"
        raise SmokeCheckError(msg)
    session_headers = dict(headers)
    session_id = resp_headers.get("mcp-session-id")
    if session_id:
        session_headers["mcp-session-id"] = session_id
    return session_headers


def check_mcp_smoke(
    base_url: str,
    *,
    timeout: float = 10.0,
    insecure: bool = False,
    mcp_token: str | None = None,
) -> dict[str, bool]:
    """Verify `<base_url>/mcp` fails closed (401) unauthenticated and accepts valid Bearer calls."""
    normalized = _validate_http_url(base_url)
    mcp_url = f"{normalized}/mcp"
    unauth_status, _hdrs, _body = _post_mcp_json(
        mcp_url,
        payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        timeout=timeout,
        insecure=insecure,
    )
    if unauth_status != 401:
        msg = f"Expected HTTP 401 on unauthenticated POST {mcp_url}, got {unauth_status}"
        raise SmokeCheckError(msg)

    clean_token = (mcp_token or "").strip()
    if not clean_token:
        return {"unauth_401": True, "auth_verified": False}

    session_headers = _init_mcp_session(
        mcp_url, token=clean_token, timeout=timeout, insecure=insecure
    )
    notif_status, _nh, _nb = _post_mcp_json(
        mcp_url,
        payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=session_headers,
        timeout=timeout,
        insecure=insecure,
    )
    if notif_status not in (200, 202, 204):
        msg = f"MCP notifications/initialized returned unexpected HTTP {notif_status}"
        raise SmokeCheckError(msg)

    call_status, _ch, call_body = _post_mcp_json(
        mcp_url,
        payload={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "macy_list_workspaces", "arguments": {}},
        },
        headers=session_headers,
        timeout=timeout,
        insecure=insecure,
    )
    if call_status != 200:
        msg = f"MCP macy_list_workspaces failed with HTTP {call_status}"
        raise SmokeCheckError(msg)
    tool_data = _extract_mcp_tool_payload(_parse_mcp_rpc_body(call_body))
    if not tool_data.get("ok") or not isinstance(tool_data.get("workspaces"), list):
        msg = f"MCP macy_list_workspaces returned invalid payload: {tool_data}"
        raise SmokeCheckError(msg)
    return {"unauth_401": True, "auth_verified": True}


def _resolve_temp_storage_state(storage_state_json: str | None) -> Path | None:
    raw = (storage_state_json or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Invalid PLAYWRIGHT_STORAGE_STATE JSON: {exc}"
        raise SmokeCheckError(msg) from exc
    handle, tmp_name = tempfile.mkstemp(prefix="post-deploy-state-", suffix=".json")
    path = Path(tmp_name)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(parsed, fh)
    return path


def _inspect_j1_readonly(page: Any, *, timeout_ms: int, require_auth: bool) -> bool:
    nav = page.get_by_text("Campanhas").or_(page.get_by_text("Campaigns"))
    if nav.count() == 0:
        if require_auth:
            msg = "Authenticated J1 navigation ('Campaigns') not found after page load."
            raise SmokeCheckError(msg)
        return False

    nav.first.click(timeout=timeout_ms)
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

    header = page.get_by_text("Configurações da Campanha").or_(
        page.get_by_text("Campaign Settings")
    )
    cost_label = page.get_by_text("Custo Mensal Estimado").or_(
        page.get_by_text("Estimated Monthly Cost")
    )
    if header.count() == 0 or cost_label.count() == 0:
        msg = "J1 read-only Campaign Settings markers missing after navigation."
        raise SmokeCheckError(msg)

    if page.locator('[data-testid="stException"]').count() > 0:
        msg = "Streamlit exception rendered on Campaign Settings screen."
        raise SmokeCheckError(msg)
    return True


def run_post_deploy_smoke(
    base_url: str,
    *,
    timeout: float = 30.0,
    insecure: bool = False,
    storage_state_json: str | None = None,
    require_auth: bool = False,
) -> dict[str, Any]:
    """Verify `/_stcore/health` and run read-only Playwright J1 verification."""
    normalized = _validate_http_url(base_url)
    health_body = check_health(normalized, timeout=min(timeout, 15.0), insecure=insecure)

    timeout_ms = int(timeout * 1000)
    state_path = _resolve_temp_storage_state(storage_state_json)
    try:
        with _sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx_kwargs: dict[str, Any] = {"ignore_https_errors": insecure}
                if state_path is not None:
                    ctx_kwargs["storage_state"] = str(state_path)
                context = browser.new_context(**ctx_kwargs)
                page = context.new_page()
                resp = page.goto(normalized, timeout=timeout_ms)
                if resp is not None and getattr(resp, "status", 200) >= 400:
                    msg = f"Root page returned HTTP {resp.status} for {normalized}"
                    raise SmokeCheckError(msg)
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                if page.locator('[data-testid="stException"]').count() > 0:
                    msg = "Streamlit exception rendered on root application page."
                    raise SmokeCheckError(msg)
                j1_checked = _inspect_j1_readonly(
                    page, timeout_ms=timeout_ms, require_auth=require_auth
                )
            finally:
                browser.close()
    finally:
        if state_path is not None:
            with contextlib.suppress(OSError):
                state_path.unlink()

    return {"url": normalized, "health": health_body, "j1_checked": j1_checked}


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for post-deploy smoke checks."""
    parser = argparse.ArgumentParser(
        description="Macy post-deploy J1 read-only + health smoke check"
    )
    parser.add_argument("--url", required=True, help="Target application base URL")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds")
    parser.add_argument("--insecure", action="store_true", help="Ignore self-signed TLS certs")
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="Fail unless authenticated J1 Campaign Settings screen is reachable",
    )
    parser.add_argument(
        "--check-mcp",
        action="store_true",
        help="Verify fail-closed 401 on POST /mcp and authenticated session when token is provided",
    )
    parser.add_argument(
        "--mcp-token",
        default=None,
        help="Optional Bearer token for authenticated /mcp smoke (defaults to MCP_AUTH_TOKEN env)",
    )
    args = parser.parse_args(argv)

    storage_json = os.environ.get("PROD_PLAYWRIGHT_STORAGE_STATE") or os.environ.get(
        "PLAYWRIGHT_STORAGE_STATE"
    )
    mcp_token = args.mcp_token or os.environ.get("MCP_AUTH_TOKEN")
    try:
        result = run_post_deploy_smoke(
            args.url,
            timeout=args.timeout,
            insecure=args.insecure,
            storage_state_json=storage_json,
            require_auth=args.require_auth,
        )
        mcp_result = (
            check_mcp_smoke(
                args.url,
                timeout=min(args.timeout, 15.0),
                insecure=args.insecure,
                mcp_token=mcp_token,
            )
            if args.check_mcp
            else None
        )
    except Exception as exc:
        print(f"Post-deploy smoke check FAILED: {exc}")
        return 1

    print(
        f"Post-deploy smoke check OK: url={result['url']} health={result['health']} "
        f"j1_checked={result['j1_checked']} mcp_checked={mcp_result is not None}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
