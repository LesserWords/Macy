"""Tests for LLM structured call helper, retries, prompt caching, and usage ledger.

Implements ADR-002, ADR-007, and ADR-016.
"""

import json
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import SecretStr, ValidationError

from engine import db
from engine.contracts import Opportunity
from engine.integrations.fakes import FakeLLM


def _sample_opportunity_dict() -> dict[str, Any]:
    sub = {"score": 85.0, "evidence": "Strong evidence"}
    return {
        "title": "Myopia Prevention in Schools",
        "angle": "Public health initiative",
        "funnel_stage": "TOFU",
        "confidence": 0.9,
        "novelty": sub,
        "audience_fit": sub,
        "brand_fit": sub,
        "competitor_gap": sub,
        "trend": sub,
        "urgency": sub,
        "weighted_total": 85.0,
    }


def test_structured_call_with_fake_llm() -> None:
    from engine.llm import structured_call

    fake_data = _sample_opportunity_dict()
    fake = FakeLLM(settings=FakeLLM.Settings(default_response=fake_data))

    result = structured_call(
        stage="score",
        model_cls=Opportunity,
        prompt="Score this research topic",
        input={"topic": "Myopia"},
        plugin=fake,
    )
    assert isinstance(result, Opportunity)
    assert result.title == "Myopia Prevention in Schools"
    assert result.confidence == 0.9


def test_structured_call_openrouter_messages_and_sticky_routing() -> None:
    from engine.llm import build_openrouter_call_params

    run_id = uuid.uuid4()
    params = build_openrouter_call_params(
        prompt="Stable system prompt prefix",
        input_data={"key": "value"},
        run_id=run_id,
        stage="brief",
        model="anthropic/claude-opus-5.5",
        data_policy="deny",
    )

    messages = params["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    # Stable prefix with cache_control ephemeral
    assert messages[0]["content"][0]["type"] == "text"
    assert messages[0]["content"][0]["text"] == "Stable system prompt prefix"
    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    # Sticky routing session_id
    assert params["extra_body"]["session_id"] == str(run_id)
    assert params["extra_body"]["provider"]["require_parameters"] is True
    assert params["extra_body"]["provider"]["data_collection"] == "deny"


def test_structured_call_json_mode_fallback() -> None:
    from engine.llm import structured_call

    call_count = 0

    class FailThenSucceedLLM:
        def healthcheck(self) -> None:
            pass

        def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt fails schema validation
                return {"invalid": "data"}
            # Fallback returns valid schema
            return _sample_opportunity_dict()

    adapter = FailThenSucceedLLM()
    res = structured_call(
        stage="score",
        model_cls=Opportunity,
        prompt="System prompt",
        input={"topic": "Myopia"},
        plugin=adapter,
        max_retries=1,
    )
    assert isinstance(res, Opportunity)
    assert call_count >= 2


def _setup_test_workspace_and_run(
    conn: psycopg.Connection[Any], prefix: str
) -> tuple[uuid.UUID, uuid.UUID]:
    from datetime import UTC, datetime, timedelta

    now_id = uuid.uuid4().hex[:8]
    ws_id = db.create_workspace(
        conn,
        slug=f"ws-{prefix}-{now_id}",
        name=f"{prefix} Test WS",
        industry_pack="general",
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, ui_locale) VALUES (%s, 'pt-BR') RETURNING id",
            (f"u-{prefix}-{now_id}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]

    camp_id = db.create_campaign(
        conn,
        workspace_id=ws_id,
        name=f"{prefix} Campaign",
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        config={},
        config_schema_version=1,
        created_by=user_id,
    )
    run_id = db.create_run(
        conn,
        workspace_id=ws_id,
        campaign_id=camp_id,
        trigger="manual",
        window_start=datetime.now(UTC),
        window_end=datetime.now(UTC) + timedelta(days=7),
        run_cap_usd=Decimal("10.00"),
        code_version="v1.0",
        pack_version="pack-1.0",
        created_by=user_id,
    )
    return ws_id, run_id


def test_structured_call_records_usage_and_payload_in_db(db_conn: psycopg.Connection[Any]) -> None:
    from engine.llm import structured_call

    ws_id, run_id = _setup_test_workspace_and_run(db_conn, "db")

    fake_data = _sample_opportunity_dict()
    fake = FakeLLM(settings=FakeLLM.Settings(default_response=fake_data))

    opp = structured_call(
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="score",
        model_cls=Opportunity,
        prompt="Score topic",
        input={"topic": "Myopia in children"},
        plugin=fake,
        prompt_version="prompt-v1.0",
    )
    assert isinstance(opp, Opportunity)

    # Check usage ledger
    usage_rows = db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(usage_rows) == 1
    row = usage_rows[0]
    assert row["stage"] == "score"
    assert row["kind"] == "llm"
    assert row["run_id"] == run_id
    assert row["cost_usd"] >= Decimal("0.000000")

    # Check payload table
    payload = db.get_llm_payload(db_conn, workspace_id=ws_id, usage_id=row["id"])
    assert payload is not None
    assert "request" in payload
    assert "response" in payload


def test_openrouter_adapter_healthcheck_and_complete() -> None:
    from engine.integrations.openrouter import OpenRouterLLM

    # Healthcheck failure on empty API key
    empty_settings = OpenRouterLLM.Settings(api_key=None)
    empty_settings.api_key = None
    adapter_empty = OpenRouterLLM(settings=empty_settings)
    adapter_empty.settings.api_key = None
    with pytest.raises(ValueError, match="not configured"):
        adapter_empty.healthcheck()

    # Complete with mock schema completion
    mock_client = MagicMock()
    mock_opp = Opportunity.model_validate(_sample_opportunity_dict())
    mock_instructor = MagicMock()
    mock_instructor.chat.completions.create.return_value = mock_opp
    adapter = OpenRouterLLM(
        settings=OpenRouterLLM.Settings(api_key=SecretStr("test-key")),
        client=mock_client,
        instructor_client=mock_instructor,
    )

    res_dict = adapter.complete(
        prompt="Score topic",
        schema=Opportunity,
        system="System instructions",
        extra_body={"custom": 1},
    )
    assert isinstance(res_dict, dict)
    assert res_dict["title"] == "Myopia Prevention in Schools"

    # Complete with non-BaseModel schema return
    mock_instructor.chat.completions.create.return_value = {"custom": "dict"}
    res_raw = adapter.complete("Prompt", schema=dict)
    assert res_raw == {"custom": "dict"}


def test_structured_call_openrouter_success_with_db(db_conn: psycopg.Connection[Any]) -> None:
    from engine.integrations.openrouter import OpenRouterLLM
    from engine.llm import structured_call

    ws_id, run_id = _setup_test_workspace_and_run(db_conn, "or")

    mock_client = MagicMock()
    mock_instructor = MagicMock()
    adapter = OpenRouterLLM(
        settings=OpenRouterLLM.Settings(api_key=SecretStr("test-key")),
        client=mock_client,
        instructor_client=mock_instructor,
    )

    mock_opp = Opportunity.model_validate(_sample_opportunity_dict())

    # Setup raw completion mock with usage details
    raw_completion = MagicMock()
    raw_completion.id = "gen-test-or-999"
    usage_mock = MagicMock()
    usage_mock.cost = 0.000125
    usage_mock.prompt_tokens = 45
    usage_mock.completion_tokens = 30
    prompt_tokens_details = MagicMock()
    prompt_tokens_details.cached_tokens = 15
    usage_mock.prompt_tokens_details = prompt_tokens_details
    raw_completion.usage = usage_mock

    mock_instructor.chat.completions.create_with_completion.return_value = (
        mock_opp,
        raw_completion,
    )

    # Call with BaseModel input and default reasoning stage (write/evaluate)
    input_model = Opportunity.model_validate(_sample_opportunity_dict())
    opp = structured_call(
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="write",
        model_cls=Opportunity,
        prompt="Write article",
        input=input_model,
        plugin=adapter,
        prompt_version="prompt-write-v1",
    )
    assert opp.title == mock_opp.title

    # Verify usage row in DB
    usage_rows = db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(usage_rows) == 1
    row = usage_rows[0]
    assert row["vendor"] == "openrouter"
    assert row["cost_usd"] == Decimal("0.000125")
    assert row["input_tokens"] == 45
    assert row["output_tokens"] == 30
    assert row["cached_tokens"] == 15
    assert row["generation_id"] == "gen-test-or-999"

    # Verify payload
    payload = db.get_llm_payload(db_conn, workspace_id=ws_id, usage_id=row["id"])
    assert payload is not None
    assert "messages" in payload["request"]
    assert payload["response"]["title"] == mock_opp.title

    # Verify cross-tenant isolation on payload
    other_ws = uuid.uuid4()
    assert db.get_llm_payload(db_conn, workspace_id=other_ws, usage_id=row["id"]) is None


def test_structured_call_openrouter_json_fallback_with_db(db_conn: psycopg.Connection[Any]) -> None:
    from engine.integrations.openrouter import OpenRouterLLM
    from engine.llm import structured_call

    ws_id, run_id = _setup_test_workspace_and_run(db_conn, "fb")

    mock_client = MagicMock()
    mock_instructor = MagicMock()
    adapter = OpenRouterLLM(
        settings=OpenRouterLLM.Settings(api_key=SecretStr("test-key")),
        client=mock_client,
        instructor_client=mock_instructor,
    )

    # Instructor fails (e.g. schema mismatch), triggering JSON mode fallback
    mock_instructor.chat.completions.create_with_completion.side_effect = RuntimeError(
        "Strict schema rejected by endpoint"
    )

    # Setup json fallback completion response
    json_response_mock = MagicMock()
    json_response_mock.id = "gen-json-fb-1"
    choice_mock = MagicMock()
    choice_mock.message.content = json.dumps(_sample_opportunity_dict())
    json_response_mock.choices = [choice_mock]
    json_usage = MagicMock()
    json_usage.cost = 0.000080
    json_usage.prompt_tokens = 30
    json_usage.completion_tokens = 25
    json_usage.prompt_tokens_details = None
    json_response_mock.usage = json_usage

    mock_client.chat.completions.create.return_value = json_response_mock

    opp = structured_call(
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="score",
        model_cls=Opportunity,
        prompt="Score topic with fallback",
        input="Plain string input",
        plugin=adapter,
    )
    assert opp.title == "Myopia Prevention in Schools"

    # Verify ledger recorded the fallback call
    usage_rows = db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(usage_rows) == 1
    assert usage_rows[0]["cost_usd"] == Decimal("0.000080")


def test_structured_call_fake_adapter_exhausted_retries_raises() -> None:
    from engine.llm import structured_call

    class AlwaysFailsLLM:
        def healthcheck(self) -> None:
            pass

        def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
            return {"completely": "invalid"}

    adapter = AlwaysFailsLLM()
    with pytest.raises(ValidationError):
        structured_call(
            stage="score",
            model_cls=Opportunity,
            prompt="Prompt",
            input={},
            plugin=adapter,
            max_retries=1,
        )


def test_structured_call_openrouter_without_db_and_with_reasoning() -> None:
    from engine.integrations.openrouter import OpenRouterLLM
    from engine.llm import _extract_cost_and_tokens, structured_call

    mock_client = MagicMock()
    mock_instructor = MagicMock()
    adapter = OpenRouterLLM(
        settings=OpenRouterLLM.Settings(api_key=SecretStr("test-key")),
        client=mock_client,
        instructor_client=mock_instructor,
    )

    mock_opp = Opportunity.model_validate(_sample_opportunity_dict())
    raw_completion = MagicMock()
    raw_completion.usage = None
    raw_completion.id = None
    mock_instructor.chat.completions.create_with_completion.return_value = (
        mock_opp,
        raw_completion,
    )

    # Call without DB connection and with custom reasoning effort
    res = structured_call(
        stage="research",
        model_cls=Opportunity,
        prompt="Analyze research",
        input={"some": "input"},
        plugin=adapter,
        reasoning_effort="medium",
    )
    assert res.title == mock_opp.title

    # Non-OpenRouter string input & default non-schema completion
    fake_str = FakeLLM()
    assert fake_str.complete("just a prompt") == {"result": "fake_llm_completion"}
    str_opp = structured_call(
        stage="brief",
        model_cls=Opportunity,
        prompt="Evaluate string payload",
        input="Plain string brief context",
        plugin=FakeLLM(settings=FakeLLM.Settings(default_response=_sample_opportunity_dict())),
    )
    assert str_opp.title == "Myopia Prevention in Schools"

    # _extract_cost_and_tokens with usage missing or empty extra
    cost, in_t, out_t, c_t, gen = _extract_cost_and_tokens(raw_completion)
    assert cost == Decimal("0.000000")
    assert in_t == 0 and out_t == 0 and c_t == 0 and gen is None

    empty_usage = MagicMock()
    empty_usage.cost = None
    empty_usage.model_extra = {"cost": 0.000045}
    empty_usage.prompt_tokens = 10
    empty_usage.completion_tokens = 15
    empty_usage.prompt_tokens_details = None
    cost2, _, _, _, _ = _extract_cost_and_tokens(MagicMock(usage=empty_usage, id="1"))
    assert cost2 == Decimal("0.000045")


def test_structured_call_negative_retries_raises() -> None:
    from engine.integrations.fakes import FakeLLM
    from engine.llm import structured_call

    fake = FakeLLM()
    with pytest.raises(RuntimeError, match="Failed to complete"):
        structured_call(
            stage="score",
            model_cls=Opportunity,
            prompt="Prompt",
            input={},
            plugin=fake,
            max_retries=-1,
        )


def test_openrouter_llm_with_duck_typed_client() -> None:
    from engine.integrations.openrouter import OpenRouterLLM

    class CustomClient:
        instructor_client = MagicMock()

    custom = CustomClient()
    adapter = OpenRouterLLM(client=custom)  # type: ignore[arg-type]
    assert adapter.instructor_client == custom.instructor_client


def test_openrouter_structured_completion_uses_json_schema_mode() -> None:
    import httpx2
    from openai import OpenAI
    from pydantic import create_model

    from engine.integrations.openrouter import OpenRouterLLM

    answer_model = create_model("Answer", city=(str, ...))
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "gen-1",
                "object": "chat.completion",
                "created": 1,
                "model": "anthropic/claude-haiku-5.5",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"city": "Lisbon"}'},
                    }
                ],
            },
        )

    client = OpenAI(
        base_url="https://openrouter.test/api/v1",
        api_key="test-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    adapter = OpenRouterLLM(
        settings=OpenRouterLLM.Settings(api_key=SecretStr("test-key")), client=client
    )

    result = adapter.complete("Capital of Portugal?", answer_model)

    assert result == {"city": "Lisbon"}
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert "tool_choice" not in requests[0]


def _adapter_failing_instructor() -> tuple[Any, MagicMock]:
    from engine.integrations.openrouter import OpenRouterLLM

    mock_client = MagicMock()
    mock_instructor = MagicMock()
    mock_instructor.chat.completions.create_with_completion.side_effect = RuntimeError("refused")
    response = MagicMock()
    response.choices[0].message.content = json.dumps(_sample_opportunity_dict())
    mock_client.chat.completions.create.return_value = response
    adapter = OpenRouterLLM(
        settings=OpenRouterLLM.Settings(api_key=SecretStr("test-key")),
        client=mock_client,
        instructor_client=mock_instructor,
    )
    return adapter, mock_client


def test_structured_call_fallback_warns_and_returns_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from engine.llm import structured_call

    adapter, _ = _adapter_failing_instructor()

    with caplog.at_level("WARNING", logger="engine.llm"):
        opp = structured_call(
            stage="score", model_cls=Opportunity, prompt="Score", input="x", plugin=adapter
        )

    assert opp.title == "Myopia Prevention in Schools"
    assert "RuntimeError" in caplog.text


def test_structured_call_fallback_warns_with_response_schema_in_request() -> None:
    from engine.llm import structured_call

    adapter, mock_client = _adapter_failing_instructor()

    structured_call(stage="score", model_cls=Opportunity, prompt="Score", input="x", plugin=adapter)

    sent = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert '"funnel_stage"' in sent[-1]["content"]
