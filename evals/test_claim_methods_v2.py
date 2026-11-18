"""Routing tests for plain_tuned and per_claim_v2 with scripted model replies (no network)."""

from typing import Any

from evals.claim_benchmark.harness import (
    EXTRACT_PROMPT_V2,
    PLAIN_TUNED_PROMPT,
    Variant,
    per_claim_v2,
    plain_tuned,
)

SOURCE = "Sales rose 10% in 2025. The firm expects to open 3 stores in 2027."
VARIANT = Variant(
    "c/x",
    "c",
    "en-US",
    "T",
    SOURCE,
    ("Sales rose 10% in 2025.", "The firm expects 5 stores in 2027.", "Readers should act now."),
)


class Scripted:
    """Replies by response schema name and records every prompt it receives."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.prompts: list[tuple[str, str]] = []

    def healthcheck(self) -> None: ...

    def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        self.prompts.append((schema.__name__, prompt))
        reply = self.replies[schema.__name__]
        return dict(reply(prompt)) if callable(reply) else dict(reply)


def test_plain_tuned_ignores_not_checkable_findings() -> None:
    llm = Scripted(
        {
            "PlainTunedReport": {
                "findings": [
                    {
                        "quote": "Readers should act now.",
                        "kind": "not_checkable",
                        "reason": "advice",
                    }
                ]
            }
        }
    )

    result = plain_tuned(llm, VARIANT)

    assert result.publish is True
    assert result.flagged == []


def test_plain_tuned_flags_unsupported_and_contradicted() -> None:
    llm = Scripted(
        {
            "PlainTunedReport": {
                "findings": [
                    {
                        "quote": "The firm expects 5 stores in 2027.",
                        "kind": "contradicted",
                        "reason": "3",
                    },
                    {
                        "quote": "Readers should act now.",
                        "kind": "not_checkable",
                        "reason": "advice",
                    },
                ]
            }
        }
    )

    result = plain_tuned(llm, VARIANT)

    assert result.publish is False
    assert result.flagged == ["The firm expects 5 stores in 2027."]


def test_per_claim_v2_judges_attributed_and_skips_advice() -> None:
    def judge(prompt: str) -> dict[str, str]:
        if "Sales rose 10% in 2025." in prompt:
            return {"status": "supported", "evidence_quote": "Sales rose 10% in 2025."}
        return {
            "status": "contradicted",
            "evidence_quote": "The firm expects to open 3 stores in 2027.",
        }

    llm = Scripted(
        {
            "ClaimListV2": {
                "claims": [
                    {"text": "Sales rose 10% in 2025.", "kind": "fact"},
                    {"text": "The firm expects 5 stores in 2027.", "kind": "attributed"},
                    {"text": "Readers should act now.", "kind": "advice"},
                ]
            },
            "ClaimJudgment": judge,
        }
    )

    result = per_claim_v2(llm, VARIANT)

    judged = [prompt for name, prompt in llm.prompts if name == "ClaimJudgment"]
    assert len(judged) == 2
    assert not any("Readers should act now." in prompt for prompt in judged)
    assert result.publish is False
    assert result.flagged == ["The firm expects 5 stores in 2027."]


def test_v2_prompts_cover_advice_and_attributed_statements() -> None:
    for prompt in (PLAIN_TUNED_PROMPT, EXTRACT_PROMPT_V2):
        lowered = prompt.lower()
        assert "advice" in lowered
        assert "attributed" in lowered
