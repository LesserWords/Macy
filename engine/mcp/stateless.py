"""Stateless MCP store adapter for Path B zero-DB audit and compliance tools (ADR-029)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from engine import capabilities, services
from engine.contracts import CrawlConfig
from engine.mcp.auth import AdaptDraftRequest, EvaluateDraftRequest, ImportDraftRequest
from engine.mcp.errors import MCPError, MCPErrorCode

STATELESS_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "macy_audit_url",
        "macy_crawl_site",
        "macy_import_draft",
        "macy_adapt_draft",
        "macy_evaluate_draft",
    }
)


class StatelessStoreAdapter:
    """In-memory, zero-DB adapter serving deterministic compliance and SEO/GEO audits."""

    def get_enabled_capabilities(self, *, workspace_id: uuid.UUID) -> list[str]:
        del workspace_id
        return capabilities.normalize_enabled_capabilities(["core-compliance", "seo-audit"])

    def audit_url(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        url: str,
        check_external: bool = False,
    ) -> dict[str, Any]:
        del workspace_id, user_id
        return services.run_url_audit(url=url, check_external=check_external)

    def crawl_site(self, **params: Any) -> dict[str, Any]:
        """Run bounded BFS site crawl in stateless mode without DB writes."""
        plan: Literal["free", "paid"] = "paid" if params.get("plan") == "paid" else "free"
        crawl_cfg = CrawlConfig(plan=plan, max_pages=params.get("max_pages"))
        return services.run_site_crawl(
            start_url=str(params["start_url"]),
            config=crawl_cfg,
        )

    def import_draft(self, request: ImportDraftRequest) -> dict[str, Any]:
        return services.check_draft_stateless(
            file_path=request.file_path,
            markdown_text=request.body_md,
            format_slug=request.format_slug,
            topic=request.topic,
            primary_keyword=request.primary_keyword,
        )

    def adapt_draft(self, request: AdaptDraftRequest) -> dict[str, Any]:
        return services.adapt_draft_stateless(
            file_path=request.file_path,
            markdown_text=request.body_md,
            brand_fixture=request.brand_fixture,
            industry_pack=request.industry_pack,
            format_slug=request.format_slug,
            primary_keyword=request.primary_keyword,
            locale=request.locale or "pt-BR",
            max_similarity=request.max_similarity,
            deterministic_only=request.deterministic_only,
            offline=request.offline,
            model=request.model,
        )

    def evaluate_draft(self, request: EvaluateDraftRequest) -> dict[str, Any]:
        if request.body_md is None:
            msg = "body_md is required when evaluating drafts in stateless MCP mode"
            raise MCPError(MCPErrorCode.INVALID_STATE, msg)
        return services.check_draft_stateless(
            markdown_text=request.body_md,
            format_slug=request.format_slug,
            primary_keyword=request.primary_keyword,
        )
