"""MCP tools for workspace discovery, research, scoring, and Gate 1 decisions (ADR-027)."""

from __future__ import annotations

from typing import Any

from engine.mcp.auth import (
    IngestResearchRequest,
    MCPBundleProtocol,
    ResearchRequest,
    authorize_bundle_workspace,
    parse_optional_uuid,
    parse_uuid,
    resolve_bundle_actor,
    run_mcp_tool,
)


def _register_discovery_tools(bundle: MCPBundleProtocol) -> None:
    def macy_list_workspaces(
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """List workspaces where the authenticated actor holds active membership."""

        def _exec() -> dict[str, Any]:
            actor = resolve_bundle_actor(
                bundle,
                workspace_id=None,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            workspaces = bundle.store.list_accessible_workspaces(user_id=actor.user_id)
            return {"count": len(workspaces), "workspaces": workspaces}

        return run_mcp_tool("macy_list_workspaces", _exec)

    def macy_get_workspace_status(
        workspace_id: str,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get workspace budget spend vs caps, source counts, integrations, and Gate 1/2 queues."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.get_workspace_status(workspace_id=ws_uuid, user_id=actor.user_id)

        return run_mcp_tool("macy_get_workspace_status", _exec, workspace_id=workspace_id)

    for tool_fn in (macy_list_workspaces, macy_get_workspace_status):
        bundle.mcp.tool()(tool_fn)


def _register_ingest_tool(bundle: MCPBundleProtocol) -> None:
    def macy_ingest_research_documents(  # noqa: PLR0913
        workspace_id: str,
        topic: str,
        documents: list[dict[str, Any]],
        run_id: str | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest external agent-gathered research documents and facts into workspace."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.ingest_research(
                IngestResearchRequest(
                    workspace_id=ws_uuid,
                    user_id=actor.user_id,
                    topic=topic,
                    documents=documents,
                    run_id=parse_optional_uuid(run_id, "run_id"),
                )
            )

        return run_mcp_tool(
            "macy_ingest_research_documents", _exec, workspace_id=workspace_id, run_id=run_id
        )

    bundle.mcp.tool()(macy_ingest_research_documents)


def _register_gate1_tools(bundle: MCPBundleProtocol) -> None:
    def macy_research_topic(  # noqa: PLR0913
        workspace_id: str,
        topic: str | None = None,
        query: str | None = None,
        run_id: str | None = None,
        window_days: int = 7,
        offline: bool | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Collect grounded research documents with canonical URLs, dates, and extracted text."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.run_research(
                ResearchRequest(
                    workspace_id=ws_uuid,
                    user_id=actor.user_id,
                    query=topic if topic is not None else query,
                    run_id=parse_optional_uuid(run_id, "run_id"),
                    window_days=window_days,
                    offline=offline,
                )
            )

        return run_mcp_tool("macy_research_topic", _exec, workspace_id=workspace_id, run_id=run_id)

    def macy_score_topics(
        workspace_id: str,
        run_id: str | None = None,
        offline: bool | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Score verified research records using weighted rubric sub-scores and totals."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            scored = bundle.store.score_topics(
                workspace_id=ws_uuid,
                user_id=actor.user_id,
                run_id=parse_optional_uuid(run_id, "run_id"),
                offline=offline,
            )
            return {
                "run_id": scored["run_id"],
                "workspace_id": scored["workspace_id"],
                "campaign_id": scored["campaign_id"],
                "campaign_name": scored["campaign_name"],
                "articles_per_week": scored["articles_per_week"],
                "recommended_count": scored["recommended_count"],
                "total_count": scored["total_count"],
                "topics": scored["items"],
            }

        return run_mcp_tool("macy_score_topics", _exec, workspace_id=workspace_id, run_id=run_id)

    def macy_list_topics_awaiting_approval(
        workspace_id: str,
        run_id: str | None = None,
        limit: int = 50,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """List scored Gate 1 topics awaiting creator approval for a workspace."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            topics = bundle.store.list_topics_for_approval(
                workspace_id=ws_uuid,
                user_id=actor.user_id,
                run_id=parse_optional_uuid(run_id, "run_id"),
                limit=limit,
            )
            return {"workspace_id": ws_uuid, "count": len(topics), "topics": topics}

        return run_mcp_tool(
            "macy_list_topics_awaiting_approval", _exec, workspace_id=workspace_id, run_id=run_id
        )

    def macy_decide_topic(  # noqa: PLR0913
        workspace_id: str,
        opportunity_id: str,
        decision: str,
        note: str | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Approve or reject a scored topic at Gate 1 (campaign creator owner only)."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.decide_topic(
                workspace_id=ws_uuid,
                opportunity_id=parse_uuid(opportunity_id, "opportunity_id"),
                decision=decision,
                user_id=actor.user_id,
                note=note,
            )

        return run_mcp_tool("macy_decide_topic", _exec, workspace_id=workspace_id)

    for tool_fn in (
        macy_research_topic,
        macy_score_topics,
        macy_list_topics_awaiting_approval,
        macy_decide_topic,
    ):
        bundle.mcp.tool()(tool_fn)


def register_research_tools(bundle: MCPBundleProtocol) -> None:
    """Register M5-04 workspace discovery, research, scoring, and Gate 1 MCP tools."""
    _register_discovery_tools(bundle)
    _register_gate1_tools(bundle)
    _register_ingest_tool(bundle)
