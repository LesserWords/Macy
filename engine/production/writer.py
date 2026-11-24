"""Blog article writer, claim provenance validator, and revision persistence (F13, ADR-002)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
import psycopg
from pydantic import ValidationError

from engine import db, events, llm
from engine.contracts import BrandProfile, ContentDraft, EditorialBrief, Finding
from engine.integrations.fakes import FakeLLM
from engine.integrations.ports import LLMPort
from engine.strategy.brief import (
    _coerce_mapping,
    _safe_load_format_pack,
    _safe_load_industry_pack,
)

PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "write_blog.md"
WRITE_VERSION_PREFIX = "write_blog.md"
MAX_DRAFT_REVISION = 2
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_PDF_PAGES = 50


class InvalidDraftProvenanceError(ValueError):
    """Raised when a draft fact claim lacks a valid workspace document reference."""


class DraftRevisionLimitReachedError(ValueError):
    """Raised when attempting to create a draft revision beyond revision 2 (F13 AC3)."""


@dataclass(frozen=True)
class _WritePromptInputSpec:
    """Bundled dynamic inputs for writer prompt user payload construction."""

    brief: EditorialBrief
    evidence_documents: list[dict[str, Any]]
    locale: str
    revision: int
    findings: Sequence[Finding | dict[str, Any] | str] | None


@dataclass(frozen=True)
class _WriteExecutionSpec:
    """Execution parameters for persistent draft generation."""

    workspace_id: uuid.UUID
    brief_id: uuid.UUID
    findings: Sequence[Finding | dict[str, Any] | str] | None
    actor_user_id: uuid.UUID | None
    offline: bool
    llm_adapter: LLMPort | None
    model: str | None


def get_write_prompt_and_version() -> tuple[str, str]:
    """Load prompts/write_blog.md and return (content, deterministic sha256 tag)."""
    if not PROMPT_FILE.exists():
        msg = f"Writer prompt template missing at {PROMPT_FILE}"
        raise FileNotFoundError(msg)
    body = PROMPT_FILE.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return body, f"{WRITE_VERSION_PREFIX}:{digest}"


def build_write_system_prompt(
    *,
    base_prompt: str,
    brand_profile: BrandProfile | dict[str, Any],
    brand_version: int = 1,
    industry_pack: dict[str, Any] | None = None,
    format_pack: dict[str, Any] | None = None,
) -> str:
    """Assemble cacheable writer system prompt from template, brand, industry, and format."""
    brand_map = _coerce_mapping(brand_profile)
    ind_map = _coerce_mapping(industry_pack)
    fmt_map = _coerce_mapping(format_pack) or {"slug": "blog", "min_words": 800, "max_words": 2500}

    blocks = [
        base_prompt.strip(),
        "## Pinned Brand Voice & Compliance",
        f"- Pinned Brand Version: v{brand_version}",
        f"- Voice: {brand_map.get('voice', '')}",
        f"- Positioning: {brand_map.get('positioning', '')}",
        f"- Capabilities: {', '.join(brand_map.get('capabilities', []))}",
        f"- Disallowed Claims: {', '.join(brand_map.get('prohibited_claims', []))}",
        f"- Forbidden Regex Patterns: {', '.join(brand_map.get('prohibited_phrases', []))}",
        f"- CTA Directives: {' | '.join(brand_map.get('cta_rules', []))}",
        "## Article Format Specification",
        f"- Format: {fmt_map.get('slug', 'blog')}",
        f"- Word Range: {fmt_map.get('min_words', 800)}-{fmt_map.get('max_words', 2500)}",
        f"- Required Sections: {', '.join(fmt_map.get('structure', []))}",
    ]
    if ind_map:
        blocks.extend(
            [
                "## Industry Context",
                f"- Industry: {ind_map.get('name', ind_map.get('slug', ''))}",
                f"- Focus: {ind_map.get('description', '')}",
            ]
        )
    return "\n\n".join(blocks)


def _normalize_finding_entry(item: Finding | dict[str, Any] | str) -> dict[str, str]:
    if isinstance(item, Finding):
        return {
            "criterion": item.criterion,
            "severity": item.severity,
            "detail": item.detail,
            "responsible_module": item.responsible_module,
        }
    if isinstance(item, dict):
        return {
            "criterion": str(item.get("criterion") or "editorial_review"),
            "severity": str(item.get("severity") or "major"),
            "detail": str(item.get("detail") or ""),
            "responsible_module": str(item.get("responsible_module") or "write"),
        }
    return {
        "criterion": "editorial_review",
        "severity": "major",
        "detail": str(item),
        "responsible_module": "write",
    }


def _prepare_write_user_input(spec: _WritePromptInputSpec) -> dict[str, Any]:
    normalized_findings = [_normalize_finding_entry(f) for f in (spec.findings or [])]
    return {
        "locale": spec.locale,
        "revision": spec.revision,
        "brief": spec.brief.model_dump(mode="json"),
        "evidence_documents": spec.evidence_documents,
        "findings": normalized_findings,
    }


def _parse_claim_source_uuid(raw_source: Any) -> uuid.UUID | None:
    if raw_source is None or not str(raw_source).strip():
        return None
    try:
        return uuid.UUID(str(raw_source))
    except (ValueError, TypeError, AttributeError) as exc:
        msg = f"Invalid document UUID in draft claim: '{raw_source}'"
        raise InvalidDraftProvenanceError(msg) from exc


def _verify_claim_doc_exists(
    doc_uuid: uuid.UUID,
    *,
    valid_document_ids: set[uuid.UUID] | None = None,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
) -> None:
    if conn is not None and workspace_id is not None:
        if db.get_document(conn, workspace_id=workspace_id, document_id=doc_uuid) is None:
            msg = (
                f"Document '{doc_uuid}' cited in draft claim was not found "
                f"in workspace {workspace_id}"
            )
            raise InvalidDraftProvenanceError(msg)
    elif valid_document_ids is not None and doc_uuid not in valid_document_ids:
        msg = f"Document '{doc_uuid}' is not present in workspace evidence documents"
        raise InvalidDraftProvenanceError(msg)


def validate_draft_claim_provenance(
    draft: ContentDraft,
    *,
    valid_document_ids: set[uuid.UUID] | None = None,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Verify every claim's provenance and return normalized claim rows for draft_claims."""
    normalized_rows: list[dict[str, Any]] = []
    for claim in draft.claims:
        doc_uuid = _parse_claim_source_uuid(claim.source_id)
        if claim.kind == "fact" and doc_uuid is None:
            msg = f"Fact claim '{claim.text}' is missing a source_id"
            raise InvalidDraftProvenanceError(msg)

        if doc_uuid is not None:
            _verify_claim_doc_exists(
                doc_uuid,
                valid_document_ids=valid_document_ids,
                conn=conn,
                workspace_id=workspace_id,
            )
            claim.source_id = str(doc_uuid)

        normalized_rows.append(
            {
                "kind": claim.kind,
                "text": claim.text,
                "document_id": doc_uuid,
            }
        )
    return normalized_rows


def _call_write_llm(**kwargs: Any) -> ContentDraft:
    try:
        return llm.structured_call(
            stage="write",
            model_cls=ContentDraft,
            reasoning_effort="high",
            **kwargs,
        )
    except ValidationError as exc:
        if "source_id" in str(exc):
            msg = f"Invalid document UUID in draft claim: {exc}"
            raise InvalidDraftProvenanceError(msg) from exc
        raise


def write_draft_with_prompt(  # noqa: PLR0913
    *,
    brief: EditorialBrief | dict[str, Any],
    evidence_documents: list[dict[str, Any]],
    brand_profile: BrandProfile | dict[str, Any],
    brand_version: int = 1,
    locale: str = "pt-BR",
    revision: int = 0,
    findings: Sequence[Finding | dict[str, Any] | str] | None = None,
    industry_pack: dict[str, Any] | None = None,
    format_pack: dict[str, Any] | None = None,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
) -> ContentDraft:
    """Pure article drafting helper evaluated against prompts/write_blog.md and ContentDraft."""
    validated_brief = (
        brief if isinstance(brief, EditorialBrief) else EditorialBrief.model_validate(brief)
    )
    validated_brand = (
        brand_profile
        if isinstance(brand_profile, BrandProfile)
        else BrandProfile.model_validate(brand_profile)
    )
    resolved_format = format_pack or _safe_load_format_pack(validated_brief.format or "blog")
    base_prompt, prompt_ver = get_write_prompt_and_version()
    system_prompt = build_write_system_prompt(
        base_prompt=base_prompt,
        brand_profile=validated_brand,
        brand_version=brand_version,
        industry_pack=industry_pack,
        format_pack=resolved_format,
    )
    input_payload = _prepare_write_user_input(
        _WritePromptInputSpec(
            brief=validated_brief,
            evidence_documents=evidence_documents,
            locale=locale,
            revision=revision,
            findings=findings,
        )
    )

    draft_model = _call_write_llm(
        prompt=system_prompt,
        input=input_payload,
        plugin=llm_adapter,
        prompt_version=prompt_ver,
        model=model,
    )

    if evidence_documents:
        valid_ids = {
            uuid.UUID(str(doc["id"])) for doc in evidence_documents if doc.get("id") is not None
        }
        validate_draft_claim_provenance(draft_model, valid_document_ids=valid_ids)

    return draft_model


def _format_doc_row_for_writer(doc_row: dict[str, Any]) -> dict[str, Any]:
    raw_meta = doc_row.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    raw_facts = meta.get("facts")
    facts = raw_facts if isinstance(raw_facts, list) else []
    summary = str(
        meta.get("summary") or doc_row.get("extracted_text") or doc_row.get("title") or ""
    )
    return {
        "id": str(doc_row["id"]),
        "title": str(doc_row.get("title") or ""),
        "source_name": str(meta.get("source_name") or "evidence"),
        "source_role": str(meta.get("source_role") or "data"),
        "url": str(doc_row.get("canonical_url") or doc_row.get("url") or ""),
        "published_at": str(doc_row.get("published_at") or ""),
        "summary": summary[:500],
        "facts": [str(f) for f in facts[:5]],
    }


def _resolve_brief_evidence_docs(
    conn: psycopg.Connection[Any],
    *,
    ws_id: uuid.UUID,
    brief_ctx: dict[str, Any],
    brief_model: EditorialBrief,
) -> list[dict[str, Any]]:
    primary_uuid = uuid.UUID(str(brief_ctx["primary_document_id"]))
    candidate_ids = list(
        dict.fromkeys(
            [*(uuid.UUID(str(rf.source_id)) for rf in brief_model.required_facts), primary_uuid]
        )
    )
    docs: list[dict[str, Any]] = []
    for doc_uuid in candidate_ids:
        doc_row = db.get_document(conn, workspace_id=ws_id, document_id=doc_uuid)
        if doc_row is not None and str(doc_row.get("fetch_status") or "ok").strip().lower() == "ok":
            docs.append(_format_doc_row_for_writer(doc_row))
    return docs


def _execute_write(conn: psycopg.Connection[Any], spec: _WriteExecutionSpec) -> dict[str, Any]:
    brief_ctx = db.get_brief_writing_context(
        conn, workspace_id=spec.workspace_id, brief_id=spec.brief_id
    )
    if brief_ctx is None:
        msg = f"Brief {spec.brief_id} not found in workspace {spec.workspace_id}"
        raise ValueError(msg)

    latest_draft = db.get_latest_draft_for_brief(
        conn, workspace_id=spec.workspace_id, brief_id=spec.brief_id
    )
    brief_next = 0 if latest_draft is None else int(latest_draft["revision"]) + 1
    opp_max = db.get_max_draft_revision_for_opportunity(
        conn,
        workspace_id=spec.workspace_id,
        opportunity_id=uuid.UUID(str(brief_ctx["opportunity_id"])),
    )
    opp_next = 0 if opp_max is None else opp_max + 1
    next_revision = max(brief_next, opp_next)
    if next_revision > MAX_DRAFT_REVISION:
        msg = (
            f"Brief {spec.brief_id} has already reached maximum revision "
            f"{MAX_DRAFT_REVISION}; revision {next_revision} is not allowed"
        )
        raise DraftRevisionLimitReachedError(msg)

    brief_model = EditorialBrief.model_validate(brief_ctx["brief_payload"])
    brand_model = BrandProfile.model_validate(brief_ctx["brand_payload"])
    brand_version = int(brief_ctx["brand_version"])
    locale = str(brief_ctx.get("campaign_locale") or "pt-BR")

    evidence_docs = _resolve_brief_evidence_docs(
        conn, ws_id=spec.workspace_id, brief_ctx=brief_ctx, brief_model=brief_model
    )
    industry_pack = _safe_load_industry_pack(
        brief_ctx.get("industry_pack"), brief_ctx.get("workspace_config")
    )
    format_pack = _safe_load_format_pack(brief_model.format or "blog")
    base_prompt, prompt_ver = get_write_prompt_and_version()
    system_prompt = build_write_system_prompt(
        base_prompt=base_prompt,
        brand_profile=brand_model,
        brand_version=brand_version,
        industry_pack=industry_pack,
        format_pack=format_pack,
    )
    input_payload = _prepare_write_user_input(
        _WritePromptInputSpec(
            brief=brief_model,
            evidence_documents=evidence_docs,
            locale=locale,
            revision=next_revision,
            findings=spec.findings,
        )
    )

    resolved_llm = spec.llm_adapter or (FakeLLM() if spec.offline else None)
    draft_model = _call_write_llm(
        prompt=system_prompt,
        input=input_payload,
        workspace_id=spec.workspace_id,
        run_id=uuid.UUID(str(brief_ctx["run_id"])),
        conn=conn,
        plugin=resolved_llm,
        prompt_version=prompt_ver,
        model=spec.model,
    )

    claim_rows = validate_draft_claim_provenance(
        draft_model, conn=conn, workspace_id=spec.workspace_id
    )

    draft_row = db.create_next_draft_revision(
        conn,
        workspace_id=spec.workspace_id,
        brief_id=spec.brief_id,
        prompt_version=prompt_ver,
        schema_version=draft_model.schema_version,
        payload=draft_model.model_dump(mode="json"),
        claims=claim_rows,
        revision=next_revision,
    )

    events.emit(
        conn,
        workspace_id=spec.workspace_id,
        event_type="draft.created",
        entity_type="draft",
        entity_id=uuid.UUID(str(draft_row["id"])),
        payload={
            "draft_id": str(draft_row["id"]),
            "brief_id": str(spec.brief_id),
            "opportunity_id": str(brief_ctx["opportunity_id"]),
            "run_id": str(brief_ctx["run_id"]),
            "revision": int(draft_row["revision"]),
            "claims_count": len(claim_rows),
            "title": draft_model.title,
            "slug": draft_model.slug,
        },
        actor_user_id=spec.actor_user_id,
    )
    return draft_row


def write(  # noqa: PLR0913
    brief: uuid.UUID | str | dict[str, Any],
    findings: Sequence[Finding | dict[str, Any] | str] | None = None,
    *,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate and persist a sourced ContentDraft and draft_claims rows for a brief (F13)."""
    if isinstance(brief, dict):
        brief_uuid = uuid.UUID(str(brief["id"]))
        raw_ws = workspace_id or brief.get("workspace_id")
    else:
        brief_uuid = uuid.UUID(str(brief))
        raw_ws = workspace_id

    if raw_ws is None:
        msg = "workspace_id is required to write an article draft"
        raise ValueError(msg)
    ws_uuid = uuid.UUID(str(raw_ws))

    spec = _WriteExecutionSpec(
        workspace_id=ws_uuid,
        brief_id=brief_uuid,
        findings=findings,
        actor_user_id=actor_user_id,
        offline=offline,
        llm_adapter=llm_adapter,
        model=model,
    )
    if conn is not None:
        return _execute_write(conn, spec)
    with db.get_connection() as managed_conn:
        return _execute_write(managed_conn, spec)


def _slugify_text(text: str) -> str:
    """Convert arbitrary title text into a lowercase hyphenated ASCII slug."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")
    return cleaned or "imported-draft"


def _extract_h1_title(body_md: str) -> str | None:
    """Return the first Markdown H1 heading text if present."""
    for line in body_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            if heading:
                return heading
    return None


def _default_meta_description(body_md: str, fallback: str) -> str:
    """Derive a concise meta description from the first non-heading paragraph."""
    for line in body_md.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:155]
    return fallback[:155]


def extract_file_to_markdown(file_path: Path | str) -> str:
    """Extract UTF-8 Markdown from a .md or .pdf file with 10 MB / 50-page limits (M5-07)."""
    path = Path(file_path)
    if not path.is_file():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    suffix = path.suffix.lower()
    if suffix not in {".md", ".pdf"}:
        msg = f"Unsupported file extension '{suffix}'; expected .md or .pdf."
        raise ValueError(msg)

    size_bytes = path.stat().st_size
    if size_bytes > MAX_IMPORT_BYTES:
        msg = f"File exceeds maximum allowed size of 10 MB ({size_bytes} bytes)."
        raise ValueError(msg)

    if suffix == ".md":
        return path.read_text(encoding="utf-8")

    import pymupdf
    import pymupdf4llm  # type: ignore[import-untyped]

    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        page_count = len(doc)
        if page_count > MAX_IMPORT_PDF_PAGES:
            msg = f"PDF exceeds maximum allowed 50 pages ({page_count} pages)."
            raise ValueError(msg)
        return str(pymupdf4llm.to_markdown(doc))


def parse_markdown_draft(
    markdown_text: str,
    *,
    default_title: str | None = None,
) -> ContentDraft:
    """Parse Markdown with optional YAML frontmatter into a validated ContentDraft (M5-07)."""
    post = frontmatter.loads(markdown_text)
    meta = dict(post.metadata or {})
    body_md = post.content.strip()

    raw_title = str(meta.get("title") or "").strip()
    title = raw_title or _extract_h1_title(body_md) or default_title or "Imported Draft"
    raw_slug = str(meta.get("slug") or "").strip()
    slug = raw_slug or _slugify_text(title)
    raw_meta_desc = str(meta.get("meta_description") or "").strip()
    meta_description = raw_meta_desc or _default_meta_description(body_md, title)

    cta = (
        meta["cta"]
        if isinstance(meta.get("cta"), dict)
        else {"headline": "Saiba mais", "button_text": "Fale conosco"}
    )
    claims = meta["claims"] if isinstance(meta.get("claims"), list) else []
    faq = meta["faq"] if isinstance(meta.get("faq"), list) else []

    return ContentDraft.model_validate(
        {
            "title": title,
            "slug": slug,
            "meta_description": meta_description,
            "body_md": body_md,
            "faq": faq,
            "cta": cta,
            "claims": claims,
        }
    )
