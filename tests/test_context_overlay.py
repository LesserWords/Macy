"""Tests for M8-01 hybrid context overlay, starter packs, snapshot fallback & UI YAML I/O."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from engine import config, db, services
from engine.contracts import BrandProfile


def _write_custom_pack(packs_root: Path, slug: str, name: str) -> Path:
    ind_dir = packs_root / "industries" / slug
    ind_dir.mkdir(parents=True, exist_ok=True)
    (ind_dir / "profile.yaml").write_text(
        f"name: {name}\n"
        f"slug: {slug}\n"
        "market: US\n"
        "locales:\n  - en-US\n  - pt-BR\n"
        f"description: Custom pack for {name}\n",
        encoding="utf-8",
    )
    (ind_dir / "sources.yaml").write_text(
        "sources:\n"
        '  - {name: "Example Source", domain: "example.com", url: "https://example.com", '
        'fetch_mode: "site_search", tier: 1, role: "news", locales: ["en-US"], '
        'topics: ["updates"], enabled: true}\n',
        encoding="utf-8",
    )
    (ind_dir / "audience.md").write_text(f"# Audience for {name}\n", encoding="utf-8")
    (ind_dir / "challenges.md").write_text(f"# Challenges for {name}\n", encoding="utf-8")
    (ind_dir / "exclusions.md").write_text(f"# Exclusions for {name}\n", encoding="utf-8")
    (ind_dir / "terminology.md").write_text(f"# Terminology for {name}\n", encoding="utf-8")
    return ind_dir


def _write_custom_fixture(fixtures_root: Path, filename: str, positioning: str) -> Path:
    fixtures_root.mkdir(parents=True, exist_ok=True)
    path = fixtures_root / filename
    path.write_text(
        "schema_version: 1\n"
        'voice: "Clear, technical, objective"\n'
        f'positioning: "{positioning}"\n'
        'capabilities:\n  - "Automated source verification"\n'
        'prohibited_claims:\n  - "Guaranteed outcomes"\n'
        'prohibited_phrases:\n  - "\\\\bguaranteed win\\\\b"\n'
        'cta_rules:\n  - "TOFU: Read guide"\n  - "MOFU: Try check"\n  - "BOFU: Book demo"\n',
        encoding="utf-8",
    )
    return path


def test_cascading_overlay_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D1: Resolve packs/fixtures across MACY_CONTEXT_DIR > Pocket /data/context > OSS."""
    for var in (
        "MACY_CONTEXT_DIR",
        "MACY_PACKS_DIR",
        "MACY_FIXTURES_DIR",
        "MACY_DEFAULT_PACK",
        "MACY_DEFAULT_BRAND_FIXTURE",
        "MACY_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    assert config.get_default_industry_pack() == "general"
    assert config.get_default_brand_fixture().name == "seed_example.yaml"

    data_dir = tmp_path / "data"
    pocket_ctx = data_dir / "context"
    _write_custom_pack(pocket_ctx / "packs", "pocket-niche", "Pocket Niche")
    _write_custom_fixture(pocket_ctx / "fixtures", "seed_pocket.yaml", "Pocket Brand")

    env_ctx = tmp_path / "env_ctx"
    _write_custom_pack(env_ctx / "packs", "general", "Overridden General")
    _write_custom_fixture(env_ctx / "fixtures", "seed_env.yaml", "Env Brand")

    monkeypatch.setenv("MACY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MACY_CONTEXT_DIR", str(env_ctx))

    # L1 overrides built-in general; L2 provides pocket-niche; L3 still provides b2b-saas & formats
    assert config.load_industry_pack("general").name == "Overridden General"
    assert config.load_industry_pack("pocket-niche").name == "Pocket Niche"
    assert config.load_industry_pack("b2b-saas").slug == "b2b-saas"
    assert config.load_format_pack("blog").slug == "blog"

    packs = config.list_available_industry_packs()
    assert {"general", "pocket-niche", "b2b-saas"}.issubset(set(packs))

    fixtures = config.list_available_brand_fixtures()
    assert {"seed_example", "seed_pocket", "seed_env"}.issubset(set(fixtures))
    assert config.resolve_brand_fixture_path("seed_pocket.yaml") == (
        pocket_ctx / "fixtures" / "seed_pocket.yaml"
    )
    assert config.resolve_brand_fixture_path(None).name == "seed_example.yaml"
    assert config.get_packs_roots(tmp_path) == [tmp_path]
    assert config.get_fixtures_roots(tmp_path) == [tmp_path]

    digest_a = config.compute_pack_version("pocket-niche", "blog")
    digest_b = config.compute_pack_version("pocket-niche", "blog")
    assert digest_a == digest_b
    assert len(digest_a) == 64

    # Explicit MACY_PACKS_DIR / MACY_FIXTURES_DIR / MACY_DEFAULT_* overrides
    explicit_packs = tmp_path / "explicit_packs"
    explicit_fixtures = tmp_path / "explicit_fixtures"
    _write_custom_pack(explicit_packs, "explicit-pack", "Explicit Pack")
    exp_fix = _write_custom_fixture(explicit_fixtures, "seed_explicit.yaml", "Explicit Brand")
    monkeypatch.setenv("MACY_PACKS_DIR", str(explicit_packs))
    monkeypatch.setenv("MACY_FIXTURES_DIR", str(explicit_fixtures))
    monkeypatch.setenv("MACY_DEFAULT_PACK", "explicit-pack")
    monkeypatch.setenv("MACY_DEFAULT_BRAND_FIXTURE", "seed_explicit.yaml")

    assert config.get_default_industry_pack() == "explicit-pack"
    assert config.get_default_brand_fixture() == exp_fix
    assert config.load_industry_pack("explicit-pack").name == "Explicit Pack"


def test_user_agent_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1: HTTP User-Agent defaults neutrally and honors MACY_USER_AGENT across adapters."""
    from engine import safe_http, sitemap
    from engine.integrations.rss import RSSFeedSource
    from engine.integrations.trafilatura_extractor import TrafilaturaExtractor
    from spikes.trials.docling_plugin import DoclingExtractor

    monkeypatch.delenv("MACY_USER_AGENT", raising=False)
    assert config.get_user_agent() == "MacyContentEngine/1.0"
    assert RSSFeedSource().settings.user_agent == "MacyContentEngine/1.0"
    assert TrafilaturaExtractor().settings.user_agent == "MacyContentEngine/1.0"
    assert DoclingExtractor().settings.user_agent == "MacyContentEngine/1.0"

    monkeypatch.setenv("MACY_USER_AGENT", "CustomMacyBot/2.1 (+https://example.org)")
    assert config.get_user_agent() == "CustomMacyBot/2.1 (+https://example.org)"
    assert RSSFeedSource().settings.user_agent == "CustomMacyBot/2.1 (+https://example.org)"
    assert TrafilaturaExtractor().settings.user_agent == "CustomMacyBot/2.1 (+https://example.org)"
    assert DoclingExtractor().settings.user_agent == "CustomMacyBot/2.1 (+https://example.org)"

    captured_headers: list[dict[str, str]] = []
    monkeypatch.setattr("engine.safe_http.validate_safe_url", lambda *_a, **_kw: None)

    def _fake_safe_request(
        _method: str,
        _url: str,
        *,
        headers: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> safe_http.SafeResponse:
        captured_headers.append(dict(headers or {}))
        return safe_http.SafeResponse(
            status_code=200,
            headers={},
            content=b"<urlset><url><loc>https://example.com/a</loc></url></urlset>",
        )

    monkeypatch.setattr("engine.safe_http.safe_request", _fake_safe_request)
    sitemap.fetch_sitemap_articles("https://example.com/sitemap.xml", fetch_titles=True)
    assert all(
        h.get("User-Agent") == "CustomMacyBot/2.1 (+https://example.org)" for h in captured_headers
    )


def test_starter_packs_and_example_fixture() -> None:
    """D3: Built-in general & b2b-saas packs and seed_example.yaml validate cleanly."""
    for slug in ("general", "b2b-saas"):
        pack = config.load_industry_pack(slug)
        assert pack.slug == slug
        assert {"pt-BR", "en-US"}.issubset(set(pack.locales))
        assert len(pack.sources) >= 4
        assert {"audience", "challenges", "exclusions", "terminology"}.issubset(
            set(pack.markdown_docs.keys())
        )

    example_path = config.REPO_ROOT / "fixtures" / "seed_example.yaml"
    assert example_path.is_file()
    raw = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    profile = BrandProfile.model_validate(raw)
    assert profile.schema_version == 1
    assert len(profile.capabilities) >= 4
    assert len(profile.cta_rules) >= 3


def test_workspace_industry_pack_snapshot_fallback(
    db_conn: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: Workspace persists industry_pack_snapshot for diskless runtime fallback."""
    import engine.strategy.score as score_mod
    from engine.integrations.fakes import FakeLLM
    from engine.production import write as run_write
    from engine.quality import EvalExecutionSpec, evaluate_persisted_draft
    from engine.strategy import brief as run_brief

    ext_ctx = tmp_path / "ext_ctx"
    _write_custom_pack(ext_ctx / "packs", "ephemeral-niche", "Ephemeral Niche")
    _write_custom_fixture(ext_ctx / "fixtures", "seed_ephemeral.yaml", "Ephemeral Brand")
    monkeypatch.setenv("MACY_CONTEXT_DIR", str(ext_ctx))
    monkeypatch.setenv("MACY_DEFAULT_PACK", "ephemeral-niche")

    slug = f"ws-snap-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="Snapshot WS", timezone="UTC")
    ws_id = ws["workspace_id"]
    assert ws["industry_pack"] == "ephemeral-niche"

    ws_cfg = db.get_workspace_config(db_conn, workspace_id=ws_id) or {}
    assert ws_cfg["industry_pack_snapshot"]["slug"] == "ephemeral-niche"
    assert "audience" in ws_cfg["industry_pack_snapshot"]["markdown_docs"]

    owner = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"snap-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = owner["user"]["user_id"]
    services.seed_brand(db_conn, workspace_slug=slug, fixture_path="seed_ephemeral.yaml")

    # Remove the external context directory completely and clear env override
    shutil.rmtree(ext_ctx)
    monkeypatch.delenv("MACY_CONTEXT_DIR")
    monkeypatch.delenv("MACY_DEFAULT_PACK")

    resolved = config.resolve_industry_pack_dict("ephemeral-niche", ws_cfg)
    assert resolved["name"] == "Ephemeral Niche"
    assert "audience" in resolved["markdown_docs"]

    camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        locale="en-US",
        market="US",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("10.00"),
        name="Snapshot Campaign",
    )
    now = datetime.now(UTC)
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp["id"],
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_id,
    )
    doc = db.upsert_document(
        db_conn,
        workspace_id=ws_id,
        url=f"https://example.com/snap-{uuid.uuid4().hex[:6]}",
        canonical_url=f"https://example.com/snap-{uuid.uuid4().hex[:6]}",
        title="Snapshot Article",
        date_status="verified",
        fetch_status="ok",
        content_hash=b"snap-h1",
    )
    rr = db.upsert_research_record(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        document_id=doc["id"],
        status="verified",
        schema_version=1,
        payload={
            "headline": "Snapshot Article",
            "facts": ["Fact 1"],
            "summary": "Summary 1",
            "verification_status": "verified",
        },
    )
    assert rr["id"] is not None
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    opps = score_mod.score_run(db_conn, run_row, llm_adapter=FakeLLM())
    assert len(opps) == 1

    opp_id = uuid.UUID(str(opps[0]["id"]))
    db.decide_opportunity(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        decision="approved",
        user_id=user_id,
        decided_at=datetime.now(UTC),
        note="Approve snapshot test",
    )
    brief_res = run_brief(
        opp_id,
        conn=db_conn,
        workspace_id=ws_id,
        actor_user_id=user_id,
        offline=True,
    )
    brief_id = uuid.UUID(str(brief_res["id"]))
    write_res = run_write(
        brief_id,
        conn=db_conn,
        workspace_id=ws_id,
        actor_user_id=user_id,
        offline=True,
    )
    draft_id = uuid.UUID(str(write_res["id"]))
    eval_res = evaluate_persisted_draft(
        db_conn,
        EvalExecutionSpec(
            workspace_id=ws_id,
            draft_id=draft_id,
            actor_user_id=user_id,
            offline=True,
        ),
    )
    assert eval_res["report"].verdict in {"PASS", "REVISION", "REJECT"}

    # Verify fallback edge cases in resolve_industry_pack_dict
    assert config.resolve_industry_pack_dict(None, None) == {}
    assert config.resolve_industry_pack_dict("missing-slug", None) == {"slug": "missing-slug"}


def test_brand_fixture_yaml_import_export_and_ui(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: Service YAML import/export round-trips and Admin UI exposes pack/fixture controls."""
    from streamlit.testing.v1 import AppTest

    slug = f"ws-yaml-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="YAML WS", timezone="UTC")
    ws_id = ws["workspace_id"]
    email = f"yaml-{uuid.uuid4().hex[:8]}@example.com"
    owner = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = owner["user"]["user_id"]

    assert services.get_default_industry_pack() == "general"
    assert "general" in services.list_available_industry_packs()
    assert "seed_example" in services.list_available_brand_fixtures()

    with pytest.raises(ValueError, match="No brand profile"):
        services.export_brand_fixture_yaml(db_conn, workspace_id=ws_id, user_id=user_id)

    sample_yaml = (config.REPO_ROOT / "fixtures" / "seed_example.yaml").read_text(encoding="utf-8")
    imported = services.import_brand_fixture_yaml(
        db_conn, workspace_id=ws_id, user_id=user_id, yaml_text=sample_yaml
    )
    assert imported["version"] == 1

    exported_yaml = services.export_brand_fixture_yaml(db_conn, workspace_id=ws_id, user_id=user_id)
    assert BrandProfile.model_validate(yaml.safe_load(exported_yaml)).positioning

    # Also exercise managed-connection (conn=None) path
    monkeypatch.setenv(
        "DATABASE_URL", "postgres://app:app_secret@localhost:5432/macy?sslmode=disable"
    )
    imported_managed = services.import_brand_fixture_yaml(
        None, workspace_id=ws_id, user_id=user_id, yaml_text=exported_yaml
    )
    assert imported_managed["version"] == 2
    assert "Acme Content OS" in services.export_brand_fixture_yaml(
        None, workspace_id=ws_id, user_id=user_id
    )

    with pytest.raises(ValueError, match="mapping"):
        services.import_brand_fixture_yaml(
            db_conn, workspace_id=ws_id, user_id=user_id, yaml_text="- not\n- a-mapping\n"
        )
    with pytest.raises(ValueError, match="YAML"):
        services.import_brand_fixture_yaml(
            db_conn, workspace_id=ws_id, user_id=user_id, yaml_text=":\n  [\n"
        )

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MACY_TEST_USER_EMAIL", email)
    at = AppTest.from_file(str(config.REPO_ROOT / "app.py"), default_timeout=15)
    at.run()
    assert not at.exception
    at.sidebar.radio(key="main_nav").set_value("Perfil da Marca").run()
    assert not at.exception
    assert any("seed_example" in str(sb.options) for sb in at.selectbox)
