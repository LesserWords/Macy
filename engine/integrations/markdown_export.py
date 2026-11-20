"""Markdown export publisher plugin implementing PublisherPort (F16 AC6, ADR-021)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from engine.contracts import DraftCTA, PublishedContent, PublishRequest

_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize_segment(value: str, fallback: str) -> str:
    cleaned = _SEGMENT_RE.sub("-", value).strip("-_")
    return cleaned or fallback


def _format_cta_section(cta: DraftCTA | dict[str, str]) -> str:
    if not cta:
        return ""
    headline = str(cta.get("headline") or "").strip()
    body = str(cta.get("body") or cta.get("text") or "").strip()
    button = str(cta.get("button_text") or "").strip()
    core = " — ".join(part for part in (headline, body) if part)
    if button and core:
        return f"{core} ({button})"
    if button:
        return button
    return core


def _render_markdown(request: PublishRequest) -> str:
    frontmatter: dict[str, Any] = {
        "title": request.draft.title,
        "meta_description": request.draft.meta_description,
        "slug": request.draft.slug,
        "locale": request.locale,
        "destination": request.destination,
        "mode": request.mode,
        "draft": request.mode != "live",
        "draft_id": str(request.draft_id),
    }
    if request.scheduled_at is not None:
        frontmatter["scheduled_at"] = request.scheduled_at.isoformat()
    if request.schema_json_ld is not None:
        frontmatter["schema_json_ld"] = request.schema_json_ld

    fm_yaml = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()

    parts = [f"---\n{fm_yaml}\n---", request.draft.body_md.strip()]
    if request.draft.faq:
        faq_lines = ["## FAQ", ""]
        for item in request.draft.faq:
            q_txt = str(item.get("question") or item.get("q") or "").strip()
            a_txt = str(item.get("answer") or item.get("a") or "").strip()
            faq_lines.append(f"- **{q_txt}** — {a_txt}")
        parts.append("\n".join(faq_lines))

    cta_text = _format_cta_section(request.draft.cta)
    if cta_text:
        parts.append(f"## CTA\n\n{cta_text}")

    return "\n\n".join(parts).rstrip() + "\n"


class MarkdownExportPublisher:
    """Writes approved drafts to local Markdown files with YAML front-matter."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        export_dir: str = "./exports"
        base_url: str | None = None
        default_mode: Literal["draft", "scheduled", "live"] = "draft"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()
        self._exported: dict[tuple[uuid.UUID, str], PublishedContent] = {}

    def healthcheck(self) -> None:
        """Verify that export_dir is non-empty and writable."""
        raw_dir = self.settings.export_dir.strip()
        if not raw_dir:
            msg = "markdown_export export_dir cannot be empty"
            raise ValueError(msg)
        root = Path(raw_dir)
        if root.exists() and not root.is_dir():
            msg = f"export_dir '{root}' exists and is not a directory"
            raise ValueError(msg)
        root.mkdir(parents=True, exist_ok=True)

    def publish(self, request: PublishRequest) -> PublishedContent:
        """Export draft Markdown file idempotently and return PublishedContent."""
        self.healthcheck()
        key = (request.draft_id, request.destination)
        cached = self._exported.get(key)
        if cached is not None:
            return cached

        root_dir = Path(self.settings.export_dir.strip()).resolve()
        safe_dest = _sanitize_segment(request.destination, "default")
        safe_slug = _sanitize_segment(request.draft.slug, str(request.draft_id))
        dest_dir = (root_dir / safe_dest).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        target_file = (dest_dir / f"{safe_slug}.md").resolve()
        if not target_file.is_relative_to(root_dir):  # pragma: no cover
            msg = f"Resolved export path '{target_file}' escapes export_dir"
            raise ValueError(msg)

        markdown = _render_markdown(request)
        target_file.write_text(markdown, encoding="utf-8")

        pub_at = datetime.fromtimestamp(target_file.stat().st_mtime, tz=UTC)
        url = (
            f"{self.settings.base_url.rstrip('/')}/{safe_slug}"
            if self.settings.base_url
            else target_file.as_uri()
        )
        status = "scheduled" if request.mode == "scheduled" else "published"
        result = PublishedContent(
            destination=request.destination,
            external_id=f"md-{request.draft_id}",
            url=url,
            status=status,
            published_at=pub_at,
            raw={
                "path": str(target_file),
                "mode": request.mode,
                "is_live": request.mode == "live",
                "draft": request.mode != "live",
            },
        )
        self._exported[key] = result
        return result
