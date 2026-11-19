"""Editorial brief synthesis, evidence grounding, and versioned persistence (F12, F03 AC2)."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel, ValidationError

from engine import budget, config, db, events, llm, research, seo
from engine.contracts import BrandProfile, EditorialBrief, KeywordData, ResearchRecord
from engine.integrations.config import plugin_config
from engine.integrations.fakes import FakeExtractor, FakeKeywordProvider, FakeLLM, FakeNewsSearch
from engine.integrations.ports import ExtractorPort, KeywordProviderPort, LLMPort, NewsSearchPort
from engine.integrations.registry import IntegrationMissing
from engine.safe_http import SafeHTTPError

logger = logging.getLogger(__name__)
PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "brief.md"
MIN_SYNTH_VERSION_PREFIX = "brief.md"
EVIDENCE_ROLES: tuple[str, ...] = ("data", "management")


class InvalidEvidenceReferenceError(ValueError):
    """Raised when EditorialBrief cites a missing, malformed, or cross-tenant document."""


@dataclass(frozen=True)
class BriefAdapters:
    """Optional integration adapter overrides for brief generation."""

    llm_adapter: LLMPort | None = None
    keyword_provider: KeywordProviderPort | None = None
    news_search_adapter: NewsSearchPort | None = None
    extractor_adapter: ExtractorPort | None = None
    model: str | None = None


def get_brief_prompt_and_version() -> tuple[str, str]:
    """Load the editorial brief prompt template and compute its deterministic SHA256 tag."""
    if not PROMPT_FILE.exists():
        msg = f"Brief prompt template missing at {PROMPT_FILE}"
        raise FileNotFoundError(msg)
    prompt_body = PROMPT_FILE.read_text(encoding="utf-8")
    digest = hashlib.sha256(prompt_body.encode("utf-8")).hexdigest()[:16]
    return prompt_body, f"{MIN_SYNTH_VERSION_PREFIX}:{digest}"


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    return {}


def _safe_load_industry_pack(
    pack_slug: str | None,
    workspace_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return config.resolve_industry_pack_dict(pack_slug, workspace_config)


def _safe_load_format_pack(format_slug: str = "blog") -> dict[str, Any]:
    try:
        return config.load_format_pack(format_slug).model_dump(mode="json")
    except config.PackValidationError:
        return {
            "slug": format_slug,
            "name": format_slug.title(),
            "min_words": 800,
            "max_words": 2500,
            "structure": ["title", "meta", "slug", "body", "faq", "cta"],
        }


def build_brief_system_prompt(
    *,
    base_prompt: str,
    brand_profile: BrandProfile | dict[str, Any],
    brand_version: int,
    industry_pack: dict[str, Any] | None = None,
    format_pack: dict[str, Any] | None = None,
) -> str:
    """Construct stable cached system prompt combining brief guide, brand, industry, and format."""
    brand = _coerce_mapping(brand_profile)
    ind = _coerce_mapping(industry_pack)
    fmt = _coerce_mapping(format_pack) or {"slug": "blog", "min_words": 800, "max_words": 2500}

    sections = [
        base_prompt.strip(),
        "## Active Brand Profile",
        f"- Pinned Brand Version: v{brand_version}",
        f"- Voice: {brand.get('voice', '')}",
        f"- Positioning: {brand.get('positioning', '')}",
        f"- Verified Capabilities: {', '.join(brand.get('capabilities', []))}",
        f"- Prohibited Claims: {', '.join(brand.get('prohibited_claims', []))}",
        f"- Prohibited Regex Patterns: {', '.join(brand.get('prohibited_phrases', []))}",
        f"- CTA Rules: {' | '.join(brand.get('cta_rules', []))}",
        "## Target Format Constraints",
        f"- Format Slug: {fmt.get('slug', 'blog')}",
        f"- Word Range: {fmt.get('min_words', 800)}-{fmt.get('max_words', 2500)}",
        f"- Structure: {', '.join(fmt.get('structure', []))}",
    ]
    if ind:
        sections.extend(
            [
                "## Industry Context",
                f"- Industry: {ind.get('name', ind.get('slug', ''))}",
                f"- Description: {ind.get('description', '')}",
            ]
        )
    return "\n\n".join(sections)


def validate_brief_evidence_references(
    editorial_brief: EditorialBrief,
    *,
    valid_document_ids: set[uuid.UUID] | None = None,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
) -> None:
    """Enforce that every required_facts[].source_id maps to an existing workspace document."""
    for fact_item in editorial_brief.required_facts:
        raw_source = fact_item.source_id
        try:
            doc_uuid = uuid.UUID(str(raw_source))
        except (ValueError, TypeError, AttributeError) as exc:
            msg = f"Invalid document UUID in required_facts: '{raw_source}'"
            raise InvalidEvidenceReferenceError(msg) from exc

        if conn is not None and workspace_id is not None:
            if db.get_document(conn, workspace_id=workspace_id, document_id=doc_uuid) is None:
                msg = (
                    f"Document '{doc_uuid}' cited in required_facts was not found "
                    f"in workspace {workspace_id}"
                )
                raise InvalidEvidenceReferenceError(msg)
        elif valid_document_ids is not None and doc_uuid not in valid_document_ids:
            msg = f"Document '{doc_uuid}' is not present in workspace evidence documents"
            raise InvalidEvidenceReferenceError(msg)

        fact_item.source_id = str(doc_uuid)


@dataclass(frozen=True)
class _BriefPromptContext:
    """Bundled dynamic inputs for editorial brief prompt payload construction."""

    opportunity: dict[str, Any]
    evidence_documents: list[dict[str, Any]]
    keywords: Sequence[KeywordData | dict[str, Any]]
    brand_profile_id: uuid.UUID | None
    brand_version: int
    locale: str
    target_format: str
    revision_findings: list[dict[str, Any] | str] | None


@dataclass(frozen=True)
class _BriefExecutionSpec:
    """Execution parameters for persistent brief generation."""

    workspace_id: uuid.UUID
    opportunity_id: uuid.UUID
    revision_findings: list[dict[str, Any] | str] | None
    actor_user_id: uuid.UUID | None
    offline: bool
    adapters: BriefAdapters


def _prepare_brief_input(ctx: _BriefPromptContext) -> dict[str, Any]:
    kw_dicts = [
        kw.model_dump(mode="json") if isinstance(kw, BaseModel) else dict(kw) for kw in ctx.keywords
    ]
    return {
        "locale": ctx.locale,
        "format": ctx.target_format,
        "brand_profile_id": str(ctx.brand_profile_id) if ctx.brand_profile_id else None,
        "brand_version": ctx.brand_version,
        "opportunity": {
            "title": str(ctx.opportunity.get("title", "")),
            "angle": str(ctx.opportunity.get("angle", "")),
            "funnel_stage": str(ctx.opportunity.get("funnel_stage", "mofu")).lower(),
            "decision_note": ctx.opportunity.get("decision_note") or "",
            "summary": str(ctx.opportunity.get("summary", "")),
        },
        "keywords": kw_dicts,
        "evidence_documents": ctx.evidence_documents,
        "revision_findings": list(ctx.revision_findings or []),
    }


def synthesize_brief_with_prompt(  # noqa: PLR0913
    *,
    opportunity: dict[str, Any],
    evidence_documents: list[dict[str, Any]],
    keywords: Sequence[KeywordData | dict[str, Any]],
    brand_profile: BrandProfile | dict[str, Any],
    brand_profile_id: uuid.UUID | None = None,
    brand_version: int = 1,
    locale: str = "pt-BR",
    industry_pack: dict[str, Any] | None = None,
    format_pack: dict[str, Any] | None = None,
    revision_findings: list[dict[str, Any] | str] | None = None,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
) -> EditorialBrief:
    """Pure editorial brief synthesis helper evaluated against prompts/brief.md and contracts."""
    validated_brand = (
        brand_profile
        if isinstance(brand_profile, BrandProfile)
        else BrandProfile.model_validate(brand_profile)
    )
    resolved_format = format_pack or _safe_load_format_pack("blog")
    base_prompt, prompt_ver = get_brief_prompt_and_version()
    system_prompt = build_brief_system_prompt(
        base_prompt=base_prompt,
        brand_profile=validated_brand,
        brand_version=brand_version,
        industry_pack=industry_pack,
        format_pack=resolved_format,
    )
    input_payload = _prepare_brief_input(
        _BriefPromptContext(
            opportunity=opportunity,
            evidence_documents=evidence_documents,
            keywords=keywords,
            brand_profile_id=brand_profile_id,
            brand_version=brand_version,
            locale=locale,
            target_format=str(resolved_format.get("slug", "blog")),
            revision_findings=revision_findings,
        )
    )

    brief_model: EditorialBrief = llm.structured_call(
        stage="brief",
        model_cls=EditorialBrief,
        prompt=system_prompt,
        input=input_payload,
        plugin=llm_adapter,
        prompt_version=prompt_ver,
        model=model,
    )
    brief_model.brand_profile_id = brand_profile_id
    brief_model.brand_version = brand_version

    if evidence_documents:
        valid_ids = {
            uuid.UUID(str(doc["id"])) for doc in evidence_documents if doc.get("id") is not None
        }
        validate_brief_evidence_references(brief_model, valid_document_ids=valid_ids)

    return brief_model


def _resolve_evidence_adapters(
    conn: psycopg.Connection[Any],
    workspace_id: uuid.UUID,
    *,
    offline: bool,
    adapters: BriefAdapters,
) -> tuple[NewsSearchPort | None, ExtractorPort | None]:
    search_port: NewsSearchPort | None = adapters.news_search_adapter
    if search_port is None:
        if offline:
            search_port = FakeNewsSearch()
        else:
            try:
                cls_ns, cfg_ns = plugin_config(conn, workspace_id=workspace_id, port="news_search")
                search_port = cls_ns(settings=cfg_ns)
            except IntegrationMissing:
                search_port = None

    extractor_port: ExtractorPort | None = adapters.extractor_adapter
    if extractor_port is None:
        if offline:
            extractor_port = FakeExtractor()
        else:
            try:
                cls_ext, cfg_ext = plugin_config(conn, workspace_id=workspace_id, port="extractor")
                extractor_port = cls_ext(settings=cfg_ext)
            except IntegrationMissing:
                extractor_port = None

    return search_port, extractor_port


def _attach_single_evidence_item(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    source_row: dict[str, Any],
    item: ResearchRecord,
    extractor_port: ExtractorPort,
) -> None:
    extracted = extractor_port.extract(item.url)
    raw_meta = extracted.raw or {}
    canonical = research.canonicalize_url(extracted.canonical_url or item.canonical_url or item.url)
    resolved_pub, date_status = research.resolve_date_status(
        extracted.published_at or item.published_at, url=canonical
    )
    facts = extracted.facts or item.facts
    summary = extracted.summary or item.summary
    extracted_text = str(raw_meta.get("extracted_text") or summary or "")

    db.upsert_document(
        conn,
        workspace_id=workspace_id,
        source_id=source_row.get("id"),
        url=item.url,
        canonical_url=canonical,
        title=extracted.headline or item.headline,
        published_at=resolved_pub,
        date_status=date_status,
        fetch_status="ok",
        extracted_text=extracted_text,
        meta={
            "source_name": source_row.get("name", item.source),
            "source_role": source_row.get("role", "data"),
            "summary": summary,
            "facts": facts,
        },
    )


def _search_source_for_evidence(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    source_row: dict[str, Any],
    opp_title: str,
    locale: str,
    market: str,
    search_port: NewsSearchPort,
    extractor_port: ExtractorPort,
) -> None:
    domain = str(source_row.get("domain") or "").strip().lower()
    topics = [str(t).strip() for t in (source_row.get("topics") or []) if str(t).strip()]
    query_terms = " ".join(topics[:2]) or opp_title[:40]
    query = f"site:{domain} {query_terms}".strip() if domain else query_terms

    try:
        hits = search_port.search(query, locale=locale, market=market)
    except budget.BudgetCapReached:
        raise
    except Exception as exc:
        logger.debug("Evidence search failed for domain '%s': %s", domain, exc)
        return

    for item in hits[:2]:
        try:
            _attach_single_evidence_item(
                conn,
                workspace_id=workspace_id,
                source_row=source_row,
                item=item,
                extractor_port=extractor_port,
            )
        except budget.BudgetCapReached:
            raise
        except Exception as exc:
            logger.debug("Evidence extraction failed for '%s': %s", item.url, exc)


def _format_evidence_doc(doc: dict[str, Any]) -> dict[str, Any]:
    raw_meta = doc.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    raw_facts = meta.get("facts")
    facts: list[Any] = raw_facts if isinstance(raw_facts, list) else []
    summary = str(meta.get("summary") or doc.get("extracted_text") or doc.get("title") or "")
    return {
        "id": str(doc["id"]),
        "title": str(doc.get("title") or ""),
        "source_name": str(doc.get("source_name") or meta.get("source_name") or "evidence"),
        "source_role": str(doc.get("source_role") or meta.get("source_role") or "data"),
        "url": str(doc.get("canonical_url") or doc.get("url") or ""),
        "published_at": str(doc.get("published_at") or ""),
        "summary": summary[:400],
        "facts": [str(f) for f in facts[:5]],
    }


def _sync_evidence_sources(
    conn: psycopg.Connection[Any],
    *,
    opp_ctx: dict[str, Any],
    opp_title: str,
    offline: bool,
    adapters: BriefAdapters,
) -> None:
    ws_id = uuid.UUID(str(opp_ctx["workspace_id"]))
    locale = str(opp_ctx.get("campaign_locale") or "pt-BR")
    market = str(opp_ctx.get("campaign_market") or "BR")
    search_port, extractor_port = _resolve_evidence_adapters(
        conn, ws_id, offline=offline, adapters=adapters
    )
    if search_port is None or extractor_port is None:
        return

    all_sources = db.list_sources(
        conn,
        workspace_id=ws_id,
        industry_pack=opp_ctx.get("industry_pack"),
        include_shared=True,
    )
    matching_sources = [
        src
        for src in all_sources
        if src.get("enabled")
        and src.get("role") in EVIDENCE_ROLES
        and (not src.get("locales") or locale in src["locales"])
    ]
    for src in matching_sources[:3]:
        _search_source_for_evidence(
            conn,
            workspace_id=ws_id,
            source_row=src,
            opp_title=opp_title,
            locale=locale,
            market=market,
            search_port=search_port,
            extractor_port=extractor_port,
        )


def _build_primary_fallback_doc(opp_ctx: dict[str, Any]) -> dict[str, Any]:
    record_payload = opp_ctx.get("record_payload") or {}
    summary = str(record_payload.get("summary") or opp_ctx.get("document_title") or "")
    return {
        "id": str(opp_ctx["primary_document_id"]),
        "title": str(opp_ctx.get("document_title") or ""),
        "source_name": str(opp_ctx.get("source_name") or "primary"),
        "source_role": str(opp_ctx.get("source_role") or "news"),
        "url": str(opp_ctx.get("document_canonical_url") or opp_ctx.get("document_url") or ""),
        "published_at": str(opp_ctx.get("document_published_at") or ""),
        "summary": summary,
        "facts": [str(f) for f in (record_payload.get("facts") or [])[:5]],
    }


def _gather_evidence_documents(
    conn: psycopg.Connection[Any],
    opp_ctx: dict[str, Any],
    *,
    offline: bool,
    adapters: BriefAdapters,
) -> list[dict[str, Any]]:
    ws_id = uuid.UUID(str(opp_ctx["workspace_id"]))
    record_payload = opp_ctx.get("record_payload") or {}
    opp_title = str(record_payload.get("headline") or opp_ctx.get("document_title") or "")
    _sync_evidence_sources(
        conn, opp_ctx=opp_ctx, opp_title=opp_title, offline=offline, adapters=adapters
    )

    docs = db.list_evidence_documents(conn, workspace_id=ws_id, roles=EVIDENCE_ROLES, limit=20)
    formatted = [_format_evidence_doc(d) for d in docs]
    primary_id = str(opp_ctx["primary_document_id"])
    if all(item["id"] != primary_id for item in formatted):
        formatted.append(_build_primary_fallback_doc(opp_ctx))
    return formatted


_SEED_PUNCTUATION = re.compile(r"[:;,.!?\"'“”‘’«»()\[\]{}|/\\—–]+")
_SEED_MAX_CHARS = 60
_keyword_log = logging.getLogger(__name__)


def _sanitize_seed_term(raw: str) -> str:
    """Strip punctuation that autocomplete endpoints reject and cap the term length."""
    cleaned = " ".join(_SEED_PUNCTUATION.sub(" ", raw).split())
    if len(cleaned) <= _SEED_MAX_CHARS:
        return cleaned
    clipped = cleaned[:_SEED_MAX_CHARS]
    head, sep, _tail = clipped.rpartition(" ")
    return (head if sep and head else clipped).strip()


def _collect_candidate_seed_terms(opp_ctx: dict[str, Any]) -> list[str]:
    return [
        term
        for term in (_sanitize_seed_term(c) for c in _raw_candidate_seed_terms(opp_ctx))
        if term
    ]


def _raw_candidate_seed_terms(opp_ctx: dict[str, Any]) -> list[str]:
    payload = opp_ctx.get("record_payload") or {}
    has_explicit = "keywords" in payload or "seed_terms" in payload
    candidates: list[str] = list(seo.extract_seed_terms(payload)) if has_explicit else []

    headline = str(payload.get("headline") or opp_ctx.get("document_title") or "").strip()
    if headline:
        candidates.append(" ".join(headline.split()[:6]))

    for entity in payload.get("entities") or []:
        ent_str = str(entity).strip()
        if ent_str:
            candidates.append(ent_str)

    angle = str(opp_ctx.get("angle") or "").strip()
    if angle and len(candidates) < 2:
        candidates.append(" ".join(angle.split()[:5]))
    return candidates


def _dedupe_seed_terms(candidates: list[str], *, limit: int = 5) -> list[str]:
    deduped: list[str] = []
    seen_norms: set[str] = set()
    for term in candidates:
        norm = seo.normalize_keyword(term)
        if norm and norm not in seen_norms:
            seen_norms.add(norm)
            deduped.append(term)
        if len(deduped) >= limit:
            break
    return deduped


def _extract_brief_seed_terms(opp_ctx: dict[str, Any]) -> list[str]:
    return _dedupe_seed_terms(_collect_candidate_seed_terms(opp_ctx), limit=5)


def _estimate_keywords_with_cache(
    conn: psycopg.Connection[Any],
    *,
    seed_terms: list[str],
    locale: str,
    market: str,
) -> list[KeywordData]:
    norm_map = {term: seo.normalize_keyword(term) for term in seed_terms}
    cached = db.get_fresh_keywords_from_cache(
        conn,
        provider="dataforseo",
        locale=locale,
        market=market,
        normalized_keywords=list(dict.fromkeys(norm_map.values())),
    )
    results: list[KeywordData] = []
    for term in seed_terms:
        cached_item = cached.get(norm_map[term])
        if cached_item is not None:
            kw_data = dict(cached_item)
            kw_data["keyword"] = term
            results.append(KeywordData(**kw_data))
        else:
            results.append(
                KeywordData(
                    keyword=term,
                    search_volume=0,
                    difficulty=0.0,
                    cpc_usd=0.0,
                    intent="informational",
                    is_estimate=True,
                )
            )
    return results


def _resolve_brief_keywords(
    conn: psycopg.Connection[Any],
    opp_ctx: dict[str, Any],
    *,
    offline: bool,
    keyword_provider: KeywordProviderPort | None,
) -> list[KeywordData]:
    seed_terms = _extract_brief_seed_terms(opp_ctx)
    if not seed_terms:
        return []

    ws_id = uuid.UUID(str(opp_ctx["workspace_id"]))
    run_id = uuid.UUID(str(opp_ctx["run_id"]))
    locale = str(opp_ctx.get("campaign_locale") or "pt-BR")
    market = str(opp_ctx.get("campaign_market") or "BR")

    if keyword_provider is not None:
        prov_name = "fake" if isinstance(keyword_provider, FakeKeywordProvider) else "dataforseo"
        return seo.keywords_for(
            seed_terms,
            conn=conn,
            workspace_id=ws_id,
            run_id=run_id,
            locale=locale,
            market=market,
            provider=keyword_provider,
            provider_name=prov_name,
        )

    if offline:
        return seo.keywords_for(
            seed_terms,
            conn=conn,
            workspace_id=ws_id,
            run_id=run_id,
            locale=locale,
            market=market,
            provider=FakeKeywordProvider(),
            provider_name="fake",
        )

    try:
        cls_kw, cfg_kw = plugin_config(conn, workspace_id=ws_id, port="keyword_provider")
        instance = cls_kw(settings=cfg_kw)
        prov_name = "fake" if cls_kw is FakeKeywordProvider else "dataforseo"
        return seo.keywords_for(
            seed_terms,
            conn=conn,
            workspace_id=ws_id,
            run_id=run_id,
            locale=locale,
            market=market,
            provider=instance,
            provider_name=prov_name,
        )
    except IntegrationMissing:
        return _estimate_keywords_with_cache(
            conn, seed_terms=seed_terms, locale=locale, market=market
        )
    except (ValueError, SafeHTTPError, OSError) as exc:
        _keyword_log.warning(
            "keyword provider failed (%s); falling back to estimated keywords", exc
        )
        return _estimate_keywords_with_cache(
            conn, seed_terms=seed_terms, locale=locale, market=market
        )


def _call_brief_llm(**kwargs: Any) -> EditorialBrief:
    try:
        return llm.structured_call(stage="brief", model_cls=EditorialBrief, **kwargs)
    except ValidationError as exc:
        if "source_id" in str(exc):
            msg = f"Invalid document UUID in required_facts: {exc}"
            raise InvalidEvidenceReferenceError(msg) from exc
        raise


def _execute_brief(conn: psycopg.Connection[Any], spec: _BriefExecutionSpec) -> dict[str, Any]:
    opp_ctx = db.get_opportunity_context(
        conn, workspace_id=spec.workspace_id, opportunity_id=spec.opportunity_id
    )
    if opp_ctx is None:
        msg = f"Opportunity {spec.opportunity_id} not found in workspace {spec.workspace_id}"
        raise ValueError(msg)

    status = str(opp_ctx.get("status") or "")
    if status != "approved":
        msg = f"Opportunity {spec.opportunity_id} has status '{status}', expected 'approved'"
        raise ValueError(msg)

    brand_row = db.get_current_brand_profile(conn, workspace_id=spec.workspace_id)
    if brand_row is None:
        msg = (
            f"Workspace {spec.workspace_id} has no current brand profile; "
            "save a brand profile before generating a brief"
        )
        raise ValueError(msg)

    brand_model = BrandProfile.model_validate(brand_row["payload"])
    brand_profile_id = uuid.UUID(str(brand_row["id"]))
    brand_version = int(brand_row["version"])

    evidence_docs = _gather_evidence_documents(
        conn, opp_ctx, offline=spec.offline, adapters=spec.adapters
    )
    keywords = _resolve_brief_keywords(
        conn, opp_ctx, offline=spec.offline, keyword_provider=spec.adapters.keyword_provider
    )

    industry_pack = _safe_load_industry_pack(
        opp_ctx.get("industry_pack"), opp_ctx.get("workspace_config")
    )
    format_pack = _safe_load_format_pack("blog")
    base_prompt, prompt_ver = get_brief_prompt_and_version()
    system_prompt = build_brief_system_prompt(
        base_prompt=base_prompt,
        brand_profile=brand_model,
        brand_version=brand_version,
        industry_pack=industry_pack,
        format_pack=format_pack,
    )

    record_payload = opp_ctx.get("record_payload") or {}
    opp_summary = {
        "title": str(record_payload.get("headline") or opp_ctx.get("document_title") or ""),
        "angle": str(opp_ctx.get("angle") or ""),
        "funnel_stage": str(opp_ctx.get("funnel_stage") or "mofu"),
        "decision_note": opp_ctx.get("decision_note") or "",
        "summary": str(record_payload.get("summary") or ""),
    }
    input_payload = _prepare_brief_input(
        _BriefPromptContext(
            opportunity=opp_summary,
            evidence_documents=evidence_docs,
            keywords=keywords,
            brand_profile_id=brand_profile_id,
            brand_version=brand_version,
            locale=str(opp_ctx.get("campaign_locale") or "pt-BR"),
            target_format=str(format_pack.get("slug", "blog")),
            revision_findings=spec.revision_findings,
        )
    )

    resolved_llm = spec.adapters.llm_adapter or (FakeLLM() if spec.offline else None)
    editorial_brief = _call_brief_llm(
        prompt=system_prompt,
        input=input_payload,
        workspace_id=spec.workspace_id,
        run_id=uuid.UUID(str(opp_ctx["run_id"])),
        conn=conn,
        plugin=resolved_llm,
        prompt_version=prompt_ver,
        model=spec.adapters.model,
    )
    editorial_brief.brand_profile_id = brand_profile_id
    editorial_brief.brand_version = brand_version

    validate_brief_evidence_references(editorial_brief, conn=conn, workspace_id=spec.workspace_id)

    brief_row = db.create_next_brief_version(
        conn,
        workspace_id=spec.workspace_id,
        opportunity_id=spec.opportunity_id,
        brand_profile_id=brand_profile_id,
        prompt_version=prompt_ver,
        keywords=[kw.model_dump(mode="json") for kw in keywords],
        schema_version=editorial_brief.schema_version,
        payload=editorial_brief.model_dump(mode="json"),
    )

    events.emit(
        conn,
        workspace_id=spec.workspace_id,
        event_type="brief.created",
        entity_type="brief",
        entity_id=uuid.UUID(str(brief_row["id"])),
        payload={
            "brief_id": str(brief_row["id"]),
            "opportunity_id": str(spec.opportunity_id),
            "run_id": str(opp_ctx["run_id"]),
            "version": int(brief_row["version"]),
            "brand_profile_id": str(brand_profile_id),
            "brand_version": brand_version,
            "primary_keyword": editorial_brief.primary_keyword,
            "revision": bool(spec.revision_findings),
        },
        actor_user_id=spec.actor_user_id,
    )
    return brief_row


def brief(  # noqa: PLR0913
    opportunity: uuid.UUID | str | dict[str, Any],
    *,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    revision_findings: list[dict[str, Any] | str] | None = None,
    actor_user_id: uuid.UUID | None = None,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    keyword_provider: KeywordProviderPort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate and persist a grounded, brand-pinned editorial brief for an approved topic."""
    if isinstance(opportunity, dict):
        opp_uuid = uuid.UUID(str(opportunity["id"]))
        raw_ws = workspace_id or opportunity.get("workspace_id")
    else:
        opp_uuid = uuid.UUID(str(opportunity))
        raw_ws = workspace_id

    if raw_ws is None:
        msg = "workspace_id is required to generate an editorial brief"
        raise ValueError(msg)
    ws_uuid = uuid.UUID(str(raw_ws))

    spec = _BriefExecutionSpec(
        workspace_id=ws_uuid,
        opportunity_id=opp_uuid,
        revision_findings=revision_findings,
        actor_user_id=actor_user_id,
        offline=offline,
        adapters=BriefAdapters(
            llm_adapter=llm_adapter,
            keyword_provider=keyword_provider,
            news_search_adapter=news_search_adapter,
            extractor_adapter=extractor_adapter,
            model=model,
        ),
    )
    if conn is not None:
        return _execute_brief(conn, spec)
    with db.get_connection() as managed_conn:
        return _execute_brief(managed_conn, spec)
