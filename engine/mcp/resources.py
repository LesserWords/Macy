"""Tenant-scoped read-only MCP resources for brand, weekly report, and draft claims."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from engine.mcp.auth import (
    MCPBundleProtocol,
    authorize_bundle_workspace,
    jsonable,
    raise_sdk_mcp_error,
    resolve_draft_bundle_access,
)


def _read_workspace_json(
    bundle: MCPBundleProtocol,
    workspace_id: str,
    suffix: str,
    loader: Callable[[uuid.UUID, uuid.UUID], Any],
) -> str:
    uri = f"macy://workspaces/{workspace_id}/{suffix}"
    try:
        ws_uuid, actor = authorize_bundle_workspace(bundle, workspace_id=workspace_id)
        return json.dumps(jsonable(loader(ws_uuid, actor.user_id)), ensure_ascii=False)
    except Exception as exc:
        raise_sdk_mcp_error(exc, mcp_tool=f"resource:{suffix}", uri=uri, workspace_id=workspace_id)


def register_resources(bundle: MCPBundleProtocol) -> None:
    """Register tenant-scoped macy:// resources enforcing require_member."""

    def macy_workspace_brand(workspace_id: str) -> str:
        """Current BrandProfile rules, capabilities, prohibited phrases, and CTA rules."""
        return _read_workspace_json(
            bundle,
            workspace_id,
            "brand",
            lambda ws, uid: {
                "workspace_id": ws,
                "brand_profile": bundle.store.current_brand(workspace_id=ws, user_id=uid),
            },
        )

    def macy_workspace_weekly_report(workspace_id: str) -> str:
        """Latest weekly scored topics report and Gate 1/2 approval queue counts."""
        return _read_workspace_json(
            bundle,
            workspace_id,
            "weekly-report",
            lambda ws, uid: bundle.store.get_weekly_report(workspace_id=ws, user_id=uid),
        )

    def macy_draft_claims(draft_id: str) -> str:
        """Draft markdown, persisted FK draft_claims, quality report, and run cost."""
        uri = f"macy://drafts/{draft_id}/claims"
        try:
            ws_uuid, draft_uuid, actor = resolve_draft_bundle_access(bundle, draft_id=draft_id)
            review = bundle.store.get_draft_review_bundle(
                workspace_id=ws_uuid, draft_id=draft_uuid, user_id=actor.user_id
            )
            return json.dumps(jsonable(review), ensure_ascii=False)
        except Exception as exc:
            raise_sdk_mcp_error(exc, mcp_tool="resource:draft-claims", uri=uri)

    bundle.mcp.resource("macy://workspaces/{workspace_id}/brand", mime_type="application/json")(
        macy_workspace_brand
    )
    bundle.mcp.resource(
        "macy://workspaces/{workspace_id}/weekly-report", mime_type="application/json"
    )(macy_workspace_weekly_report)
    bundle.mcp.resource("macy://drafts/{draft_id}/claims", mime_type="application/json")(
        macy_draft_claims
    )
