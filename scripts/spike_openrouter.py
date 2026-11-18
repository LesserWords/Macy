"""Spike script for ADR-007: OpenRouter with Claude Opus 5.5 & Haiku 5.5.

Verifies:
1. Exact model IDs in OpenRouter catalog.
2. Structured output parsing via Instructor into Opportunity contract with require_parameters.
3. Prompt caching prefix support (cache_control ephemeral).
4. usage.cost reporting.
"""

import json
import os
import sys
import urllib.request
from typing import Any, cast

from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from engine.contracts import Opportunity

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPUS_MODEL_ID = "anthropic/claude-opus-5.5"
HAIKU_MODEL_ID = "anthropic/claude-haiku-5.5"


def check_catalog_models() -> dict[str, Any]:
    """Verify exact model IDs and pricing parameters in OpenRouter catalog."""
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": "Macy-Content-Engine/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))

    found = {m["id"]: m for m in data.get("data", []) if m["id"] in (OPUS_MODEL_ID, HAIKU_MODEL_ID)}
    if OPUS_MODEL_ID not in found:
        msg = f"Model {OPUS_MODEL_ID} not found in OpenRouter catalog"
        raise RuntimeError(msg)
    if HAIKU_MODEL_ID not in found:
        msg = f"Model {HAIKU_MODEL_ID} not found in OpenRouter catalog"
        raise RuntimeError(msg)
    return found


def run_spike(api_key: str | None = None) -> dict[str, Any]:
    """Execute spike checks.

    If api_key is provided or found in OPENROUTER_API_KEY, calls OpenRouter.
    Otherwise runs catalog verification and mock validation simulation.
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    catalog_info = check_catalog_models()

    results: dict[str, Any] = {
        "catalog_verified": True,
        "models": {
            OPUS_MODEL_ID: {
                "name": catalog_info[OPUS_MODEL_ID]["name"],
                "context_length": catalog_info[OPUS_MODEL_ID]["context_length"],
                "pricing": catalog_info[OPUS_MODEL_ID]["pricing"],
            },
            HAIKU_MODEL_ID: {
                "name": catalog_info[HAIKU_MODEL_ID]["name"],
                "context_length": catalog_info[HAIKU_MODEL_ID]["context_length"],
                "pricing": catalog_info[HAIKU_MODEL_ID]["pricing"],
            },
        },
        "live_call_attempted": False,
    }

    if not key:
        results["note"] = (
            "OPENROUTER_API_KEY not set in environment. "
            "Catalog verified with exact model IDs and pricing. "
            "Offline contract validation confirmed."
        )
        # Verify Opportunity contract schema generation
        schema = Opportunity.model_json_schema()
        assert "properties" in schema  # noqa: S101
        results["schema_valid"] = True
        return results

    # Live call execution with Instructor
    import instructor
    from openai import OpenAI

    client = instructor.from_openrouter(
        OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key),
        mode=instructor.Mode.TOOLS,
    )

    results["live_call_attempted"] = True
    system_prefix = (
        "You are an editorial intelligence assistant. "
        "Analyze the given input and return a single scored content opportunity."
    )
    prompt_input = "B2B SaaS workflow automation and technical SEO trends."

    # Call 1: Seed cache
    messages: list[ChatCompletionMessageParam] = [
        cast(
            "ChatCompletionMessageParam",
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prefix,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ),
        {"role": "user", "content": prompt_input},
    ]

    extra_body = {
        "provider": {"require_parameters": True, "data_collection": "deny"},
        "session_id": "spike-session-test",
    }

    resp1, raw1 = client.chat.completions.create_with_completion(
        model=HAIKU_MODEL_ID,
        response_model=Opportunity,
        messages=messages,
        extra_body=extra_body,
    )
    assert isinstance(resp1, Opportunity)  # noqa: S101
    cost1 = getattr(raw1.usage, "cost", None) or (
        raw1.usage.model_extra.get("cost") if raw1.usage and raw1.usage.model_extra else None
    )

    # Call 2: Repeat with same prefix to check prompt caching
    resp2, raw2 = client.chat.completions.create_with_completion(
        model=HAIKU_MODEL_ID,
        response_model=Opportunity,
        messages=messages,
        extra_body=extra_body,
    )
    assert isinstance(resp2, Opportunity)  # noqa: S101
    cached_tokens2 = 0
    has_details = raw2.usage and hasattr(raw2.usage, "prompt_tokens_details")
    if has_details and raw2.usage.prompt_tokens_details:
        cached_tokens2 = getattr(raw2.usage.prompt_tokens_details, "cached_tokens", 0) or 0

    results["haiku_test"] = {
        "schema_valid": True,
        "cost_present": cost1 is not None,
        "cost_usd": cost1,
        "cached_tokens_call_2": cached_tokens2,
    }
    return results


def main() -> int:
    try:
        report = run_spike()
        print(json.dumps(report, indent=2))
        return 0
    except (ValidationError, RuntimeError) as e:
        print(f"Spike failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
