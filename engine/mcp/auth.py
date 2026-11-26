"""Fail-closed authentication and caller identity for MCP transports (ADR-013, ADR-027)."""

from __future__ import annotations

import contextvars
import hmac
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, NoReturn, Protocol

from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError as SDKMCPError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from engine import capabilities
from engine.auth import FakeIdentityForbiddenError
from engine.mcp.errors import MCPError, MCPErrorCode, format_exception_error

NON_LOCAL_ENVS = frozenset({"staging", "production", "prod"})
_ACTIVE_MCP_TOOL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_ACTIVE_MCP_TOOL", default=None
)


@dataclass(frozen=True)
class ResearchRequest:
    """Typed parameters passed from MCP research tool to live/mock stores."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    query: str | None = None
    run_id: uuid.UUID | None = None
    window_days: int = 7
    offline: bool | None = None


@dataclass(frozen=True)
class BriefRequest:
    """Typed parameters for retrieving or creating a workspace editorial brief."""

    workspace_id: uuid.UUID
    opportunity_id: uuid.UUID
    user_id: uuid.UUID
    force_regenerate: bool = False
    offline: bool | None = None
    format_slug: str = "blog"
    funnel_stage: str | None = None


@dataclass(frozen=True)
class GenerateDraftRequest:
    """Typed parameters for generating an FK-grounded article draft."""

    workspace_id: uuid.UUID
    brief_id: uuid.UUID
    user_id: uuid.UUID
    offline: bool | None = None


@dataclass(frozen=True)
class VerifyClaimsRequest:
    """Typed parameters for runtime claim verification over draft or explicit passages."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    draft_id: uuid.UUID | None = None
    claims: Sequence[str] | None = None
    passages: Sequence[str] | None = None
    run_id: uuid.UUID | None = None
    offline: bool | None = None


@dataclass(frozen=True)
class EvaluateDraftRequest:
    """Typed parameters for deterministic + rubric draft quality evaluation."""

    workspace_id: uuid.UUID
    draft_id: uuid.UUID
    user_id: uuid.UUID
    offline: bool | None = None
    deterministic_only: bool = False
    body_md: str | None = None
    format_slug: str = "blog"
    primary_keyword: str | None = None


@dataclass(frozen=True)
class ImportDraftRequest:
    """Typed parameters for importing an external .md/.pdf or Markdown draft."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    file_path: str | None = None
    body_md: str | None = None
    brief_id: uuid.UUID | None = None
    format_slug: str = "blog"
    topic: str | None = None
    primary_keyword: str | None = None
    deterministic_only: bool = True
    offline: bool = False


@dataclass(frozen=True)
class AdaptDraftRequest:
    """Typed parameters for adapting a pass-by-value draft to a target brand/persona."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    file_path: str | None = None
    body_md: str | None = None
    brand_fixture: str | None = None
    industry_pack: str | None = None
    format_slug: str = "blog"
    primary_keyword: str | None = None
    locale: str | None = None
    max_similarity: float = 0.65
    deterministic_only: bool = True
    offline: bool = False
    model: str | None = None


@dataclass(frozen=True)
class IngestResearchRequest:
    """Typed parameters for external agent research document ingestion."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    topic: str
    documents: Sequence[dict[str, Any]]
    run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DraftDecisionRequest:
    """Typed parameters for Gate 2 creator draft decision (approved/declined)."""

    workspace_id: uuid.UUID
    draft_id: uuid.UUID
    decision: str
    user_id: uuid.UUID
    note: str | None = None


@dataclass(frozen=True)
class PublishDraftRequest:
    """Typed parameters for publishing an approved draft via PublisherPort."""

    workspace_id: uuid.UUID
    draft_id: uuid.UUID
    user_id: uuid.UUID
    mode: str | None = None
    destination: str | None = None
    env: Mapping[str, str] | None = None


class ActorLookupProtocol(Protocol):
    """Read/membership boundary shared by live services and the dev mock store."""

    def get_user_by_email(self, email: str) -> dict[str, Any] | None: ...

    def get_user_by_id(self, user_id: uuid.UUID) -> dict[str, Any] | None: ...

    def get_workspace_owner_identity(self, *, workspace_id: uuid.UUID) -> dict[str, Any] | None: ...

    def require_member(self, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> str: ...


class MCPStoreProtocol(ActorLookupProtocol, Protocol):
    """Unified store contract shared by ServiceStoreAdapter and MockStore across M5-04/M5-05."""

    def list_accessible_workspaces(self, *, user_id: uuid.UUID) -> list[dict[str, Any]]: ...

    def get_workspace_status(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Any]: ...

    def run_research(self, request: ResearchRequest) -> dict[str, Any]: ...

    def ingest_research(self, request: IngestResearchRequest) -> dict[str, Any]: ...

    def score_topics(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        offline: bool | None = None,
    ) -> dict[str, Any]: ...

    def list_topics_for_approval(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    def decide_topic(  # noqa: PLR0913
        self,
        *,
        workspace_id: uuid.UUID,
        opportunity_id: uuid.UUID,
        decision: str,
        user_id: uuid.UUID,
        note: str | None = None,
    ) -> dict[str, Any]: ...

    def get_or_create_brief(self, request: BriefRequest) -> dict[str, Any]: ...

    def generate_draft(self, request: GenerateDraftRequest) -> dict[str, Any]: ...

    def verify_claims(self, request: VerifyClaimsRequest) -> dict[str, Any]: ...

    def evaluate_draft(self, request: EvaluateDraftRequest) -> dict[str, Any]: ...

    def import_draft(self, request: ImportDraftRequest) -> dict[str, Any]: ...

    def adapt_draft(self, request: AdaptDraftRequest) -> dict[str, Any]: ...

    def list_drafts_for_review(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        include_decided: bool = False,
    ) -> list[dict[str, Any]]: ...

    def decide_draft(self, request: DraftDecisionRequest) -> dict[str, Any]: ...

    def publish_draft(self, request: PublishDraftRequest) -> dict[str, Any]: ...

    def current_brand(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Any] | None: ...

    def get_weekly_report(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Any]: ...

    def get_draft_workspace_id(self, *, draft_id: uuid.UUID) -> uuid.UUID | None: ...

    def get_draft_review_bundle(
        self,
        *,
        workspace_id: uuid.UUID,
        draft_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]: ...

    def audit_url(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        url: str,
        check_external: bool = False,
    ) -> dict[str, Any]: ...

    def crawl_site(  # noqa: PLR0913
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        start_url: str,
        max_pages: int | None = None,
        plan: Literal["free", "paid"] = "free",
    ) -> dict[str, Any]: ...


class MCPBundleProtocol(Protocol):
    """Minimal bundle interface required to register and execute MCP handlers."""

    mcp: MCPServer[Any]
    allow_owner_fallback: bool
    transport: Literal["stdio", "streamable-http"]
    env: Mapping[str, str] | None
    stateless: bool

    @property
    def store(self) -> MCPStoreProtocol: ...


@dataclass(frozen=True)
class MCPAuthConfig:
    """Validated transport authentication configuration."""

    auth_token: str
    pinned_actor_email: str | None


@dataclass(frozen=True)
class ResolvedActor:
    """Authenticated caller identity bound to an MCP request."""

    user_id: uuid.UUID
    email: str
    source: str


def is_non_local_environment(env: Mapping[str, str] | None = None) -> bool:
    """Return True when active APP_ENV or ENV is staging, production, or prod."""
    source = os.environ if env is None else env
    raw = source.get("APP_ENV") or source.get("ENV") or "development"
    return raw.strip().lower() in NON_LOCAL_ENVS


def is_staging_environment(env: Mapping[str, str] | None = None) -> bool:
    """Return True when active APP_ENV or ENV is staging."""
    source = os.environ if env is None else env
    raw = source.get("APP_ENV") or source.get("ENV") or ""
    return raw.strip().lower() == "staging"


def _is_placeholder_secret(value: str) -> bool:
    clean = value.strip().lower()
    return not clean or "change_me" in clean or "placeholder" in clean


def verify_bearer_token(auth_header: str | None, expected_token: str | None) -> bool:
    """Verify HTTP Authorization Bearer header in constant time (fail-closed)."""
    if not expected_token or _is_placeholder_secret(expected_token):
        return False
    if not auth_header:
        return False
    scheme, sep, provided = auth_header.strip().partition(" ")
    if not sep or scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(
        provided.lstrip().encode("utf-8"),
        expected_token.strip().encode("utf-8"),
    )


def validate_http_startup_config(env: Mapping[str, str] | None = None) -> MCPAuthConfig:
    """Validate MCP_AUTH_TOKEN and MCP_ACTOR_EMAIL before binding streamable-http."""
    source = os.environ if env is None else env
    token = source.get("MCP_AUTH_TOKEN", "").strip()
    actor_email = source.get("MCP_ACTOR_EMAIL", "").strip().lower()

    if is_non_local_environment(source):
        if _is_placeholder_secret(token):
            msg = "MCP_AUTH_TOKEN must be configured (non-placeholder) in staging or production"
            raise MCPError(MCPErrorCode.AUTH_REQUIRED, msg)
        if _is_placeholder_secret(actor_email) or "@" not in actor_email:
            msg = "MCP_ACTOR_EMAIL must be configured in staging or production"
            raise MCPError(MCPErrorCode.AUTH_REQUIRED, msg)

    return MCPAuthConfig(auth_token=token, pinned_actor_email=actor_email or None)


def _actor_from_user_row(user: dict[str, Any] | None, *, source: str, label: str) -> ResolvedActor:
    if user is None:
        msg = f"Authenticated user not found for {label}"
        raise MCPError(MCPErrorCode.AUTH_REQUIRED, msg)
    return ResolvedActor(
        user_id=uuid.UUID(str(user["user_id"])),
        email=str(user["email"]).strip().lower(),
        source=source,
    )


def resolve_http_actor(
    lookup: ActorLookupProtocol,
    *,
    env: Mapping[str, str] | None = None,
    tool_actor_email: str | None = None,
    tool_actor_user_id: str | uuid.UUID | None = None,
) -> ResolvedActor:
    """Resolve pinned MCP_ACTOR_EMAIL identity and reject conflicting tool overrides."""
    source = os.environ if env is None else env
    pinned_email = source.get("MCP_ACTOR_EMAIL", "").strip().lower()
    if not pinned_email or "@" not in pinned_email:
        msg = "MCP_ACTOR_EMAIL is required for authenticated streamable-http calls"
        raise MCPError(MCPErrorCode.AUTH_REQUIRED, msg)

    resolved = _actor_from_user_row(
        lookup.get_user_by_email(pinned_email),
        source="pinned_http",
        label=pinned_email,
    )
    if tool_actor_email and tool_actor_email.strip().lower() != resolved.email:
        msg = "Caller cannot override pinned MCP_ACTOR_EMAIL identity via tool arguments"
        raise MCPError(MCPErrorCode.PERMISSION_DENIED, msg)
    if tool_actor_user_id is not None and uuid.UUID(str(tool_actor_user_id)) != resolved.user_id:
        msg = "Caller cannot override pinned MCP_ACTOR_EMAIL user_id via tool arguments"
        raise MCPError(MCPErrorCode.PERMISSION_DENIED, msg)
    return resolved


def _resolve_explicit_stdio_actor(
    lookup: ActorLookupProtocol,
    *,
    actor_email: str | None,
    actor_user_id: str | uuid.UUID | None,
) -> ResolvedActor | None:
    if actor_email:
        resolved = _actor_from_user_row(
            lookup.get_user_by_email(actor_email.strip().lower()),
            source="arg_email",
            label="actor_email",
        )
        if actor_user_id is not None and uuid.UUID(str(actor_user_id)) != resolved.user_id:
            msg = "actor_user_id does not match actor_email"
            raise MCPError(MCPErrorCode.PERMISSION_DENIED, msg)
        return resolved

    if actor_user_id is not None:
        uid = uuid.UUID(str(actor_user_id))
        return _actor_from_user_row(
            lookup.get_user_by_id(uid),
            source="arg_user_id",
            label="actor_user_id",
        )
    return None


def _resolve_env_stdio_actor(
    lookup: ActorLookupProtocol,
    source_env: Mapping[str, str],
) -> ResolvedActor | None:
    env_email = source_env.get("MACY_USER_EMAIL", "").strip().lower()
    if env_email:
        return _actor_from_user_row(
            lookup.get_user_by_email(env_email),
            source="env_user_email",
            label="MACY_USER_EMAIL",
        )

    env_uid = source_env.get("MACY_ACTOR_USER_ID", "").strip()
    if env_uid:
        return _actor_from_user_row(
            lookup.get_user_by_id(uuid.UUID(env_uid)),
            source="env_actor_user_id",
            label="MACY_ACTOR_USER_ID",
        )

    test_email = source_env.get("MACY_TEST_USER_EMAIL", "").strip().lower()
    if test_email:
        if is_non_local_environment(source_env):
            msg = "Fake identity MACY_TEST_USER_EMAIL is forbidden in staging or production"
            raise FakeIdentityForbiddenError(msg)
        return _actor_from_user_row(
            lookup.get_user_by_email(test_email),
            source="env_test_email",
            label="MACY_TEST_USER_EMAIL",
        )
    return None


def resolve_stdio_actor(  # noqa: PLR0913
    lookup: ActorLookupProtocol,
    *,
    env: Mapping[str, str] | None = None,
    workspace_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    actor_user_id: str | uuid.UUID | None = None,
    allow_owner_fallback: bool = False,
) -> ResolvedActor:
    """Resolve local stdio caller identity in priority order without silent prod escalation."""
    source_env = os.environ if env is None else env
    explicit = _resolve_explicit_stdio_actor(
        lookup, actor_email=actor_email, actor_user_id=actor_user_id
    )
    if explicit is not None:
        return explicit

    from_env = _resolve_env_stdio_actor(lookup, source_env)
    if from_env is not None:
        return from_env

    if (
        allow_owner_fallback
        and workspace_id is not None
        and not is_non_local_environment(source_env)
    ):
        owner = lookup.get_workspace_owner_identity(workspace_id=workspace_id)
        if owner is not None:
            return ResolvedActor(
                user_id=uuid.UUID(str(owner["user_id"])),
                email=str(owner["email"]).strip().lower(),
                source="owner_fallback",
            )

    msg = "No authenticated MCP caller identity resolved"
    raise MCPError(MCPErrorCode.AUTH_REQUIRED, msg)


def authorize_workspace_access(
    lookup: ActorLookupProtocol,
    *,
    actor: ResolvedActor,
    workspace_id: uuid.UUID,
) -> str:
    """Verify actor holds membership in workspace_id, raising PERMISSION_DENIED otherwise."""
    try:
        return lookup.require_member(workspace_id=workspace_id, user_id=actor.user_id)
    except PermissionError as exc:
        raise MCPError(MCPErrorCode.PERMISSION_DENIED, str(exc)) from exc


def jsonable(value: Any) -> Any:
    """Recursively normalize UUID, Decimal, and date/datetime values for JSON payloads."""
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    return value


def parse_uuid(value: str, label: str) -> uuid.UUID:
    """Parse a UUID string, raising INVALID_STATE on malformed input."""
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError) as exc:
        msg = f"Invalid UUID for {label}: '{value}'"
        raise MCPError(MCPErrorCode.INVALID_STATE, msg) from exc


def parse_optional_uuid(value: str | None, label: str) -> uuid.UUID | None:
    """Parse an optional UUID string when non-None."""
    return None if value is None else parse_uuid(value, label)


def resolve_bundle_actor(
    bundle: MCPBundleProtocol,
    *,
    workspace_id: uuid.UUID | None,
    actor_email: str | None,
    actor_user_id: str | None,
) -> ResolvedActor:
    """Resolve authenticated actor for the bundle's active MCP transport."""
    if bundle.transport == "streamable-http":
        return resolve_http_actor(
            bundle.store,
            env=bundle.env,
            tool_actor_email=actor_email,
            tool_actor_user_id=actor_user_id,
        )
    return resolve_stdio_actor(
        bundle.store,
        env=bundle.env,
        workspace_id=workspace_id,
        actor_email=actor_email,
        actor_user_id=actor_user_id,
        allow_owner_fallback=bundle.allow_owner_fallback if workspace_id is not None else False,
    )


def authorize_bundle_workspace(
    bundle: MCPBundleProtocol,
    *,
    workspace_id: str,
    actor_email: str | None = None,
    actor_user_id: str | None = None,
) -> tuple[uuid.UUID, ResolvedActor]:
    """Parse workspace_id, authenticate actor, and enforce workspace membership."""
    ws_uuid = parse_uuid(workspace_id, "workspace_id")
    if getattr(bundle, "stateless", False):
        actor = ResolvedActor(
            user_id=uuid.UUID(int=0),
            email="stateless@local",
            source="stateless",
        )
    else:
        actor = resolve_bundle_actor(
            bundle,
            workspace_id=ws_uuid,
            actor_email=actor_email,
            actor_user_id=actor_user_id,
        )
        authorize_workspace_access(bundle.store, actor=actor, workspace_id=ws_uuid)
    active_tool = _ACTIVE_MCP_TOOL.get()
    if active_tool is not None:
        cap_slug = capabilities.capability_for_mcp_tool(active_tool)
        if cap_slug is not None:
            getter = getattr(bundle.store, "get_enabled_capabilities", None)
            if getter is not None:
                enabled = getter(workspace_id=ws_uuid)
                if cap_slug not in enabled:
                    msg = f"Capability '{cap_slug}' is disabled for workspace {ws_uuid}"
                    raise MCPError(MCPErrorCode.CAPABILITY_DISABLED, msg)
    return ws_uuid, actor


def resolve_draft_bundle_access(
    bundle: MCPBundleProtocol,
    *,
    draft_id: str,
    expected_workspace_id: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, ResolvedActor]:
    """Resolve draft owning workspace and enforce tenant membership fail-closed."""
    draft_uuid = parse_uuid(draft_id, "draft_id")
    expected_ws = parse_optional_uuid(expected_workspace_id, "workspace_id")
    actor = resolve_bundle_actor(
        bundle, workspace_id=expected_ws, actor_email=None, actor_user_id=None
    )
    if expected_ws is not None:
        authorize_workspace_access(bundle.store, actor=actor, workspace_id=expected_ws)
    actual_ws = bundle.store.get_draft_workspace_id(draft_id=draft_uuid)
    if actual_ws is None or (expected_ws is not None and actual_ws != expected_ws):
        msg = f"Draft {draft_uuid} not found"
        raise MCPError(MCPErrorCode.NOT_FOUND, msg)
    authorize_workspace_access(bundle.store, actor=actor, workspace_id=actual_ws)
    return actual_ws, draft_uuid, actor


def run_mcp_tool(
    tool_name: str,
    fn: Callable[[], dict[str, Any]],
    *,
    workspace_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute an MCP tool body, normalizing JSON output and scrubbing/capturing errors."""
    token = _ACTIVE_MCP_TOOL.set(tool_name)
    try:
        payload: dict[str, Any] = jsonable(fn())
    except Exception as exc:
        return format_exception_error(
            exc,
            workspace_id=workspace_id,
            run_id=run_id,
            mcp_tool=tool_name,
        )
    finally:
        _ACTIVE_MCP_TOOL.reset(token)
    return {"ok": True, **payload}


def raise_sdk_mcp_error(
    exc: Exception,
    *,
    mcp_tool: str,
    uri: str | None = None,
    workspace_id: str | None = None,
) -> NoReturn:
    """Convert internal exceptions to scrubbed MCP protocol errors for resources and prompts."""
    err = format_exception_error(exc, workspace_id=workspace_id, mcp_tool=mcp_tool)
    rpc_code = INVALID_PARAMS if err["error"] == MCPErrorCode.NOT_FOUND.value else INTERNAL_ERROR
    data = {**err, "uri": uri} if uri is not None else dict(err)
    msg = f"{err['error']}: {err['message']}"
    raise SDKMCPError(code=rpc_code, message=msg, data=data) from exc
