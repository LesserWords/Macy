"""OpenRouter LLM plugin implementing LLMPort protocol (ADR-005, ADR-007)."""

import os
from typing import Any, Literal

import instructor
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class OpenRouterLLM:
    """LLM port adapter communicating with OpenRouter."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        api_key: SecretStr | None = Field(
            default_factory=lambda: SecretStr(
                os.environ.get("OPENROUTER_API_KEY", "test-openrouter-key")
            )
        )
        default_model: str = "anthropic/claude-haiku-5.5"
        models_by_stage: dict[str, str] = Field(
            default_factory=lambda: {
                "research": "anthropic/claude-haiku-5.5",
                "score": "anthropic/claude-haiku-5.5",
                "brief": "anthropic/claude-opus-5.5",
                "write": "anthropic/claude-opus-5.5",
                "evaluate": "anthropic/claude-opus-5.5",
            }
        )
        data_policy: Literal["deny", "allow"] = "deny"
        base_url: str = "https://openrouter.ai/api/v1"

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
        raw_key = (
            self.settings.api_key.get_secret_value()
            if isinstance(self.settings.api_key, SecretStr)
            else self.settings.api_key
        )
        api_key = raw_key or "test-openrouter-key"
        base_client = client or OpenAI(
            base_url=self.settings.base_url,
            api_key=api_key,
        )
        self.client = base_client
        if instructor_client is not None:
            self.instructor_client = instructor_client
        elif isinstance(base_client, OpenAI):
            self.instructor_client = instructor.from_openrouter(
                base_client,
                mode=instructor.Mode.JSON_SCHEMA,
            )
        else:
            self.instructor_client = getattr(base_client, "instructor_client", None)

    def healthcheck(self) -> None:
        """Verify connectivity to OpenRouter."""
        raw_key = (
            self.settings.api_key.get_secret_value()
            if isinstance(self.settings.api_key, SecretStr)
            else self.settings.api_key
        )
        if not raw_key:
            msg = "OpenRouter API key is not configured"
            raise ValueError(msg)

    def complete(
        self,
        prompt: str,
        schema: type[Any] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute completion and return structured response dictionary."""
        target_model = model or self.settings.default_model
        messages: list[ChatCompletionMessageParam] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "provider": {
                "require_parameters": True,
                "data_collection": self.settings.data_policy,
            }
        }
        if extra_body:
            body.update(extra_body)

        if schema is not None:
            resp = self.instructor_client.chat.completions.create(
                model=target_model,
                response_model=schema,
                messages=messages,
                extra_body=body,
            )
            if isinstance(resp, BaseModel):
                return resp.model_dump()
            return dict(resp)

        raw_resp = self.client.chat.completions.create(
            model=target_model,
            messages=messages,
            extra_body=body,
        )
        choice = raw_resp.choices[0] if raw_resp.choices else None
        content = choice.message.content if choice and choice.message else ""
        return {
            "result": content,
            "model": raw_resp.model,
            "id": raw_resp.id,
        }
