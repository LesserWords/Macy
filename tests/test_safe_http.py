"""Tests for safe_http: SSRF prevention, IP validation, timeouts, caps, redirects."""

import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.safe_http import (
    BlockedIPError,
    CrossHostRedirectError,
    InsecureSchemeError,
    ResponseTooLargeError,
    SafeRedirectHandler,
    UnsafeURLError,
    _execute_request,
    _is_production_environment,
    _read_body_with_cap,
    is_ip_allowed,
    resolve_and_validate_host,
    safe_request,
    validate_safe_url,
)


def test_is_production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _is_production_environment(True) is True
    assert _is_production_environment(False) is False

    monkeypatch.setenv("APP_ENV", "production")
    assert _is_production_environment(None) is True

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENV", "prod")
    assert _is_production_environment(None) is False  # APP_ENV takes precedence

    monkeypatch.delenv("APP_ENV")
    assert _is_production_environment(None) is True  # ENV=prod


def test_insecure_scheme_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(InsecureSchemeError, match="HTTPS is required in production"):
        validate_safe_url("http://example.com/api")

    # In dev/test, http is accepted for valid public hosts
    monkeypatch.setenv("APP_ENV", "development")
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    ):
        validate_safe_url("http://example.com/api", is_production=False)


def test_unsupported_scheme_refused() -> None:
    with pytest.raises(InsecureSchemeError, match="Unsupported scheme"):
        validate_safe_url("ftp://example.com/file.txt")
    with pytest.raises(InsecureSchemeError, match="Unsupported scheme"):
        validate_safe_url("file:///etc/passwd")


def test_malformed_urls_refused() -> None:
    with pytest.raises(UnsafeURLError, match="non-empty string"):
        validate_safe_url("")
    with pytest.raises(UnsafeURLError, match="missing valid hostname"):
        validate_safe_url("https:///no-host")


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "127.0.0.1",
        "127.0.0.2",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # AWS/GCP metadata
        "169.254.1.1",  # link-local
        "100.64.0.1",  # Carrier-grade NAT
        "0.0.0.0",  # unspecified
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 private unique local
        "fd00::1",
    ],
)
def test_literal_blocked_ips_refused(blocked_ip: str) -> None:
    assert not is_ip_allowed(blocked_ip)
    host_target = f"[{blocked_ip}]" if ":" in blocked_ip else blocked_ip
    with pytest.raises(BlockedIPError):
        validate_safe_url(f"https://{host_target}/test", is_production=False)


def test_invalid_ip_string_not_allowed() -> None:
    assert not is_ip_allowed("invalid-ip")
    assert not is_ip_allowed("999.999.999.999")


@pytest.mark.parametrize(
    "allowed_ip",
    [
        "93.184.216.34",
        "8.8.8.8",
        "1.1.1.1",
        "2606:4700:4700::1111",
    ],
)
def test_public_ips_allowed(allowed_ip: str) -> None:
    assert is_ip_allowed(allowed_ip)
    host_target = f"[{allowed_ip}]" if ":" in allowed_ip else allowed_ip
    # Ensure validate_safe_url accepts literal allowed IPs
    validate_safe_url(f"https://{host_target}/test", is_production=False)


@given(st.ip_addresses(v=4))
@settings(max_examples=100)
def test_hypothesis_ipv4_addresses(ip: ipaddress.IPv4Address) -> None:
    ip_str = str(ip)
    expected_blocked = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")
        or ip in ipaddress.ip_network("169.254.0.0/16")
    )
    assert is_ip_allowed(ip_str) != expected_blocked


@given(st.ip_addresses(v=6))
@settings(max_examples=100)
def test_hypothesis_ipv6_addresses(ip: ipaddress.IPv6Address) -> None:
    ip_str = str(ip)
    expected_blocked = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
    assert is_ip_allowed(ip_str) != expected_blocked


def test_blocked_hostnames_refused() -> None:
    with pytest.raises(BlockedIPError, match="Cloud metadata hostname blocked"):
        resolve_and_validate_host("metadata.google.internal")

    with pytest.raises(BlockedIPError, match="Cloud metadata hostname blocked"):
        resolve_and_validate_host("instance-data")


def test_host_resolution_failure() -> None:
    with (
        patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")),
        pytest.raises(UnsafeURLError, match="Failed to resolve hostname"),
    ):
        resolve_and_validate_host("nonexistent.invalid")

    with (
        patch("socket.getaddrinfo", return_value=[]),
        pytest.raises(UnsafeURLError, match="resolved to no addresses"),
    ):
        resolve_and_validate_host("empty.example.com")


def test_dns_rebinding_refused() -> None:
    # Hostname resolves to both a public IP and a private IP
    addr_info = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ]
    with (
        patch("socket.getaddrinfo", return_value=addr_info),
        pytest.raises(BlockedIPError, match="blocked IP"),
    ):
        validate_safe_url("https://rebind.example.com", is_production=False)


def test_response_size_cap_content_length() -> None:
    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Length": "2000000"}
    with pytest.raises(ResponseTooLargeError, match="exceeds 1048576 bytes cap"):
        _read_body_with_cap(mock_resp, max_bytes=1_048_576)


def test_response_size_cap_invalid_content_length_handled() -> None:
    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Length": "not-a-number"}
    mock_resp.read.side_effect = [b"hello", b""]
    data = _read_body_with_cap(mock_resp, max_bytes=1_048_576)
    assert data == b"hello"


def test_response_size_cap_streaming_exceeded() -> None:
    mock_resp = MagicMock()
    mock_resp.headers = {}
    mock_resp.read.side_effect = [b"A" * 600_000, b"B" * 600_000]

    with (
        patch("engine.safe_http.validate_safe_url"),
        patch("engine.safe_http._execute_request", return_value=mock_resp),
        pytest.raises(ResponseTooLargeError, match="exceeds 1048576 bytes cap"),
    ):
        safe_request("GET", "https://example.com/huge.bin")


def test_safe_redirect_handler_cross_host() -> None:
    handler = SafeRedirectHandler(initial_host="api.example.com", is_production=False)
    req = urllib.request.Request("https://api.example.com/initial")

    with pytest.raises(CrossHostRedirectError, match="Cross-host redirect refused"):
        handler.redirect_request(
            req,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="https://attacker.com/steal",
        )


def test_safe_redirect_handler_too_many_redirects() -> None:
    handler = SafeRedirectHandler(initial_host="example.com", max_redirects=2)
    req = urllib.request.Request("https://example.com/one")

    with patch("engine.safe_http.validate_safe_url"):
        # 1st redirect
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/two")
        # 2nd redirect
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/three")
        # 3rd redirect -> exceeds max
        with pytest.raises(UnsafeURLError, match="Too many redirects"):
            handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/four")


def test_safe_redirect_handler_same_host_allowed() -> None:
    handler = SafeRedirectHandler(initial_host="example.com", is_production=False)
    req = urllib.request.Request("https://example.com/old-path")

    with patch("engine.safe_http.validate_safe_url") as mock_val:
        new_req = handler.redirect_request(
            req,
            fp=None,
            code=301,
            msg="Moved Permanently",
            headers={},
            newurl="/new-path",
        )
        assert new_req is not None
        mock_val.assert_called_once_with("https://example.com/new-path", is_production=False)


def test_safe_request_success() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "application/json", "Content-Length": "16"}
    mock_resp.read.side_effect = [b'{"status": "ok"}', b""]

    with (
        patch("engine.safe_http.validate_safe_url"),
        patch("engine.safe_http._execute_request", return_value=mock_resp),
    ):
        resp = safe_request(
            "POST",
            "https://example.com/api",
            json_data={"key": "val"},
            headers={"X-Custom": "test"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert resp.text == '{"status": "ok"}'
        assert resp.headers["Content-Type"] == "application/json"
        assert resp.headers["Content-Length"] == "16"
        mock_resp.close.assert_called_once()


def test_safe_request_http_error_response() -> None:
    # Test handling of HTTPError (e.g. 404 response body should be read as SafeResponse)
    error_headers = http.client.HTTPMessage()
    error_headers["Content-Type"] = "application/json"
    err = urllib.error.HTTPError(
        url="https://example.com/notfound",
        code=404,
        msg="Not Found",
        hdrs=error_headers,
        fp=MagicMock(read=MagicMock(side_effect=[b'{"error": "not found"}', b""])),
    )

    with (
        patch("engine.safe_http.validate_safe_url"),
        patch("engine.safe_http._execute_request", side_effect=err),
    ):
        resp = safe_request("GET", "https://example.com/notfound")
        assert resp.status_code == 404
        assert resp.json() == {"error": "not found"}


def test_safe_request_with_raw_bytes() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.headers = {"Content-Type": "application/octet-stream"}
    mock_resp.read.side_effect = [b"created", b""]

    with (
        patch("engine.safe_http.validate_safe_url"),
        patch("engine.safe_http._execute_request", return_value=mock_resp) as mock_exec,
    ):
        resp = safe_request(
            "PUT",
            "https://example.com/upload",
            data=b"raw payload",
        )
        assert resp.status_code == 201
        assert resp.content == b"created"
        assert mock_exec.call_count == 1
        call_req = mock_exec.call_args[0][0]
        assert call_req.data == b"raw payload"


def test_execute_request_opens_opener() -> None:
    req = urllib.request.Request("https://example.com/test")
    mock_opener = MagicMock()
    with patch("urllib.request.build_opener", return_value=mock_opener):
        _execute_request(req, timeout=5.0, is_production=False)
        mock_opener.open.assert_called_once_with(req, timeout=5.0)


def test_safe_http_test_allow_hosts_only_in_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SAFE_HTTP_TEST_ALLOW_HOSTS permits Docker bridge IPs only in APP_ENV=test."""
    bridge_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.5", 9000))]
    monkeypatch.setenv("SAFE_HTTP_TEST_ALLOW_HOSTS", "webhook-receiver, ,localhost,metadata")

    with patch("socket.getaddrinfo", return_value=bridge_info):
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        validate_safe_url("http://webhook-receiver:9000/publish")

        with pytest.raises(BlockedIPError):
            validate_safe_url("http://other-internal-service:9000/publish")

        for non_test_env in ("development", "staging", "production"):
            monkeypatch.setenv("APP_ENV", non_test_env)
            if non_test_env == "production":
                with pytest.raises(InsecureSchemeError):
                    validate_safe_url("http://webhook-receiver:9000/publish")
                with pytest.raises(BlockedIPError):
                    validate_safe_url("https://webhook-receiver:9000/publish")
            else:
                with pytest.raises(BlockedIPError):
                    validate_safe_url("http://webhook-receiver:9000/publish")

        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("ENV", "production")
        with pytest.raises(BlockedIPError):
            validate_safe_url("http://webhook-receiver:9000/publish")
        monkeypatch.delenv("ENV")

        for forbidden_host in ("localhost", "metadata.google.internal", "172.18.0.5", "127.0.0.1"):
            with pytest.raises(BlockedIPError):
                validate_safe_url(f"http://{forbidden_host}:9000/publish")

    # Even for allowlisted host in test env, loopback / metadata / CGNAT resolutions remain blocked
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SAFE_HTTP_TEST_ALLOW_HOSTS", "webhook-receiver")
    for dangerous_ip in ("127.0.0.1", "169.254.169.254", "100.64.0.1"):
        danger_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (dangerous_ip, 9000))]
        with (
            patch("socket.getaddrinfo", return_value=danger_info),
            pytest.raises(BlockedIPError),
        ):
            validate_safe_url("http://webhook-receiver:9000/publish")
