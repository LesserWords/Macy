"""Opportunity scoring engine, code-computed weighted totals, and rubric evaluation.

F09, ADR-002, ADR-004.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel

from engine import config, db, llm
from engine.contracts import Opportunity, ResearchRecord, ScoringWeights
from engine.integrations.ports import LLMPort

PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "score.md"


def get_score_prompt_and_version() -> tuple[str, str]:
    """Load score prompt text and compute deterministic SHA256 prompt version."""
    if not PROMPT_FILE.exists():
        msg = f"Score prompt file not found at {PROMPT_FILE}"
        raise FileNotFoundError(msg)
    content = PROMPT_FILE.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return content, f"score.md:{prompt_hash}"


def _prepare_score_input(
    record: ResearchRecord | dict[str, Any],
    brand_profile: Any | None = None,
    industry_pack: Any | None = None,
) -> dict[str, Any]:
    """Format structured research record and strategy context for LLM prompt input."""
    if isinstance(record, ResearchRecord):
        rec_dict = record.model_dump(mode="json")
    else:
        rec_dict = dict(record)

    brand_dict = (
        brand_profile.model_dump(mode="json")
        if isinstance(brand_profile, BaseModel)
        else (dict(brand_profile) if isinstance(brand_profile, dict) else {})
    )
    industry_dict = (
        industry_pack.model_dump(mode="json")
        if isinstance(industry_pack, BaseModel)
        else (dict(industry_pack) if isinstance(industry_pack, dict) else {})
    )

    return {
        "record": {
            "headline": rec_dict.get("headline", ""),
            "source": rec_dict.get("source", ""),
            "url": rec_dict.get("url", ""),
            "summary": rec_dict.get("summary", ""),
            "facts": rec_dict.get("facts", []),
            "entities": rec_dict.get("entities", []),
            "source_type": rec_dict.get("source_type", "news"),
            "published_at": str(rec_dict.get("published_at") or ""),
        },
        "brand": brand_dict,
        "industry": industry_dict,
    }


def _apply_keyword_estimate_flag(opp: Opportunity, keyword_data: Any | None) -> None:
    """Ensure SEO / trend score is flagged is_estimate when keyword metrics are absent (F09 AC2)."""
    if keyword_data is None or getattr(keyword_data, "search_volume", 0) == 0:
        opp.trend.is_estimate = True
    else:
        opp.trend.is_estimate = False


def score_record_with_prompt(  # noqa: PLR0913
    record: ResearchRecord | dict[str, Any],
    *,
    weights: ScoringWeights | None = None,
    brand_profile: dict[str, Any] | None = None,
    industry_pack: dict[str, Any] | None = None,
    llm_adapter: LLMPort | None = None,
    keyword_data: Any | None = None,
    model: str | None = None,
) -> Opportunity:
    """Evaluate record with LLM scoring prompt and compute weighted total in code (F09 AC1)."""
    prompt_text, prompt_ver = get_score_prompt_and_version()
    input_payload = _prepare_score_input(record, brand_profile, industry_pack)

    opp: Opportunity = llm.structured_call(
        stage="score",
        model_cls=Opportunity,
        prompt=prompt_text,
        input=input_payload,
        plugin=llm_adapter,
        prompt_version=prompt_ver,
        model=model,
    )

    opp.compute_weighted_total(weights)
    _apply_keyword_estimate_flag(opp, keyword_data)
    return opp


def score_record(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    record_row: dict[str, Any],
    campaign_weights: ScoringWeights | None = None,
    brand_profile: dict[str, Any] | None = None,
    industry_pack: Any | None = None,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
    keyword_data: Any | None = None,
) -> tuple[Opportunity, dict[str, Any]]:
    """Score a single verified research record and persist opportunity to database."""
    prompt_text, prompt_ver = get_score_prompt_and_version()
    payload = record_row.get("payload") or {}
    record_for_prompt = {
        "headline": payload.get("headline") or record_row.get("title", ""),
        "source": record_row.get("source", ""),
        "url": record_row.get("canonical_url") or record_row.get("url", ""),
        "summary": payload.get("summary", ""),
        "facts": payload.get("facts", []),
        "entities": payload.get("entities", []),
        "source_type": payload.get("source_type", "news"),
        "published_at": record_row.get("published_at"),
    }
    input_payload = _prepare_score_input(record_for_prompt, brand_profile, industry_pack)

    opp: Opportunity = llm.structured_call(
        stage="score",
        model_cls=Opportunity,
        prompt=prompt_text,
        input=input_payload,
        workspace_id=workspace_id,
        run_id=run_id,
        conn=conn,
        plugin=llm_adapter,
        prompt_version=prompt_ver,
        model=model,
    )

    opp.compute_weighted_total(campaign_weights)
    _apply_keyword_estimate_flag(opp, keyword_data)

    scores_data = {
        "novelty": opp.novelty.model_dump(mode="json"),
        "audience_fit": opp.audience_fit.model_dump(mode="json"),
        "brand_fit": opp.brand_fit.model_dump(mode="json"),
        "competitor_gap": opp.competitor_gap.model_dump(mode="json"),
        "trend": opp.trend.model_dump(mode="json"),
        "urgency": opp.urgency.model_dump(mode="json"),
    }

    db_row = db.upsert_opportunity(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        record_id=record_row["id"],
        scores=scores_data,
        total=Decimal(str(opp.weighted_total)),
        funnel_stage=opp.funnel_stage.lower(),
        confidence=opp.confidence_level,
        angle=opp.angle,
        status="scored",
    )
    return opp, db_row


def _fetch_verified_records_for_scoring(
    conn: psycopg.Connection[Any],
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Retrieve non-duplicate verified research records for this run (F09 AC1)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rr.id, rr.workspace_id, rr.run_id, rr.document_id,
                   rr.status, rr.schema_version, rr.payload,
                   d.title, d.canonical_url, d.url, d.published_at, d.date_status,
                   s.name AS source
            FROM research_records rr
            JOIN documents d
              ON d.workspace_id = rr.workspace_id AND d.id = rr.document_id
            LEFT JOIN sources s
              ON s.workspace_id = d.workspace_id AND s.id = d.source_id
            WHERE rr.workspace_id = %(workspace_id)s
              AND rr.run_id = %(run_id)s
              AND rr.status NOT IN ('duplicate', 'failed')
              AND d.fetch_status = 'ok'
              AND (
                  rr.status = 'verified'
                  OR d.date_status = 'verified'
                  OR COALESCE(rr.payload->>'verification_status', '') = 'verified'
              )
            ORDER BY rr.created_at ASC
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        cols = [desc[0] for desc in cur.description] if cur.description else []
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _rank_opportunities_for_run(
    conn: psycopg.Connection[Any],
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Assign 1-based ranks to opportunities ordered by total descending."""
    opps = db.list_opportunities(conn, workspace_id=workspace_id, run_id=run_id)
    ranked_opps: list[dict[str, Any]] = []
    for rank_idx, opp in enumerate(opps, start=1):
        updated = db.update_opportunity_rank(
            conn,
            workspace_id=workspace_id,
            opportunity_id=opp["id"],
            rank=rank_idx,
        )
        ranked_opps.append(updated or opp)
    return ranked_opps


def score_run(
    conn: psycopg.Connection[Any],
    run_row: dict[str, Any],
    *,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Execute opportunity scoring stage across all verified records for a run (F09 AC1)."""
    ws_id = uuid.UUID(str(run_row["workspace_id"]))
    run_id = uuid.UUID(str(run_row["id"]))

    campaign_row = db.get_campaign(conn, workspace_id=ws_id, campaign_id=run_row["campaign_id"])
    camp_config = campaign_row.get("config", {}) if campaign_row else {}
    weights_dict = camp_config.get("scoring_weights", {})
    try:
        weights = ScoringWeights(**weights_dict) if weights_dict else ScoringWeights()
    except Exception:
        weights = ScoringWeights()

    brand_row = db.get_current_brand_profile(conn, workspace_id=ws_id)
    brand_profile = brand_row.get("profile", {}) if brand_row else {}

    ws_row = db.get_workspace(conn, workspace_id=ws_id)
    ind_slug = ws_row.get("industry_pack") if ws_row else config.get_default_industry_pack()
    ws_cfg = ws_row.get("config") if ws_row else None
    industry_pack: Any = config.resolve_industry_pack_dict(ind_slug, ws_cfg) if ind_slug else {}

    records = _fetch_verified_records_for_scoring(conn, ws_id, run_id)
    for rec in records:
        score_record(
            conn,
            workspace_id=ws_id,
            run_id=run_id,
            record_row=rec,
            campaign_weights=weights,
            brand_profile=brand_profile,
            industry_pack=industry_pack,
            llm_adapter=llm_adapter,
            model=model,
        )

    return _rank_opportunities_for_run(conn, ws_id, run_id)
