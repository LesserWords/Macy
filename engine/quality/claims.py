"""Runtime claim verification decoupled from evals/ and httpx2 (ADR-026, ADR-027)."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

from engine import safe_http
from engine.integrations.ports import LLMPort

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
JEV_CONFIDENCE_THRESHOLD = 0.75
JEV_MAX_STATE_CHARS = 24_000

ClaimVerdict = Literal["supported", "refuted", "unverifiable"]

_JEV_CRITERIA: dict[str, str] = {
    "supported": (
        "source_text states the claim, including every number, date, name, role, "
        "direction, scope and cause."
    ),
    "contradicted": "source_text states something incompatible with the claim.",
    "unverifiable": "source_text does not state the claim.",
}
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "from",
        "into",
        "across",
        "dos",
        "das",
        "com",
        "para",
        "por",
        "uma",
        "uns",
        "que",
        "em",
    }
)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)*%?")
_WORD_RE = re.compile(r"[0-9a-z]{3,}")


@dataclass(frozen=True)
class DeterministicClaimCheck:
    """Result of deterministic numeric, lexical, and quote span verification."""

    numbers_ok: bool
    preliminary_verdict: ClaimVerdict
    confidence: float
    evidence_quote: str


@dataclass(frozen=True)
class ClaimVerificationResult:
    """Normalized runtime verification outcome for a single factual claim."""

    claim: str
    verdict: ClaimVerdict
    confidence: float
    evidence_quote: str
    cost_usd: Decimal
    judge: str


class _LLMClaimVerdict(BaseModel):
    verdict: str
    confidence: float = 0.5
    evidence_quote: str = ""


def _fold_ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalize_single_separator(body: str, sep: str) -> str:
    parts = body.split(sep)
    if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) >= 1):
        return body.replace(sep, "")
    return body.replace(",", ".")


def _canonical_number(raw: str) -> str:
    body = raw.strip().rstrip("%")
    last_comma, last_dot = body.rfind(","), body.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        dec_sep, thou_sep = (",", ".") if last_comma > last_dot else (".", ",")
        norm = body.replace(thou_sep, "").replace(dec_sep, ".")
    elif last_comma >= 0:
        norm = _normalize_single_separator(body, ",")
    elif last_dot >= 0:
        norm = _normalize_single_separator(body, ".")
    else:
        norm = body
    try:
        formatted = format(Decimal(norm).normalize(), "f")
        return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
    except InvalidOperation:
        return body


def _extract_numbers(text: str) -> list[str]:
    return [_canonical_number(m.group(0)) for m in _NUMBER_RE.finditer(text)]


def _content_tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(_fold_ascii(text)) if w not in _STOPWORDS}


def _select_best_segment(
    claim_text: str,
    source_text: str,
) -> tuple[str, float, tuple[str, ...]]:
    segments = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", source_text) if s.strip()]
    if not segments:
        return "", 0.0, ()
    claim_tokens = _content_tokens(claim_text)
    claim_num_set = set(_extract_numbers(claim_text))
    best_seg, best_score, best_lex = segments[0], -1.0, 0.0
    for seg in segments:
        seg_tokens = _content_tokens(seg)
        lex = len(claim_tokens & seg_tokens) / max(1, len(claim_tokens))
        num_bonus = (
            0.35 * len(claim_num_set & set(_extract_numbers(seg))) / max(1, len(claim_num_set))
            if claim_num_set
            else 0.0
        )
        score = lex + num_bonus
        if score > best_score:
            best_seg, best_score, best_lex = seg, score, lex
    return best_seg, best_lex, tuple(_extract_numbers(best_seg))


def _score_preliminary_verdict(
    *,
    numbers_ok: bool,
    best_seg: str,
    best_lex: float,
    seg_nums: tuple[str, ...],
    claim_nums: list[str],
) -> tuple[ClaimVerdict, float]:
    if not numbers_ok:
        has_conflict = bool(set(seg_nums) - set(claim_nums))
        verdict: ClaimVerdict = "refuted" if (best_lex >= 0.40 and has_conflict) else "unverifiable"
        return verdict, (0.85 if verdict == "refuted" else 0.45)

    threshold = 0.45 if claim_nums else 0.55
    if best_lex >= threshold and best_seg:
        return "supported", round(min(0.95, 0.55 + 0.40 * best_lex), 2)
    return "unverifiable", 0.45


def verify_claim_deterministic(claim_text: str, source_text: str) -> DeterministicClaimCheck:
    """Verify claim numbers against source passages and extract the closest verbatim span."""
    claim_nums = _extract_numbers(claim_text)
    source_nums = set(_extract_numbers(source_text))
    numbers_ok = all(n in source_nums for n in claim_nums)
    best_seg, best_lex, seg_nums = _select_best_segment(claim_text, source_text)
    verdict, conf = _score_preliminary_verdict(
        numbers_ok=numbers_ok,
        best_seg=best_seg,
        best_lex=best_lex,
        seg_nums=seg_nums,
        claim_nums=claim_nums,
    )
    return DeterministicClaimCheck(
        numbers_ok=numbers_ok,
        preliminary_verdict=verdict,
        confidence=conf,
        evidence_quote=best_seg[:400],
    )


def normalize_verdict(raw: str | None) -> ClaimVerdict:
    """Normalize Jev or LLM verdict strings to canonical supported/refuted/unverifiable."""
    clean = str(raw or "").strip().lower()
    if clean == "supported":
        return "supported"
    if clean in {"refuted", "contradicted"}:
        return "refuted"
    return "unverifiable"


def _post_jev_decisions(
    source_text: str,
    claims: Sequence[str],
    jev_api_key: str,
) -> tuple[dict[int, tuple[ClaimVerdict | None, float]], Decimal]:
    questions = {
        f"claim_{idx}": {
            "type": "choice",
            "instructions": f"Compare this claim with source_text: {claim}",
            "criteria": _JEV_CRITERIA,
        }
        for idx, claim in enumerate(claims)
    }
    resp = safe_http.safe_request(
        "POST",
        DECISIONS_URL,
        headers={"Authorization": f"Bearer {jev_api_key}"},
        json_data={
            "model": JEV_MODEL,
            "state": {"source_text": source_text[:JEV_MAX_STATE_CHARS]},
            "questions": questions,
            "provider": {"data_collection": "deny"},
        },
        timeout=30.0,
    )
    if resp.status_code >= 400:
        msg = f"Jev decisions API returned HTTP {resp.status_code}"
        raise RuntimeError(msg)

    data = resp.json()
    raw_cost = (data.get("usage") or {}).get("cost") or 0.0
    total_cost = Decimal(str(raw_cost)).quantize(Decimal("0.000001"))
    answers = data.get("answers") or {}
    parsed: dict[int, tuple[ClaimVerdict | None, float]] = {}
    for idx in range(len(claims)):
        ans = answers.get(f"claim_{idx}")
        if not isinstance(ans, dict) or ans.get("type") != "choice" or "choice" not in ans:
            parsed[idx] = (None, 0.0)
        else:
            parsed[idx] = (normalize_verdict(ans["choice"]), float(ans.get("confidence") or 0.0))
    return parsed, total_cost


def _is_unconfigured_fake_llm(llm: LLMPort | None) -> bool:
    if llm is None or not llm.__class__.__name__.startswith("Fake"):
        return False
    default_resp = getattr(getattr(llm, "settings", None), "default_response", None)
    return default_resp == {"result": "fake_llm_completion"}


def _escalate_claim_to_llm(
    llm: LLMPort,
    claim_text: str,
    source_text: str,
) -> tuple[ClaimVerdict, float, str]:
    prompt = (
        "Verify whether SOURCE supports CLAIM. Reply JSON with keys verdict "
        "(supported|refuted|unverifiable), confidence (0-1), and verbatim evidence_quote.\n"
        f"CLAIM: {claim_text}\nSOURCE:\n{source_text[:JEV_MAX_STATE_CHARS]}"
    )
    raw = llm.complete(prompt, schema=_LLMClaimVerdict)
    parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
    return (
        normalize_verdict(str(parsed.get("verdict") or "unverifiable")),
        float(parsed.get("confidence") or 0.5),
        str(parsed.get("evidence_quote") or "").strip(),
    )


def _apply_guards(
    *,
    verdict: ClaimVerdict,
    confidence: float,
    quote: str,
    det: DeterministicClaimCheck,
    combined_source: str,
) -> tuple[ClaimVerdict, float, str]:
    safe_quote = quote if (quote and quote in combined_source) else det.evidence_quote
    if verdict == "supported" and (not det.numbers_ok or not safe_quote):
        return "unverifiable", min(confidence, 0.5), safe_quote
    return verdict, confidence, safe_quote


def _distribute_costs(total_cost: Decimal, count: int) -> list[Decimal]:
    if count <= 0 or total_cost <= Decimal("0"):
        return [Decimal("0.000000")] * count
    per = (total_cost / Decimal(count)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    rem = total_cost - (per * count)
    return [per + rem if idx == 0 else per for idx in range(count)]


def _resolve_single_claim(
    claim_text: str,
    combined_source: str,
    jev_ans: tuple[ClaimVerdict | None, float] | None,
    active_llm: LLMPort | None,
    cost_usd: Decimal,
) -> ClaimVerificationResult:
    det = verify_claim_deterministic(claim_text, combined_source)
    judge = "deterministic"
    verdict, conf, quote = det.preliminary_verdict, det.confidence, det.evidence_quote

    if jev_ans is not None:
        jev_verdict, jev_conf = jev_ans
        if jev_verdict is not None and jev_conf >= JEV_CONFIDENCE_THRESHOLD:
            verdict, conf, judge = jev_verdict, jev_conf, "jev"
        elif active_llm is not None:
            verdict, conf, quote = _escalate_claim_to_llm(active_llm, claim_text, combined_source)
            judge = "llm"
        elif jev_verdict is not None:
            verdict, conf, judge = jev_verdict, jev_conf, "jev"
    elif active_llm is not None:
        verdict, conf, quote = _escalate_claim_to_llm(active_llm, claim_text, combined_source)
        judge = "llm"

    final_verdict, final_conf, final_quote = _apply_guards(
        verdict=verdict,
        confidence=conf,
        quote=quote,
        det=det,
        combined_source=combined_source,
    )
    return ClaimVerificationResult(
        claim=claim_text,
        verdict=final_verdict,
        confidence=round(final_conf, 4),
        evidence_quote=final_quote,
        cost_usd=cost_usd,
        judge=judge,
    )


def verify_claims_against_sources(
    claims: Sequence[str],
    passages: Sequence[str],
    *,
    llm: LLMPort | None = None,
    jev_api_key: str | None = None,
) -> list[ClaimVerificationResult]:
    """Verify factual claims using numeric/quote guards, optional Jev HTTP API, and LLMPort."""
    clean_claims = [c.strip() for c in claims if c and c.strip()]
    if not clean_claims:
        return []
    combined_source = "\n\n".join(p.strip() for p in passages if p and p.strip())
    jev_answers: dict[int, tuple[ClaimVerdict | None, float]] = {}
    total_cost = Decimal("0.000000")
    if jev_api_key and combined_source:
        jev_answers, total_cost = _post_jev_decisions(combined_source, clean_claims, jev_api_key)

    active_llm = None if _is_unconfigured_fake_llm(llm) else llm
    per_costs = _distribute_costs(total_cost, len(clean_claims))
    return [
        _resolve_single_claim(
            claim,
            combined_source,
            jev_answers.get(idx),
            active_llm,
            per_costs[idx],
        )
        for idx, claim in enumerate(clean_claims)
    ]
