"""Jev claim judge through the OpenRouter Decisions API (OpenSpec BM-2, ADR-026)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2

from evals.claim_benchmark import BenchmarkDataError, rules

# Still under /alpha/ on 2026-11-10 (model page, tutorial and API reference). Change here only.
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 524, 529})
Label = Literal["supported", "contradicted", "unverifiable"]
CRITERIA: dict[str, str] = {
    "supported": (
        "source_text states the claim, including every number, date, name, role, "
        "direction, scope and cause."
    ),
    "contradicted": "source_text states something incompatible with the claim.",
    "unverifiable": "source_text does not state the claim.",
}


@dataclass(frozen=True)
class ClaimDecision:
    label: Label | None  # None: refused or missing, so the LLM judge decides.
    confidence: float
    probabilities: dict[str, float]


@dataclass
class DecisionBatch:
    decisions: list[ClaimDecision]
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)


def _question(claim: str) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": f"Compare this claim with source_text: {claim}",
        "criteria": CRITERIA,
    }


def _encode(source: str, questions: dict[str, Any]) -> bytes:
    body = {
        "model": rules.JEV_MODEL,
        "state": {"source_text": source},
        "questions": questions,
        "provider": {"data_collection": "deny"},
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _chunks(source: str, questions: dict[str, Any]) -> Iterator[str]:
    """Pack whole paragraphs so no request body exceeds the frozen byte limit."""
    current = ""
    for paragraph in source.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(_encode(candidate, questions)) <= rules.MAX_STATE_BYTES:
            current = candidate
            continue
        if len(_encode(paragraph, questions)) > rules.MAX_STATE_BYTES:
            msg = f"A paragraph is too large for one request ({rules.MAX_STATE_BYTES} bytes)"
            raise BenchmarkDataError(msg)
        yield current
        current = paragraph
    yield current


def _parse(answer: dict[str, Any] | None) -> ClaimDecision:
    if not answer or answer.get("type") != "choice" or answer.get("choice") not in CRITERIA:
        return ClaimDecision(None, 0.0, {})
    return ClaimDecision(
        answer["choice"],
        float(answer.get("confidence") or 0.0),
        dict(answer.get("probabilities") or {}),
    )


def _merge(per_chunk: Sequence[ClaimDecision]) -> ClaimDecision:
    """Supported if any chunk supports it; otherwise unverifiable, or refused if all refused."""
    if len(per_chunk) == 1:
        return per_chunk[0]
    supported = [d for d in per_chunk if d.label == "supported"]
    if supported:
        return max(supported, key=lambda d: d.confidence)
    if all(d.label is None for d in per_chunk):
        return ClaimDecision(None, 0.0, {})
    return ClaimDecision("unverifiable", 0.0, {})


class JevClient:
    """Judges claims from one source, one request per source chunk."""

    def __init__(
        self,
        api_key: str,
        http: httpx2.Client | None = None,
        *,
        attempts: int = 3,
        backoff: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._http = http or httpx2.Client(timeout=60.0)
        self._attempts = attempts
        self._backoff = backoff

    def judge_claims(self, source: str, claims: Sequence[str]) -> DecisionBatch:
        if not claims:
            return DecisionBatch(decisions=[])
        questions = {f"claim_{index}": _question(claim) for index, claim in enumerate(claims)}
        batch = DecisionBatch(decisions=[])
        per_chunk: list[list[ClaimDecision]] = []
        for chunk in _chunks(source, questions):
            data = self._post(_encode(chunk, questions))
            batch.cost_usd += float((data.get("usage") or {}).get("cost") or 0.0)
            if data.get("model"):
                batch.models.add(str(data["model"]))
            answers = data.get("answers") or {}
            per_chunk.append([_parse(answers.get(name)) for name in questions])
        batch.decisions = [_merge([chunk[i] for chunk in per_chunk]) for i in range(len(claims))]
        return batch

    def _post(self, body: bytes) -> dict[str, Any]:
        """POST, retrying dropped connections and transient server errors only."""
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        for attempt in range(1, self._attempts + 1):
            last = attempt == self._attempts
            try:
                response = self._http.post(DECISIONS_URL, content=body, headers=headers)
            except httpx2.TransportError:
                if last:
                    raise
            else:
                if response.status_code not in RETRY_STATUSES or last:
                    response.raise_for_status()
                    data: dict[str, Any] = response.json()
                    return data
            time.sleep(self._backoff * 2 ** (attempt - 1))
        msg = "attempts must be at least 1"
        raise ValueError(msg)
