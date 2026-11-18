"""In-memory fake adapters implementing port protocols for testing and local dev (ADR-005)."""

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from engine.contracts import (
    AuditCheckResult,
    KeywordData,
    PublishedContent,
    PublishRequest,
    ResearchRecord,
)


def _extract_json_input(prompt: str) -> dict[str, Any]:
    marker = "\n\nInput:\n"
    if marker not in prompt:
        return {}
    tail = prompt.rsplit(marker, 1)[-1]
    json_text = tail.split("\nValidation error in previous attempt:", 1)[0].strip()
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_fake_required_facts(evidence_docs: list[Any], *, is_pt: bool) -> list[dict[str, str]]:
    default_fact = (
        "Dados setoriais verificados na fonte citada."
        if is_pt
        else "Verified industry benchmark from cited source."
    )
    required: list[dict[str, str]] = []
    for doc in evidence_docs[:3]:
        if not isinstance(doc, dict) or not doc.get("id"):
            continue
        facts_list = doc.get("facts") or []
        candidate = (
            str(facts_list[0]).strip()
            if facts_list
            else str(doc.get("summary") or doc.get("title") or "").strip()
        )
        required.append({"fact": candidate or default_fact, "source_id": str(doc["id"])})
    return required


def _extract_fake_brief_angle(
    opp: dict[str, Any],
    findings: list[Any],
    *,
    is_pt: bool,
) -> tuple[str, str]:
    default_angle = (
        "Guia prático baseado em evidências" if is_pt else "Evidence-based practical playbook"
    )
    base_angle = str(opp.get("angle") or default_angle).strip()
    note = str(opp.get("decision_note") or "").strip()
    angle_parts = [p for p in (base_angle, note) if p]
    if findings:
        first_finding = findings[0]
        raw_detail = (
            first_finding.get("detail") if isinstance(first_finding, dict) else first_finding
        )
        finding_text = str(raw_detail or "").strip()
        if finding_text:
            angle_parts.append(finding_text)
    return base_angle, " — ".join(angle_parts)


def _build_localized_brief_copy(
    primary_kw: str,
    base_angle: str,
    funnel: str,
    *,
    is_pt: bool,
) -> tuple[str, list[str], str]:
    if is_pt:
        audience = "Gestores e proprietários de óticas independentes e redes no Brasil"
        outline = [
            f"Cenário atual e indicadores sobre {primary_kw}",
            f"Evidências setoriais e impactos na operação: {base_angle}",
            "Boas práticas de implementação na rotina da loja",
            "Perguntas frequentes sobre adoção e retorno operacional",
            "Próximos passos e checklist consultivo",
        ]
        cta_map = {
            "tofu": "Baixar o guia diagnóstico gratuito para gestão de óticas.",
            "bofu": "Agendar demonstração consultiva para avaliar implantação na sua ótica.",
        }
        default_cta = "Acessar o checklist operacional completo para estruturar o processo na loja."
        return audience, outline, cta_map.get(funnel, default_cta)

    audience = "Independent optometry clinic owners and optical retail managers"
    outline = [
        f"Market context and patient demand around {primary_kw}",
        f"Industry benchmark evidence and clinical impact: {base_angle}",
        "Step-by-step workflow implementation for independent practices",
        "Frequently asked questions from practitioners and parents",
        "Actionable next steps and readiness checklist",
    ]
    cta_map = {
        "tofu": "Download the educational practice readiness guide.",
        "bofu": "Schedule a personalized workflow consultation for your practice.",
    }
    return (
        audience,
        outline,
        cta_map.get(funnel, "Get the practical implementation checklist for your clinical team."),
    )


def _build_fake_editorial_brief(prompt: str) -> dict[str, Any]:
    payload = _extract_json_input(prompt)
    is_pt = str(payload.get("locale") or "pt-BR").lower().startswith("pt")
    opp = payload.get("opportunity") or {}
    default_title = "Pauta Editorial Estratégica" if is_pt else "Strategic Editorial Topic"
    title = str(opp.get("title") or default_title).strip()
    findings = payload.get("revision_findings") or []
    base_angle, full_angle = _extract_fake_brief_angle(opp, findings, is_pt=is_pt)

    kw_items = payload.get("keywords") or []
    kw_names = [
        str(k.get("keyword", "")).strip()
        for k in kw_items
        if isinstance(k, dict) and str(k.get("keyword", "")).strip()
    ]
    primary_kw = kw_names[0] if kw_names else title.lower()
    funnel = str(opp.get("funnel_stage") or "mofu").lower()
    audience, outline, cta_goal = _build_localized_brief_copy(
        primary_kw, base_angle, funnel, is_pt=is_pt
    )

    return {
        "brand_profile_id": payload.get("brand_profile_id"),
        "brand_version": int(payload.get("brand_version") or 1),
        "topic": title,
        "target_audience": audience,
        "primary_keyword": primary_kw,
        "secondary_keywords": kw_names[1:5],
        "format": str(payload.get("format") or "blog"),
        "angle": full_angle,
        "outline": outline,
        "required_facts": _build_fake_required_facts(
            payload.get("evidence_documents") or [], is_pt=is_pt
        ),
        "cta_goal": cta_goal,
    }


def _build_fake_extracted_research(prompt: str) -> dict[str, Any]:
    title = "Fake Structured Headline"
    for line in prompt.splitlines():
        if line.startswith("Title:"):
            candidate = line.removeprefix("Title:").strip()
            if candidate:
                title = candidate
            break
    return {
        "headline": title,
        "summary": f"Structured summary for: {title}",
        "facts": ["Key market insight", "Secondary industry finding"],
        "entities": ["Optical Retail"],
        "event_date": "2026-11-05",
        "source_type": "news",
    }


def _build_fake_opportunity() -> dict[str, Any]:
    sub = {
        "score": 8.0,
        "evidence": "Verified optical market insight",
        "is_estimate": False,
    }
    return {
        "title": "Fake Opportunity Topic",
        "angle": "Retail perspective for optical chains",
        "funnel_stage": "mofu",
        "confidence": "high",
        "novelty": sub,
        "audience_fit": sub,
        "brand_fit": sub,
        "competitor_gap": sub,
        "trend": {
            "score": 7.0,
            "evidence": "Estimated organic trend",
            "is_estimate": True,
        },
        "urgency": sub,
        "weighted_total": 79.0,
    }


def _slugify_kebab(text: str) -> str:
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug[:64].strip("-") or "artigo-blog-setorial"


def _fallback_fact_claim_from_doc(doc: dict[str, Any], *, is_pt: bool) -> dict[str, Any]:
    doc_facts = doc.get("facts") or []
    fact_text = (
        str(doc_facts[0]).strip()
        if doc_facts
        else str(doc.get("summary") or doc.get("title") or "").strip()
    )
    default_fact = (
        "Indicador setorial verificado em documento de evidência."
        if is_pt
        else "Verified sector indicator from evidence document."
    )
    return {
        "text": fact_text or default_fact,
        "kind": "fact",
        "source_id": str(doc["id"]),
    }


def _build_fake_draft_claims(
    brief_dict: dict[str, Any],
    evidence_docs: list[Any],
    *,
    is_pt: bool,
) -> list[dict[str, Any]]:
    req_facts = brief_dict.get("required_facts") or []
    claims = [
        {
            "text": str(rf.get("fact") or "").strip(),
            "kind": "fact",
            "source_id": str(rf["source_id"]),
        }
        for rf in req_facts
        if isinstance(rf, dict) and rf.get("source_id")
    ]
    if not claims:
        valid_docs = [d for d in evidence_docs[:2] if isinstance(d, dict) and d.get("id")]
        claims = [_fallback_fact_claim_from_doc(doc, is_pt=is_pt) for doc in valid_docs]

    analysis_text = (
        "A revisão periódica de indicadores operacionais reduz gargalos de execução."
        if is_pt
        else "Periodic operational indicator reviews reduce execution bottlenecks."
    )
    rec_text = (
        "Estruture rituais semanais de acompanhamento com a equipe de loja."
        if is_pt
        else "Establish weekly review rituals across your clinical and retail staff."
    )
    return [
        *claims,
        {"text": analysis_text, "kind": "analysis", "source_id": None},
        {"text": rec_text, "kind": "recommendation", "source_id": None},
    ]


def _extract_finding_notes(findings: list[Any]) -> list[str]:
    notes: list[str] = []
    for item in findings:
        raw = item.get("detail") if isinstance(item, dict) else item
        cleaned = str(raw or "").strip()
        if cleaned:
            notes.append(cleaned)
    return notes


def _format_fake_section_paragraph(primary_kw: str, fact_ref: str, *, is_pt: bool) -> str:
    if is_pt:
        return (
            f"Nesta etapa do plano sobre {primary_kw}, o gestor deve avaliar os indicadores "
            f"internos em conjunto com as referências do setor. {fact_ref} "
            "Ao conectar o diagnóstico quantitativo à rotina de compras, precificação e "
            "atendimento no balcão, a ótica evita imobilização desnecessária de capital e "
            "melhora a conversão de receituários em vendas de maior valor agregado, mantendo "
            "rastreabilidade completa em cada decisão operacional."
        )
    return (
        f"In this phase of executing {primary_kw}, clinic leaders should benchmark "
        f"internal performance against documented industry data. {fact_ref} "
        "Connecting empirical evidence to chairside communication, optical dispensing, "
        "and follow-up scheduling helps independent practices reduce operational friction "
        "while improving patient outcomes and long-term optical capture rates."
    )


def _build_fake_draft_body_md(
    title: str,
    brief_dict: dict[str, Any],
    claims: list[dict[str, Any]],
    finding_notes: list[str],
    *,
    is_pt: bool,
) -> str:
    primary_kw = str(brief_dict.get("primary_keyword") or title).strip()
    outline = [str(item) for item in (brief_dict.get("outline") or []) if str(item).strip()]
    sections = outline or [
        "Panorama e contexto do mercado",
        "Evidências e indicadores essenciais",
        "Aplicação prática na rotina",
        "Próximos passos",
    ]
    fact_lines = [c["text"] for c in claims if c["kind"] == "fact"]
    intro = (
        f"A implementação consistente de **{primary_kw}** tornou-se prioridade para gestores "
        "que buscam previsibilidade operacional, controle de margem e atendimento consultivo "
        "no varejo óptico. Quando decisões são orientadas por dados reais e processos bem "
        "definidos, a equipe ganha clareza sobre prioridades diárias, giro de mercadorias e "
        "qualidade da jornada do cliente em loja."
        if is_pt
        else f"Building a disciplined workflow around **{primary_kw}** has become essential for "
        "independent optical practices seeking predictable margins, stronger retention, "
        "and structured clinical execution. Grounding day-to-day decisions in verified sector "
        "benchmarks allows teams to align inventory, staff training, and patient education."
    )
    paragraphs: list[str] = [f"# {title}", intro]

    for idx, heading in enumerate(sections):
        paragraphs.append(f"## {heading}")
        fact_ref = fact_lines[idx % len(fact_lines)] if fact_lines else ""
        paragraphs.append(_format_fake_section_paragraph(primary_kw, fact_ref, is_pt=is_pt))

    if finding_notes:
        rev_heading = "## Ajustes de Revisão Editorial" if is_pt else "## Editorial Revision Notes"
        paragraphs.append(rev_heading)
        paragraphs.extend(f"- {note}" for note in finding_notes)

    fmt_slug = str(brief_dict.get("format") or "blog").strip().lower()
    _pad_fake_paragraphs(paragraphs, primary_kw, fact_lines, fmt_slug, is_pt=is_pt)
    return "\n\n".join(paragraphs)


def _pad_fake_paragraphs(
    paragraphs: list[str],
    primary_kw: str,
    fact_lines: list[str],
    fmt_slug: str,
    *,
    is_pt: bool,
) -> None:
    """Append domain-relevant filler paragraphs until fake draft meets format min_words."""
    target_words = {"blog-short": 450, "blog-long": 1850}.get(fmt_slug, 850)
    filler_idx = 0
    while len("\n\n".join(paragraphs).split()) < target_words:
        fact_ref = fact_lines[filler_idx % len(fact_lines)] if fact_lines else ""
        paragraphs.append(_format_fake_section_paragraph(primary_kw, fact_ref, is_pt=is_pt))
        filler_idx += 1


def _build_fake_content_draft(prompt: str) -> dict[str, Any]:
    payload = _extract_json_input(prompt)
    is_pt = str(payload.get("locale") or "pt-BR").lower().startswith("pt")
    brief_dict = payload.get("brief") or {}
    evidence_docs = payload.get("evidence_documents") or []
    findings = payload.get("findings") or []

    topic = str(brief_dict.get("topic") or ("Guia Prático" if is_pt else "Practical Guide")).strip()
    primary_kw = str(brief_dict.get("primary_keyword") or topic).strip()
    title = f"{topic}: {primary_kw}" if primary_kw.lower() not in topic.lower() else topic

    if is_pt:
        meta = (
            f"Guia prático sobre {primary_kw}: veja dados verificados, indicadores operacionais "
            "e passos claros para aplicar na gestão da sua ótica."
        )
        faq = [
            {
                "question": f"Como iniciar a aplicação de {primary_kw} na ótica?",
                "answer": "Organize os registros de vendas e estoque dos últimos 90 dias.",
            },
            {
                "question": "Com qual frequência os indicadores devem ser revisados?",
                "answer": "Recomenda-se acompanhamento semanal de giro e fechamento mensal.",
            },
        ]
        cta = {
            "headline": "Estruture sua gestão com dados confiáveis",
            "body": str(brief_dict.get("cta_goal") or "Acesse nosso checklist operacional."),
            "button_text": "Solicitar diagnóstico consultivo",
        }
    else:
        meta = (
            f"Practical playbook on {primary_kw}: review verified benchmarks, clinical workflows, "
            "and actionable steps for independent optical practices."
        )
        faq = [
            {
                "question": f"How should a practice start implementing {primary_kw}?",
                "answer": "Audit baseline patient records and staff workflows over 90 days.",
            },
            {
                "question": "How often should performance benchmarks be reviewed?",
                "answer": "Review core operational metrics weekly and run a full audit monthly.",
            },
        ]
        cta = {
            "headline": "Upgrade your practice workflow with verified data",
            "body": str(brief_dict.get("cta_goal") or "Download the implementation checklist."),
            "button_text": "Get the clinical playbook",
        }

    claims = _build_fake_draft_claims(brief_dict, evidence_docs, is_pt=is_pt)
    finding_notes = _extract_finding_notes(findings)
    body_md = _build_fake_draft_body_md(title, brief_dict, claims, finding_notes, is_pt=is_pt)

    return {
        "title": title,
        "meta_description": meta[:158],
        "slug": _slugify_kebab(primary_kw or title),
        "body_md": body_md,
        "faq": faq,
        "cta": cta,
        "claims": claims,
    }


def _extract_doc_corpus_chunks(doc: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    for field in ("facts", "summary", "extracted_text"):
        val = doc.get(field)
        if isinstance(val, list):
            chunks.extend(str(item) for item in val)
        elif isinstance(val, str) and val.strip():
            chunks.append(val)
    return chunks


def _collect_fake_eval_evidence(
    evidence_docs: list[Any],
    brief: dict[str, Any],
) -> tuple[set[str], str, bool]:
    docs = [d for d in evidence_docs if isinstance(d, dict)]
    valid_ids = {str(d["id"]) for d in docs if d.get("id")}
    doc_chunks = [chunk for doc in docs for chunk in _extract_doc_corpus_chunks(doc)]
    has_rich = bool(doc_chunks)
    req_facts = [
        str(rf["fact"])
        for rf in (brief.get("required_facts") or [])
        if isinstance(rf, dict) and rf.get("fact")
    ]
    return valid_ids, "\n".join([*doc_chunks, *req_facts]), has_rich


def _check_fake_eval_facts(
    draft: dict[str, Any],
    brief: dict[str, Any],
    evidence_docs: list[Any],
) -> list[dict[str, str]]:
    import re

    claims = draft.get("claims") or []
    body_md = str(draft.get("body_md") or "")
    valid_ids, corpus, has_rich = _collect_fake_eval_evidence(evidence_docs, brief)
    fact_claims = [c for c in claims if isinstance(c, dict) and c.get("kind") == "fact"]
    bad_source = any(
        not c.get("source_id") or (valid_ids and str(c.get("source_id")) not in valid_ids)
        for c in fact_claims
    )
    body_pcts = set(re.findall(r"\b\d+(?:[.,]\d+)?%", f"{body_md}\n{claims}"))
    corpus_pcts = set(re.findall(r"\b\d+(?:[.,]\d+)?%", corpus))
    hallucinated_pcts = body_pcts - corpus_pcts if has_rich else set()

    if not claims or bad_source or hallucinated_pcts:
        return [
            {
                "criterion": "factual_accuracy",
                "severity": "critical",
                "detail": (
                    f"Ungrounded or hallucinated percentages {sorted(hallucinated_pcts)}"
                    if hallucinated_pcts
                    else "Draft lacks verifiable factual claim provenance against evidence."
                ),
                "responsible_module": "write" if evidence_docs else "research",
            }
        ]
    return []


def _check_fake_eval_brand_rules(full_text: str, brand: dict[str, Any]) -> list[dict[str, str]]:
    import re

    bad_brand_markers = (
        r"\bgarante aumento\b",
        r"\blucro garantido\b",
        r"\bsubstitui diagn[oó]stico\b",
        r"\bguarantees? \d+%",
        r"\breplaces? medical diagnosis\b",
        r"\bhomologa[cç][aã]o oficial\b",
        *(str(p) for p in (brand.get("prohibited_phrases") or []) if str(p).strip()),
    )
    if any(re.search(pat, full_text, re.IGNORECASE) for pat in bad_brand_markers):
        return [
            {
                "criterion": "brand_voice_alignment",
                "severity": "critical",
                "detail": "Draft asserts prohibited medical/financial claims or disallowed terms.",
                "responsible_module": "write",
            }
        ]
    return []


def _check_fake_eval_brief_fit(
    claims: list[Any],
    brief: dict[str, Any],
    h2_count: int,
) -> list[dict[str, str]]:
    req_fact_ids = {
        str(rf["source_id"]) for rf in (brief.get("required_facts") or []) if isinstance(rf, dict)
    }
    cited_ids = {
        str(c.get("source_id"))
        for c in claims
        if isinstance(c, dict) and c.get("kind") == "fact" and c.get("source_id")
    }
    outline = brief.get("outline") or []
    if (req_fact_ids and not (req_fact_ids & cited_ids)) or (len(outline) >= 3 and h2_count < 2):
        return [
            {
                "criterion": "audience_relevance",
                "severity": "major",
                "detail": "Draft drifts from the brief outline, angle, and required facts.",
                "responsible_module": "write",
            }
        ]
    return []


def _check_fake_eval_brand_and_brief(
    draft: dict[str, Any],
    brief: dict[str, Any],
    brand: dict[str, Any],
) -> list[dict[str, str]]:
    body_md = str(draft.get("body_md") or "")
    claims = draft.get("claims") or []
    full_text = "\n".join(
        [str(draft.get("title") or ""), str(draft.get("meta_description") or ""), body_md]
    )
    findings = _check_fake_eval_brand_rules(full_text, brand)
    h2_count = sum(1 for line in body_md.splitlines() if line.strip().startswith("## "))
    if len(body_md.split()) < 35 or not draft.get("faq") or not draft.get("cta") or h2_count < 1:
        findings.append(
            {
                "criterion": "clarity_and_structure",
                "severity": "major",
                "detail": "Draft body, H2 hierarchy, FAQ, or CTA lacks publication depth.",
                "responsible_module": "write",
            }
        )
    findings.extend(_check_fake_eval_brief_fit(claims, brief, h2_count))
    return findings


def _fake_eval_findings(
    payload: dict[str, Any],
) -> tuple[list[dict[str, str]], float]:
    draft = payload.get("draft") or {}
    brief = payload.get("brief") or {}
    evidence_docs = payload.get("evidence_documents") or []
    brand = payload.get("brand_profile") or {}
    det_findings = payload.get("deterministic_findings") or []

    findings = [
        *_check_fake_eval_facts(draft, brief, evidence_docs),
        *_check_fake_eval_brand_and_brief(draft, brief, brand),
    ]
    deductions = sum(35.0 if f["severity"] == "critical" else 25.0 for f in findings)
    deductions += sum(
        15.0 if df.get("severity") == "major" else 5.0
        for df in det_findings
        if isinstance(df, dict)
    )
    return findings, max(0.0, round(92.0 - deductions, 2))


def _build_fake_quality_report(prompt: str) -> dict[str, Any]:
    payload = _extract_json_input(prompt)
    findings, score = _fake_eval_findings(payload)
    has_critical = any(f["severity"] == "critical" for f in findings)
    has_research = any(
        f["responsible_module"] == "research" and f["severity"] == "critical" for f in findings
    )
    if has_research:
        verdict = "REJECT"
    elif has_critical or score < 70.0:
        verdict = "REVISION"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "findings": findings,
        "score": score,
    }


class FakeLLM:
    """Fake LLM adapter returning predetermined structured responses."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        model_name: str = "fake-llm-v1"
        models_by_stage: dict[str, str] = Field(default_factory=dict)
        api_key: SecretStr | None = None
        default_response: dict[str, Any] = Field(
            default_factory=lambda: {"result": "fake_llm_completion"}
        )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify fake LLM connectivity."""

    def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
        """Return fake completion dictionary."""
        schema_name = getattr(schema, "__name__", "") if schema is not None else ""
        if schema_name == "LLMExtractedResearch":
            return _build_fake_extracted_research(prompt)
        has_custom_response = self.settings.default_response != {"result": "fake_llm_completion"}
        if schema_name == "Opportunity":
            return (
                dict(self.settings.default_response)
                if has_custom_response
                else _build_fake_opportunity()
            )
        if schema_name == "EditorialBrief":
            return (
                dict(self.settings.default_response)
                if has_custom_response
                else _build_fake_editorial_brief(prompt)
            )
        if schema_name == "ContentDraft":
            return (
                dict(self.settings.default_response)
                if has_custom_response
                else _build_fake_content_draft(prompt)
            )
        if schema_name == "QualityReport":
            return (
                dict(self.settings.default_response)
                if has_custom_response
                else _build_fake_quality_report(prompt)
            )
        return dict(self.settings.default_response)


class FakeFeedSource:
    """Fake feed source returning synthetic research records."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        default_items_count: int = 2
        auth_token: SecretStr | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify fake feed source availability."""

    def fetch_feed(self, url: str, since: datetime | None = None) -> list[ResearchRecord]:
        """Return synthetic research records from feed."""
        records: list[ResearchRecord] = []
        now_dt = since or datetime.now(UTC)
        for i in range(self.settings.default_items_count):
            records.append(
                ResearchRecord(
                    headline=f"Feed item {i + 1} from {url}",
                    source=url,
                    url=f"{url}/item-{i + 1}",
                    published_at=now_dt,
                    event_date=now_dt.date(),
                    summary=f"Summary for feed item {i + 1} from {url}",
                    facts=[f"Fact {i + 1}A", f"Fact {i + 1}B"],
                    entities=[f"Entity {i + 1}"],
                    source_type="rss",
                    verification_status="verified",
                )
            )
        return records


class FakeNewsSearch:
    """Fake news search adapter returning matching synthetic articles."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        default_results_count: int = 2
        api_key: SecretStr | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify fake search availability."""

    def search(
        self,
        query: str,
        locale: str,
        market: str,
        since: date | None = None,
        *,
        domain: str | None = None,
        **kwargs: Any,
    ) -> list[ResearchRecord]:
        """Return synthetic search results."""
        records: list[ResearchRecord] = []
        effective_domain = domain
        if not effective_domain and query.startswith("site:"):
            effective_domain = query.split()[0].removeprefix("site:")
        base_source = effective_domain or "fake-news-service"
        target_date = since or date.today()
        published_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=UTC)
        for i in range(self.settings.default_results_count):
            target_url = (
                f"https://{effective_domain}/article-{i + 1}"
                if effective_domain
                else f"https://news.example.com/{locale}/{query}/{i + 1}"
            )
            headline = (
                f"Industry update {i + 1} from {effective_domain} ({locale})"
                if effective_domain
                else f"News {i + 1} for '{query}' in {locale}/{market}"
            )
            records.append(
                ResearchRecord(
                    headline=headline,
                    source=base_source,
                    url=target_url,
                    published_at=published_dt,
                    event_date=target_date,
                    summary=f"News summary for query '{query}' item {i + 1}",
                    facts=[f"Key finding on {query}"],
                    entities=[query, market],
                    source_type="news",
                    verification_status="verified",
                )
            )
        return records


class FakeExtractor:
    """Fake HTML and article text extractor."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        user_agent: str = "FakeExtractor/1.0"
        api_key: SecretStr | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify fake extractor readiness."""

    def extract(self, url: str, html: str | None = None) -> ResearchRecord:
        """Extract clean text and metadata into a research record."""
        preview = (html[:60] + "...") if html else f"Extracted text from {url}"
        return ResearchRecord(
            headline=f"Extracted article from {url}",
            source=url,
            url=url,
            summary=preview,
            facts=["Extracted fact 1", "Extracted fact 2"],
            entities=["Extracted entity"],
            source_type="webpage",
        )


class FakeKeywordProvider:
    """Fake SEO metrics and search intent provider."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        default_volume: int = 1500
        default_cpc: float = 1.25
        api_key: SecretStr | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify fake keyword provider connectivity."""

    def lookup(
        self,
        terms: list[str],
        locale: str,
        market: str,
        **kwargs: Any,
    ) -> list[KeywordData]:
        """Return keyword volume and competition metrics."""
        return [
            KeywordData(
                keyword=term,
                search_volume=self.settings.default_volume,
                difficulty=28.5,
                cpc_usd=self.settings.default_cpc,
                intent="informational",
            )
            for term in terms
        ]


class FakePublisher:
    """Fake CMS destination publisher."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        platform_name: str = "fake-cms"
        endpoint_url: str | None = None
        api_key: SecretStr | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()
        self._published: dict[tuple[uuid.UUID, str], PublishedContent] = {}

    def healthcheck(self) -> None:
        """Verify fake publisher destination."""

    def publish(self, request: PublishRequest) -> PublishedContent:
        """Publish content draft idempotently and return published content details."""
        key = (request.draft_id, request.destination)
        existing = self._published.get(key)
        if existing is not None:
            return existing

        is_live = request.mode == "live"
        status = "scheduled" if request.mode == "scheduled" else "published"
        result = PublishedContent(
            destination=request.destination,
            external_id=f"fake-{request.draft_id}",
            url=f"https://{request.destination}.example.com/{request.draft.slug}",
            status=status,
            published_at=datetime.now(UTC),
            raw={
                "platform": self.settings.platform_name,
                "mode": request.mode,
                "is_live": is_live,
                "draft_id": str(request.draft_id),
            },
        )
        self._published[key] = result
        return result


class FakeAnalyticsSource:
    """Fake content performance analytics source."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        views_multiplier: int = 100
        api_key: SecretStr | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def healthcheck(self) -> None:
        """Verify fake analytics source readiness."""

    def get_metrics(self, external_id: str, since: date) -> dict[str, Any]:
        """Return synthetic traffic and conversion metrics."""
        return {
            "external_id": external_id,
            "since": since.isoformat(),
            "views": 250 * self.settings.views_multiplier,
            "unique_visitors": 180 * self.settings.views_multiplier,
            "clicks": 35 * self.settings.views_multiplier,
            "conversions": 5 * self.settings.views_multiplier,
        }


class FakeBrowserAudit:
    """Fake BrowserAuditPort returning curated axe-core usability checks for tests."""

    CURATED_RULES: tuple[str, ...] = (
        "image-alt",
        "document-title",
        "html-has-lang",
        "heading-order",
        "link-name",
        "color-contrast",
        "meta-viewport",
    )

    def healthcheck(self) -> None:
        """Verify fake browser audit readiness."""

    def audit_accessibility(self, url: str) -> list[AuditCheckResult]:
        """Return passing curated axe-core check results for unit tests."""
        return [
            AuditCheckResult(
                check=f"axe_{rule.replace('-', '_')}",
                category="usability",
                status="pass",
                score=1.0,
                detail=f"axe-core rule '{rule}' passed on {url}.",
            )
            for rule in self.CURATED_RULES
        ]


class FakePageSpeed:
    """Fake PageSpeedPort returning Core Web Vitals checks for tests."""

    def healthcheck(self) -> None:
        """Verify fake PageSpeed port readiness."""

    def fetch_metrics(self, url: str, strategy: str = "mobile") -> list[AuditCheckResult]:
        """Return synthetic LCP, CLS, and INP performance checks."""
        return [
            AuditCheckResult(
                check="psi_lcp",
                category="performance",
                status="pass",
                score=1.0,
                detail=f"LCP 1850ms ({strategy}) for {url}.",
                metadata={"value_ms": 1850.0, "strategy": strategy},
            ),
            AuditCheckResult(
                check="psi_cls",
                category="performance",
                status="pass",
                score=1.0,
                detail=f"CLS 0.04 ({strategy}) for {url}.",
                metadata={"value": 0.04, "strategy": strategy},
            ),
            AuditCheckResult(
                check="psi_inp",
                category="performance",
                status="pass",
                score=1.0,
                detail=f"INP 120ms ({strategy}) for {url}.",
                metadata={"value_ms": 120.0, "strategy": strategy},
            ),
        ]


class FakeRankTracker:
    """Fake RankTrackerPort returning deterministic SERP rank snapshots for tests."""

    def healthcheck(self) -> None:
        """Verify fake rank tracker readiness."""

    def check_rank(
        self,
        *,
        keyword: str,
        domain: str,
        locale: str = "pt-BR",
        market: str = "BR",
    ) -> dict[str, Any]:
        """Return synthetic ranking at position 7 for domain."""
        return {
            "keyword": keyword,
            "domain": domain,
            "locale": locale,
            "market": market,
            "position": 7,
            "ranking_url": f"https://{domain}/guia/{keyword.replace(' ', '-')}",
            "provider": "fake",
        }
