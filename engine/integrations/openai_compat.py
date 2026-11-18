"""Universal OpenAI-compatible and local Ollama LLM plugins implementing LLMPort (ADR-031)."""

from __future__ import annotations

import ipaddress
import os
from typing import Any, ClassVar
from urllib.parse import urlsplit

import instructor
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict, Field, SecretStr

_METADATA_HOSTNAMES = frozenset({"metadata.google.internal", "metadata"})


def validate_llm_base_url(url: str) -> str:
    """Validate HTTP(S) LLM endpoint URL, allowing local/LAN hosts while blocking cloud metadata."""
    clean = url.strip()
    parsed = urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        msg = f"Unsafe or invalid http/https URL: {url}"
        raise ValueError(msg)

    host = parsed.hostname.strip().lower()
    if host in _METADATA_HOSTNAMES:
        msg = f"Unsafe metadata host is forbidden: {host}"
        raise ValueError(msg)

    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None

    if ip_obj is not None and (
        ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_unspecified
    ):
        msg = f"Unsafe link-local/unspecified IP is forbidden: {host}"
        raise ValueError(msg)

    return clean


def _resolve_secret(secret: SecretStr | str | None, env_var: str, fallback: str) -> str:
    if isinstance(secret, SecretStr):
        return secret.get_secret_value().strip()
    if isinstance(secret, str):
        return secret.strip()
    return os.environ.get(env_var, fallback).strip()


class UniversalOpenAILLM:
    """Generic OpenAI-compatible LLMPort adapter (OpenAI, Groq, Together, LM Studio, vLLM)."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")
    _REQUIRE_API_KEY: ClassVar[bool] = True

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        base_url: str = Field(
            default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
        api_key: SecretStr | None = Field(
            default_factory=lambda: SecretStr(os.environ.get("OPENAI_API_KEY", "test-openai-key"))
        )
        default_model: str = Field(
            default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        )
        models_by_stage: dict[str, str] = Field(default_factory=dict)
        timeout_seconds: float = Field(default=60.0, gt=0.0, le=300.0)

    settings: Settings
    client: OpenAI
    instructor_client: Any

    def __init__(
        self,
        settings: Settings | None = None,
        client: OpenAI | None = None,
        instructor_client: Any | None = None,
    ) -> None:
        self.settings = settings or self.Settings()
        api_key = (
            _resolve_secret(self.settings.api_key, "OPENAI_API_KEY", "test-openai-key") or "ollama"
        )
        base_client = client or OpenAI(
            base_url=self.settings.base_url,
            api_key=api_key,
            timeout=self.settings.timeout_seconds,
        )
        self.client = base_client
        if instructor_client is not None:
            self.instructor_client = instructor_client
        elif isinstance(base_client, OpenAI):
            self.instructor_client = instructor.from_openai(
                base_client,
                mode=instructor.Mode.JSON,
            )
        else:
            self.instructor_client = getattr(base_client, "instructor_client", None)

    def healthcheck(self) -> None:
        """Verify LLM endpoint URL and credentials (when required)."""
        validate_llm_base_url(self.settings.base_url)
        if not self._REQUIRE_API_KEY:
            return
        raw_key = _resolve_secret(self.settings.api_key, "OPENAI_API_KEY", "")
        if not raw_key:
            msg = "OpenAI-compatible API key is not configured"
            raise ValueError(msg)

    def complete(
        self,
        prompt: str,
        schema: type[Any] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        """Execute chat completion and return structured dictionary."""
        target_model = model or self.settings.default_model
        messages: list[ChatCompletionMessageParam] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        if schema is not None:
            resp = self.instructor_client.chat.completions.create(
                model=target_model,
                response_model=schema,
                messages=messages,
            )
            if isinstance(resp, BaseModel):
                return resp.model_dump()
            return dict(resp)

        raw_resp = self.client.chat.completions.create(
            model=target_model,
            messages=messages,
        )
        choice = raw_resp.choices[0] if raw_resp.choices else None
        content = choice.message.content if choice and choice.message else ""
        return {
            "result": content,
            "model": raw_resp.model,
            "id": raw_resp.id,
        }


class OllamaLLM(UniversalOpenAILLM):
    """Zero-key local Ollama LLMPort adapter talking to OpenAI-compatible /v1 API."""

    _REQUIRE_API_KEY: ClassVar[bool] = False

    class Settings(UniversalOpenAILLM.Settings):
        base_url: str = Field(
            default_factory=lambda: os.environ.get(
                "OLLAMA_BASE_URL",
                "http://localhost:11434/v1",
            )
        )
        api_key: SecretStr | None = Field(default_factory=lambda: SecretStr("ollama"))
        default_model: str = Field(
            default_factory=lambda: os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
        )
