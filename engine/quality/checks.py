"""Pure deterministic draft quality checks run prior to LLM evaluation (F14, ADR-025)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.contracts import (
    BrandProfile,
    CampaignConfig,
    ContentDraft,
    EditorialBrief,
    Finding,
    QualityReport,
)
from engine.research import detect_language
from engine.seo import normalize_keyword

DEFAULT_FRESHNESS_DAYS: int = 90
MIN_META_LENGTH: int = 110
MAX_META_LENGTH: int = 160
MAX_SLUG_LENGTH: int = 100
DEFAULT_MIN_KEYWORD_DENSITY: float = 0.5
DEFAULT_MAX_KEYWORD_DENSITY: float = 2.5
MAX_KEYWORD_DENSITY_MAJOR: float = 4.0
DEFAULT_WORDS_PER_SUBHEADING: int = 300
MIN_WORDS_FOR_READABILITY: int = 100
MIN_SENTENCES_FOR_READABILITY: int = 3
FLESCH_PASS_MIN: float = 60.0
FLESCH_MINOR_MIN: float = 40.0
FLESCH_BASE_EN: float = 206.835
FLESCH_BASE_PT_MARTINS: float = 248.835

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>()\"']+")
H1_LINE_RE = re.compile(r"^\s*#\s+(.+?)\s*#*\s*$")
SUBHEADING_LINE_RE = re.compile(r"^\s*#{2,3}\s+(.+?)\s*#*\s*$")
MARKDOWN_LINK_URL_RE = re.compile(r"\(https?://[^)\s]+\)")
MARKDOWN_LINK_FULL_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
PT_VOWEL_GROUP_RE = re.compile(r"[aeiouyáéíóúâêôãõàü]+")
EN_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")

CTA_INTENSITY_RANK: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
RANK_TO_CTA_INTENSITY: dict[int, str] = {v: k for k, v in CTA_INTENSITY_RANK.items()}

CTA_HIGH_RE = re.compile(
    r"\b(?:"
    r"compre agora|comprar agora|assine agora|contrate agora|"
    r"fale com vendas|falar com vendas|fale com um vendedor|time comercial|"
    r"agende uma demonstracao|agendar demonstracao|peca um orcamento|solicitar orcamento|"
    r"desconto exclusivo|oferta por tempo limitado|garanta sua vaga|"
    r"buy now|purchase now|subscribe now|sign up now|"
    r"book a demo|schedule a demo|request a quote|get a quote|"
    r"talk to sales|contact sales|speak with sales|start free trial|"
    r"limited[- ]time offer|exclusive discount|claim your discount"
    r")\b"
)

CTA_MEDIUM_RE = re.compile(
    r"\b(?:"
    r"demonstracao guiada|conheca o sistema|conheca a plataforma|solicitar avaliacao|"
    r"fale com um especialista|converse com um especialista|ver planos|"
    r"checklist operacional|planilha de diagnostico|"
    r"guided demo|schedule a walkthrough|talk to an expert|speak with a specialist|"
    r"implementation checklist|see pricing"
    r")\b"
)


class DeterministicCheckResult(BaseModel):
    """Structured outcome returned by a deterministic quality check function."""

    check: str
    passed: bool
    severity: Literal["critical", "major", "minor"] = "critical"
    responsible_module: Literal["research", "brief", "write"] = "write"
    detail: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_finding(self) -> Finding | None:
        """Convert failed check result into an evaluator Finding contract."""
        if self.passed:
            return None
        return Finding(
            criterion=self.check,
            severity=self.severity,
            detail=self.detail,
            responsible_module=self.responsible_module,
        )


def _normalize_doc_id(raw_id: Any) -> str | None:
    if raw_id is None:
        return None
    cleaned = str(raw_id).strip()
    if not cleaned:
        return None
    try:
        return str(uuid.UUID(cleaned))
    except ValueError:
        return cleaned


def _index_evidence_documents(
    evidence_documents: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    if evidence_documents is None:
        return None
    indexed: dict[str, dict[str, Any]] = {}
    items = (
        evidence_documents.items()
        if isinstance(evidence_documents, Mapping)
        else ((doc.get("id"), doc) for doc in evidence_documents)
    )
    for raw_key, doc in items:
        norm_key = _normalize_doc_id(doc.get("id") or raw_key)
        if norm_key is not None:
            indexed[norm_key] = dict(doc)
    return indexed


def _extract_body_urls(body_md: str) -> set[str]:
    return {match.group(0).rstrip(").,;'\"") for match in URL_IN_TEXT_RE.finditer(body_md)}


def _collect_cited_docs(
    draft: ContentDraft,
    docs_by_id: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    cited: dict[str, dict[str, Any]] = {}
    for claim in draft.claims:
        norm_id = _normalize_doc_id(claim.source_id)
        if norm_id is not None and norm_id in docs_by_id:
            cited[norm_id] = docs_by_id[norm_id]

    body_urls = _extract_body_urls(draft.body_md)
    if body_urls:
        for doc_id, doc in docs_by_id.items():
            doc_urls = {str(u).strip() for u in (doc.get("url"), doc.get("canonical_url")) if u}
            if doc_urls & body_urls:
                cited[doc_id] = doc
    return list(cited.items())


def _coerce_utc_datetime(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
    if isinstance(raw, date):
        return datetime.combine(raw, time.min, tzinfo=UTC)
    cleaned = str(raw).strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def check_fact_claims_have_source(
    draft: ContentDraft,
    evidence_documents: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None = None,
) -> DeterministicCheckResult:
    """Verify every fact claim has a source_id and exists in supplied evidence documents."""
    docs_by_id = _index_evidence_documents(evidence_documents)
    missing_indices: list[int] = []
    unknown_sources: list[str] = []

    for idx, claim in enumerate(draft.claims):
        norm_id = _normalize_doc_id(claim.source_id)
        if claim.kind == "fact" and norm_id is None:
            missing_indices.append(idx)
            continue
        if norm_id is not None and docs_by_id is not None and norm_id not in docs_by_id:
            unknown_sources.append(norm_id)

    issues: list[str] = []
    if missing_indices:
        issues.append(f"fact claims without source_id at indices {missing_indices}")
    if unknown_sources:
        issues.append(f"unknown cited source_ids {unknown_sources}")

    return DeterministicCheckResult(
        check="fact_sources",
        passed=not issues,
        severity="critical",
        responsible_module="write",
        detail=("All fact claims reference valid source IDs." if not issues else "; ".join(issues)),
        metadata={"missing_indices": missing_indices, "unknown_sources": unknown_sources},
    )


def _evaluate_doc_freshness(
    doc_id: str,
    doc: dict[str, Any],
    *,
    cutoff: datetime,
    max_allowed: datetime,
    max_age_days: int,
) -> str | None:
    date_status = str(doc.get("date_status") or "").strip().lower()
    if date_status == "unverified":
        return f"document '{doc_id}' has unverified date_status"
    pub_dt = _coerce_utc_datetime(doc.get("published_at"))
    if pub_dt is None:
        return f"document '{doc_id}' is missing a valid published_at date"
    if pub_dt < cutoff or pub_dt > max_allowed:
        return (
            f"document '{doc_id}' published_at {pub_dt.date().isoformat()} "
            f"exceeds {max_age_days}-day freshness window"
        )
    return None


def check_sources_fresh(
    draft: ContentDraft,
    evidence_documents: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None = None,
    *,
    max_age_days: int = DEFAULT_FRESHNESS_DAYS,
    reference_time: datetime | None = None,
) -> DeterministicCheckResult:
    """Verify all cited evidence documents have verified dates inside the freshness window."""
    docs_by_id = _index_evidence_documents(evidence_documents)
    if docs_by_id is None:
        return DeterministicCheckResult(
            check="source_freshness",
            passed=True,
            severity="critical",
            responsible_module="research",
            detail="Skipped freshness check: no evidence_documents supplied.",
            metadata={"skipped": True},
        )

    ref_now = _coerce_utc_datetime(reference_time) or datetime.now(UTC)
    cutoff = ref_now - timedelta(days=max_age_days)
    max_allowed = ref_now + timedelta(hours=24)

    failures: list[str] = []
    cited_docs = _collect_cited_docs(draft, docs_by_id)
    for doc_id, doc in cited_docs:
        failure = _evaluate_doc_freshness(
            doc_id,
            doc,
            cutoff=cutoff,
            max_allowed=max_allowed,
            max_age_days=max_age_days,
        )
        if failure is not None:
            failures.append(failure)

    return DeterministicCheckResult(
        check="source_freshness",
        passed=not failures,
        severity="critical",
        responsible_module="research",
        detail=(
            f"All {len(cited_docs)} cited source(s) fall inside the {max_age_days}-day window."
            if not failures
            else "; ".join(failures)
        ),
        metadata={"checked_count": len(cited_docs), "failures": failures},
    )


def check_cited_urls_fetched_ok(
    draft: ContentDraft,
    evidence_documents: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None = None,
) -> DeterministicCheckResult:
    """Verify every cited evidence document was fetched with status 'ok'."""
    docs_by_id = _index_evidence_documents(evidence_documents)
    if docs_by_id is None:
        return DeterministicCheckResult(
            check="cited_urls_fetched",
            passed=True,
            severity="critical",
            responsible_module="research",
            detail="Skipped fetch status check: no evidence_documents supplied.",
            metadata={"skipped": True},
        )

    failures: list[str] = []
    cited_docs = _collect_cited_docs(draft, docs_by_id)
    for doc_id, doc in cited_docs:
        status = str(doc.get("fetch_status") or "").strip().lower()
        if status != "ok":
            url_label = doc.get("canonical_url") or doc.get("url") or doc_id
            failures.append(f"cited source '{url_label}' has fetch_status='{status or 'missing'}'")

    return DeterministicCheckResult(
        check="cited_urls_fetched",
        passed=not failures,
        severity="critical",
        responsible_module="research",
        detail=(
            f"All {len(cited_docs)} cited source URL(s) have fetch_status='ok'."
            if not failures
            else "; ".join(failures)
        ),
        metadata={"checked_count": len(cited_docs), "failures": failures},
    )


def _extract_first_h1(body_md: str) -> str:
    for line in body_md.splitlines():
        match = H1_LINE_RE.match(line)
        if match:
            return match.group(1).strip()
    return ""


def _coerce_draft(draft: ContentDraft | Mapping[str, Any]) -> ContentDraft:
    return draft if isinstance(draft, ContentDraft) else ContentDraft.model_validate(draft)


def _keyword_boundary_pattern(norm_kw: str) -> re.Pattern[str]:
    prefix = r"\b" if norm_kw[0].isalnum() else ""
    suffix = r"\b" if norm_kw[-1].isalnum() else ""
    return re.compile(f"{prefix}{re.escape(norm_kw)}{suffix}")


def _contains_normalized_keyword(text: str, norm_kw: str) -> bool:
    norm_text = normalize_keyword(text)
    if not norm_kw or not norm_text:
        return False
    return _keyword_boundary_pattern(norm_kw).search(norm_text) is not None


def _extract_subheadings(body_md: str) -> list[str]:
    headings: list[str] = []
    for line in body_md.splitlines():
        match = SUBHEADING_LINE_RE.match(line)
        if match:
            headings.append(match.group(1).strip())
    return headings


def _extract_first_paragraph(body_md: str) -> str:
    for block in re.split(r"\n\s*\n", body_md.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        non_headings = [ln for ln in lines if not ln.startswith("#")]
        if non_headings:
            return " ".join(non_headings)
    return ""


def _classify_density(
    count: int,
    density: float,
    min_density: float,
    max_density: float,
) -> tuple[bool, Literal["major", "minor"], str]:
    if count == 0:
        return False, "major", "Primary keyword does not appear in draft body."
    if density > MAX_KEYWORD_DENSITY_MAJOR:
        return (
            False,
            "major",
            f"Keyword density {density:.2f}% exceeds {MAX_KEYWORD_DENSITY_MAJOR:.1f}% (stuffing).",
        )
    if density < min_density or density > max_density:
        return (
            False,
            "minor",
            (
                f"Keyword density {density:.2f}% is outside target "
                f"{min_density:.1f}%-{max_density:.1f}%."
            ),
        )
    return True, "minor", f"Keyword density {density:.2f}% is within target band."


def check_keyword_density(
    draft: ContentDraft | Mapping[str, Any],
    primary_keyword: str,
    *,
    min_density: float = DEFAULT_MIN_KEYWORD_DENSITY,
    max_density: float = DEFAULT_MAX_KEYWORD_DENSITY,
) -> DeterministicCheckResult:
    """Verify normalized primary keyword density falls within target percentage bounds."""
    draft_model = _coerce_draft(draft)
    norm_kw = normalize_keyword(primary_keyword)
    norm_body = normalize_keyword(draft_model.body_md)
    total_words = len(norm_body.split())
    if not norm_kw or total_words == 0:
        return DeterministicCheckResult(
            check="keyword_density",
            passed=False,
            severity="major",
            responsible_module="write",
            detail="Cannot compute keyword density with empty keyword or body.",
            metadata={"density": 0.0, "count": 0, "total_words": total_words},
        )
    count = len(_keyword_boundary_pattern(norm_kw).findall(norm_body))
    density = round((count / total_words) * 100.0, 2)
    passed, severity, detail = _classify_density(count, density, min_density, max_density)
    return DeterministicCheckResult(
        check="keyword_density",
        passed=passed,
        severity=severity,
        responsible_module="write",
        detail=detail,
        metadata={
            "density": density,
            "count": count,
            "total_words": total_words,
            "min_density": min_density,
            "max_density": max_density,
        },
    )


def check_keyword_in_first_paragraph(
    draft: ContentDraft | Mapping[str, Any],
    primary_keyword: str,
) -> DeterministicCheckResult:
    """Verify normalized primary keyword appears in the opening body paragraph."""
    draft_model = _coerce_draft(draft)
    norm_kw = normalize_keyword(primary_keyword)
    first_para = _extract_first_paragraph(draft_model.body_md)
    found = bool(norm_kw) and _contains_normalized_keyword(first_para, norm_kw)
    return DeterministicCheckResult(
        check="keyword_in_first_paragraph",
        passed=found,
        severity="minor",
        responsible_module="write",
        detail=(
            f"Primary keyword '{primary_keyword}' found in opening paragraph."
            if found
            else f"Primary keyword '{primary_keyword}' missing from opening paragraph."
        ),
        metadata={"found": found},
    )


def check_keyword_in_headings(
    draft: ContentDraft | Mapping[str, Any],
    primary_keyword: str,
) -> DeterministicCheckResult:
    """Verify normalized primary keyword appears in at least one H2/H3 subheading."""
    draft_model = _coerce_draft(draft)
    norm_kw = normalize_keyword(primary_keyword)
    headings = _extract_subheadings(draft_model.body_md)
    found = bool(norm_kw) and any(_contains_normalized_keyword(h, norm_kw) for h in headings)
    return DeterministicCheckResult(
        check="keyword_in_headings",
        passed=found,
        severity="minor",
        responsible_module="write",
        detail=(
            f"Primary keyword '{primary_keyword}' found in H2/H3 subheadings."
            if found
            else f"Primary keyword '{primary_keyword}' missing from H2/H3 subheadings."
        ),
        metadata={"found": found, "subheading_count": len(headings)},
    )


def check_subheading_density(
    draft: ContentDraft | Mapping[str, Any],
    *,
    words_per_subheading: int = DEFAULT_WORDS_PER_SUBHEADING,
) -> DeterministicCheckResult:
    """Verify long drafts include at least one H2/H3 subheading per words_per_subheading words."""
    draft_model = _coerce_draft(draft)
    word_count = len(draft_model.body_md.split())
    if word_count < words_per_subheading:
        return _skipped_result(
            "subheading_density",
            severity="minor",
            reason=f"draft under {words_per_subheading} words.",
        )
    headings = _extract_subheadings(draft_model.body_md)
    recommended = max(1, word_count // words_per_subheading)
    passed = len(headings) >= recommended
    return DeterministicCheckResult(
        check="subheading_density",
        passed=passed,
        severity="minor",
        responsible_module="write",
        detail=(
            f"Subheading density OK ({len(headings)} H2/H3 for {word_count} words)."
            if passed
            else (
                f"Only {len(headings)} H2/H3 subheadings for {word_count} words "
                f"(recommended >= {recommended})."
            )
        ),
        metadata={
            "subheading_count": len(headings),
            "recommended": recommended,
            "word_count": word_count,
        },
    )


def _strip_markdown_for_readability(body_md: str) -> str:
    prose_lines = [
        ln.lstrip("#").strip() for ln in body_md.splitlines() if not ln.strip().startswith("#")
    ]
    joined = " ".join(prose_lines)
    no_links = MARKDOWN_LINK_FULL_RE.sub(r"\1", joined)
    return re.sub(r"[*_`>]+", " ", no_links)


def count_syllables_word(word: str, *, lang: str = "pt") -> int:
    """Estimate syllable count in a single Portuguese or English word."""
    lowered = word.lower()
    if lang.startswith("pt"):
        cleaned = re.sub(r"[^a-zà-öø-ÿ]", "", lowered)
        return max(1, len(PT_VOWEL_GROUP_RE.findall(cleaned)))
    cleaned = re.sub(r"[^a-z]", "", lowered)
    if len(cleaned) <= 3:
        return 1
    trimmed = re.sub(r"e$", "", cleaned)
    return max(1, len(EN_VOWEL_GROUP_RE.findall(trimmed)))


def count_syllables(text: str, *, lang: str = "pt") -> int:
    """Count total syllables across alpha word tokens in text."""
    words = [tok for tok in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text) if tok]
    if not words:
        return 0
    return sum(count_syllables_word(word, lang=lang) for word in words)


def _classify_flesch(score: float) -> tuple[bool, Literal["major", "minor"], str]:
    if score >= FLESCH_PASS_MIN:
        return (
            True,
            "minor",
            f"Readability score {score:.1f} meets target (>= {FLESCH_PASS_MIN:.0f}).",
        )
    if score >= FLESCH_MINOR_MIN:
        return (
            False,
            "minor",
            f"Readability score {score:.1f} is moderately difficult (40-60); simplify sentences.",
        )
    return (
        False,
        "major",
        f"Readability score {score:.1f} is very difficult (< {FLESCH_MINOR_MIN:.0f}); simplify.",
    )


def check_readability(
    draft: ContentDraft | Mapping[str, Any],
    *,
    locale: str = "pt-BR",
    min_words_for_readability: int = MIN_WORDS_FOR_READABILITY,
) -> DeterministicCheckResult:
    """Evaluate bilingual readability (en-US Flesch or pt-BR Martins et al. Flesch)."""
    draft_model = _coerce_draft(draft)
    prose = _strip_markdown_for_readability(draft_model.body_md)
    lang = "pt" if locale.lower().startswith("pt") else "en"
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", prose)
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(prose) if s.strip()]
    if len(words) < min_words_for_readability or len(sentences) < MIN_SENTENCES_FOR_READABILITY:
        return _skipped_result(
            "readability",
            severity="minor",
            reason=(
                f"prose has fewer than {min_words_for_readability} words "
                f"or {MIN_SENTENCES_FOR_READABILITY} sentences."
            ),
        )

    word_count = len(words)
    sentence_count = len(sentences)
    syllable_count = sum(count_syllables_word(w, lang=lang) for w in words)
    base = FLESCH_BASE_PT_MARTINS if lang == "pt" else FLESCH_BASE_EN
    formula = "flesch_martins_pt" if lang == "pt" else "flesch_en"
    raw_score = base - 1.015 * (word_count / sentence_count) - 84.6 * (syllable_count / word_count)
    score = round(max(0.0, min(100.0, raw_score)), 1)
    passed, severity, detail = _classify_flesch(score)
    return DeterministicCheckResult(
        check="readability",
        passed=passed,
        severity=severity,
        responsible_module="write",
        detail=detail,
        metadata={
            "score": score,
            "formula": formula,
            "locale": locale,
            "words": word_count,
            "sentences": sentence_count,
            "syllables": syllable_count,
        },
    )


def check_primary_keyword_placement(
    draft: ContentDraft,
    primary_keyword: str,
) -> DeterministicCheckResult:
    """Verify primary keyword appears in title, H1 heading, and meta description."""
    norm_kw = normalize_keyword(primary_keyword)
    if not norm_kw:
        return DeterministicCheckResult(
            check="primary_keyword_placement",
            passed=False,
            severity="major",
            responsible_module="write",
            detail="Primary keyword cannot be blank.",
            metadata={"missing_slots": ["title", "h1", "meta"]},
        )

    h1_text = _extract_first_h1(draft.body_md)
    slot_checks = {
        "title": _contains_normalized_keyword(draft.title, norm_kw),
        "h1": _contains_normalized_keyword(h1_text, norm_kw),
        "meta": _contains_normalized_keyword(draft.meta_description, norm_kw),
    }
    missing = [slot for slot, present in slot_checks.items() if not present]

    return DeterministicCheckResult(
        check="primary_keyword_placement",
        passed=not missing,
        severity="major",
        responsible_module="write",
        detail=(
            f"Primary keyword '{primary_keyword}' present in title, H1, and meta."
            if not missing
            else f"Primary keyword '{primary_keyword}' missing from: {', '.join(missing)}"
        ),
        metadata={"missing_slots": missing, **slot_checks},
    )


def check_meta_length(
    draft: ContentDraft,
    *,
    min_length: int = MIN_META_LENGTH,
    max_length: int = MAX_META_LENGTH,
) -> DeterministicCheckResult:
    """Verify stripped meta_description length stays within [min_length, max_length]."""
    length = len(draft.meta_description.strip())
    passed = min_length <= length <= max_length
    return DeterministicCheckResult(
        check="meta_length",
        passed=passed,
        severity="major",
        responsible_module="write",
        detail=(
            f"Meta description length {length} is within [{min_length}, {max_length}]."
            if passed
            else f"Meta description length {length} outside [{min_length}, {max_length}]."
        ),
        metadata={"length": length, "min_length": min_length, "max_length": max_length},
    )


def check_slug_format(
    draft: ContentDraft,
    *,
    max_length: int = MAX_SLUG_LENGTH,
) -> DeterministicCheckResult:
    """Verify draft slug matches lowercase URL-safe kebab-case rules."""
    slug = draft.slug
    passed = 1 <= len(slug) <= max_length and SLUG_RE.fullmatch(slug) is not None
    return DeterministicCheckResult(
        check="slug_format",
        passed=passed,
        severity="critical",
        responsible_module="write",
        detail=(
            f"Slug '{slug}' matches lowercase kebab-case format."
            if passed
            else f"Invalid slug '{slug}': expected lowercase kebab-case len 1..{max_length}."
        ),
        metadata={"slug": slug, "length": len(slug), "max_length": max_length},
    )


def _extract_prohibited_patterns(
    prohibited_phrases: Sequence[str] | BrandProfile | Mapping[str, Any] | None,
) -> list[str]:
    if prohibited_phrases is None:
        return []
    if isinstance(prohibited_phrases, BrandProfile):
        return list(prohibited_phrases.prohibited_phrases)
    if isinstance(prohibited_phrases, Mapping):
        raw = prohibited_phrases.get("prohibited_phrases") or []
        return [str(p).strip() for p in raw if str(p).strip()]
    return [str(p).strip() for p in prohibited_phrases if str(p).strip()]


def _draft_text_surfaces(draft: ContentDraft) -> list[tuple[str, str]]:
    faq_text = " ".join(
        f"{item.get('question', '')} {item.get('answer', '')}" for item in draft.faq
    )
    return [
        ("title", draft.title),
        ("meta_description", draft.meta_description),
        ("slug", draft.slug),
        ("body_md", draft.body_md),
        ("faq", faq_text),
        ("cta", " ".join(str(v) for v in draft.cta.values())),
        ("claims", " ".join(c.text for c in draft.claims)),
    ]


def check_no_prohibited_phrases(
    draft: ContentDraft,
    prohibited_phrases: Sequence[str] | BrandProfile | Mapping[str, Any] | None = None,
) -> DeterministicCheckResult:
    """Verify no brand prohibited regex pattern appears across any draft surface."""
    patterns = _extract_prohibited_patterns(prohibited_phrases)
    matches: list[str] = []
    surfaces = _draft_text_surfaces(draft)

    for pattern in patterns:
        compiled = re.compile(pattern, re.IGNORECASE)
        for surface_name, text in surfaces:
            if compiled.search(text):
                matches.append(f"{surface_name}:{pattern}")

    return DeterministicCheckResult(
        check="prohibited_phrases",
        passed=not matches,
        severity="critical",
        responsible_module="write",
        detail=(
            "Draft contains no prohibited brand phrases."
            if not matches
            else f"Prohibited brand phrases matched: {', '.join(matches)}"
        ),
        metadata={"matches": matches},
    )


def _resolve_cta_limit(
    funnel_stage: str,
    campaign_config: CampaignConfig | Mapping[str, Any] | None,
    cta_intensity_limits: Mapping[str, str] | None,
) -> str:
    default_limits = CampaignConfig().cta_intensity_limits
    raw_limits: Mapping[str, str] = default_limits
    if cta_intensity_limits is not None:
        raw_limits = cta_intensity_limits
    elif isinstance(campaign_config, CampaignConfig):
        raw_limits = campaign_config.cta_intensity_limits
    elif isinstance(campaign_config, Mapping) and isinstance(
        campaign_config.get("cta_intensity_limits"), Mapping
    ):
        raw_limits = campaign_config["cta_intensity_limits"]

    normalized_limits = {
        str(k).strip().upper(): str(v).strip().lower() for k, v in raw_limits.items()
    }
    stage_key = funnel_stage.strip().upper() or "MOFU"
    candidate = normalized_limits.get(stage_key, default_limits.get(stage_key, "medium"))
    return candidate if candidate in CTA_INTENSITY_RANK else "medium"


def _classify_cta_intensity(draft: ContentDraft) -> str:
    explicit = normalize_keyword(str(draft.cta.get("intensity") or ""))
    explicit_rank = CTA_INTENSITY_RANK.get(explicit, 0)

    cta_text = normalize_keyword(
        " ".join(str(v) for k, v in draft.cta.items() if k != "intensity" and v)
    )
    body_text = normalize_keyword(draft.body_md)

    text_rank = 0
    if CTA_HIGH_RE.search(cta_text) or CTA_HIGH_RE.search(body_text):
        text_rank = CTA_INTENSITY_RANK["high"]
    elif CTA_MEDIUM_RE.search(cta_text):
        text_rank = CTA_INTENSITY_RANK["medium"]
    elif cta_text:
        text_rank = CTA_INTENSITY_RANK["low"]

    return RANK_TO_CTA_INTENSITY[max(explicit_rank, text_rank)]


def check_cta_intensity(
    draft: ContentDraft,
    *,
    funnel_stage: str = "mofu",
    campaign_config: CampaignConfig | Mapping[str, Any] | None = None,
    cta_intensity_limits: Mapping[str, str] | None = None,
) -> DeterministicCheckResult:
    """Verify CTA aggressiveness does not exceed campaign limit for funnel_stage."""
    limit = _resolve_cta_limit(funnel_stage, campaign_config, cta_intensity_limits)
    detected = _classify_cta_intensity(draft)
    passed = CTA_INTENSITY_RANK[detected] <= CTA_INTENSITY_RANK[limit]

    return DeterministicCheckResult(
        check="cta_intensity",
        passed=passed,
        severity="critical",
        responsible_module="write",
        detail=(
            f"CTA intensity '{detected}' is within '{limit}' limit for stage '{funnel_stage}'."
            if passed
            else (
                f"CTA intensity '{detected}' exceeds campaign limit '{limit}' "
                f"for stage '{funnel_stage}'."
            )
        ),
        metadata={
            "detected": detected,
            "limit": limit,
            "funnel_stage": funnel_stage.lower(),
        },
    )


def _extract_language_sample(draft: ContentDraft) -> str:
    faq_bits = [f"{item.get('question', '')} {item.get('answer', '')}" for item in draft.faq]
    cta_bits = [str(v) for k, v in draft.cta.items() if k != "intensity" and v]
    raw_text = "\n".join([draft.title, draft.meta_description, draft.body_md, *faq_bits, *cta_bits])
    without_urls = MARKDOWN_LINK_URL_RE.sub(" ", raw_text)
    without_raw_urls = URL_IN_TEXT_RE.sub(" ", without_urls)
    return re.sub(r"[#*`>_]+", " ", without_raw_urls).strip()


def check_language_matches(
    draft: ContentDraft,
    *,
    expected_locale: str = "pt-BR",
) -> DeterministicCheckResult:
    """Verify draft language matches campaign locale via lingua-py restricted to pt/en."""
    expected_lang = expected_locale.strip().lower().replace("_", "-").split("-")[0]
    if expected_lang not in {"pt", "en"}:
        return DeterministicCheckResult(
            check="language_match",
            passed=False,
            severity="critical",
            responsible_module="write",
            detail=f"Unsupported campaign locale '{expected_locale}'; expected pt or en.",
            metadata={"expected": expected_lang, "detected": None},
        )

    sample = _extract_language_sample(draft)
    detected = detect_language(sample)
    passed = detected == expected_lang

    return DeterministicCheckResult(
        check="language_match",
        passed=passed,
        severity="critical",
        responsible_module="write",
        detail=(
            f"Detected language '{detected}' matches campaign locale '{expected_locale}'."
            if passed
            else (
                f"Language mismatch: detected '{detected}' for campaign locale "
                f"'{expected_locale}' (expected '{expected_lang}')."
            )
        ),
        metadata={"expected": expected_lang, "detected": detected},
    )


def check_word_count(
    draft: ContentDraft | Mapping[str, Any],
    *,
    min_words: int | None = None,
    max_words: int | None = None,
) -> DeterministicCheckResult:
    """Verify draft body word count falls within format bounds when supplied."""
    if min_words is None and max_words is None:
        return _skipped_result(
            "word_count",
            severity="critical",
            reason="no format word-count bounds supplied.",
        )
    draft_model = draft if isinstance(draft, ContentDraft) else ContentDraft.model_validate(draft)
    word_count = len(draft_model.body_md.split())
    meta: dict[str, Any] = {
        "word_count": word_count,
        "min_words": min_words,
        "max_words": max_words,
    }
    if min_words is not None and word_count < min_words:
        return DeterministicCheckResult(
            check="word_count",
            passed=False,
            severity="critical",
            responsible_module="write",
            detail=f"Word count {word_count} is below minimum {min_words} for format.",
            metadata=meta,
        )
    if max_words is not None and word_count > max_words:
        return DeterministicCheckResult(
            check="word_count",
            passed=False,
            severity="major",
            responsible_module="write",
            detail=f"Word count {word_count} exceeds maximum {max_words} for format.",
            metadata=meta,
        )
    return DeterministicCheckResult(
        check="word_count",
        passed=True,
        severity="critical",
        responsible_module="write",
        detail=f"Word count {word_count} is within format bounds.",
        metadata=meta,
    )


class DeterministicRunOptions(BaseModel):
    """Optional threshold and clock overrides for deterministic checks."""

    model_config = ConfigDict(extra="forbid")

    max_age_days: int = DEFAULT_FRESHNESS_DAYS
    reference_time: datetime | None = None
    meta_min_length: int = MIN_META_LENGTH
    meta_max_length: int = MAX_META_LENGTH
    slug_max_length: int = MAX_SLUG_LENGTH
    min_words: int | None = None
    max_words: int | None = None
    check_extended_seo: bool = False


class DeterministicReport(BaseModel):
    """Aggregated deterministic check report returned prior to LLM evaluation (F14 AC1)."""

    schema_version: int = 1
    verdict: Literal["PASS", "REVISION"]
    all_passed: bool
    critical_passed: bool
    checks: dict[str, DeterministicCheckResult]
    findings: list[Finding] = Field(default_factory=list)

    def to_quality_report(self) -> QualityReport:
        """Convert deterministic run results into a QualityReport contract."""
        return QualityReport(
            schema_version=self.schema_version,
            verdict=self.verdict,
            findings=list(self.findings),
            score=None,
        )


def _resolve_primary_keyword(
    primary_keyword: str | None,
    brief: EditorialBrief | Mapping[str, Any] | None,
) -> str | None:
    if primary_keyword is not None:
        return primary_keyword
    if isinstance(brief, EditorialBrief):
        return brief.primary_keyword
    if isinstance(brief, Mapping) and "primary_keyword" in brief:
        return str(brief["primary_keyword"])
    return None


def _skipped_result(
    check_name: str,
    *,
    severity: Literal["critical", "major", "minor"],
    reason: str,
) -> DeterministicCheckResult:
    return DeterministicCheckResult(
        check=check_name,
        passed=True,
        severity=severity,
        responsible_module="write",
        detail=f"Skipped {check_name}: {reason}",
        metadata={"skipped": True},
    )


def _word_shingles(text: str, width: int = 3) -> set[tuple[str, ...]]:
    tokens = [t for t in normalize_keyword(text).split() if t]
    if len(tokens) < width:
        return {(tok,) for tok in tokens}
    return {tuple(tokens[i : i + width]) for i in range(len(tokens) - width + 1)}


def check_adaptation_uniqueness(
    source_draft: ContentDraft,
    adapted_draft: ContentDraft,
    *,
    max_similarity: float = 0.65,
) -> DeterministicCheckResult:
    """Verify adapted draft has distinct slug/title and low body shingle overlap."""
    same_slug = source_draft.slug.strip().lower() == adapted_draft.slug.strip().lower()
    same_title = normalize_keyword(source_draft.title) == normalize_keyword(adapted_draft.title)
    src_shingles = _word_shingles(source_draft.body_md)
    dst_shingles = _word_shingles(adapted_draft.body_md)
    union_count = len(src_shingles | dst_shingles)
    similarity = round(len(src_shingles & dst_shingles) / union_count, 4) if union_count else 1.0
    passed = not same_slug and not same_title and similarity <= max_similarity
    reasons = [
        label
        for flag, label in (
            (same_slug, "identical slug"),
            (same_title, "identical title"),
            (
                similarity > max_similarity,
                f"body similarity {similarity:.2f} > {max_similarity:.2f}",
            ),
        )
        if flag
    ]
    detail = (
        f"Adapted draft is distinct (shingle similarity {similarity:.2f} <= {max_similarity:.2f})."
        if passed
        else f"Adapted draft failed anti-cannibalization gate: {', '.join(reasons)}."
    )
    return DeterministicCheckResult(
        check="adaptation_uniqueness",
        passed=passed,
        severity="critical",
        responsible_module="write",
        detail=detail,
        metadata={
            "similarity": similarity,
            "max_similarity": max_similarity,
            "same_slug": same_slug,
            "same_title": same_title,
        },
    )


def _run_extended_seo_checks(
    draft_model: ContentDraft,
    resolved_kw: str | None,
    *,
    locale: str = "pt-BR",
) -> list[DeterministicCheckResult]:
    if resolved_kw is None:
        kw_results = [
            _skipped_result(name, severity="minor", reason="no brief or primary_keyword supplied.")
            for name in ("keyword_density", "keyword_in_first_paragraph", "keyword_in_headings")
        ]
    else:
        kw_results = [
            check_keyword_density(draft_model, resolved_kw),
            check_keyword_in_first_paragraph(draft_model, resolved_kw),
            check_keyword_in_headings(draft_model, resolved_kw),
        ]
    return [
        *kw_results,
        check_subheading_density(draft_model),
        check_readability(draft_model, locale=locale),
    ]


def run(  # noqa: PLR0913
    draft: ContentDraft | Mapping[str, Any],
    *,
    brief: EditorialBrief | Mapping[str, Any] | None = None,
    evidence_documents: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None = None,
    brand_profile: BrandProfile | Mapping[str, Any] | None = None,
    campaign_config: CampaignConfig | Mapping[str, Any] | None = None,
    locale: str = "pt-BR",
    primary_keyword: str | None = None,
    funnel_stage: str = "mofu",
    options: DeterministicRunOptions | None = None,
) -> DeterministicReport:
    """Execute all deterministic checks against a draft and return Verdict PASS/REVISION."""
    draft_model = draft if isinstance(draft, ContentDraft) else ContentDraft.model_validate(draft)
    opts = options or DeterministicRunOptions()
    resolved_kw = _resolve_primary_keyword(primary_keyword, brief)

    kw_result = (
        check_primary_keyword_placement(draft_model, resolved_kw)
        if resolved_kw is not None
        else _skipped_result(
            "primary_keyword_placement",
            severity="major",
            reason="no brief or primary_keyword supplied.",
        )
    )
    phrase_result = (
        check_no_prohibited_phrases(draft_model, brand_profile)
        if brand_profile is not None
        else _skipped_result(
            "prohibited_phrases",
            severity="critical",
            reason="no brand_profile supplied.",
        )
    )

    ordered_results = [
        check_fact_claims_have_source(draft_model, evidence_documents),
        check_sources_fresh(
            draft_model,
            evidence_documents,
            max_age_days=opts.max_age_days,
            reference_time=opts.reference_time,
        ),
        check_cited_urls_fetched_ok(draft_model, evidence_documents),
        kw_result,
        check_meta_length(
            draft_model,
            min_length=opts.meta_min_length,
            max_length=opts.meta_max_length,
        ),
        check_slug_format(draft_model, max_length=opts.slug_max_length),
        phrase_result,
        check_cta_intensity(
            draft_model,
            funnel_stage=funnel_stage,
            campaign_config=campaign_config,
        ),
        check_language_matches(draft_model, expected_locale=locale),
    ]
    if opts.min_words is not None or opts.max_words is not None:
        ordered_results.append(
            check_word_count(
                draft_model,
                min_words=opts.min_words,
                max_words=opts.max_words,
            )
        )
    if opts.check_extended_seo:
        ordered_results.extend(_run_extended_seo_checks(draft_model, resolved_kw, locale=locale))

    findings: list[Finding] = []
    for res in ordered_results:
        finding = res.to_finding()
        if finding is not None:
            findings.append(finding)

    critical_passed = not any(
        not res.passed and res.severity == "critical" for res in ordered_results
    )
    return DeterministicReport(
        verdict="PASS" if critical_passed else "REVISION",
        all_passed=all(res.passed for res in ordered_results),
        critical_passed=critical_passed,
        checks={res.check: res for res in ordered_results},
        findings=findings,
    )
