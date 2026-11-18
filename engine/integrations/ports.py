"""Protocol definitions for the 7 external integration ports (ADR-005, PRD §5.6)."""

from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from engine.contracts import (
    AuditCheckResult,
    KeywordData,
    PublishedContent,
    PublishRequest,
    ResearchRecord,
)


@runtime_checkable
class LLMPort(Protocol):
    """LLM gateway port for structured completions and evaluations."""

    def healthcheck(self) -> None:
        """Verify connectivity to LLM provider."""
        ...

    def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
        """Execute a prompt and return structured response dictionary."""
        ...


@runtime_checkable
class FeedSourcePort(Protocol):
    """Feed polling port for RSS/Atom and site feeds."""

    def healthcheck(self) -> None:
        """Verify feed source availability."""
        ...

    def fetch_feed(self, url: str, since: datetime | None = None) -> list[ResearchRecord]:
        """Fetch and parse feed items into ResearchRecord contracts."""
        ...


@runtime_checkable
class NewsSearchPort(Protocol):
    """Search port for querying recent news articles."""

    def healthcheck(self) -> None:
        """Verify search API connectivity."""
        ...

    def search(
        self,
        query: str,
        locale: str,
        market: str,
        since: date | None = None,
    ) -> list[ResearchRecord]:
        """Execute news search query and return research records."""
        ...


@runtime_checkable
class ExtractorPort(Protocol):
    """Text extraction port from web pages and HTML."""

    def healthcheck(self) -> None:
        """Verify extractor readiness."""
        ...

    def extract(self, url: str, html: str | None = None) -> ResearchRecord:
        """Extract clean text, metadata and facts from a URL or HTML document."""
        ...


@runtime_checkable
class KeywordProviderPort(Protocol):
    """SEO metrics port for search volume, CPC, and intent."""

    def healthcheck(self) -> None:
        """Verify keyword provider connectivity."""
        ...

    def lookup(
        self,
        terms: list[str],
        locale: str,
        market: str,
        **kwargs: Any,
    ) -> list[KeywordData]:
        """Retrieve keyword volume and competition metrics."""
        ...


@runtime_checkable
class PublisherPort(Protocol):
    """CMS and export publishing port."""

    def healthcheck(self) -> None:
        """Verify publishing destination connectivity."""
        ...

    def publish(self, request: PublishRequest) -> PublishedContent:
        """Publish an approved draft to destination and return published content details."""
        ...


@runtime_checkable
class AnalyticsSourcePort(Protocol):
    """Analytics port for content performance metrics."""

    def healthcheck(self) -> None:
        """Verify analytics source connectivity."""
        ...

    def get_metrics(self, external_id: str, since: date) -> dict[str, Any]:
        """Fetch article traffic and engagement metrics."""
        ...


@runtime_checkable
class BrowserAuditPort(Protocol):
    """Optional headless browser accessibility audit port (e.g. Playwright + axe-core)."""

    def healthcheck(self) -> None:
        """Verify headless browser and axe-core dependencies are installed."""
        ...

    def audit_accessibility(self, url: str) -> list[AuditCheckResult]:
        """Run accessibility audit rules on a live URL and return usability checks."""
        ...


@runtime_checkable
class PageSpeedPort(Protocol):
    """Optional Google PageSpeed Insights / Core Web Vitals port."""

    def healthcheck(self) -> None:
        """Verify PageSpeed API credentials and availability."""
        ...

    def fetch_metrics(self, url: str, strategy: str = "mobile") -> list[AuditCheckResult]:
        """Fetch LCP/CLS/INP Core Web Vitals checks for a URL."""
        ...


@runtime_checkable
class RankTrackerPort(Protocol):
    """Paid toggle port for automated SERP rank tracking (Serper / DataForSEO SERP)."""

    def healthcheck(self) -> None:
        """Verify SERP provider credentials and feature enablement."""
        ...

    def check_rank(
        self,
        *,
        keyword: str,
        domain: str,
        locale: str = "pt-BR",
        market: str = "BR",
    ) -> dict[str, Any]:
        """Return organic rank snapshot (position, ranking_url, provider) for keyword/domain."""
        ...
