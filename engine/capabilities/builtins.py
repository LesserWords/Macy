"""Built-in capability plugin definitions (ADR-029, F22)."""

from __future__ import annotations

from engine.capabilities.ports import CapabilityManifest


class CoreComplianceCapability:
    """Always-on deterministic compliance, JSON-LD schema, and workspace basics."""

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            slug="core-compliance",
            title_msgid="Content Compliance & JSON-LD",
            always_enabled=True,
            required_ports=(),
            optional_ports=(),
            ui_screens=("dashboard", "brand_profile", "integrations", "webhooks"),
            cli_commands=(
                "import-draft",
                "adapt-draft",
                "capabilities",
                "plugins",
                "integrations",
                "create-workspace",
                "add-owner",
                "seed-brand",
                "rotate-key",
                "test-sentry",
                "purge",
                "anonymise-user",
                "mcp",
                "init",
            ),
            mcp_tools=(
                "macy_list_workspaces",
                "macy_get_workspace_status",
                "macy_import_draft",
                "macy_adapt_draft",
                "macy_evaluate_draft",
            ),
        )


class SeoAuditCapability:
    """CMS-agnostic 7-category SEO + GEO URL audit and bounded site crawler."""

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            slug="seo-audit",
            title_msgid="SEO & GEO Audit",
            always_enabled=False,
            required_ports=(),
            optional_ports=("browser_audit", "pagespeed"),
            ui_screens=("audit",),
            cli_commands=("audit",),
            mcp_tools=("macy_audit_url", "macy_crawl_site"),
        )


class EditorialPipelineCapability:
    """Human-gated sourced research, scoring, briefing, writing, and publishing."""

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            slug="editorial-pipeline",
            title_msgid="Editorial Pipeline",
            always_enabled=False,
            required_ports=("llm", "feed_source", "extractor"),
            optional_ports=("news_search", "keyword_provider", "publisher"),
            ui_screens=("report", "draft_review", "campaigns", "sources", "runs"),
            cli_commands=(
                "run-week",
                "run-now",
                "run-stage",
                "crontab",
                "seed-sources",
                "verify-sources",
                "import-sitemap",
                "webhooks-deliver",
            ),
            mcp_tools=(
                "macy_ingest_research_documents",
                "macy_research_topic",
                "macy_score_topics",
                "macy_list_topics_awaiting_approval",
                "macy_decide_topic",
                "macy_get_editorial_brief",
                "macy_generate_draft",
                "macy_verify_claims",
                "macy_list_drafts_awaiting_approval",
                "macy_decide_draft",
                "macy_publish_draft",
            ),
        )


class RouteMonitorsCapability:
    """Scheduled URL/TLS regression monitors, hybrid GSC insights, and SERP rank tracking."""

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            slug="route-monitors",
            title_msgid="Route Monitors & GSC",
            always_enabled=False,
            required_ports=(),
            optional_ports=("analytics_source", "rank_tracker"),
            ui_screens=(),
            cli_commands=(),
            mcp_tools=(),
        )
