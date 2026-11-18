"""MCP tools for SEO briefing, drafting, claims verification, evaluation, Gate 2 & publish."""

from __future__ import annotations

import uuid
from typing import Any

from engine.mcp.auth import (
    BriefRequest,
    DraftDecisionRequest,
    EvaluateDraftRequest,
    GenerateDraftRequest,
    ImportDraftRequest,
    MCPBundleProtocol,
    PublishDraftRequest,
    VerifyClaimsRequest,
    authorize_bundle_workspace,
    parse_optional_uuid,
    parse_uuid,
    run_mcp_tool,
)


def _register_brief_and_draft_tools(bundle: MCPBundleProtocol) -> None:
    def macy_get_editorial_brief(  # noqa: PLR0913
        workspace_id: str,
        opportunity_id: str,
        force_regenerate: bool = False,
        offline: bool | None = None,
        format_slug: str = "blog",
        funnel_stage: str | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get or create an SEO-grounded, brand-pinned editorial brief for an approved topic."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.get_or_create_brief(
                BriefRequest(
                    workspace_id=ws_uuid,
                    opportunity_id=parse_uuid(opportunity_id, "opportunity_id"),
                    user_id=actor.user_id,
                    force_regenerate=force_regenerate,
                    offline=offline,
                    format_slug=format_slug,
                    funnel_stage=funnel_stage,
                )
            )

        return run_mcp_tool("macy_get_editorial_brief", _exec, workspace_id=workspace_id)

    def macy_generate_draft(
        workspace_id: str,
        brief_id: str,
        offline: bool | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate a source-grounded article draft and FK-anchored draft_claims rows."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.generate_draft(
                GenerateDraftRequest(
                    workspace_id=ws_uuid,
                    brief_id=parse_uuid(brief_id, "brief_id"),
                    user_id=actor.user_id,
                    offline=offline,
                )
            )

        return run_mcp_tool("macy_generate_draft", _exec, workspace_id=workspace_id)

    def macy_verify_claims(  # noqa: PLR0913
        workspace_id: str,
        draft_id: str | None = None,
        claims: list[str] | None = None,
        passages: list[str] | None = None,
        run_id: str | None = None,
        offline: bool | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify factual claims deterministically and via runtime judge without evals/."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.verify_claims(
                VerifyClaimsRequest(
                    workspace_id=ws_uuid,
                    user_id=actor.user_id,
                    draft_id=parse_optional_uuid(draft_id, "draft_id"),
                    claims=claims,
                    passages=passages,
                    run_id=parse_optional_uuid(run_id, "run_id"),
                    offline=offline,
                )
            )

        return run_mcp_tool("macy_verify_claims", _exec, workspace_id=workspace_id, run_id=run_id)

    for tool_fn in (macy_get_editorial_brief, macy_generate_draft, macy_verify_claims):
        bundle.mcp.tool()(tool_fn)


def _register_import_draft_tool(bundle: MCPBundleProtocol) -> None:
    def macy_import_draft(  # noqa: PLR0913
        workspace_id: str,
        body_md: str | None = None,
        file_path: str | None = None,
        brief_id: str | None = None,
        format_slug: str = "blog",
        topic: str | None = None,
        primary_keyword: str | None = None,
        deterministic_only: bool = True,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Import an external .md/.pdf or Markdown draft and run editorial compliance."""

        def _exec() -> dict[str, Any]:
            if file_path is not None and bundle.transport == "streamable-http":
                msg = (
                    "file_path is not allowed over streamable-http transport; pass body_md instead."
                )
                raise ValueError(msg)
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.import_draft(
                ImportDraftRequest(
                    workspace_id=ws_uuid,
                    user_id=actor.user_id,
                    file_path=file_path,
                    body_md=body_md,
                    brief_id=parse_optional_uuid(brief_id, "brief_id"),
                    format_slug=format_slug,
                    topic=topic,
                    primary_keyword=primary_keyword,
                    deterministic_only=deterministic_only,
                )
            )

        return run_mcp_tool("macy_import_draft", _exec, workspace_id=workspace_id)

    bundle.mcp.tool()(macy_import_draft)


def _register_gate2_and_publish_tools(bundle: MCPBundleProtocol) -> None:
    def macy_evaluate_draft(  # noqa: PLR0913
        workspace_id: str,
        draft_id: str = "stateless",
        offline: bool | None = None,
        deterministic_only: bool = False,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
        body_md: str | None = None,
        format_slug: str = "blog",
        primary_keyword: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate a draft with deterministic checks and isolated rubric scoring."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            resolved_draft_id = (
                parse_optional_uuid(None if draft_id == "stateless" else draft_id, "draft_id")
                or uuid.UUID(int=0)
                if getattr(bundle, "stateless", False)
                else parse_uuid(draft_id, "draft_id")
            )
            return bundle.store.evaluate_draft(
                EvaluateDraftRequest(
                    workspace_id=ws_uuid,
                    draft_id=resolved_draft_id,
                    user_id=actor.user_id,
                    offline=offline,
                    deterministic_only=deterministic_only,
                    body_md=body_md,
                    format_slug=format_slug,
                    primary_keyword=primary_keyword,
                )
            )

        return run_mcp_tool("macy_evaluate_draft", _exec, workspace_id=workspace_id)

    def macy_list_drafts_awaiting_approval(
        workspace_id: str,
        include_decided: bool = False,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """List Gate 2 evaluated drafts awaiting campaign creator decision."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            drafts = bundle.store.list_drafts_for_review(
                workspace_id=ws_uuid,
                user_id=actor.user_id,
                include_decided=include_decided,
            )
            return {"workspace_id": ws_uuid, "count": len(drafts), "drafts": drafts}

        return run_mcp_tool("macy_list_drafts_awaiting_approval", _exec, workspace_id=workspace_id)

    def macy_decide_draft(  # noqa: PLR0913
        workspace_id: str,
        draft_id: str,
        decision: str,
        note: str | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Record Gate 2 decision ('approved' or 'declined') by campaign creator owner."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.decide_draft(
                DraftDecisionRequest(
                    workspace_id=ws_uuid,
                    draft_id=parse_uuid(draft_id, "draft_id"),
                    decision=decision,
                    user_id=actor.user_id,
                    note=note,
                )
            )

        return run_mcp_tool("macy_decide_draft", _exec, workspace_id=workspace_id)

    def macy_publish_draft(  # noqa: PLR0913
        workspace_id: str,
        draft_id: str,
        mode: str | None = None,
        destination: str | None = None,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Publish an approved draft via PublisherPort (creator only; staging forces draft)."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.publish_draft(
                PublishDraftRequest(
                    workspace_id=ws_uuid,
                    draft_id=parse_uuid(draft_id, "draft_id"),
                    user_id=actor.user_id,
                    mode=mode,
                    destination=destination,
                    env=bundle.env,
                )
            )

        return run_mcp_tool("macy_publish_draft", _exec, workspace_id=workspace_id)

    for tool_fn in (
        macy_evaluate_draft,
        macy_list_drafts_awaiting_approval,
        macy_decide_draft,
        macy_publish_draft,
    ):
        bundle.mcp.tool()(tool_fn)


def _register_audit_tools(bundle: MCPBundleProtocol) -> None:
    def macy_audit_url(
        workspace_id: str,
        url: str,
        check_external: bool = False,
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Run single-URL SEO + GEO audit across 7 categories with A+..F grading."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            return bundle.store.audit_url(
                workspace_id=ws_uuid,
                user_id=actor.user_id,
                url=url,
                check_external=check_external,
            )

        return run_mcp_tool("macy_audit_url", _exec, workspace_id=workspace_id)

    def macy_crawl_site(  # noqa: PLR0913
        workspace_id: str,
        start_url: str,
        max_pages: int | None = None,
        plan: str = "free",
        actor_email: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Run bounded BFS site crawl respecting robots.txt and plan limits (20/50/200)."""

        def _exec() -> dict[str, Any]:
            ws_uuid, actor = authorize_bundle_workspace(
                bundle,
                workspace_id=workspace_id,
                actor_email=actor_email,
                actor_user_id=actor_user_id,
            )
            norm_plan: Any = "paid" if str(plan).strip().lower() == "paid" else "free"
            return bundle.store.crawl_site(
                workspace_id=ws_uuid,
                user_id=actor.user_id,
                start_url=start_url,
                max_pages=max_pages,
                plan=norm_plan,
            )

        return run_mcp_tool("macy_crawl_site", _exec, workspace_id=workspace_id)

    for tool_fn in (macy_audit_url, macy_crawl_site):
        bundle.mcp.tool()(tool_fn)


def register_editorial_tools(bundle: MCPBundleProtocol) -> None:
    """Register M5-05 editorial, runtime claim check, Gate 2, and publish MCP tools."""
    _register_brief_and_draft_tools(bundle)
    _register_gate2_and_publish_tools(bundle)
    _register_import_draft_tool(bundle)
    _register_audit_tools(bundle)
