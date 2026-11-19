"""MCP server factory, stdio stream protection, and HTTP Bearer auth middleware (ADR-027)."""

from __future__ import annotations

import logging
import os
import sys
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, TextIO, cast

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from engine import __version__, capabilities, services
from engine.contracts import CrawlConfig
from engine.mcp.auth import (
    BriefRequest,
    DraftDecisionRequest,
    EvaluateDraftRequest,
    GenerateDraftRequest,
    ImportDraftRequest,
    IngestResearchRequest,
    MCPAuthConfig,
    MCPStoreProtocol,
    PublishDraftRequest,
    ResearchRequest,
    VerifyClaimsRequest,
    is_staging_environment,
    validate_http_startup_config,
    verify_bearer_token,
)
from engine.mcp.errors import MCPError, MCPErrorCode
from engine.mcp.mock_store import MockStore, validate_mock_policy
from engine.mcp.prompts import register_prompts
from engine.mcp.resources import register_resources
from engine.mcp.stateless import STATELESS_MCP_TOOLS, StatelessStoreAdapter
from engine.mcp.tools_editorial import register_editorial_tools
from engine.mcp.tools_research import register_research_tools

TransportMode = Literal["stdio", "streamable-http"]


class ServiceStoreAdapter:
    """Adapter exposing engine.services functions under the MCP store protocol (no engine.db)."""

    def check_connectivity(self) -> str:
        """Verify PostgreSQL connectivity via services without silent mock fallback."""
        try:
            return services.check_database_connection()
        except Exception as exc:
            msg = f"PostgreSQL connection failed: {exc}"
            raise MCPError(MCPErrorCode.INTERNAL_ERROR, msg) from exc

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return services.get_user_by_email(email=email)

    def get_user_by_id(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        return services.get_user_by_id(user_id=user_id)

    def get_workspace_owner_identity(self, *, workspace_id: uuid.UUID) -> dict[str, Any] | None:
        return services.get_workspace_owner_identity(workspace_id=workspace_id)

    def require_member(self, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> str:
        return services.require_member(workspace_id=workspace_id, user_id=user_id)

    def list_accessible_workspaces(self, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
        return services.list_accessible_workspaces(user_id=user_id)

    def get_workspace_status(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        return services.get_workspace_status(workspace_id=workspace_id, user_id=user_id)

    def run_research(self, request: ResearchRequest) -> dict[str, Any]:
        return services.run_mcp_research(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            query=request.query,
            run_id=request.run_id,
            window_days=request.window_days,
            offline=request.offline,
        )

    def ingest_research(self, request: IngestResearchRequest) -> dict[str, Any]:
        return services.ingest_external_research(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            topic=request.topic,
            documents=request.documents,
            run_id=request.run_id,
        )

    def score_topics(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        offline: bool | None = None,
    ) -> dict[str, Any]:
        return services.score_mcp_topics(
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            offline=offline,
        )

    def list_topics_for_approval(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return services.list_topics_for_approval(
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            limit=limit,
        )

    def decide_topic(  # noqa: PLR0913
        self,
        *,
        workspace_id: uuid.UUID,
        opportunity_id: uuid.UUID,
        decision: str,
        user_id: uuid.UUID,
        note: str | None = None,
    ) -> dict[str, Any]:
        return services.decide_topic(
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            decision=decision,
            user_id=user_id,
            note=note,
            auto_brief=False,
        )

    def get_or_create_brief(self, request: BriefRequest) -> dict[str, Any]:
        return services.get_or_create_mcp_brief(
            workspace_id=request.workspace_id,
            opportunity_id=request.opportunity_id,
            user_id=request.user_id,
            force_regenerate=request.force_regenerate,
            offline=request.offline,
        )

    def generate_draft(self, request: GenerateDraftRequest) -> dict[str, Any]:
        return services.generate_mcp_draft(
            workspace_id=request.workspace_id,
            brief_id=request.brief_id,
            user_id=request.user_id,
            offline=request.offline,
        )

    def verify_claims(self, request: VerifyClaimsRequest) -> dict[str, Any]:
        return services.verify_claims(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            draft_id=request.draft_id,
            claims=request.claims,
            passages=request.passages,
            run_id=request.run_id,
            offline=request.offline,
        )

    def evaluate_draft(self, request: EvaluateDraftRequest) -> dict[str, Any]:
        return services.evaluate_mcp_draft(
            workspace_id=request.workspace_id,
            draft_id=request.draft_id,
            user_id=request.user_id,
            offline=request.offline,
            deterministic_only=request.deterministic_only,
        )

    def import_draft(self, request: ImportDraftRequest) -> dict[str, Any]:
        return services.import_external_draft(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            file_path=request.file_path,
            markdown_text=request.body_md,
            brief_id=request.brief_id,
            format_slug=request.format_slug,
            topic=request.topic,
            primary_keyword=request.primary_keyword,
            deterministic_only=request.deterministic_only,
            offline=request.offline,
        )

    def list_drafts_for_review(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        include_decided: bool = False,
    ) -> list[dict[str, Any]]:
        return services.list_draft_reviews(
            workspace_id=workspace_id,
            user_id=user_id,
            include_decided=include_decided,
        )

    def decide_draft(self, request: DraftDecisionRequest) -> dict[str, Any]:
        return services.decide_draft(
            workspace_id=request.workspace_id,
            draft_id=request.draft_id,
            decision=request.decision,
            user_id=request.user_id,
            note=request.note,
        )

    def publish_draft(self, request: PublishDraftRequest) -> dict[str, Any]:
        eff_mode = "draft" if is_staging_environment(request.env) else request.mode
        return services.publish_draft(
            workspace_id=request.workspace_id,
            draft_id=request.draft_id,
            user_id=request.user_id,
            mode=eff_mode,
            destination=request.destination,
        )

    def current_brand(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        return services.current_brand(workspace_id=workspace_id, user_id=user_id)

    def get_weekly_report(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        return services.get_weekly_report_resource(workspace_id=workspace_id, user_id=user_id)

    def get_draft_workspace_id(self, *, draft_id: uuid.UUID) -> uuid.UUID | None:
        return services.get_draft_workspace_id(draft_id=draft_id)

    def get_draft_review_bundle(
        self,
        *,
        workspace_id: uuid.UUID,
        draft_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        return services.get_draft_review_bundle(
            workspace_id=workspace_id, draft_id=draft_id, user_id=user_id
        )

    def audit_url(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        url: str,
        check_external: bool = False,
    ) -> dict[str, Any]:
        return services.run_url_audit(
            workspace_id=workspace_id,
            user_id=user_id,
            url=url,
            check_external=check_external,
        )

    def crawl_site(  # noqa: PLR0913
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        start_url: str,
        max_pages: int | None = None,
        plan: Literal["free", "paid"] = "free",
    ) -> dict[str, Any]:
        cfg = CrawlConfig(plan=plan, max_pages=max_pages)
        return services.run_site_crawl(
            workspace_id=workspace_id,
            user_id=user_id,
            start_url=start_url,
            config=cfg,
        )

    def get_enabled_capabilities(self, *, workspace_id: uuid.UUID) -> list[str]:
        """Return enabled capability slugs for a workspace via services."""
        return services.get_enabled_capabilities(workspace_id=workspace_id)


def _extract_header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> str | None:
    target = name.lower()
    for raw_key, raw_val in headers:
        if raw_key.lower() == target:
            return raw_val.decode("latin-1")
    return None


class BearerAuthASGIMiddleware:
    """Fail-closed Bearer verifier guarding HTTP MCP routes while keeping /healthz public."""

    def __init__(self, app: ASGIApp, *, expected_token: str) -> None:
        self.app = app
        self.expected_token = expected_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if path == "/healthz" and method in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        auth_header = _extract_header(scope.get("headers") or (), b"authorization")
        if not verify_bearer_token(auth_header, self.expected_token):
            resp = JSONResponse(
                {"error": "unauthorized", "detail": "Valid Bearer token required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)


@dataclass
class MacyMCPBundle:
    """Configured MCP server instance, bound store adapter, and auth settings."""

    mcp: MCPServer[Any]
    store: MCPStoreProtocol
    auth_config: MCPAuthConfig
    allow_owner_fallback: bool
    transport: TransportMode
    mock: bool
    env: Mapping[str, str] | None = None
    stateless: bool = False

    def create_http_app(self, *, host: str = "127.0.0.1") -> ASGIApp:
        """Build the Starlette Streamable HTTP ASGI app wrapped in fail-closed Bearer auth."""
        starlette_app = self.mcp.streamable_http_app(host=host)
        return BearerAuthASGIMiddleware(
            starlette_app,
            expected_token=self.auth_config.auth_token,
        )


@contextmanager
def _protect_stdio_stream() -> Iterator[TextIO]:
    """Redirect Python sys.stdout and stdout logging handlers to sys.stderr during stdio MCP."""
    original_stdout = sys.stdout
    root_logger = logging.getLogger()
    saved_streams: list[tuple[logging.StreamHandler[Any], Any]] = []
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is original_stdout:
            saved_streams.append((handler, handler.stream))
            handler.setStream(sys.stderr)
    sys.stdout = sys.stderr
    try:
        yield original_stdout
    finally:
        sys.stdout = original_stdout
        for handler, prev_stream in saved_streams:
            handler.setStream(prev_stream)


def _register_healthz_route(mcp: MCPServer[Any]) -> None:
    async def healthz(request: Request) -> Response:
        del request
        return JSONResponse({"status": "ok"})

    mcp.custom_route("/healthz", methods=["GET"])(healthz)


def _filter_registered_tools(
    mcp: MCPServer[Any],
    enabled_capabilities: Iterable[str] | None,
    *,
    stateless: bool = False,
) -> None:
    allowed = capabilities.allowed_mcp_tools(enabled_capabilities)
    if stateless:
        allowed &= STATELESS_MCP_TOOLS
    if enabled_capabilities is None and not stateless:
        return
    for tool in list(mcp._tool_manager.list_tools()):
        if tool.name not in allowed:
            mcp.remove_tool(tool.name)


def list_tools_for_workspace(
    bundle: MacyMCPBundle,
    *,
    workspace_id: uuid.UUID,
) -> list[str]:
    """Return MCP tool names permitted for the target workspace's enabled capabilities."""
    getter = getattr(bundle.store, "get_enabled_capabilities", None)
    enabled = getter(workspace_id=workspace_id) if getter is not None else None
    allowed = capabilities.allowed_mcp_tools(enabled)
    return [t.name for t in bundle.mcp._tool_manager.list_tools() if t.name in allowed]


def _select_mcp_store(
    *,
    use_mock: bool,
    use_stateless: bool,
    source_env: Mapping[str, str],
) -> MCPStoreProtocol:
    if use_stateless:
        return cast(MCPStoreProtocol, StatelessStoreAdapter())
    if use_mock:
        print(
            "WARNING: Macy MCP running in non-production MOCK mode (acme-demo).",
            file=sys.stderr,
        )
        return MockStore.create_default(source_env)
    store = ServiceStoreAdapter()
    store.check_connectivity()
    return store


def create_mcp_server(  # noqa: PLR0913
    *,
    mock: bool = False,
    allow_owner_fallback: bool = False,
    transport: TransportMode = "stdio",
    env: Mapping[str, str] | None = None,
    enabled_capabilities: Iterable[str] | None = None,
    stateless: bool = False,
) -> MacyMCPBundle:
    """Build and validate the Macy MCP server bundle (no silent DB->mock fallback)."""
    source_env = os.environ if env is None else env
    use_stateless = stateless or source_env.get("MACY_STATELESS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    use_mock = False if use_stateless else validate_mock_policy(mock_flag=mock, env=source_env)
    auth_config = (
        validate_http_startup_config(source_env)
        if transport == "streamable-http"
        else MCPAuthConfig(
            auth_token=source_env.get("MCP_AUTH_TOKEN", "").strip(),
            pinned_actor_email=source_env.get("MCP_ACTOR_EMAIL", "").strip().lower() or None,
        )
    )
    store = _select_mcp_store(
        use_mock=use_mock,
        use_stateless=use_stateless,
        source_env=source_env,
    )
    mcp = MCPServer(
        name="macy",
        title="Macy Content Intelligence Engine",
        instructions=(
            "Macy editorial pipeline MCP server. All workspace operations enforce membership, "
            "spend caps, and human-in-the-loop Gate 1 and Gate 2 approvals."
        ),
        version=__version__,
    )
    _register_healthz_route(mcp)
    bundle = MacyMCPBundle(
        mcp=mcp,
        store=store,
        auth_config=auth_config,
        allow_owner_fallback=allow_owner_fallback,
        transport=transport,
        mock=use_mock,
        env=env,
        stateless=use_stateless,
    )
    register_research_tools(bundle)
    register_editorial_tools(bundle)
    _filter_registered_tools(mcp, enabled_capabilities, stateless=use_stateless)
    if not use_stateless:
        register_resources(bundle)
        register_prompts(bundle)
    return bundle


def run_stdio_server(
    bundle: MacyMCPBundle,
    *,
    protocol_stdout: TextIO | None = None,
) -> None:
    """Serve MCP over stdio while routing stray stdout writes and logs to stderr."""
    with _protect_stdio_stream() as captured_stdout:
        target_stdout = protocol_stdout if protocol_stdout is not None else captured_stdout
        prev_stdout = sys.stdout
        sys.stdout = target_stdout
        try:
            bundle.mcp.run(transport="stdio")
        finally:
            sys.stdout = prev_stdout


def run_streamable_http_server(
    bundle: MacyMCPBundle,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:  # pragma: no cover - uvicorn accept loop
    """Serve MCP over Streamable HTTP with fail-closed Bearer auth."""
    uvicorn.run(bundle.create_http_app(host=host), host=host, port=port, log_level="info")
