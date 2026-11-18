"""Structured LLM call helper with Instructor, OpenRouter caching, retries, and usage logging.

Implements ADR-002, ADR-007, and ADR-016.
"""

import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Any, cast

import psycopg
from pydantic import BaseModel, ValidationError

from engine import db
from engine.integrations.openrouter import OpenRouterLLM
from engine.integrations.ports import LLMPort

logger = logging.getLogger(__name__)

DEFAULT_REASONING_STAGES: set[str] = {"write", "evaluate"}


def build_openrouter_call_params(  # noqa: PLR0913
    *,
    prompt: str,
    input_data: BaseModel | dict[str, Any] | str,
    run_id: uuid.UUID | None = None,
    stage: str = "research",
    model: str = "anthropic/claude-haiku-5.5",
    data_policy: str = "deny",
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Construct messages with stable cached prefix and OpenRouter routing metadata."""
    user_str = (
        input_data.model_dump_json()
        if isinstance(input_data, BaseModel)
        else (json.dumps(input_data) if isinstance(input_data, dict) else input_data)
    )

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {"role": "user", "content": user_str},
    ]

    extra_body: dict[str, Any] = {
        "provider": {
            "require_parameters": True,
            "data_collection": data_policy,
        },
    }
    if run_id is not None:
        extra_body["session_id"] = str(run_id)

    effort = reasoning_effort or ("high" if stage in DEFAULT_REASONING_STAGES else None)
    if effort:
        extra_body["reasoning_effort"] = effort

    return {
        "model": model,
        "messages": messages,
        "extra_body": extra_body,
    }


def _extract_cost_and_tokens(completion: Any) -> tuple[Decimal, int, int, int, str | None]:
    usage = getattr(completion, "usage", None)
    cost: float | None = None
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    generation_id = getattr(completion, "id", None)

    if usage is not None:
        cost = getattr(usage, "cost", None)
        if cost is None and hasattr(usage, "model_extra") and usage.model_extra:
            cost = usage.model_extra.get("cost")

        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
            cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

    cost_usd = (
        Decimal(str(cost)).quantize(Decimal("0.000001"))
        if cost is not None
        else Decimal("0.000000")
    )
    return cost_usd, input_tokens, output_tokens, cached_tokens, generation_id


def _execute_non_openrouter[T: BaseModel](
    adapter: LLMPort,
    model_cls: type[T],
    prompt: str,
    max_retries: int,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        feedback_prompt = prompt
        if attempt > 0 and last_error:
            feedback_prompt += f"\nValidation error in previous attempt: {last_error}"

        raw_dict = adapter.complete(feedback_prompt, schema=model_cls)
        try:
            return model_cls.model_validate(raw_dict)
        except ValidationError as e:
            last_error = e

    if last_error is not None:
        raise last_error
    msg = f"Failed to complete structured call for {model_cls.__name__}"
    raise RuntimeError(msg)


def _execute_openrouter[T: BaseModel](
    adapter: OpenRouterLLM,
    target_model: str,
    model_cls: type[T],
    call_params: dict[str, Any],
    max_retries: int,
) -> tuple[T, Any]:
    try:
        raw_result = adapter.instructor_client.chat.completions.create_with_completion(
            model=target_model,
            response_model=model_cls,
            messages=call_params["messages"],
            max_retries=max_retries,
            extra_body=call_params["extra_body"],
        )
        return cast(tuple[T, Any], raw_result)
    except Exception as exc:  # Fallback to JSON mode (ADR-007)
        logger.warning(
            "Structured call failed for %s (%s); using JSON fallback",
            target_model,
            type(exc).__name__,
        )
        schema_message: Any = {
            "role": "user",
            "content": (
                "Reply with only a JSON object matching this JSON Schema:\n"
                f"{json.dumps(model_cls.model_json_schema())}"
            ),
        }
        json_resp = adapter.client.chat.completions.create(
            model=target_model,
            messages=[*call_params["messages"], schema_message],
            response_format={"type": "json_object"},
            extra_body=call_params["extra_body"],
        )
        content = (
            json_resp.choices[0].message.content
            if json_resp.choices and json_resp.choices[0].message
            else "{}"
        )
        return model_cls.model_validate_json(content or "{}"), json_resp


def _persist_usage_and_payload(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None,
    stage: str,
    vendor: str,
    model: str,
    cost_usd: Decimal,
    latency_ms: int,
    prompt_version: str | None,
    raw_completion: Any,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> None:
    cost_calc, in_toks, out_toks, cached_toks, gen_id = _extract_cost_and_tokens(raw_completion)
    final_cost = cost_usd if vendor != "openrouter" else cost_calc
    usage_id = db.record_usage(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        stage=stage,
        kind="llm",
        vendor=vendor,
        model=model,
        generation_id=gen_id,
        input_tokens=in_toks,
        output_tokens=out_toks,
        cached_tokens=cached_toks,
        cost_usd=final_cost,
        latency_ms=latency_ms,
        prompt_version=prompt_version,
    )
    db.record_llm_payload(
        conn,
        workspace_id=workspace_id,
        usage_id=usage_id,
        request=request_payload,
        response=response_payload,
    )


def _resolve_llm_adapter(
    conn: psycopg.Connection[Any] | None,
    workspace_id: uuid.UUID | None,
    plugin: LLMPort | None,
) -> LLMPort:
    """Resolve explicit LLM plugin or load workspace configured LLM integration."""
    if plugin is not None:
        return plugin
    if conn is not None and workspace_id is not None:
        from engine.integrations.config import plugin_config
        from engine.integrations.registry import IntegrationMissing

        try:
            plugin_cls, settings_obj = plugin_config(conn, workspace_id=workspace_id, port="llm")
            return cast(LLMPort, plugin_cls(settings=settings_obj))
        except IntegrationMissing:
            pass
    return OpenRouterLLM()


def _resolve_non_openrouter_model(adapter: LLMPort, stage: str, explicit_model: str | None) -> str:
    """Resolve model name for non-OpenRouter LLM adapters including stage overrides."""
    if explicit_model:
        return explicit_model
    settings_obj = getattr(adapter, "settings", None)
    stage_map = getattr(settings_obj, "models_by_stage", None)
    if isinstance(stage_map, dict) and stage in stage_map and stage_map[stage]:
        return str(stage_map[stage])
    return str(getattr(settings_obj, "model_name", "fake"))


def _format_non_openrouter_prompt(
    prompt: str,
    input_payload: dict[str, Any] | str,
) -> str:
    """Append serialized dynamic input to non-OpenRouter adapter prompts when non-empty."""
    if not input_payload:
        return prompt
    serialized = (
        input_payload
        if isinstance(input_payload, str)
        else json.dumps(input_payload, default=str, ensure_ascii=False)
    )
    return f"{prompt}\n\nInput:\n{serialized}"


def structured_call[T: BaseModel](  # noqa: PLR0913
    *,
    stage: str,
    model_cls: type[T],
    prompt: str,
    input: BaseModel | dict[str, Any] | str,
    workspace_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    conn: psycopg.Connection[Any] | None = None,
    plugin: LLMPort | None = None,
    max_retries: int = 2,
    reasoning_effort: str | None = None,
    prompt_version: str | None = None,
    model: str | None = None,
) -> T:
    """Execute a structured completion returning validated model instance and logging usage."""
    start_time = time.perf_counter()
    adapter = _resolve_llm_adapter(conn, workspace_id, plugin)
    input_payload: dict[str, Any] | str = (
        input.model_dump()
        if isinstance(input, BaseModel)
        else (dict(input) if isinstance(input, dict) else input)
    )

    if not isinstance(adapter, OpenRouterLLM):
        effective_prompt = _format_non_openrouter_prompt(prompt, input_payload)
        result = _execute_non_openrouter(adapter, model_cls, effective_prompt, max_retries)
        if conn is not None and workspace_id is not None:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            is_fake = adapter.__class__.__name__.startswith("Fake")
            vendor_name = "fake" if is_fake else "custom"
            resolved_model = _resolve_non_openrouter_model(adapter, stage, model)
            _persist_usage_and_payload(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                stage=stage,
                vendor=vendor_name,
                model=resolved_model,
                cost_usd=Decimal("0.000000"),
                latency_ms=latency_ms,
                prompt_version=prompt_version,
                raw_completion=None,
                request_payload={"prompt": prompt, "input": input_payload},
                response_payload=result.model_dump(mode="json"),
            )
        return result

    target_model = model or adapter.settings.models_by_stage.get(
        stage, adapter.settings.default_model
    )
    call_params = build_openrouter_call_params(
        prompt=prompt,
        input_data=input,
        run_id=run_id,
        stage=stage,
        model=target_model,
        data_policy=adapter.settings.data_policy,
        reasoning_effort=reasoning_effort,
    )
    response_model, raw_completion = _execute_openrouter(
        adapter, target_model, model_cls, call_params, max_retries
    )
    if conn is not None and workspace_id is not None:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        _persist_usage_and_payload(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            stage=stage,
            vendor="openrouter",
            model=target_model,
            cost_usd=Decimal("0.000000"),
            latency_ms=latency_ms,
            prompt_version=prompt_version,
            raw_completion=raw_completion,
            request_payload={
                "messages": call_params["messages"],
                "extra_body": call_params["extra_body"],
            },
            response_payload=response_model.model_dump(mode="json"),
        )
    return response_model
