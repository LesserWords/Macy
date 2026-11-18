"""Tenant-scoped MCP prompts for structured Gate 2 editorial review."""

from __future__ import annotations

from typing import Any

from engine.mcp.auth import MCPBundleProtocol, raise_sdk_mcp_error, resolve_draft_bundle_access


def _format_review_prompt(review: dict[str, Any]) -> str:
    draft = review["draft"]
    ws_id = draft["workspace_id"]
    draft_id = draft["id"]
    payload = draft.get("payload") or {}
    report = review.get("quality_report") or {}
    claims = review.get("claims") or []
    return "\n".join(
        [
            f"# Editorial Review — Draft {draft_id}",
            f"- Title: {payload.get('title') or 'Untitled'}",
            f"- Status: {draft.get('status')} (revision {draft.get('revision')})",
            f"- Evaluation: verdict={report.get('verdict')} score={report.get('score')}",
            f"- Persisted Claims: {len(claims)}",
            f"- Brand Resource: macy://workspaces/{ws_id}/brand",
            f"- Claims Resource: macy://drafts/{draft_id}/claims",
            f"- Weekly Report: macy://workspaces/{ws_id}/weekly-report",
            "",
            "Review brand voice alignment, numeric claim grounding, and rubric findings, "
            "then call `macy_decide_draft` with decision `'approved'` or `'declined'`.",
        ]
    )


def register_prompts(bundle: MCPBundleProtocol) -> None:
    """Register tenant-scoped Gate 2 editorial review prompt template."""

    def macy_editorial_review(workspace_id: str, draft_id: str) -> str:
        """Render a structured editorial review checklist for a workspace draft."""
        try:
            ws_uuid, draft_uuid, actor = resolve_draft_bundle_access(
                bundle, draft_id=draft_id, expected_workspace_id=workspace_id
            )
            review = bundle.store.get_draft_review_bundle(
                workspace_id=ws_uuid, draft_id=draft_uuid, user_id=actor.user_id
            )
        except Exception as exc:
            raise_sdk_mcp_error(
                exc, mcp_tool="prompt:macy_editorial_review", workspace_id=workspace_id
            )
        return _format_review_prompt(review)

    bundle.mcp.prompt()(macy_editorial_review)
