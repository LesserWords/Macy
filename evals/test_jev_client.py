"""HTTP-boundary tests for the Jev Decisions client (no network)."""

import json
from typing import Any

import httpx2
import pytest

from evals.claim_benchmark import BenchmarkDataError
from evals.claim_benchmark.jev import DECISIONS_URL, ClaimDecision, JevClient


def _client(answers: list[dict[str, Any]], seen: list[httpx2.Request]) -> JevClient:
    """Reply to the Nth request with answers[N]."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = {
            "id": "gen-dec-1",
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "answers": answers[len(seen) - 1],
            "usage": {"input_tokens": 100, "output_tokens": 10, "cost": 0.00002},
        }
        return httpx2.Response(200, json=body)

    return JevClient("test-key", http=httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_decisions_empty_claims_skips_network() -> None:
    seen: list[httpx2.Request] = []
    client = _client([], seen)

    batch = client.judge_claims("Sales rose 10%.", [])

    assert batch.decisions == []
    assert batch.cost_usd == 0.0
    assert not seen


def test_decisions_request_body() -> None:
    seen: list[httpx2.Request] = []
    client = _client([{}], seen)

    client.judge_claims("Sales rose 10%.", ["Sales rose 10%.", "Sales fell."])

    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions" == DECISIONS_URL
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"source_text": "Sales rose 10%."}
    assert body["provider"] == {"data_collection": "deny"}
    assert sorted(body["questions"]) == ["claim_0", "claim_1"]
    question = body["questions"]["claim_1"]
    assert question["type"] == "choice"
    assert "Sales fell." in question["instructions"]
    assert sorted(question["criteria"]) == ["contradicted", "supported", "unverifiable"]


def test_decisions_parse_choice_refusal_and_missing() -> None:
    answers = {
        "claim_0": {
            "type": "choice",
            "choice": "supported",
            "confidence": 0.91,
            "probabilities": {"supported": 0.91, "contradicted": 0.04, "unverifiable": 0.05},
        },
        "claim_1": {"type": "choice", "choice": "contradicted"},
        "claim_2": {"type": "refusal", "refusal": "declined"},
    }
    client = _client([answers], [])

    batch = client.judge_claims("S", ["a", "b", "c", "d"])

    assert batch.decisions == [
        ClaimDecision(
            "supported", 0.91, {"supported": 0.91, "contradicted": 0.04, "unverifiable": 0.05}
        ),
        ClaimDecision("contradicted", 0.0, {}),
        ClaimDecision(None, 0.0, {}),
        ClaimDecision(None, 0.0, {}),
    ]
    assert batch.cost_usd == pytest.approx(0.00002)
    assert batch.models == {"typesafe/jev-1.13-20260917"}


def _attempts_until_error(status: int) -> int:
    """Return how many requests were made before an always-failing endpoint raised."""
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(status, json={"error": {"message": "failed"}})

    transport = httpx2.MockTransport(handler)
    client = JevClient("k", http=httpx2.Client(transport=transport), backoff=0.0)
    with pytest.raises(httpx2.HTTPStatusError):
        client.judge_claims("S", ["a"])
    return len(calls)


def test_decisions_http_error_raises_after_retries() -> None:
    assert _attempts_until_error(502) == 3


def test_decisions_split_large_source_keeps_requests_under_limit() -> None:
    paragraph = "word " * 2_400  # 12,000 bytes each
    source = "\n\n".join([paragraph, paragraph, paragraph])
    unverifiable = {"claim_0": {"type": "choice", "choice": "unverifiable", "confidence": 0.8}}
    supported = {"claim_0": {"type": "choice", "choice": "supported", "confidence": 0.7}}
    seen: list[httpx2.Request] = []
    client = _client([unverifiable, supported], seen)

    batch = client.judge_claims(source, ["claim"])

    assert len(seen) == 2
    assert all(len(request.content) <= 30_000 for request in seen)
    assert batch.decisions == [ClaimDecision("supported", 0.7, {})]
    assert batch.cost_usd == pytest.approx(0.00004)


def test_decisions_no_chunk_supported_is_unverifiable() -> None:
    paragraph = "word " * 2_400
    source = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"
    contradicted = {"claim_0": {"type": "choice", "choice": "contradicted", "confidence": 0.9}}
    client = _client([contradicted, contradicted], [])

    batch = client.judge_claims(source, ["claim"])

    assert batch.decisions == [ClaimDecision("unverifiable", 0.0, {})]


def test_decisions_oversized_paragraph_rejected() -> None:
    client = _client([{}], [])

    with pytest.raises(BenchmarkDataError, match="paragraph"):
        client.judge_claims("word " * 7_000, ["claim"])


def test_decisions_retry_transient_failures_then_succeed() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx2.ConnectError("dropped", request=request)
        if len(calls) == 2:
            return httpx2.Response(502, json={"error": {"message": "upstream"}})
        answer = {"claim_0": {"type": "choice", "choice": "supported", "confidence": 0.9}}
        return httpx2.Response(200, json={"model": "m", "answers": answer, "usage": {"cost": 0.0}})

    transport = httpx2.MockTransport(handler)
    client = JevClient("k", http=httpx2.Client(transport=transport), backoff=0.0)

    batch = client.judge_claims("S", ["a"])

    assert len(calls) == 3
    assert batch.decisions == [ClaimDecision("supported", 0.9, {})]


def test_decisions_do_not_retry_client_errors() -> None:
    assert _attempts_until_error(400) == 1
