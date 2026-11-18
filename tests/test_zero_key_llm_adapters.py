"""Tests for M7-03 D1 Universal OpenAI-compatible and Ollama LLM adapters."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import BaseModel, SecretStr


class _IdeaPayload(BaseModel):
    topic: str
    score: int


def test_openai_compat_and_ollama_discovered_and_implement_port() -> None:
    """D1: openai_compat and ollama entry points resolve and satisfy LLMPort."""
    from engine.integrations.openai_compat import OllamaLLM, UniversalOpenAILLM
    from engine.integrations.ports import LLMPort
    from engine.integrations.registry import discover_plugins

    discovered = discover_plugins("llm")["llm"]
    assert discovered["openai_compat"] is UniversalOpenAILLM
    assert discovered["ollama"] is OllamaLLM

    assert isinstance(UniversalOpenAILLM(), LLMPort)
    assert isinstance(OllamaLLM(), LLMPort)


def test_universal_openai_healthcheck_and_complete() -> None:
    """D1: UniversalOpenAILLM supports base_url, healthcheck, raw text, and structured schema."""
    from engine.integrations.openai_compat import UniversalOpenAILLM

    no_key = UniversalOpenAILLM(settings=UniversalOpenAILLM.Settings(api_key=SecretStr("")))
    with pytest.raises(ValueError, match="API key"):
        no_key.healthcheck()

    bad_url = UniversalOpenAILLM(
        settings=UniversalOpenAILLM.Settings(
            base_url="ftp://invalid.example.com/v1",
            api_key=SecretStr("sk-dummy"),
        )
    )
    with pytest.raises(ValueError, match="http"):
        bad_url.healthcheck()

    meta_url = UniversalOpenAILLM(
        settings=UniversalOpenAILLM.Settings(
            base_url="http://169.254.169.254/latest/meta-data",
            api_key=SecretStr("sk-dummy"),
        )
    )
    with pytest.raises(ValueError, match="Unsafe"):
        meta_url.healthcheck()

    meta_host = UniversalOpenAILLM(
        settings=UniversalOpenAILLM.Settings(
            base_url="http://metadata.google.internal/computeMetadata/v1",
            api_key=SecretStr("sk-dummy"),
        )
    )
    with pytest.raises(ValueError, match="Unsafe"):
        meta_host.healthcheck()

    from engine.integrations.openai_compat import _resolve_secret

    assert _resolve_secret("  plain-key  ", "OPENAI_API_KEY", "") == "plain-key"
    custom_only = UniversalOpenAILLM(client=SimpleNamespace(instructor_client="ok"))  # type: ignore[arg-type]
    assert custom_only.instructor_client == "ok"

    token = "sk-test-token"
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        id="cmp-1",
        model="gpt-4o-mini",
        choices=[SimpleNamespace(message=SimpleNamespace(content="Optical retail insight"))],
    )
    mock_instructor = MagicMock()
    mock_instructor.chat.completions.create.return_value = _IdeaPayload(topic="Myopia", score=95)

    adapter = UniversalOpenAILLM(
        settings=UniversalOpenAILLM.Settings(
            base_url="https://api.groq.com/openai/v1",
            api_key=SecretStr(token),
            default_model="llama-3.3-70b-versatile",
        ),
        client=mock_client,
        instructor_client=mock_instructor,
    )
    adapter.healthcheck()

    plain = adapter.complete("Summarize trends", system="Be brief")
    assert plain == {
        "result": "Optical retail insight",
        "model": "gpt-4o-mini",
        "id": "cmp-1",
    }

    structured = adapter.complete("Score topic", schema=_IdeaPayload)
    assert structured == {"topic": "Myopia", "score": 95}

    mock_instructor.chat.completions.create.return_value = {"topic": "Lenses", "score": 88}
    assert adapter.complete("Score dict", schema=dict) == {"topic": "Lenses", "score": 88}


def test_ollama_zero_key_healthcheck_and_structured_call(db_conn: psycopg.Connection[Any]) -> None:
    """D1: OllamaLLM runs with empty API key on loopback and integrates with structured_call."""
    from engine import db, services
    from engine.integrations.openai_compat import OllamaLLM
    from engine.llm import structured_call

    adapter_zero = OllamaLLM(
        settings=OllamaLLM.Settings(
            base_url="http://localhost:11434/v1",
            api_key=None,
            default_model="llama3.1:8b",
        )
    )
    adapter_zero.healthcheck()

    slug = f"ws-ollama-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="Ollama WS")
    ws_id = ws["workspace_id"]
    mock_instructor = MagicMock()
    mock_instructor.chat.completions.create.return_value = _IdeaPayload(topic="Local LLM", score=90)
    mock_client = MagicMock()

    adapter_live = OllamaLLM(client=mock_client, instructor_client=mock_instructor)
    res = structured_call(
        stage="score",
        model_cls=_IdeaPayload,
        prompt="Score optical topic",
        input={"title": "Lentes infantis"},
        workspace_id=ws_id,
        conn=db_conn,
        plugin=adapter_live,
    )
    assert res == _IdeaPayload(topic="Local LLM", score=90)
    usage = db.list_usage(db_conn, workspace_id=ws_id)
    assert len(usage) == 1
    assert usage[0]["vendor"] == "custom"
