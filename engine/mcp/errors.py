"""Structured MCP error codes, safe exception types, and secret scrubbing (ADR-027)."""

from __future__ import annotations

from enum import StrEnum

from engine import observability, services
from engine.auth import FakeIdentityForbiddenError


class MCPErrorCode(StrEnum):
    """Machine-readable MCP tool and auth error codes."""

    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    BUDGET_CAP_EXCEEDED = "BUDGET_CAP_EXCEEDED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    MOCK_FORBIDDEN = "MOCK_FORBIDDEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class MCPError(Exception):
    """Structured error carrying a machine-readable code and secret-scrubbed message."""

    def __init__(self, code: MCPErrorCode, message: str) -> None:
        self.code = code
        self.message = observability.scrub_text(message)
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str]:
        """Serialize error payload cleanly for MCP clients."""
        return {"error": self.code.value, "message": self.message}


class MockForbiddenError(MCPError):
    """Raised when mock or standalone MCP mode is requested in staging or production."""

    def __init__(
        self,
        message: str = "Mock or standalone MCP mode is forbidden in staging or production",
    ) -> None:
        super().__init__(MCPErrorCode.MOCK_FORBIDDEN, message)


def format_exception_error(
    exc: BaseException,
    *,
    workspace_id: str | None = None,
    run_id: str | None = None,
    mcp_tool: str | None = None,
) -> dict[str, str]:
    """Translate arbitrary exceptions into scrubbed MCP error dicts and report internals."""
    if isinstance(exc, MCPError):
        return exc.to_dict()
    if isinstance(exc, FakeIdentityForbiddenError):
        return MCPError(MCPErrorCode.AUTH_REQUIRED, str(exc)).to_dict()
    if isinstance(exc, PermissionError):
        return MCPError(MCPErrorCode.PERMISSION_DENIED, str(exc)).to_dict()
    if isinstance(exc, services.MCP_BUDGET_EXCEPTIONS):
        return MCPError(MCPErrorCode.BUDGET_CAP_EXCEEDED, str(exc)).to_dict()
    if isinstance(exc, services.MCP_STATE_EXCEPTIONS):
        return MCPError(MCPErrorCode.INVALID_STATE, str(exc)).to_dict()
    if isinstance(exc, ValueError):
        lowered = str(exc).lower()
        code = (
            MCPErrorCode.NOT_FOUND
            if "not found" in lowered or "does not exist" in lowered
            else MCPErrorCode.INVALID_STATE
        )
        return MCPError(code, str(exc)).to_dict()

    observability.capture_exception(
        exc,
        workspace_id=workspace_id,
        run_id=run_id,
        mcp_tool=mcp_tool,
    )
    return MCPError(MCPErrorCode.INTERNAL_ERROR, str(exc)).to_dict()
