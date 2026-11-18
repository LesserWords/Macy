"""Unit and integration tests for M4-05 Publisher port, plugins, and workflow (F16, ADR-021)."""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError
from standardwebhooks import Webhook

from engine import db, safe_http, services
from engine.contracts import (
    ContentDraft,
    DraftClaim,
    PublishedContent,
    PublishError,
    PublishRequest,
)
from engine.integrations.http_webhook import HttpWebhookPublisher
from engine.integrations.markdown_export import MarkdownExportPublisher
from engine.integrations.registry import IntegrationMissing
from tests.test_draft_review import seed_review_workspace

TEST_ENC_KEY = Fernet.generate_key().decode("ascii")


class _TestPublishServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], secret: str) -> None:
        self.secret = secret
        self.mode = "good"
        self.received: list[dict[str, Any]] = []
        super().__init__(server_address, _TestPublishHandler)


class _TestPublishHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, _TestPublishServer)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        headers = {k.lower(): v for k, v in self.headers.items()}
        wh_secret = (
            server.secret
            if server.secret.startswith("whsec_")
            else base64.b64encode(server.secret.encode("utf-8")).decode("ascii")
        )
        verified = Webhook(wh_secret).verify(raw_body, headers)
        server.received.append({"headers": headers, "body": verified})

        if server.mode == "500":
            self._send_bytes(500, b'{"error": "upstream outage"}')
            return
        if server.mode == "malformed":
            self._send_bytes(200, b"{not-valid-json")
            return
        if server.mode == "json_array":
            self._send_bytes(200, b'["not-an-object"]')
            return
        if server.mode == "invalid_schema":
            self._send_bytes(200, json.dumps({"destination": "http_webhook"}).encode("utf-8"))
            return
        if server.mode == "remote_failed":
            payload: dict[str, Any] = {
                "destination": str(verified["destination"]),
                "external_id": "err-1",
                "status": "failed",
                "raw": {"error": "CMS rejected slug"},
            }
            self._send_bytes(200, json.dumps(payload).encode("utf-8"))
            return
        if server.mode == "live_leak":
            payload = {
                "destination": str(verified["destination"]),
                "external_id": "leak-1",
                "url": "https://cms.example.com/live",
                "status": "published",
                "raw": {"mode": "live", "is_live": True},
            }
            self._send_bytes(200, json.dumps(payload).encode("utf-8"))
            return

        slug = str(verified["draft"]["slug"])
        payload = {
            "schema_version": 1,
            "destination": str(verified["destination"]),
            "external_id": f"cms-{verified['draft_id']}",
            "url": f"https://cms.example.com/posts/{slug}",
            "status": "published",
            "published_at": "2026-11-09T14:00:00Z",
            "raw": {
                "mode": verified["mode"],
                "is_live": verified["mode"] == "live",
                "idempotency_key": headers.get("idempotency-key"),
            },
        }
        self._send_bytes(200, json.dumps(payload).encode("utf-8"))

    def _send_bytes(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _start_publish_server(secret: str | None = None) -> tuple[_TestPublishServer, str, str]:
    tok = secret or ("whsec_" + base64.b64encode(secrets.token_bytes(24)).decode("ascii"))
    srv = _TestPublishServer(("127.0.0.1", 0), secret=tok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/publish", tok


def _sample_draft() -> ContentDraft:
    return ContentDraft(
        title="Optometria Preventiva no Varejo",
        meta_description="Guia completo sobre triagem visual e atendimento em oticas.",
        slug="optometria-preventiva-varejo",
        body_md="# Optometria Preventiva\n\nConteudo editorial validado.",
        faq=[{"q": "Por que fazer triagem?", "a": "Melhora a precisao do atendimento."}],
        cta={
            "headline": "Agende uma avaliacao",
            "body": "Fale com um especialista",
            "button_text": "Agendar Agora",
        },
        claims=[DraftClaim(text="Analise de mercado optico.", kind="analysis")],
    )


def _seed_pass_draft_bed(
    conn: psycopg.Connection[Any],
    tag: str,
) -> dict[str, Any]:
    base = seed_review_workspace(conn, f"pub-{tag}", include_other_owner=True)
    ws_id = base["workspace_id"]
    creator_id = base["creator_id"]
    brief = services.generate_brief(
        conn,
        workspace_id=ws_id,
        opportunity_id=base["opportunity"]["id"],
        user_id=creator_id,
        offline=True,
    )
    payload = {
        "schema_version": 1,
        "title": "Gestao de Estoque em Oticas",
        "meta_description": "Como equilibrar o giro de armacoes e lentes no varejo.",
        "slug": "gestao-estoque-oticas",
        "body_md": "# Gestao de Estoque\n\nDados de desempenho comprovados no 3T26.",
        "faq": [{"question": "Qual o giro ideal?", "answer": "Entre 45 e 60 dias."}],
        "cta": "Solicite nosso diagnostico gratuito.",
        "claims": [{"text": "O setor cresceu 8%.", "source_id": str(base["document"]["id"])}],
    }
    draft = db.create_next_draft_revision(
        conn,
        workspace_id=ws_id,
        brief_id=brief["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload=payload,
        claims=[
            {
                "kind": "fact",
                "text": "O setor cresceu 8%.",
                "document_id": base["document"]["id"],
            }
        ],
        status="pass",
        revision=0,
    )
    return {
        "workspace_id": ws_id,
        "creator_id": creator_id,
        "other_user_id": base["other_user_id"],
        "campaign_id": base["campaign_id"],
        "run_id": base["run_id"],
        "brief": brief,
        "draft": draft,
    }


def test_markdown_export_literal_expected_file_and_idempotency_ac6(tmp_path: Path) -> None:
    """F16 AC6 & M4-05 Step 2: markdown_export matches literal expected front-matter and body."""
    fixed_id = uuid.UUID("0192f440-1111-7000-8000-000000000001")
    adapter = MarkdownExportPublisher(
        MarkdownExportPublisher.Settings(
            export_dir=str(tmp_path),
            base_url="https://blog.example.com/posts",
        )
    )
    adapter.healthcheck()

    req = PublishRequest(
        draft_id=fixed_id,
        draft=_sample_draft(),
        locale="pt-BR",
        destination="markdown_export",
        mode="draft",
    )
    res1 = adapter.publish(req)
    res2 = adapter.publish(req)

    expected_file = tmp_path / "markdown_export" / "optometria-preventiva-varejo.md"
    assert expected_file.is_file()
    expected_content = (
        "---\n"
        "title: Optometria Preventiva no Varejo\n"
        "meta_description: Guia completo sobre triagem visual e atendimento em oticas.\n"
        "slug: optometria-preventiva-varejo\n"
        "locale: pt-BR\n"
        "destination: markdown_export\n"
        "mode: draft\n"
        "draft: true\n"
        "draft_id: 0192f440-1111-7000-8000-000000000001\n"
        "---\n\n"
        "# Optometria Preventiva\n\n"
        "Conteudo editorial validado.\n\n"
        "## FAQ\n\n"
        "- **Por que fazer triagem?** — Melhora a precisao do atendimento.\n\n"
        "## CTA\n\n"
        "Agende uma avaliacao — Fale com um especialista (Agendar Agora)\n"
    )
    assert expected_file.read_text(encoding="utf-8") == expected_content
    assert res1.url == "https://blog.example.com/posts/optometria-preventiva-varejo"
    assert res1.external_id == res2.external_id
    assert res1.url == res2.url
    assert res1.raw is not None
    assert res1.raw["is_live"] is False
    assert len(list(tmp_path.rglob("*.md"))) == 1


def test_markdown_export_path_traversal_and_healthcheck(tmp_path: Path) -> None:
    """Path traversal slugs remain sandboxed; invalid export_dir fails healthcheck."""
    adapter = MarkdownExportPublisher(MarkdownExportPublisher.Settings(export_dir=str(tmp_path)))
    req = PublishRequest(
        draft_id=uuid.uuid4(),
        draft=ContentDraft(
            title="Adversarial",
            meta_description="Desc",
            slug="../../etc/passwd",
            body_md="# Body",
            cta={"headline": "Only Headline"},
        ),
        locale="en-US",
        destination="../escape",
        mode="live",
        scheduled_at=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
    )
    res = adapter.publish(req)
    assert res.raw is not None
    exported = Path(res.raw["path"]).resolve()
    assert exported.is_relative_to(tmp_path.resolve())
    assert exported.read_text(encoding="utf-8").endswith("## CTA\n\nOnly Headline\n")
    assert res.url is not None and res.url.startswith("file://")

    button_only_req = PublishRequest(
        draft_id=uuid.uuid4(),
        draft=ContentDraft(
            title="Button Only",
            meta_description="Desc",
            slug="button-only",
            body_md="# Body",
            cta={"button_text": "Saiba Mais"},
        ),
        locale="pt-BR",
        destination="markdown_export",
        mode="draft",
    )
    btn_res = adapter.publish(button_only_req)
    assert btn_res.raw is not None
    assert Path(btn_res.raw["path"]).read_text(encoding="utf-8").endswith("## CTA\n\nSaiba Mais\n")

    blocking_file = tmp_path / "not-a-dir"
    blocking_file.write_text("x", encoding="utf-8")
    bad = MarkdownExportPublisher(MarkdownExportPublisher.Settings(export_dir=str(blocking_file)))
    with pytest.raises((OSError, ValueError)):
        bad.healthcheck()

    empty_dir = MarkdownExportPublisher(MarkdownExportPublisher.Settings(export_dir="  "))
    with pytest.raises(ValueError, match="export_dir"):
        empty_dir.healthcheck()


def test_http_webhook_signed_good_malformed_and_500_responses_ac4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F16 AC4 & M4-05 Step 3: http_webhook signs requests and rejects malformed/500 answers."""
    monkeypatch.setattr(safe_http, "is_ip_allowed", lambda _ip: True)
    srv, url, secret = _start_publish_server("plain-secret-value")
    try:
        adapter = HttpWebhookPublisher(
            HttpWebhookPublisher.Settings(
                endpoint_url=url,
                signing_secret=SecretStr(secret),
                timeout_seconds=5.0,
            )
        )
        adapter.healthcheck()
        req = PublishRequest(
            draft_id=uuid.uuid4(),
            draft=_sample_draft(),
            locale="pt-BR",
            destination="http_webhook",
            mode="draft",
        )

        # 1. Good response validates PublishedContent and verifies signature
        ok = adapter.publish(req)
        assert ok.external_id == f"cms-{req.draft_id}"
        assert ok.url == "https://cms.example.com/posts/optometria-preventiva-varejo"
        assert len(srv.received) == 1
        assert srv.received[0]["headers"]["idempotency-key"] == f"{req.draft_id}:http_webhook"

        # 2. Malformed JSON response raises PublishError
        srv.mode = "malformed"
        with pytest.raises(PublishError, match="valid JSON"):
            adapter.publish(req)

        # 3. Invalid PublishedContent schema raises PublishError
        srv.mode = "invalid_schema"
        with pytest.raises(PublishError, match="PublishedContent"):
            adapter.publish(req)

        # 4. Remote 500 error raises PublishError
        srv.mode = "500"
        with pytest.raises(PublishError, match="HTTP 500"):
            adapter.publish(req)

        # 5. Remote explicit status='failed' raises PublishError
        srv.mode = "remote_failed"
        with pytest.raises(PublishError, match="CMS rejected slug"):
            adapter.publish(req)

        # 6. Remote marking content live when mode='draft' raises PublishError
        srv.mode = "live_leak"
        with pytest.raises(PublishError, match="draft"):
            adapter.publish(req)

        # 7. Non-dict JSON response (e.g. array) raises PublishError
        srv.mode = "json_array"
        with pytest.raises(PublishError, match="JSON object"):
            adapter.publish(req)

        # 8. Transport OSError from safe_request wraps into PublishError
        def _fail_request(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("connection reset")

        with monkeypatch.context() as mp:
            mp.setattr(safe_http, "safe_request", _fail_request)
            with pytest.raises(PublishError, match="Webhook delivery failed"):
                adapter.publish(req)
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_webhook_ssrf_and_settings_validation() -> None:
    """http_webhook validates endpoint URL against SSRF and requires signing secret."""
    with pytest.raises(ValueError, match="endpoint_url"):
        HttpWebhookPublisher(HttpWebhookPublisher.Settings(endpoint_url="")).healthcheck()

    with pytest.raises(ValueError, match="signing_secret"):
        HttpWebhookPublisher(
            HttpWebhookPublisher.Settings(endpoint_url="https://example.com/hook")
        ).healthcheck()

    private_target = HttpWebhookPublisher(
        HttpWebhookPublisher.Settings(
            endpoint_url="http://127.0.0.1:8080/hook",
            signing_secret=SecretStr("whsec_" + base64.b64encode(b"k" * 24).decode("ascii")),
        )
    )
    with pytest.raises(safe_http.BlockedIPError):
        private_target.healthcheck()

    req = PublishRequest(
        draft_id=uuid.uuid4(),
        draft=_sample_draft(),
        locale="pt-BR",
        destination="http_webhook",
        mode="draft",
    )
    with pytest.raises(PublishError, match="blocked IP"):
        private_target.publish(req)


def test_decide_draft_auto_publishes_markdown_export_and_idempotent_retry_ac1_ac2_ac5(
    db_conn: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F16 AC1, AC2, AC5: approval auto-publishes, deduplicates retries, emits article.published."""
    monkeypatch.setattr(safe_http, "resolve_and_validate_host", lambda _host: ["93.184.216.34"])
    bed = _seed_pass_draft_bed(db_conn, "auto-md")
    services.save_integration(
        db_conn,
        workspace_id=bed["workspace_id"],
        user_id=bed["creator_id"],
        port="publisher",
        plugin="markdown_export",
        settings={"export_dir": str(tmp_path), "base_url": "https://blog.otica.example.com"},
    )

    decided = services.decide_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=bed["draft"]["id"],
        decision="approved",
        user_id=bed["creator_id"],
        note="Publicar no blog",
    )
    assert decided["status"] == "published"
    assert decided["publish_queued"] is True
    assert decided["publication"] is not None
    assert decided["publication"]["status"] == "published"
    assert decided["publication"]["url"] == "https://blog.otica.example.com/gestao-estoque-oticas"

    evts = db.list_events(
        db_conn,
        workspace_id=bed["workspace_id"],
        event_type="article.published",
        entity_id=bed["draft"]["id"],
    )
    assert len(evts) == 1
    assert evts[0]["payload"]["url"] == "https://blog.otica.example.com/gestao-estoque-oticas"
    assert evts[0]["payload"]["destination"] == "markdown_export"

    # Idempotent repeat publish updates same row (no duplicate)
    retry = services.publish_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=bed["draft"]["id"],
        user_id=bed["creator_id"],
    )
    assert retry["is_new"] is False
    pubs = db.list_publications_for_draft(
        db_conn, workspace_id=bed["workspace_id"], draft_id=bed["draft"]["id"]
    )
    assert len(pubs) == 1


def test_staging_forces_mode_draft_ac3(
    db_conn: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F16 AC3: APP_ENV=staging forces mode='draft' even when live is requested."""
    bed = _seed_pass_draft_bed(db_conn, "staging")
    services.save_integration(
        db_conn,
        workspace_id=bed["workspace_id"],
        user_id=bed["creator_id"],
        port="publisher",
        plugin="markdown_export",
        settings={"export_dir": str(tmp_path), "default_mode": "live"},
    )
    monkeypatch.setenv("APP_ENV", "staging")
    services.decide_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=bed["draft"]["id"],
        decision="approved",
        user_id=bed["creator_id"],
    )
    res = services.publish_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=bed["draft"]["id"],
        user_id=bed["creator_id"],
        mode="live",
    )
    assert res["mode"] == "draft"
    exported = Path(res["response"]["path"])
    assert "mode: draft" in exported.read_text(encoding="utf-8")
    assert "draft: true" in exported.read_text(encoding="utf-8")


def test_failed_http_webhook_stores_error_and_allows_retry_ac4(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F16 AC4: Failed http_webhook stores failed + error; retry recovers same row to published."""
    monkeypatch.setenv("APP_ENCRYPTION_KEY", TEST_ENC_KEY)
    monkeypatch.setattr(safe_http, "is_ip_allowed", lambda _ip: True)
    bed = _seed_pass_draft_bed(db_conn, "retry-wh")

    srv, url, secret = _start_publish_server()
    try:
        services.save_integration(
            db_conn,
            workspace_id=bed["workspace_id"],
            user_id=bed["creator_id"],
            port="publisher",
            plugin="http_webhook",
            settings={"endpoint_url": url},
            secrets={"signing_secret": secret},
            encryption_key=TEST_ENC_KEY,
        )

        # Approval runs while webhook is returning HTTP 500
        srv.mode = "500"
        decided = services.decide_draft(
            db_conn,
            workspace_id=bed["workspace_id"],
            draft_id=bed["draft"]["id"],
            decision="approved",
            user_id=bed["creator_id"],
        )
        assert decided["status"] == "approved"
        assert decided["publication"]["status"] == "failed"
        assert decided["publication"]["url"] is None
        assert "HTTP 500" in decided["publication"]["error"]
        assert (
            db.list_events(
                db_conn,
                workspace_id=bed["workspace_id"],
                event_type="article.published",
                entity_id=bed["draft"]["id"],
            )
            == []
        )

        # Webhook recovers -> Retry succeeds on the same publications row
        srv.mode = "good"
        retried = services.retry_publication(
            workspace_id=bed["workspace_id"],
            draft_id=bed["draft"]["id"],
            user_id=bed["creator_id"],
            encryption_key=TEST_ENC_KEY,
        )
        assert retried["status"] == "published"
        assert retried["error"] is None
        assert retried["url"] == "https://cms.example.com/posts/gestao-estoque-oticas"
        assert retried["is_new"] is False

        refreshed = db.get_draft(
            db_conn, workspace_id=bed["workspace_id"], draft_id=bed["draft"]["id"]
        )
        assert refreshed is not None
        assert refreshed["status"] == "published"

        pub_evts = db.list_events(
            db_conn,
            workspace_id=bed["workspace_id"],
            event_type="article.published",
            entity_id=bed["draft"]["id"],
        )
        assert len(pub_evts) == 1
        assert pub_evts[0]["payload"]["url"] == retried["url"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_publish_draft_permissions_and_guards(db_conn: psycopg.Connection[Any]) -> None:
    """Enforces owner/member authorization, draft status guard, and missing publisher error."""
    bed = _seed_pass_draft_bed(db_conn, "guards")
    ws_id = bed["workspace_id"]
    creator_id = bed["creator_id"]
    draft_id = bed["draft"]["id"]

    # Pass draft cannot be published before Gate 2 approval
    with pytest.raises(ValueError, match="expected 'approved' or 'published'"):
        services.publish_draft(db_conn, workspace_id=ws_id, draft_id=draft_id, user_id=creator_id)

    # Approve without publisher configured
    services.decide_draft(
        db_conn, workspace_id=ws_id, draft_id=draft_id, decision="approved", user_id=creator_id
    )

    with pytest.raises(PermissionError, match="not a member"):
        services.publish_draft(db_conn, workspace_id=ws_id, draft_id=draft_id, user_id=uuid.uuid4())

    with pytest.raises(PermissionError, match="forbidden"):
        services.publish_draft(
            db_conn, workspace_id=ws_id, draft_id=draft_id, user_id=bed["other_user_id"]
        )

    with pytest.raises(IntegrationMissing, match="publisher"):
        services.publish_draft(db_conn, workspace_id=ws_id, draft_id=draft_id, user_id=creator_id)

    with pytest.raises(ValueError, match="not found"):
        services.publish_draft(
            db_conn, workspace_id=ws_id, draft_id=uuid.uuid4(), user_id=creator_id
        )


def test_n8n_publish_guide_sample_validates_against_contract() -> None:
    """M4-05 Step 5: docs/guides/n8n-publish.md response sample validates as PublishedContent."""
    guide_path = Path(__file__).resolve().parent.parent / "docs" / "guides" / "n8n-publish.md"
    assert guide_path.is_file(), "docs/guides/n8n-publish.md must exist"
    text = guide_path.read_text(encoding="utf-8")
    assert "Standard Webhooks" in text
    assert "PublishedContent" in text

    json_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    assert json_blocks, "n8n-publish.md must contain sample JSON code blocks"
    parsed_responses = [json.loads(block) for block in json_blocks if '"external_id"' in block]
    assert len(parsed_responses) >= 1
    model = PublishedContent.model_validate(parsed_responses[0])
    assert model.destination
    assert model.external_id
    assert model.url


def test_publish_draft_edge_cases_and_outcome_guards(
    db_conn: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers managed connection, payload-only claims fallback, and outcome safety checks."""
    monkeypatch.setattr(safe_http, "resolve_and_validate_host", lambda _host: ["93.184.216.34"])
    bed = _seed_pass_draft_bed(db_conn, "edge")
    # Create an approved draft without draft_claims rows to exercise payload fallback
    draft_no_rows = db.create_next_draft_revision(
        db_conn,
        workspace_id=bed["workspace_id"],
        brief_id=bed["brief"]["id"],
        prompt_version="write_blog.md:v1",
        schema_version=1,
        payload={
            "title": "Artigo Fallback Claims",
            "meta_description": "Resumo",
            "slug": "artigo-fallback-claims",
            "body_md": "# Conteudo",
            "faq": "not-a-list",
            "cta": "",
            "claims": [{"text": "Analise propria"}],
        },
        claims=[],
        status="pass",
        revision=1,
    )
    services.decide_draft(
        db_conn,
        workspace_id=bed["workspace_id"],
        draft_id=draft_no_rows["id"],
        decision="approved",
        user_id=bed["creator_id"],
    )
    services.save_integration(
        db_conn,
        workspace_id=bed["workspace_id"],
        user_id=bed["creator_id"],
        port="publisher",
        plugin="markdown_export",
        settings={"export_dir": str(tmp_path), "default_mode": "draft"},
    )
    pub = services.publish_draft(
        workspace_id=bed["workspace_id"],
        draft_id=draft_no_rows["id"],
        user_id=bed["creator_id"],
    )
    assert pub["status"] == "published"

    # Direct outcome validation guards
    validated_dict = services._validate_publish_outcome(
        {
            "destination": "custom",
            "external_id": "x-1",
            "url": "https://example.com/x-1",
            "status": "published",
            "raw": {"is_live": False},
        },
        mode="draft",
    )
    assert validated_dict.external_id == "x-1"

    with pytest.raises(PublishError, match="failed"):
        services._validate_publish_outcome(
            PublishedContent(
                destination="custom",
                external_id="x-2",
                status="failed",
                raw={"error": "upstream failed"},
            ),
            mode="live",
        )

    with pytest.raises(PublishError, match="mode='draft'"):
        services._validate_publish_outcome(
            PublishedContent(
                destination="custom",
                external_id="x-3",
                status="published",
                raw={"public": True},
            ),
            mode="draft",
        )

    with pytest.raises(ValidationError, match="Unsupported publish mode"):
        PublishRequest(
            draft_id=uuid.uuid4(),
            draft=_sample_draft(),
            locale="pt-BR",
            destination="http_webhook",
            mode="invalid-mode",
        )

    with pytest.raises(ValidationError, match="Unsupported PublishedContent status"):
        PublishedContent(
            destination="http_webhook",
            external_id="x-4",
            status="unknown-status",
        )
