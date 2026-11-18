"""Modular Seovela-style LLM rewrite modes decoupled from quality checks (M6-01 D4, ADR-028)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psycopg

from engine import llm
from engine.contracts import ContentDraft
from engine.integrations.fakes import FakeLLM
from engine.integrations.ports import LLMPort

SUPPORTED_REWRITE_MODES: tuple[str, ...] = ("improve", "simplify", "seo_optimize")
REWRITE_VERSION_PREFIX = "rewrite_draft.md"
PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "rewrite_draft.md"


def get_rewrite_prompt_and_version() -> tuple[str, str]:
    """Load prompts/rewrite_draft.md and return (content, deterministic sha256 tag)."""
    body = PROMPT_FILE.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return body, f"{REWRITE_VERSION_PREFIX}:{digest}"


def _build_rewrite_payload(
    draft_model: ContentDraft,
    *,
    mode: str,
    primary_keyword: str | None,
    locale: str,
) -> dict[str, Any]:
    required_facts = [
        {"fact": c.text, "source_id": str(c.source_id)}
        for c in draft_model.claims
        if c.kind == "fact" and c.source_id
    ]
    return {
        "rewrite_mode": mode,
        "locale": locale,
        "draft": draft_model.model_dump(mode="json"),
        "brief": {
            "topic": draft_model.title,
            "primary_keyword": (primary_keyword or draft_model.title).strip(),
            "required_facts": required_facts,
        },
    }


def rewrite_draft(  # noqa: PLR0913
    draft: ContentDraft | Mapping[str, Any],
    *,
    mode: str = "improve",
    primary_keyword: str | None = None,
    locale: str = "pt-BR",
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
    workspace_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    conn: psycopg.Connection[Any] | None = None,
) -> ContentDraft:
    """Rewrite an existing ContentDraft with an independent LLM mode."""
    norm_mode = mode.strip().lower()
    if norm_mode not in SUPPORTED_REWRITE_MODES:
        msg = f"Unsupported rewrite mode '{mode}'. Supported: {', '.join(SUPPORTED_REWRITE_MODES)}"
        raise ValueError(msg)

    draft_model = draft if isinstance(draft, ContentDraft) else ContentDraft.model_validate(draft)
    prompt, prompt_ver = get_rewrite_prompt_and_version()
    payload = _build_rewrite_payload(
        draft_model,
        mode=norm_mode,
        primary_keyword=primary_keyword,
        locale=locale,
    )
    resolved_llm = llm_adapter or (FakeLLM() if conn is None else None)
    return llm.structured_call(
        stage="write",
        model_cls=ContentDraft,
        prompt=prompt,
        input=payload,
        workspace_id=workspace_id,
        run_id=run_id,
        conn=conn,
        plugin=resolved_llm,
        reasoning_effort="high",
        prompt_version=prompt_ver,
        model=model,
    )
