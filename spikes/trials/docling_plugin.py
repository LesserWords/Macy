"""Docling PDF ExtractorPort plugin candidate for M3-03 (ADR-003, ADR-025, F12 AC5).

Connects to an optional docling-serve HTTP endpoint (/v1/convert/file) and processes
``application/pdf`` documents exclusively.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from engine.config import get_user_agent
from engine.contracts import ResearchRecord
from engine.research import canonicalize_url, resolve_date_status
from engine.safe_http import SafeHTTPError, safe_request, validate_safe_url

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _normalize_content_type(headers: dict[str, str]) -> str:
    for key, val in headers.items():
        if key.lower() == "content-type":
            return val.split(";", 1)[0].strip().lower()
    return ""


def _is_pdf_payload(headers: dict[str, str], body: bytes) -> tuple[bool, str]:
    ctype = _normalize_content_type(headers)
    if ctype == "application/pdf" or body.startswith(b"%PDF-"):
        return True, ctype or "application/pdf"
    return False, ctype or "application/octet-stream"


def _build_multipart_body(
    pdf_bytes: bytes,
    *,
    filename: str,
) -> tuple[bytes, str]:
    boundary = f"----MacyDocling{uuid.uuid4().hex}"
    parts: list[bytes] = []
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + pdf_bytes
        + b"\r\n"
    )
    for fmt in ("md", "json"):
        parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="to_formats"\r\n\r\n'
                f"{fmt}\r\n"
            ).encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post_to_docling(
    docling_url: str,
    pdf_bytes: bytes,
    *,
    filename: str,
    timeout: float,
) -> Any:
    endpoint = docling_url.rstrip("/") + "/v1/convert/file"
    body, content_type = _build_multipart_body(pdf_bytes, filename=filename)
    req = urllib.request.Request(  # noqa: S310
        endpoint,
        data=body,
        headers={
            "Content-Type": content_type,
            "Accept": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener()
    try:
        resp = opener.open(req, timeout=timeout)
        raw_text = resp.read().decode("utf-8", errors="replace")
        status = int(getattr(resp, "status", 200))
        return _SimpleHTTPResponse(status_code=status, text=raw_text)
    except urllib.error.HTTPError as err:
        raw_text = err.read().decode("utf-8", errors="replace")
        return _SimpleHTTPResponse(status_code=int(err.code), text=raw_text)


class _SimpleHTTPResponse:
    def __init__(self, *, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


def _extract_table_grids(tables_raw: Any) -> list[list[list[str]]]:
    if not isinstance(tables_raw, list):
        return []
    grids: list[list[list[str]]] = []
    for tbl in tables_raw:
        if not isinstance(tbl, dict):
            continue
        data = tbl.get("data")
        grid = data.get("grid") if isinstance(data, dict) else None
        if not isinstance(grid, list):
            continue
        rows: list[list[str]] = []
        for row in grid:
            if isinstance(row, list):
                rows.append([str(c.get("text", "")).strip() for c in row if isinstance(c, dict)])
        if rows:
            grids.append(rows)
    return grids


def _extract_facts_from_tables(grids: list[list[list[str]]], md_text: str) -> list[str]:
    facts: list[str] = []
    for grid in grids:
        for row in grid[1:]:
            non_empty = [cell for cell in row if cell and cell != "-"]
            if len(non_empty) >= 2:
                facts.append(f"{non_empty[0]}: {' | '.join(non_empty[1:5])}")
            if len(facts) >= 8:
                return facts
    if not facts and md_text.strip():
        for line in md_text.splitlines():
            clean = line.strip()
            if clean and not clean.startswith(("#", "|", "<!--")):
                facts.append(clean[:200])
            if len(facts) >= 5:
                break
    return facts


def _status_record(
    *,
    url: str,
    canonical: str,
    fetch_status: str,
    http_status: int | None,
    content_type: str,
) -> ResearchRecord:
    host = (urlsplit(canonical).hostname or "unknown").lower()
    return ResearchRecord(
        headline=canonical,
        source=host,
        url=url,
        canonical_url=canonical,
        published_at=None,
        summary=f"{fetch_status} ({content_type}) for {canonical}",
        source_type="data",
        verification_status="unverified",
        raw={
            "fetch_status": fetch_status,
            "http_status": http_status,
            "content_type": content_type,
            "extracted_text": None,
            "tables_count": 0,
            "tables": [],
            "date_status": "unverified",
        },
    )


class DoclingExtractor:
    """ExtractorPort plugin handling application/pdf documents via docling-serve."""

    SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        docling_url: str = "http://docling:5001"
        allow_private_service_url: bool = True
        timeout_seconds: float = Field(default=60.0, gt=0.0, le=300.0)
        max_pdf_bytes: int = Field(default=2_097_152, gt=0, le=10_485_760)
        user_agent: str = Field(default_factory=get_user_agent)

    extractor_settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.extractor_settings = settings or self.Settings()

    @property
    def settings(self) -> Settings:
        return self.extractor_settings

    def healthcheck(self) -> None:
        """Verify docling-serve endpoint configuration."""
        parsed = urlsplit(self.extractor_settings.docling_url.strip())
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            msg = f"Invalid docling_url: {self.extractor_settings.docling_url}"
            raise ValueError(msg)
        if not self.extractor_settings.allow_private_service_url:
            try:
                validate_safe_url(self.extractor_settings.docling_url)
            except SafeHTTPError as exc:
                raise ValueError(str(exc)) from exc

    def _fetch_pdf(self, url: str, canonical: str) -> tuple[bytes, int] | ResearchRecord:
        try:
            resp = safe_request(
                "GET",
                url,
                timeout=self.extractor_settings.timeout_seconds,
                max_bytes=self.extractor_settings.max_pdf_bytes,
                headers={"User-Agent": self.extractor_settings.user_agent},
            )
        except SafeHTTPError:
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="http_error",
                http_status=None,
                content_type="application/octet-stream",
            )

        if resp.status_code >= 400:
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="http_error",
                http_status=resp.status_code,
                content_type=_normalize_content_type(resp.headers) or "text/html",
            )

        is_pdf, detected_ctype = _is_pdf_payload(resp.headers, resp.content)
        if not is_pdf:
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="unsupported_content_type",
                http_status=resp.status_code,
                content_type=detected_ctype,
            )
        return resp.content, resp.status_code

    def extract(self, url: str, html: str | None = None) -> ResearchRecord:
        """Extract markdown tables and statistics from an application/pdf URL."""
        canonical = canonicalize_url(url)

        if html is not None:
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="unsupported_content_type",
                http_status=200,
                content_type="text/html",
            )

        fetch_res = self._fetch_pdf(url, canonical)
        if isinstance(fetch_res, ResearchRecord):
            return fetch_res
        pdf_bytes, http_status = fetch_res

        filename = urlsplit(canonical).path.rsplit("/", 1)[-1] or "document.pdf"
        conv_resp = _post_to_docling(
            self.extractor_settings.docling_url,
            pdf_bytes,
            filename=filename,
            timeout=self.extractor_settings.timeout_seconds,
        )
        if int(getattr(conv_resp, "status_code", 500)) >= 400:
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="extraction_failed",
                http_status=http_status,
                content_type="application/pdf",
            )

        return self._build_success_record(
            url=url,
            canonical=canonical,
            http_status=http_status,
            raw_json=str(getattr(conv_resp, "text", "")),
        )

    def _build_success_record(
        self,
        *,
        url: str,
        canonical: str,
        http_status: int,
        raw_json: str,
    ) -> ResearchRecord:
        host = (urlsplit(canonical).hostname or "unknown").lower()
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="extraction_failed",
                http_status=http_status,
                content_type="application/pdf",
            )

        if not isinstance(payload, dict) or payload.get("status") != "success":
            return _status_record(
                url=url,
                canonical=canonical,
                fetch_status="extraction_failed",
                http_status=http_status,
                content_type="application/pdf",
            )

        doc = payload.get("document") or {}
        md_text = str(doc.get("md_content") or "").strip()
        json_content = doc.get("json_content") or {}
        grids = _extract_table_grids(json_content.get("tables"))
        facts = _extract_facts_from_tables(grids, md_text)
        heading_match = _HEADING_RE.search(md_text)
        headline = heading_match.group(1).strip() if heading_match else canonical
        resolved_dt, date_status = resolve_date_status(None, url=canonical)

        return ResearchRecord(
            headline=headline,
            source=host,
            url=url,
            canonical_url=canonical,
            published_at=resolved_dt,
            event_date=resolved_dt.date() if resolved_dt else None,
            summary=md_text[:500] or headline,
            facts=facts,
            source_type="data",
            verification_status=date_status,
            raw={
                "fetch_status": "ok",
                "http_status": http_status,
                "content_type": "application/pdf",
                "extracted_text": md_text,
                "tables_count": len(grids),
                "tables": grids,
                "processing_time": payload.get("processing_time"),
                "date_status": date_status,
            },
        )
