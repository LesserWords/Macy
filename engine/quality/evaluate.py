"""LLM rubric quality evaluator, isolated prompting, and persistence gate (F14, ADR-002)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg
from pydantic import BaseModel

from engine import config, db, events, llm
from engine.config import FormatPack, RubricCriterion
from engine.contracts import (
    BrandProfile,
    ContentDraft,
    EditorialBrief,
    Finding,
    QualityReport,
)
from engine.integrations.fakes import FakeLLM
from engine.integrations.ports import LLMPort
from engine.quality import checks

EvaluatorFn = Callable[..., QualityReport]
RouteTarget = Literal["pass", "write", "brief", "research"]

PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "evaluate.md"
EVALUATE_VERSION_PREFIX = "evaluate.md"
DEFAULT_PASS_SCORE = 70.0
MAX_EVAL_REVISION = 2

_ALLOWED_DRAFT_KEYS = ("title", "meta_description", "slug", "body_md", "faq", "cta", "claims")
_ALLOWED_BRIEF_KEYS = (
    "topic",
    "target_audience",
    "primary_keyword",
    "secondary_keywords",
    "format",
    "angle",
    "outline",
    "required_facts",
    "cta_goal",
)
_ALLOWED_DOC_KEYS = (
    "id",
    "title",
    "source_name",
    "source_role",
    "url",
    "published_at",
    "date_status",
    "fetch_status",
    "summary",
    "facts",
    "extracted_text",
)
_SEVERITY_TIERS: tuple[str, ...] = ("critical", "major", "minor")
_MODULE_PRIORITY: dict[str, int] = {"research": 0, "brief": 1, "write": 2}


@dataclass(frozen=True)
class EvalExecutionSpec:
    """Bundled configuration for single-draft persisted evaluation."""

    workspace_id: uuid.UUID
    draft_id: uuid.UUID
    offline: bool = False
    llm_adapter: LLMPort | None = None
    llm_evaluator: EvaluatorFn | None = None
    model: str | None = None
    actor_user_id: uuid.UUID | None = None
    options: checks.DeterministicRunOptions | None = None
    deterministic_only: bool = False


def _resolve_word_bounds(
    format_slug: str,
    options: checks.DeterministicRunOptions | None = None,
) -> checks.DeterministicRunOptions:
    """Populate min_words/max_words from format pack unless already set in options."""
    base = options or checks.DeterministicRunOptions()
    if base.min_words is not None or base.max_words is not None:
        return base
    try:
        fmt = config.load_format_pack(format_slug)
    except (FileNotFoundError, ValueError, config.PackValidationError):
        return base
    return base.model_copy(update={"min_words": fmt.min_words, "max_words": fmt.max_words})


def get_evaluate_prompt_and_version() -> tuple[str, str]:
    """Load prompts/evaluate.md and return (content, deterministic sha256 tag)."""
    if not PROMPT_FILE.exists():
        msg = f"Evaluator prompt template missing at {PROMPT_FILE}"
        raise FileNotFoundError(msg)
    body = PROMPT_FILE.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return body, f"{EVALUATE_VERSION_PREFIX}:{digest}"


def load_rubric(
    rubric: Sequence[RubricCriterion | Mapping[str, Any]] | Mapping[str, Any] | None = None,
    *,
    format_slug: str = "blog",
) -> list[RubricCriterion]:
    """Resolve weighted format rubric from override or packs/formats/<slug>/rubric.yaml."""
    if rubric is None:
        return config.load_format_pack(format_slug).rubric
    raw_items = rubric["rubric"] if isinstance(rubric, Mapping) else rubric
    return [
        item if isinstance(item, RubricCriterion) else RubricCriterion.model_validate(item)
        for item in raw_items
    ]


def _dump_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, Mapping) else {}


def build_evaluate_system_prompt(  # noqa: PLR0913
    *,
    base_prompt: str,
    rubric: Sequence[RubricCriterion],
    brand_profile: BrandProfile | Mapping[str, Any] | None = None,
    brand_version: int = 1,
    format_pack: FormatPack | Mapping[str, Any] | None = None,
    industry_pack: Mapping[str, Any] | None = None,
) -> str:
    """Assemble isolated cacheable evaluator system prompt without writer instructions."""
    rubric_lines = [
        f"- {c.criterion}: weight={c.weight}%, critical={str(c.critical).lower()}" for c in rubric
    ]
    fmt_map = _dump_mapping(format_pack) or {"slug": "blog", "min_words": 800, "max_words": 2500}
    sections: list[str] = [
        base_prompt.strip(),
        "## Weighted Evaluation Rubric",
        "\n".join(rubric_lines),
        "## Target Format Constraints",
        (
            f"- format={fmt_map.get('slug', 'blog')}, "
            f"word_range={fmt_map.get('min_words', 800)}-{fmt_map.get('max_words', 2500)}"
        ),
    ]
    brand_map = _dump_mapping(brand_profile)
    if brand_map:
        sections.extend(
            [
                f"## Brand Compliance Guardrails (v{brand_version})",
                f"- Voice target: {brand_map.get('voice', '')}",
                f"- Approved capabilities: {', '.join(brand_map.get('capabilities', []))}",
                f"- Prohibited claims: {', '.join(brand_map.get('prohibited_claims', []))}",
                f"- Forbidden patterns: {', '.join(brand_map.get('prohibited_phrases', []))}",
                f"- CTA policy: {' | '.join(brand_map.get('cta_rules', []))}",
            ]
        )
    if industry_pack:
        sections.append(
            f"## Industry Domain\n- {industry_pack.get('name', industry_pack.get('slug', ''))}"
        )
    return "\n\n".join(sections)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _sanitize_eval_brief(brief: EditorialBrief | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if brief is None:
        return None
    raw = brief.model_dump(mode="json") if isinstance(brief, EditorialBrief) else dict(brief)
    return {k: _json_safe_value(raw[k]) for k in _ALLOWED_BRIEF_KEYS if k in raw}


def _sanitize_eval_evidence(
    evidence: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if evidence is None:
        return []
    docs = evidence.values() if isinstance(evidence, Mapping) else evidence
    return [
        {k: _json_safe_value(raw_doc[k]) for k in _ALLOWED_DOC_KEYS if k in raw_doc}
        for raw_doc in docs
    ]


def _prepare_evaluate_user_input(  # noqa: PLR0913
    *,
    draft_model: ContentDraft,
    brief: EditorialBrief | Mapping[str, Any] | None,
    evidence: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None,
    rubric: Sequence[RubricCriterion],
    det_report: checks.DeterministicReport,
    brand_profile: BrandProfile | Mapping[str, Any] | None,
    locale: str,
    funnel_stage: str,
) -> dict[str, Any]:
    """Build sanitized user JSON containing zero writer prompt or reasoning traces."""
    raw_draft = draft_model.model_dump(mode="json")
    clean_draft = {k: raw_draft[k] for k in _ALLOWED_DRAFT_KEYS if k in raw_draft}
    return {
        "locale": locale,
        "funnel_stage": funnel_stage,
        "draft": clean_draft,
        "brief": _sanitize_eval_brief(brief),
        "evidence_documents": _sanitize_eval_evidence(evidence),
        "brand_profile": _dump_mapping(brand_profile),
        "rubric": [c.model_dump(mode="json") for c in rubric],
        "deterministic_findings": [f.model_dump(mode="json") for f in det_report.findings],
    }


def _normalize_finding(item: Finding) -> Finding:
    sev = item.severity.strip().lower()
    norm_sev = sev if sev in {"critical", "major", "minor"} else "major"
    mod = item.responsible_module.strip().lower()
    norm_mod = mod if mod in {"research", "brief", "write"} else "write"
    return Finding(
        criterion=item.criterion,
        severity=norm_sev,
        detail=item.detail,
        responsible_module=norm_mod,
    )


def determine_revision_route(report: QualityReport) -> RouteTarget:
    """Resolve dominant action route: highest severity tier first, upstream-first tie-break."""
    verdict = report.verdict.strip().upper()
    if verdict == "PASS":
        return "pass"
    if verdict == "REJECT":
        return "research"
    for tier in _SEVERITY_TIERS:
        tier_modules = [
            f.responsible_module for f in report.findings if f.severity.strip().lower() == tier
        ]
        if tier_modules:
            chosen = min(tier_modules, key=lambda m: _MODULE_PRIORITY.get(m, 9))
            if chosen in {"research", "brief", "write"}:
                return chosen  # type: ignore[return-value]
    return "write"


def _merge_findings(
    raw_findings: Sequence[Finding],
    det_findings: Sequence[Finding],
) -> list[Finding]:
    merged = [_normalize_finding(f) for f in raw_findings]
    seen_criteria = {f.criterion for f in merged}
    return [
        *merged,
        *(df for df in det_findings if df.criterion not in seen_criteria),
    ]


def _has_critical_finding(merged: list[Finding], critical_criteria: set[str]) -> bool:
    return any(
        f.severity == "critical" or (f.criterion in critical_criteria and f.severity != "minor")
        for f in merged
    )


def _coerce_verdict_and_score(
    raw_verdict: str,
    raw_score: float | None,
    merged: list[Finding],
    critical_criteria: set[str],
) -> tuple[str, float | None]:
    score = max(0.0, min(100.0, round(float(raw_score), 2))) if raw_score is not None else None
    verdict = raw_verdict.strip().upper()
    if verdict not in {"PASS", "REVISION", "REJECT"}:
        verdict = "REVISION"
    needs_downgrade = _has_critical_finding(merged, critical_criteria) or (
        score is not None and score < DEFAULT_PASS_SCORE
    )
    if verdict == "PASS" and needs_downgrade:
        has_research = any(f.responsible_module == "research" for f in merged)
        verdict = "REJECT" if has_research else "REVISION"
        if score is not None and score >= DEFAULT_PASS_SCORE:
            score = 60.0
    return verdict, score


def _normalize_quality_report(
    raw_report: QualityReport,
    *,
    rubric: Sequence[RubricCriterion],
    det_report: checks.DeterministicReport,
) -> QualityReport:
    merged = _merge_findings(raw_report.findings, det_report.findings)
    critical_criteria = {c.criterion for c in rubric if c.critical}
    verdict, score = _coerce_verdict_and_score(
        raw_report.verdict, raw_report.score, merged, critical_criteria
    )
    if verdict in {"REVISION", "REJECT"} and not merged:
        merged.append(
            Finding(
                criterion="overall_quality",
                severity="critical",
                detail=f"Evaluator returned {verdict} without itemized findings.",
                responsible_module="research" if verdict == "REJECT" else "write",
            )
        )
    return QualityReport(
        schema_version=raw_report.schema_version,
        verdict=verdict,
        findings=merged,
        score=score,
    )


def _load_optional_format_and_industry(
    format_slug: str,
    industry_slug: str | None,
    workspace_config: Mapping[str, Any] | None = None,
) -> tuple[FormatPack | None, dict[str, Any] | None]:
    try:
        fmt = config.load_format_pack(format_slug)
    except config.PackValidationError:
        fmt = None
    ind = (
        config.resolve_industry_pack_dict(industry_slug, workspace_config)
        if industry_slug
        else None
    )
    return fmt, ind


def _resolve_model_label(adapter: LLMPort | None, explicit_model: str | None) -> str:
    if explicit_model:
        return explicit_model
    settings_obj = getattr(adapter, "settings", None)
    stage_map = getattr(settings_obj, "models_by_stage", None)
    if isinstance(stage_map, dict) and stage_map.get("evaluate"):
        return str(stage_map["evaluate"])
    return str(
        getattr(settings_obj, "model_name", None)
        or getattr(settings_obj, "default_model", "fake-llm-v1")
    )


def _run_llm_rubric_call(  # noqa: PLR0913
    *,
    draft_model: ContentDraft,
    brief: EditorialBrief | Mapping[str, Any] | None,
    evidence: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None,
    rubric_list: list[RubricCriterion],
    det_report: checks.DeterministicReport,
    brand_profile: BrandProfile | Mapping[str, Any] | None,
    brand_version: int,
    locale: str,
    funnel_stage: str,
    format_slug: str,
    industry_slug: str | None,
    workspace_config: Mapping[str, Any] | None = None,
    llm_adapter: LLMPort | None = None,
    conn: psycopg.Connection[Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    model: str | None = None,
) -> tuple[QualityReport, str, str]:
    base_prompt, prompt_ver = get_evaluate_prompt_and_version()
    fmt_pack, ind_pack = _load_optional_format_and_industry(
        format_slug, industry_slug, workspace_config
    )
    system_prompt = build_evaluate_system_prompt(
        base_prompt=base_prompt,
        rubric=rubric_list,
        brand_profile=brand_profile,
        brand_version=brand_version,
        format_pack=fmt_pack,
        industry_pack=ind_pack,
    )
    eval_input = _prepare_evaluate_user_input(
        draft_model=draft_model,
        brief=brief,
        evidence=evidence,
        rubric=rubric_list,
        det_report=det_report,
        brand_profile=brand_profile,
        locale=locale,
        funnel_stage=funnel_stage,
    )
    resolved_adapter = llm_adapter or (
        FakeLLM() if conn is None else llm._resolve_llm_adapter(conn, workspace_id, None)
    )
    raw_report: QualityReport = llm.structured_call(
        stage="evaluate",
        model_cls=QualityReport,
        prompt=system_prompt,
        input=eval_input,
        workspace_id=workspace_id,
        run_id=run_id,
        conn=conn,
        plugin=resolved_adapter,
        reasoning_effort="high",
        prompt_version=prompt_ver,
        model=model,
    )
    normalized = _normalize_quality_report(raw_report, rubric=rubric_list, det_report=det_report)
    resolved_model = _resolve_model_label(resolved_adapter, model)
    return normalized, resolved_model, prompt_ver


def evaluate(  # noqa: PLR0913
    draft: ContentDraft | Mapping[str, Any],
    brief: EditorialBrief | Mapping[str, Any] | None = None,
    evidence: Sequence[dict[str, Any]] | Mapping[str, dict[str, Any]] | None = None,
    rubric: Sequence[RubricCriterion | Mapping[str, Any]] | Mapping[str, Any] | None = None,
    *,
    llm_adapter: LLMPort | None = None,
    llm_evaluator: EvaluatorFn | None = None,
    deterministic_only: bool = False,
    **check_kwargs: Any,
) -> QualityReport:
    """Evaluate draft: run deterministic checks first, then isolated LLM rubric."""
    model = check_kwargs.pop("model", None)
    industry_slug = check_kwargs.pop("industry_pack", None)
    brand_version = int(check_kwargs.pop("brand_version", 1))
    explicit_fmt = check_kwargs.pop("format_slug", None)
    draft_model = draft if isinstance(draft, ContentDraft) else ContentDraft.model_validate(draft)

    brief_map = _dump_mapping(brief)
    has_explicit_brief_format = bool(brief_map.get("format"))
    fmt_slug = str(explicit_fmt or brief_map.get("format") or "blog")
    raw_options = check_kwargs.pop("options", None)
    if explicit_fmt is not None or has_explicit_brief_format:
        resolved_options = _resolve_word_bounds(fmt_slug, raw_options)
    else:
        resolved_options = raw_options

    det_report = checks.run(
        draft_model,
        brief=brief,
        evidence_documents=evidence,
        options=resolved_options,
        **check_kwargs,
    )
    if det_report.verdict != "PASS" or deterministic_only:
        return det_report.to_quality_report()

    rubric_list = load_rubric(rubric, format_slug=fmt_slug)

    if llm_evaluator is not None:
        raw_report = llm_evaluator(
            draft=draft_model,
            brief=brief,
            evidence=evidence,
            rubric=rubric_list,
            deterministic_report=det_report,
            llm_adapter=llm_adapter,
        )
        return _normalize_quality_report(raw_report, rubric=rubric_list, det_report=det_report)

    report, _model_name, _prompt_ver = _run_llm_rubric_call(
        draft_model=draft_model,
        brief=brief,
        evidence=evidence,
        rubric_list=rubric_list,
        det_report=det_report,
        brand_profile=check_kwargs.get("brand_profile"),
        brand_version=brand_version,
        locale=str(check_kwargs.get("locale") or "pt-BR"),
        funnel_stage=str(check_kwargs.get("funnel_stage") or "mofu"),
        format_slug=fmt_slug,
        industry_slug=industry_slug,
        llm_adapter=llm_adapter,
        model=model,
    )
    return report


def _load_draft_evidence_docs(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    ctx: dict[str, Any],
    brief_model: EditorialBrief,
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_ids = [
        *(rf.source_id for rf in brief_model.required_facts),
        *(c.get("document_id") for c in claims if c.get("document_id")),
        ctx.get("primary_document_id"),
    ]
    unique_ids = list(dict.fromkeys(uuid.UUID(str(raw_id)) for raw_id in raw_ids if raw_id))
    docs: list[dict[str, Any]] = []
    for doc_uuid in unique_ids:
        row = db.get_document(conn, workspace_id=workspace_id, document_id=doc_uuid)
        if row is not None:
            raw_meta = row.get("meta")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            docs.append(
                {
                    "id": str(row["id"]),
                    "title": str(row.get("title") or ""),
                    "source_name": str(meta.get("source_name") or "evidence"),
                    "source_role": str(meta.get("source_role") or "data"),
                    "url": str(row.get("canonical_url") or row.get("url") or ""),
                    "published_at": row.get("published_at"),
                    "date_status": str(row.get("date_status") or "unverified"),
                    "fetch_status": str(row.get("fetch_status") or "ok"),
                    "summary": str(meta.get("summary") or row.get("extracted_text") or "")[:500],
                    "facts": list(meta.get("facts") or [])[:5],
                }
            )
    return docs


def _resolve_persistence_outcome(
    route: RouteTarget,
    revision: int,
) -> tuple[str, str, bool, bool]:
    if route == "pass":
        return "pass", "pass", False, False
    if route == "research":
        return "reject", "reject", False, True
    if revision >= MAX_EVAL_REVISION:
        return "revision", "pass", True, False
    return "revision", "revision", False, False


def _emit_draft_awaiting_approval_if_needed(
    conn: psycopg.Connection[Any],
    *,
    ctx: dict[str, Any],
    report: QualityReport,
    surfaced_after_max: bool,
    actor_user_id: uuid.UUID | None,
) -> None:
    ws_id = uuid.UUID(str(ctx["workspace_id"]))
    draft_id = uuid.UUID(str(ctx["id"]))
    existing = db.list_events(
        conn,
        workspace_id=ws_id,
        event_type="draft.awaiting_approval",
        entity_id=draft_id,
    )
    if existing:
        return
    events.emit(
        conn,
        workspace_id=ws_id,
        event_type="draft.awaiting_approval",
        entity_type="draft",
        entity_id=draft_id,
        payload={
            "draft_id": str(draft_id),
            "brief_id": str(ctx["brief_id"]),
            "opportunity_id": str(ctx["opportunity_id"]),
            "run_id": str(ctx["run_id"]),
            "revision": int(ctx["revision"]),
            "verdict": report.verdict,
            "score": report.score,
            "surfaced_after_max_revisions": surfaced_after_max,
        },
        actor_user_id=actor_user_id,
    )


def evaluate_persisted_draft(
    conn: psycopg.Connection[Any],
    spec: EvalExecutionSpec,
) -> dict[str, Any]:
    """Run deterministic + LLM evaluation on a persisted draft and update DB status/reports."""
    ctx = db.get_draft_evaluation_context(
        conn, workspace_id=spec.workspace_id, draft_id=spec.draft_id
    )
    if ctx is None:
        msg = f"Draft {spec.draft_id} not found in workspace {spec.workspace_id}"
        raise ValueError(msg)

    evaluating_row = db.transition_draft_status(
        conn,
        workspace_id=spec.workspace_id,
        draft_id=spec.draft_id,
        expected_statuses=("draft",),
        new_status="evaluating",
    )
    if evaluating_row is None:
        msg = f"Draft {spec.draft_id} is in status '{ctx['status']}', expected 'draft'"
        raise ValueError(msg)

    draft_model = ContentDraft.model_validate(ctx["draft_payload"])
    brief_model = EditorialBrief.model_validate(ctx["brief_payload"])
    brand_model = BrandProfile.model_validate(ctx["brand_payload"])
    claims = db.list_draft_claims(conn, workspace_id=spec.workspace_id, draft_id=spec.draft_id)
    evidence_docs = _load_draft_evidence_docs(
        conn, workspace_id=spec.workspace_id, ctx=ctx, brief_model=brief_model, claims=claims
    )

    locale = str(ctx.get("campaign_locale") or "pt-BR")
    raw_funnel = brief_model.funnel_stage or ctx.get("funnel_stage") or "mofu"
    funnel_stage = str(raw_funnel).strip().lower()
    fmt_slug = brief_model.format or "blog"
    resolved_options = _resolve_word_bounds(fmt_slug, spec.options)
    det_report = checks.run(
        draft_model,
        brief=brief_model,
        evidence_documents=evidence_docs,
        brand_profile=brand_model,
        campaign_config=ctx.get("campaign_config") or {},
        locale=locale,
        funnel_stage=funnel_stage,
        options=resolved_options,
    )

    rubric_list = load_rubric(format_slug=fmt_slug)
    if det_report.verdict != "PASS" or spec.deterministic_only:
        report = det_report.to_quality_report()
        eval_model = "deterministic"
        prompt_ver = "deterministic:v1"
    elif spec.llm_evaluator is not None:
        raw_report = spec.llm_evaluator(
            draft=draft_model,
            brief=brief_model,
            evidence=evidence_docs,
            rubric=rubric_list,
            deterministic_report=det_report,
            llm_adapter=spec.llm_adapter,
        )
        report = _normalize_quality_report(raw_report, rubric=rubric_list, det_report=det_report)
        eval_model = "injected-evaluator"
        _, prompt_ver = get_evaluate_prompt_and_version()
    else:
        resolved_llm = spec.llm_adapter or (FakeLLM() if spec.offline else None)
        report, eval_model, prompt_ver = _run_llm_rubric_call(
            draft_model=draft_model,
            brief=brief_model,
            evidence=evidence_docs,
            rubric_list=rubric_list,
            det_report=det_report,
            brand_profile=brand_model,
            brand_version=int(ctx["brand_version"]),
            locale=locale,
            funnel_stage=funnel_stage,
            format_slug=fmt_slug,
            industry_slug=ctx.get("industry_pack"),
            workspace_config=ctx.get("workspace_config"),
            llm_adapter=resolved_llm,
            conn=conn,
            workspace_id=spec.workspace_id,
            run_id=uuid.UUID(str(ctx["run_id"])),
            model=spec.model,
        )

    route = determine_revision_route(report)
    qr_verdict, new_status, surfaced, returned = _resolve_persistence_outcome(
        route, int(ctx["revision"])
    )
    qr_row = db.upsert_quality_report(
        conn,
        workspace_id=spec.workspace_id,
        draft_id=spec.draft_id,
        verdict=qr_verdict,
        score=report.score,
        checks={k: v.model_dump(mode="json") for k, v in det_report.checks.items()},
        findings=[f.model_dump(mode="json") for f in report.findings],
        evaluator_model=eval_model,
        prompt_version=prompt_ver,
    )
    updated_draft = db.transition_draft_status(
        conn,
        workspace_id=spec.workspace_id,
        draft_id=spec.draft_id,
        expected_statuses=("evaluating",),
        new_status=new_status,
    )
    if returned:
        db.return_opportunity_to_pool(
            conn,
            workspace_id=spec.workspace_id,
            opportunity_id=uuid.UUID(str(ctx["opportunity_id"])),
            note=f"Returned to pool by quality evaluator ({route})",
        )

    events.emit(
        conn,
        workspace_id=spec.workspace_id,
        event_type="draft.evaluated",
        entity_type="draft",
        entity_id=spec.draft_id,
        payload={
            "draft_id": str(spec.draft_id),
            "brief_id": str(ctx["brief_id"]),
            "opportunity_id": str(ctx["opportunity_id"]),
            "run_id": str(ctx["run_id"]),
            "revision": int(ctx["revision"]),
            "verdict": report.verdict,
            "route": route,
            "surfaced_to_owner": surfaced,
            "topic_returned_to_pool": returned,
        },
        actor_user_id=spec.actor_user_id,
    )
    if new_status == "pass":
        _emit_draft_awaiting_approval_if_needed(
            conn,
            ctx=ctx,
            report=report,
            surfaced_after_max=surfaced,
            actor_user_id=spec.actor_user_id,
        )

    return {
        "draft": updated_draft,
        "quality_report": qr_row,
        "report": report,
        "route": route,
        "surfaced_to_owner": surfaced,
        "topic_returned_to_pool": returned,
        "context": ctx,
    }
