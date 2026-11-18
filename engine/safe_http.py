"""Safe outbound HTTP client with SSRF protection, size caps, and redirect controls.

Implements requirements from ADR-008, ADR-023, and F20:
- HTTPS enforcement in production
- Explicit IP blocklist (private RFC1918, loopback, link-local, CGNAT, metadata endpoints)
- DNS resolution validation (DNS rebinding prevention)
- Configurable timeout (default 10s)
- Response body size cap (default 1 MB / 1_048_576 bytes)
- Cross-host redirect refusal
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SECONDS: float = 10.0
DEFAULT_MAX_BYTES: int = 1_048_576  # 1 MB
MAX_REDIRECTS: int = 3

CGNAT_NETWORK_V4 = ipaddress.ip_network("100.64.0.0/10")
LINK_LOCAL_NETWORK_V4 = ipaddress.ip_network("169.254.0.0/16")
BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)


class SafeHTTPError(Exception):
    """Base exception for all safe_http errors."""


class UnsafeURLError(SafeHTTPError):
    """Raised when a URL is malformed, unresolvable, or violates security rules."""


class BlockedIPError(UnsafeURLError):
    """Raised when a URL targets a private, loopback, link-local, or metadata address."""


class InsecureSchemeError(UnsafeURLError):
    """Raised when a URL scheme is not allowed or violates production HTTPS policy."""


class ResponseTooLargeError(SafeHTTPError):
    """Raised when the response size exceeds the configured cap."""


class CrossHostRedirectError(SafeHTTPError):
    """Raised when an HTTP redirect targets a different domain/host."""


@dataclass(frozen=True)
class SafeResponse:
    """Immutable representation of a safe HTTP response."""

    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        """Decode response content as utf-8, replacing invalid sequences."""
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse response content as JSON."""
        return json.loads(self.text)


def is_ip_allowed(ip_str: str) -> bool:
    """Return True if the IP address is a safe, publicly-routable address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False

    return not (
        isinstance(ip, ipaddress.IPv4Address)
        and (ip in CGNAT_NETWORK_V4 or ip in LINK_LOCAL_NETWORK_V4)
    )


def _allowed_test_hosts() -> frozenset[str]:
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    env_name = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "")).strip().lower()
    if app_env != "test" or env_name in ("staging", "production", "prod"):
        return frozenset()
    raw_hosts = os.environ.get("SAFE_HTTP_TEST_ALLOW_HOSTS", "")
    return frozenset(
        clean
        for item in raw_hosts.split(",")
        if (clean := item.strip().lower())
        and clean not in BLOCKED_HOSTNAMES
        and clean != "localhost"
    )


def _is_test_bridge_ip_allowed(hostname: str, ip_str: str) -> bool:
    if hostname not in _allowed_test_hosts():
        return False
    ip = ipaddress.ip_address(ip_str)
    if ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_NETWORK_V4:
        return False
    return bool(ip.is_private)


def resolve_and_validate_host(hostname: str) -> list[str]:
    """Resolve hostname and assert that all resolved IP addresses are safe."""
    clean_host = hostname.strip().lower()
    if clean_host in BLOCKED_HOSTNAMES:
        raise BlockedIPError(f"Cloud metadata hostname blocked: {hostname}")

    # Check if host is already a literal IP
    try:
        ip = ipaddress.ip_address(clean_host)
        ip_str = str(ip)
        if not is_ip_allowed(ip_str):
            raise BlockedIPError(f"URL points to blocked IP: {ip_str}")
        return [ip_str]
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(clean_host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Failed to resolve hostname {clean_host}: {e}") from e

    if not addr_info:
        raise UnsafeURLError(f"Hostname {clean_host} resolved to no addresses")

    resolved_ips: list[str] = []
    for item in addr_info:
        sockaddr = item[4]
        ip_str = str(sockaddr[0])
        if not (is_ip_allowed(ip_str) or _is_test_bridge_ip_allowed(clean_host, ip_str)):
            raise BlockedIPError(f"Hostname {clean_host} resolved to blocked IP: {ip_str}")
        resolved_ips.append(ip_str)

    return resolved_ips


def _is_production_environment(is_production: bool | None = None) -> bool:
    if is_production is not None:
        return is_production
    env_name = os.environ.get("APP_ENV", os.environ.get("ENV", "development")).lower()
    return env_name in ("production", "prod")


def validate_safe_url(url: str, is_production: bool | None = None) -> None:
    """Validate that the given URL is safe to fetch according to SSRF and scheme rules."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL must be a non-empty string")

    parsed = urllib.parse.urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise InsecureSchemeError(f"Unsupported scheme: {parsed.scheme}")

    prod = _is_production_environment(is_production)
    if prod and scheme != "https":
        raise InsecureSchemeError("HTTPS is required in production")

    if not parsed.hostname:
        raise UnsafeURLError(f"URL missing valid hostname: {url}")

    resolve_and_validate_host(parsed.hostname)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that blocks cross-host redirects and enforces SSRF rules on redirects."""

    def __init__(
        self,
        initial_host: str,
        is_production: bool = False,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        super().__init__()
        self.initial_host = initial_host.split(":")[0].lower()
        self.is_production = is_production
        self.max_redirects = max_redirects
        self._redirect_count = 0

    def redirect_request(  # noqa: PLR0913
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        self._redirect_count += 1
        if self._redirect_count > self.max_redirects:
            raise UnsafeURLError(f"Too many redirects: exceeded {self.max_redirects}")

        full_new_url = urllib.parse.urljoin(req.full_url, newurl)
        new_parsed = urllib.parse.urlsplit(full_new_url)
        new_host = (new_parsed.hostname or "").lower()

        if new_host != self.initial_host:
            raise CrossHostRedirectError(
                f"Cross-host redirect refused: from {self.initial_host} to {new_host}"
            )

        validate_safe_url(full_new_url, is_production=self.is_production)
        return super().redirect_request(req, fp, code, msg, headers, full_new_url)


def _read_body_with_cap(response: Any, max_bytes: int) -> bytes:
    """Read response body in chunks enforcing max_bytes ceiling."""
    cl = response.headers.get("Content-Length")
    if cl is not None:
        try:
            if int(cl) > max_bytes:
                raise ResponseTooLargeError(
                    f"Response Content-Length {cl} exceeds {max_bytes} bytes cap"
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total_read = 0
    chunk_size = 64 * 1024
    while True:
        chunk = response.read(chunk_size)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > max_bytes:
            raise ResponseTooLargeError(
                f"Response body exceeds {max_bytes} bytes cap ({total_read} bytes read)"
            )
        chunks.append(chunk)

    return b"".join(chunks)


def _execute_request(
    req: urllib.request.Request,
    timeout: float,
    is_production: bool,
) -> Any:
    """Execute HTTP request with custom safe redirect handler."""
    handler = SafeRedirectHandler(initial_host=req.host, is_production=is_production)
    opener = urllib.request.build_opener(handler)
    return opener.open(req, timeout=timeout)


def safe_request(  # noqa: PLR0913
    method: str,
    url: str,
    *,
    is_production: bool | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    json_data: Any = None,
) -> SafeResponse:
    """Perform a safe outbound HTTP request with SSRF guards and size limits."""
    prod = _is_production_environment(is_production)
    validate_safe_url(url, is_production=prod)

    req_headers = dict(headers or {})
    req_body = data
    if json_data is not None:
        req_body = json.dumps(json_data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(  # noqa: S310
        url,
        data=req_body,
        headers=req_headers,
        method=method.upper(),
    )

    try:
        response = _execute_request(req, timeout=timeout, is_production=prod)
    except urllib.error.HTTPError as err:
        body = _read_body_with_cap(err, max_bytes=max_bytes)
        resp_headers = {k: v for k, v in err.headers.items()}
        return SafeResponse(
            status_code=err.code,
            headers=resp_headers,
            content=body,
        )

    try:
        body = _read_body_with_cap(response, max_bytes=max_bytes)
        status_code = getattr(response, "status", getattr(response, "code", 200))
        resp_headers = {k: v for k, v in response.headers.items()}
        return SafeResponse(
            status_code=status_code,
            headers=resp_headers,
            content=body,
        )
    finally:
        if hasattr(response, "close"):
            response.close()
