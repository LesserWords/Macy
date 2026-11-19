"""Domain contracts and Pydantic models for the Content Engine (PRD §5.3, DATA-MODEL)."""

import re
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class ScoringWeights(BaseModel):
    """Scoring weights per criterion summing to 100."""

    novelty: int = Field(default=30, ge=0, le=100)
    audience_fit: int = Field(default=20, ge=0, le=100)
    brand_fit: int = Field(default=20, ge=0, le=100)
    competitor_gap: int = Field(default=15, ge=0, le=100)
    trend: int = Field(default=10, ge=0, le=100)
    urgency: int = Field(default=5, ge=0, le=100)

    @property
    def total(self) -> int:
        return (
            self.novelty
            + self.audience_fit
            + self.brand_fit
            + self.competitor_gap
            + self.trend
            + self.urgency
        )

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total != 100:
            msg = f"Weights must sum to 100 (got {self.total})"
            raise ValueError(msg)
        return self


class CampaignConfig(BaseModel):
    """Campaign strategy and configuration contract."""

    schema_version: int = 1
    goal: str = "authority"
    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)
    cta_intensity_limits: dict[str, str] = Field(
        default_factory=lambda: {"TOFU": "low", "MOFU": "medium", "BOFU": "high"}
    )
    pillars: list[str] = Field(default_factory=list)


class BrandProfile(BaseModel):
    """Brand voice, positioning and rules contract."""

    schema_version: int = 1
    voice: str
    positioning: str
    capabilities: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    prohibited_phrases: list[str] = Field(default_factory=list)
    cta_rules: list[str] = Field(default_factory=list)

    @field_validator("voice", "positioning")
    @staticmethod
    def validate_non_empty_text(value: str, info: ValidationInfo) -> str:
        cleaned = value.strip()
        if not cleaned:
            msg = f"Field '{info.field_name}' must not be empty"
            raise ValueError(msg)
        return cleaned

    @field_validator("capabilities", "prohibited_claims", "prohibited_phrases", "cta_rules")
    @staticmethod
    def validate_list_entries(items: list[str], info: ValidationInfo) -> list[str]:
        cleaned_items: list[str] = []
        for item in items:
            stripped = item.strip()
            if not stripped:
                msg = f"Field '{info.field_name}' must not contain empty entries"
                raise ValueError(msg)
            if info.field_name == "prohibited_phrases":
                try:
                    re.compile(stripped)
                except re.error as e:
                    msg = f"Invalid regex in prohibited_phrases '{stripped}': {e}"
                    raise ValueError(msg) from e
            cleaned_items.append(stripped)
        return cleaned_items


class ResearchRecord(BaseModel):
    """Document observation from collection stage."""

    schema_version: int = 1
    headline: str
    source: str
    url: str
    canonical_url: str | None = None
    published_at: datetime | None = None
    event_date: date | None = None
    summary: str
    facts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    source_type: str = "news"
    verification_status: str = "unverified"
    raw: dict[str, Any] | None = None


class SubScore(BaseModel):
    """Criterion sub-score with rationale."""

    score: float = Field(ge=0.0, le=10.0)
    evidence: str
    is_estimate: bool = False

    @field_validator("score", mode="before")
    @classmethod
    def normalize_score(cls, v: Any) -> float:
        val = float(v)
        if 10.0 < val <= 100.0:
            return round(val / 10.0, 2)
        return val


class Opportunity(BaseModel):
    """Scored content opportunity candidate."""

    schema_version: int = 1
    title: str
    angle: str
    funnel_stage: str
    confidence: float | str = Field(default=0.8)
    novelty: SubScore
    audience_fit: SubScore
    brand_fit: SubScore
    competitor_gap: SubScore
    trend: SubScore
    urgency: SubScore
    weighted_total: float = Field(default=0.0, ge=0.0, le=100.0)

    @property
    def confidence_level(self) -> str:
        if isinstance(self.confidence, str):
            cleaned = self.confidence.strip().lower()
            if cleaned in ("low", "medium", "high"):
                return cleaned
        try:
            val = float(self.confidence)
            if val >= 0.7:
                return "high"
            if val >= 0.4:
                return "medium"
            return "low"
        except (ValueError, TypeError):
            return "medium"

    @field_validator("funnel_stage", mode="before")
    @classmethod
    def validate_funnel_stage(cls, v: Any) -> str:
        if isinstance(v, str):
            cleaned = v.strip().lower()
            if cleaned in ("tofu", "mofu", "bofu"):
                return cleaned
        return "mofu"

    def compute_weighted_total(self, weights: ScoringWeights | None = None) -> float:
        w = weights or ScoringWeights()
        total = (
            self.novelty.score * w.novelty
            + self.audience_fit.score * w.audience_fit
            + self.brand_fit.score * w.brand_fit
            + self.competitor_gap.score * w.competitor_gap
            + self.trend.score * w.trend
            + self.urgency.score * w.urgency
        ) / 10.0
        self.weighted_total = round(min(100.0, max(0.0, total)), 2)
        return self.weighted_total


def _validate_document_uuid(value: str | uuid.UUID) -> str | uuid.UUID:
    """Require a UUID so LLM citations of source names fail validation and trigger a retry."""
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        msg = (
            "source_id must be a valid document UUID copied from evidence_documents[].id, "
            "not a source name or title"
        )
        raise ValueError(msg) from exc
    return value


class RequiredFact(BaseModel):
    """Fact requirement from research evidence."""

    fact: str
    source_id: str | uuid.UUID

    @field_validator("source_id")
    @classmethod
    def _require_uuid_source(cls, value: str | uuid.UUID) -> str | uuid.UUID:
        return _validate_document_uuid(value)


class EditorialBrief(BaseModel):
    """Editorial brief approved for writing."""

    schema_version: int = 1
    brand_profile_id: uuid.UUID | None = None
    brand_version: int = 1
    topic: str
    target_audience: str
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    format: str = "blog"
    angle: str
    outline: list[str] = Field(default_factory=list)
    required_facts: list[RequiredFact] = Field(default_factory=list)
    cta_goal: str


class DraftClaim(BaseModel):
    """Atomic claim with provenance tracing."""

    text: str
    kind: str = "analysis"  # fact, analysis, recommendation
    source_id: str | uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_fact_source(self) -> Self:
        if self.kind == "fact" and not self.source_id:
            msg = "Facts must have a source_id for provenance"
            raise ValueError(msg)
        if self.kind == "fact":
            _validate_document_uuid(str(self.source_id))
        return self


class _DictAccessModel(BaseModel):
    """Pydantic model that keeps dict-style read access for legacy consumers."""

    def get(self, key: str, default: Any = None) -> Any:
        value = getattr(self, key, None) if key in type(self).model_fields else None
        return value if value else default

    def items(self) -> list[tuple[str, Any]]:
        return [(k, v) for k, v in self.model_dump().items() if v]

    def values(self) -> list[Any]:
        return [v for _, v in self.items()]


def _rename_legacy_keys(raw: Any, aliases: dict[str, str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    out: dict[str, Any] = {}
    for key, value in raw.items():
        target = aliases.get(str(key), str(key))
        out.setdefault(target, value)
    return out


class FAQItem(_DictAccessModel):
    """One FAQ entry with explicit properties (keeps strict JSON schema non-empty)."""

    question: str
    answer: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy(cls, raw: Any) -> Any:
        return _rename_legacy_keys(raw, {"q": "question", "a": "answer"})


class DraftCTA(_DictAccessModel):
    """Call-to-action block with explicit properties."""

    headline: str = ""
    body: str = ""
    button_text: str = ""
    url: str = ""
    intensity: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy(cls, raw: Any) -> Any:
        return _rename_legacy_keys(
            raw,
            {"text": "body", "title": "headline", "button": "button_text", "action": "button_text"},
        )


class ContentDraft(BaseModel):
    """Generated article draft with claims and metadata."""

    schema_version: int = 1
    title: str
    meta_description: str
    slug: str
    body_md: str
    faq: list[FAQItem] = Field(default_factory=list)
    cta: DraftCTA = Field(default_factory=DraftCTA)
    claims: list[DraftClaim] = Field(default_factory=list)


class Finding(BaseModel):
    """Quality finding pointing to a responsible module."""

    criterion: str
    severity: str  # critical, major, minor
    detail: str
    responsible_module: str  # research, brief, write


class QualityReport(BaseModel):
    """Evaluation verdict with findings."""

    schema_version: int = 1
    verdict: str  # PASS, REVISION, REJECT
    findings: list[Finding] = Field(default_factory=list)
    score: float | None = None


class PublishError(RuntimeError):
    """Raised when publishing to a destination fails or returns invalid contract data."""


class PublishRequest(BaseModel):
    """Request payload sent to Publisher port."""

    schema_version: int = 1
    draft_id: uuid.UUID
    draft: ContentDraft
    locale: str
    destination: str
    mode: str = "draft"  # draft, scheduled, live
    scheduled_at: datetime | None = None
    schema_json_ld: dict[str, Any] | list[dict[str, Any]] | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        norm = value.strip().lower()
        if norm not in ("draft", "scheduled", "live"):
            msg = f"Unsupported publish mode '{value}'"
            raise ValueError(msg)
        return norm


class PublishedContent(BaseModel):
    """Response returned by Publisher port."""

    schema_version: int = 1
    destination: str
    external_id: str
    url: str | None = None
    status: str = "published"  # published, scheduled, draft, failed
    published_at: datetime | None = None
    raw: dict[str, Any] | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        norm = value.strip().lower()
        if norm not in ("published", "scheduled", "draft", "failed"):
            msg = f"Unsupported PublishedContent status '{value}'"
            raise ValueError(msg)
        return norm


class KeywordData(BaseModel):
    """Keyword metrics contract from SEO port."""

    schema_version: int = 1
    keyword: str
    search_volume: int = 0
    difficulty: float = 0.0
    cpc_usd: float = 0.0
    intent: str = "informational"
    is_estimate: bool = False


AuditCategory = Literal[
    "onpage",
    "performance",
    "usability",
    "security",
    "links",
    "social",
    "ai",
]
AuditStatus = Literal["pass", "warning", "error", "info"]


class AuditCheckResult(BaseModel):
    """Single deterministic or optional-adapter audit finding for a URL."""

    check: str
    category: AuditCategory
    status: AuditStatus
    score: float = Field(ge=0.0, le=1.0)
    detail: str
    recommendation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CategoryScore(BaseModel):
    """Normalized score summary for one audit category."""

    category: AuditCategory
    weight: int = Field(ge=0)
    score: float = Field(ge=0.0, le=100.0)
    passed: int = 0
    warnings: int = 0
    errors: int = 0
    info: int = 0


class CrawlConfig(BaseModel):
    """Bounded crawl configuration parameterized by workspace plan tier (ADR-028)."""

    plan: Literal["free", "paid"] = "free"
    max_pages: int | None = Field(default=None, ge=1)
    paid_max_pages: int = Field(default=50, ge=1, le=200)
    hard_cap: int = Field(default=200, ge=1, le=200)
    max_bytes: int = Field(default=3_000_000, ge=1024)
    external_link_cap: int = Field(default=100, ge=0, le=500)
    concurrency: int = Field(default=2, ge=1, le=8)
    axe_enabled: bool = False
    psi_enabled: bool = False

    @property
    def effective_max_pages(self) -> int:
        """Resolve effective crawl page ceiling respecting plan and hard cap 200."""
        plan_cap = self.paid_max_pages if self.plan == "paid" else 20
        plan_cap = min(plan_cap, self.hard_cap)
        if self.max_pages is None:
            return plan_cap
        return min(self.max_pages, plan_cap, self.hard_cap)


class PageAuditReport(BaseModel):
    """Complete single-URL SEO and GEO audit report."""

    schema_version: int = 1
    url: str
    final_url: str | None = None
    status_code: int
    title: str | None = None
    meta_description: str | None = None
    ttfb_ms: float = 0.0
    byte_weight: int = 0
    word_count: int = 0
    overall_score: float = Field(ge=0.0, le=100.0)
    grade: str
    categories: dict[AuditCategory, CategoryScore]
    checks: list[AuditCheckResult]
    recommendations: list[str]
    internal_links: list[str] = Field(default_factory=list)
    external_links: list[str] = Field(default_factory=list)
    json_ld_types: list[str] = Field(default_factory=list)
    audited_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SiteAuditReport(BaseModel):
    """Aggregated site-wide bounded crawl and multipage SEO audit report."""

    schema_version: int = 1
    start_url: str
    pages_crawled: int
    max_pages: int
    overall_score: float = Field(ge=0.0, le=100.0)
    grade: str
    pages: list[PageAuditReport]
    broken_internal_links: list[dict[str, Any]] = Field(default_factory=list)
    broken_external_links: list[dict[str, Any]] = Field(default_factory=list)
    redirect_chains: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_titles: dict[str, list[str]] = Field(default_factory=dict)
    duplicate_meta_descriptions: dict[str, list[str]] = Field(default_factory=dict)
    orphan_pages: list[str] = Field(default_factory=list)
    crawled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
