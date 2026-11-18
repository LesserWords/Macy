"""Signed HTTP webhook publisher plugin implementing PublisherPort (F16 AC4, ADR-021)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from engine import safe_http, webhooks
from engine.contracts import PublishedContent, PublishError, PublishRequest


def _normalize_webhook_secret(secret: str) -> str:
    """Return Standard Webhooks-compatible secret (pass-through whsec_, else base64 UTF-8)."""
    if secret.startswith("whsec_"):
        return secret
    return base64.b64encode(secret.encode("utf-8")).decode("ascii")


def _validate_published_response(
    parsed: PublishedContent,
    request: PublishRequest,
) -> PublishedContent:
    if parsed.status == "failed":
        detail = (parsed.raw or {}).get("error") or "Remote publisher returned status 'failed'"
        raise PublishError(str(detail))

    raw_meta = parsed.raw or {}
    if request.mode == "draft" and (
        raw_meta.get("is_live") is True
        or raw_meta.get("live") is True
        or raw_meta.get("public") is True
    ):
        msg = "Destination reported content live while PublishRequest.mode is 'draft'"
        raise PublishError(msg)

    return parsed


class HttpWebhookPublisher:
    """Publishes approved drafts by posting Standard Webhooks-signed requests via safe_http."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        endpoint_url: str = ""
        signing_secret: SecretStr = Field(default=SecretStr(""))
        timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
        default_mode: Literal["draft", "scheduled", "live"] = "draft"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def _raw_secret(self) -> str:
        val = self.settings.signing_secret
        return val.get_secret_value() if isinstance(val, SecretStr) else str(val)

    def healthcheck(self) -> None:
        """Verify endpoint_url passes SSRF policy and signing_secret is present."""
        endpoint = self.settings.endpoint_url.strip()
        if not endpoint:
            msg = "http_webhook endpoint_url cannot be empty"
            raise ValueError(msg)
        safe_http.validate_safe_url(endpoint)
        if not self._raw_secret().strip():
            msg = "http_webhook signing_secret is required"
            raise ValueError(msg)

    def publish(self, request: PublishRequest) -> PublishedContent:
        """Sign and POST PublishRequest, validating response against PublishedContent."""
        try:
            self.healthcheck()
        except (safe_http.SafeHTTPError, ValueError) as exc:
            raise PublishError(str(exc)) from exc

        endpoint = self.settings.endpoint_url.strip()
        payload_json = json.dumps(
            request.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        msg_id = uuid.uuid5(request.draft_id, request.destination)
        headers = webhooks.build_webhook_headers(
            _normalize_webhook_secret(self._raw_secret().strip()),
            msg_id,
            datetime.now(UTC),
            payload_json,
        )
        headers["Idempotency-Key"] = f"{request.draft_id}:{request.destination}"

        try:
            resp = safe_http.safe_request(
                "POST",
                endpoint,
                headers=headers,
                data=payload_json.encode("utf-8"),
                timeout=self.settings.timeout_seconds,
            )
        except (safe_http.SafeHTTPError, OSError) as exc:
            msg = f"Webhook delivery failed: {exc}"
            raise PublishError(msg) from exc

        if not (200 <= resp.status_code < 300):
            snippet = resp.text[:300].strip()
            msg = f"Webhook returned HTTP {resp.status_code}: {snippet}"
            raise PublishError(msg)

        try:
            data = resp.json()
        except ValueError as exc:
            msg = "Webhook response is not valid JSON"
            raise PublishError(msg) from exc

        if not isinstance(data, dict):
            msg = "Webhook response is not valid JSON object"
            raise PublishError(msg)

        try:
            published = PublishedContent.model_validate(data)
        except ValidationError as exc:
            msg = f"Webhook response failed PublishedContent validation: {exc}"
            raise PublishError(msg) from exc

        return _validate_published_response(published, request)
