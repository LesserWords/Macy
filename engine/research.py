"""Research collection, URL canonicalisation, date verification, and LLM extraction (F07)."""

from __future__ import annotations

import functools
import hashlib
import logging
import posixpath
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from lingua import Language, LanguageDetector, LanguageDetectorBuilder
from pydantic import BaseModel, Field

from engine import db, llm
from engine.config import load_prompt
from engine.contracts import ResearchRecord
from engine.integrations.ports import ExtractorPort, FeedSourcePort, LLMPort, NewsSearchPort

TRACKING_PARAM_EXACT: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "twclid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "hsctatracking",
        "igshid",
        "yclid",
        "ref",
        "ref_src",
        "ref_url",
        "rb_clickid",
        "vero_id",
        "oly_enc_id",
        "oly_anon_id",
    }
)

URL_DATE_PATTERN = re.compile(r"/(20\d{2})[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:/|$|-)")
DEFAULT_TRIGRAM_THRESHOLD: float = 0.6
DEDUP_WINDOW_DAYS: int = 14


def trigram_similarity(conn: psycopg.Connection[Any], title_a: str, title_b: str) -> float:
    """Compute PostgreSQL pg_trgm similarity between two title strings."""
    with conn.cursor() as cur:
        cur.execute("SELECT similarity(%s, %s)", (title_a, title_b))
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0


def is_near_duplicate_title(
    conn: psycopg.Connection[Any],
    title_a: str,
    title_b: str,
    threshold: float = DEFAULT_TRIGRAM_THRESHOLD,
) -> bool:
    """Return True if trigram similarity meets or exceeds the threshold."""
    return trigram_similarity(conn, title_a, title_b) >= threshold


class LLMExtractedResearch(BaseModel):
    """Structured output schema for the research LLM extraction step."""

    headline: str
    summary: str
    facts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    event_date: date | None = None
    source_type: str = "news"


def _is_tracking_param(key: str) -> bool:
    lowered = key.strip().lower()
    return lowered.startswith("utm_") or lowered in TRACKING_PARAM_EXACT


def _normalize_url_path(raw_path: str) -> str:
    if not raw_path or raw_path == "/":
        return "/"
    collapsed = re.sub(r"/+", "/", "/" + raw_path.lstrip("/"))
    return posixpath.normpath(collapsed)


def _format_netloc(scheme: str, hostname: str, port: int | None) -> str:
    default_port = 80 if scheme == "http" else 443
    if port is None or port == default_port:
        return hostname
    return f"{hostname}:{port}"


def canonicalize_url(url: str) -> str:
    """Normalize a URL deterministically and strip tracking query parameters and fragments."""
    cleaned = url.strip()
    if not cleaned:
        msg = "Invalid URL: empty string"
        raise ValueError(msg)

    parsed = urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        msg = f"Invalid URL: {url}"
        raise ValueError(msg)

    netloc = _format_netloc(scheme, parsed.hostname.lower(), parsed.port)
    path = _normalize_url_path(parsed.path)

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    kept_params.sort(key=lambda pair: (pair[0], pair[1]))
    query = urlencode(kept_params, doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def infer_date_from_url(url: str) -> datetime | None:
    """Attempt to infer a publication date (YYYY/MM/DD or YYYY-MM-DD) from the URL path."""
    match = URL_DATE_PATTERN.search(url)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return datetime(year, month, day, 0, 0, tzinfo=UTC)
    except ValueError:
        return None


def resolve_date_status(
    published_at: datetime | None,
    *,
    url: str = "",
    inferred_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[datetime | None, str]:
    """Resolve publication timestamp and date_status ('verified' | 'inferred' | 'unverified')."""
    ref_now = _ensure_utc(now) or datetime.now(UTC)
    max_allowed = ref_now + timedelta(hours=24)

    explicit = _ensure_utc(published_at)
    if explicit is not None:
        if explicit > max_allowed:
            return None, "unverified"
        return explicit, "verified"

    candidate_inferred = _ensure_utc(inferred_at) or infer_date_from_url(url)
    if candidate_inferred is not None and candidate_inferred <= max_allowed:
        return candidate_inferred, "inferred"

    return None, "unverified"


def cap_confidence(confidence: str | float, date_status: str) -> str | float:
    """Cap record/opportunity confidence at 'medium' (or 0.6) when date_status is 'unverified'."""
    if date_status != "unverified":
        return confidence
    if isinstance(confidence, str):
        return "medium" if confidence == "high" else confidence
    return min(confidence, 0.6)


@functools.lru_cache(maxsize=1)
def _get_language_detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_languages(
        Language.PORTUGUESE,
        Language.ENGLISH,
    ).build()


def detect_language(text: str | None) -> str | None:
    """Detect primary BCP-47 language tag ('pt' or 'en') using lingua-py."""
    if not text or not text.strip():
        return None
    detected = _get_language_detector().detect_language_of(text)
    if detected is None:
        return None
    return "pt" if detected == Language.PORTUGUESE else "en"


def _upsert_document_from_extracted(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    source_id: uuid.UUID | None,
    item: ResearchRecord,
    extracted: ResearchRecord,
) -> dict[str, Any]:
    raw_meta = extracted.raw or {}
    fetch_status = str(raw_meta.get("fetch_status", "ok"))
    http_status = raw_meta.get("http_status")
    extracted_text: str | None = raw_meta.get("extracted_text")
    content_hash = (
        hashlib.sha256(extracted_text.encode("utf-8")).digest() if extracted_text else None
    )
    lang = detect_language(extracted_text)

    pub_candidate = extracted.published_at or item.published_at
    canonical = canonicalize_url(extracted.canonical_url or item.canonical_url or item.url)
    resolved_pub, date_status = resolve_date_status(pub_candidate, url=canonical)
    if raw_meta.get("date_status") == "inferred" and date_status == "verified":
        date_status = "inferred"

    meta = {
        "author": raw_meta.get("author"),
        "site_name": raw_meta.get("site_name") or item.source,
        "outbound_links": raw_meta.get("outbound_links", []),
    }
    return db.upsert_document(
        conn,
        workspace_id=workspace_id,
        source_id=source_id,
        url=item.url,
        canonical_url=canonical,
        content_hash=content_hash,
        title=extracted.headline or item.headline,
        lang=lang,
        published_at=resolved_pub,
        date_status=date_status,
        fetch_status=fetch_status,
        http_status=int(http_status) if isinstance(http_status, int) else None,
        extracted_text=extracted_text,
        meta=meta,
    )


def _run_llm_research_extraction(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    doc_row: dict[str, Any],
    source_name: str,
    source_role: str,
    llm_adapter: LLMPort | None,
) -> ResearchRecord:
    prompt_template = load_prompt("extract_research")
    text_body = doc_row.get("extracted_text") or doc_row["title"]
    full_prompt = (
        f"{prompt_template}\n\n"
        f"Source: {source_name} (role: {source_role})\n"
        f"URL: {doc_row['canonical_url']}\n"
        f"Title: {doc_row['title']}\n\n"
        f"Content:\n{text_body}"
    )
    llm_out = llm.structured_call(
        conn=conn,
        workspace_id=workspace_id,
        run_id=run_id,
        stage="research",
        model_cls=LLMExtractedResearch,
        prompt=full_prompt,
        input={
            "url": doc_row["canonical_url"],
            "title": doc_row["title"],
            "source": source_name,
        },
        plugin=llm_adapter,
        prompt_version="extract_research.md",
    )
    return ResearchRecord(
        schema_version=1,
        headline=llm_out.headline,
        source=source_name,
        url=doc_row["url"],
        canonical_url=doc_row["canonical_url"],
        published_at=doc_row["published_at"],
        event_date=llm_out.event_date,
        summary=llm_out.summary,
        facts=llm_out.facts,
        entities=llm_out.entities,
        source_type=llm_out.source_type or source_role,
        verification_status=doc_row["date_status"],
    )


def process_feed_item(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    source_row: dict[str, Any],
    item: ResearchRecord,
    extractor_adapter: ExtractorPort,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any]:
    """Extract a feed item, persist document, and run LLM extraction with failure isolation."""
    extracted = extractor_adapter.extract(item.url)
    doc_row = _upsert_document_from_extracted(
        conn,
        workspace_id=workspace_id,
        source_id=source_row.get("id"),
        item=item,
        extracted=extracted,
    )

    if doc_row["fetch_status"] != "ok":
        return db.upsert_research_record(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            document_id=doc_row["id"],
            status="failed",
            schema_version=1,
            payload={
                "error": f"Document fetch_status={doc_row['fetch_status']}",
                "url": doc_row["canonical_url"],
            },
        )

    try:
        record = _run_llm_research_extraction(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            doc_row=doc_row,
            source_name=str(source_row.get("name", item.source)),
            source_role=str(source_row.get("role", "news")),
            llm_adapter=llm_adapter,
        )
    except Exception as exc:
        return db.upsert_research_record(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            document_id=doc_row["id"],
            status="failed",
            schema_version=1,
            payload={
                "error": str(exc),
                "url": doc_row["canonical_url"],
            },
        )

    return db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        document_id=doc_row["id"],
        status="extracted",
        schema_version=record.schema_version,
        payload=record.model_dump(mode="json"),
    )


def _process_collected_items(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    source_row: dict[str, Any],
    items: list[ResearchRecord],
    seen_canonicals: set[str],
    extractor_adapter: ExtractorPort,
    llm_adapter: LLMPort | None,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for item in items:
        canonical = canonicalize_url(item.canonical_url or item.url)
        if canonical in seen_canonicals:
            continue
        seen_canonicals.add(canonical)
        rr_row = process_feed_item(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            source_row=source_row,
            item=item,
            extractor_adapter=extractor_adapter,
            llm_adapter=llm_adapter,
        )
        collected.append(rr_row)
    return collected


def _resolve_sources_by_mode(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    fetch_mode: str,
    required_field: str,
    sources: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if sources is not None:
        return sources
    return [
        s
        for s in db.list_sources(conn, workspace_id=workspace_id)
        if s.get("enabled") and s.get("fetch_mode") == fetch_mode and s.get(required_field)
    ]


_LOG = logging.getLogger(__name__)


def collect_feed_sources(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    since: datetime | None,
    feed_adapter: FeedSourcePort,
    extractor_adapter: ExtractorPort,
    llm_adapter: LLMPort | None = None,
    sources: list[dict[str, Any]] | None = None,
    seen_canonicals: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Poll enabled feed sources, canonicalize URLs, extract documents, and store records."""
    source_rows = _resolve_sources_by_mode(
        conn,
        workspace_id=workspace_id,
        fetch_mode="feed",
        required_field="feed_url",
        sources=sources,
    )
    seen = seen_canonicals if seen_canonicals is not None else set()
    results: list[dict[str, Any]] = []
    failures: list[Exception] = []

    for src in source_rows:
        feed_url = src.get("feed_url")
        if not feed_url:
            continue
        try:
            items = feed_adapter.fetch_feed(str(feed_url), since=since)
        except Exception as exc:
            failures.append(exc)
            _LOG.warning("research feed source skipped (%s): %s", feed_url, exc)
            continue
        results.extend(
            _process_collected_items(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                source_row=src,
                items=items,
                seen_canonicals=seen,
                extractor_adapter=extractor_adapter,
                llm_adapter=llm_adapter,
            )
        )

    if failures and not results and len(failures) == len(source_rows):
        raise failures[0]
    return results


def collect_site_search_sources(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    locale: str,
    market: str,
    since: date | None,
    news_search_adapter: NewsSearchPort,
    extractor_adapter: ExtractorPort,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    seen_canonicals: set[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
    llm_adapter: LLMPort | None = None,
) -> list[dict[str, Any]]:
    """Query enabled site_search sources scoped to their domain through NewsSearchPort."""
    seen = set() if seen_canonicals is None else seen_canonicals
    collected_rows: list[dict[str, Any]] = []
    failures: list[Exception] = []
    target_sources = _resolve_sources_by_mode(
        conn,
        workspace_id=workspace_id,
        fetch_mode="site_search",
        required_field="domain",
        sources=sources,
    )

    for src in target_sources:
        domain = str(src.get("domain") or "").strip().lower()
        if not domain:
            continue
        topics = [str(t).strip() for t in (src.get("topics") or []) if str(t).strip()]
        domain_query = f"site:{domain} {' '.join(topics)}".strip()
        try:
            found_items = news_search_adapter.search(
                domain_query,
                locale=locale,
                market=market,
                since=since,
            )
        except Exception as exc:
            failures.append(exc)
            _LOG.warning("research site search skipped (%s): %s", domain, exc)
            continue
        batch = _process_collected_items(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            source_row=src,
            items=found_items,
            seen_canonicals=seen,
            extractor_adapter=extractor_adapter,
            llm_adapter=llm_adapter,
        )
        collected_rows.extend(batch)

    if failures and not collected_rows and len(failures) == len(target_sources):
        raise failures[0]
    return collected_rows


def collect(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    run: dict[str, Any],
    *,
    feed_adapter: FeedSourcePort,
    news_search_adapter: NewsSearchPort,
    extractor_adapter: ExtractorPort,
    llm_adapter: LLMPort | None = None,
    include_shared: bool = True,
) -> list[dict[str, Any]]:
    """Execute a run's research collection across feed and site_search sources (F07 AC1)."""
    workspace_id = uuid.UUID(str(run["workspace_id"]))
    run_id = uuid.UUID(str(run["id"]))
    campaign_id = uuid.UUID(str(run["campaign_id"]))
    window_start: datetime | None = run.get("window_start")
    since_date: date | None = window_start.date() if window_start is not None else None

    campaign = db.get_campaign(conn, workspace_id=workspace_id, campaign_id=campaign_id)
    locale = str((campaign or {}).get("locale") or run.get("locale") or "pt-BR")
    market = str((campaign or {}).get("market") or run.get("market") or "BR")

    enabled_sources = [
        s
        for s in db.list_sources(conn, workspace_id=workspace_id, include_shared=include_shared)
        if s.get("enabled")
    ]
    feed_sources = [s for s in enabled_sources if s.get("fetch_mode") == "feed"]
    site_search_sources = [s for s in enabled_sources if s.get("fetch_mode") == "site_search"]

    seen_canonicals: set[str] = set()
    collect_feed_sources(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        since=window_start,
        feed_adapter=feed_adapter,
        extractor_adapter=extractor_adapter,
        llm_adapter=llm_adapter,
        sources=feed_sources,
        seen_canonicals=seen_canonicals,
    )
    collect_site_search_sources(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        locale=locale,
        market=market,
        since=since_date,
        news_search_adapter=news_search_adapter,
        extractor_adapter=extractor_adapter,
        llm_adapter=llm_adapter,
        sources=site_search_sources,
        seen_canonicals=seen_canonicals,
    )
    # Wire deduplication into research collection (F08 AC1, AC2)
    dedup(conn, run)
    return db.list_research_records(conn, workspace_id=workspace_id, run_id=run_id)


def dedup(
    conn: psycopg.Connection[Any],
    run: dict[str, Any],
    *,
    threshold: float = DEFAULT_TRIGRAM_THRESHOLD,
    window_days: int = DEDUP_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Scan extracted records in the run and mark near-duplicates within the window (F08 AC2)."""
    workspace_id = uuid.UUID(str(run["workspace_id"]))
    run_id = uuid.UUID(str(run["id"]))
    records = db.list_research_records(conn, workspace_id=workspace_id, run_id=run_id)

    updated_records: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("status") == "duplicate":
            updated_records.append(rec)
            continue
        record_id = uuid.UUID(str(rec["id"]))
        match = db.find_earlier_near_duplicate_record(
            conn,
            workspace_id=workspace_id,
            record_id=record_id,
            threshold=threshold,
            window_days=window_days,
        )
        if match is not None:
            dup_row = db.mark_research_record_duplicate(
                conn,
                workspace_id=workspace_id,
                record_id=record_id,
                duplicate_of=uuid.UUID(str(match["duplicate_of"])),
            )
            if dup_row is not None:
                updated_records.append(dup_row)
                continue
        updated_records.append(rec)

    return updated_records


def re_run_failed_record(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    extractor_adapter: ExtractorPort | None = None,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any] | None:
    """Re-run extraction for a single failed research record (F18 AC3)."""
    rec_row = db.get_research_record(conn, workspace_id=workspace_id, record_id=record_id)
    if not rec_row:
        return None

    run_id = uuid.UUID(str(rec_row["run_id"]))
    doc_id = uuid.UUID(str(rec_row["document_id"]))
    doc_row = db.get_document(conn, workspace_id=workspace_id, document_id=doc_id)
    if not doc_row:
        return None

    if doc_row.get("fetch_status") != "ok" and extractor_adapter is not None:
        extracted = extractor_adapter.extract(doc_row["canonical_url"])
        item_stub = ResearchRecord(
            url=doc_row["url"],
            canonical_url=doc_row["canonical_url"],
            headline=doc_row["title"],
            summary=doc_row["title"],
            source=str(doc_row.get("site_name") or "Source"),
        )
        src_id = (
            uuid.UUID(str(doc_row["source_id"])) if doc_row.get("source_id") is not None else None
        )
        doc_row = _upsert_document_from_extracted(
            conn,
            workspace_id=workspace_id,
            source_id=src_id,
            item=item_stub,
            extracted=extracted,
        )

    if doc_row.get("fetch_status") != "ok":
        return db.upsert_research_record(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            document_id=doc_id,
            status="failed",
            schema_version=1,
            payload={
                "error": f"Document fetch_status={doc_row.get('fetch_status')}",
                "url": doc_row["canonical_url"],
            },
        )

    source_name = "Re-run Source"
    source_role = "news"
    src_id = doc_row.get("source_id")
    if src_id is not None:
        src_row = db.get_source(conn, workspace_id=workspace_id, source_id=uuid.UUID(str(src_id)))
        if src_row:
            source_name = str(src_row.get("name", source_name))
            source_role = str(src_row.get("role", source_role))

    try:
        record = _run_llm_research_extraction(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            doc_row=doc_row,
            source_name=source_name,
            source_role=source_role,
            llm_adapter=llm_adapter,
        )
    except Exception as exc:
        return db.upsert_research_record(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            document_id=doc_id,
            status="failed",
            schema_version=1,
            payload={
                "error": str(exc),
                "url": doc_row["canonical_url"],
            },
        )

    return db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        document_id=doc_id,
        status="extracted",
        schema_version=record.schema_version,
        payload=record.model_dump(mode="json"),
    )
