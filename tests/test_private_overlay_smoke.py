"""End-to-end smoke suite for M8-04 private context overlay and DB snapshot parity."""

from __future__ import annotations

import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

import engine.strategy.score as score_mod
from engine import config, db, services
from engine.integrations.fakes import FakeLLM


def _materialize_private_context(root: Path, *, pack_slug: str, fixture_name: str) -> Path:
    """Create a realistic external private context overlay tree without touching OSS files."""
    pack_dir = root / "packs" / "industries" / pack_slug
    pack_dir.mkdir(parents=True, exist_ok=True)
    profile_data = {
        "name": "Industrial Robotics",
        "slug": pack_slug,
        "market": "BR",
        "locales": ["en-US", "pt-BR"],
        "description": "Private vertical pack for factory automation and industrial robotics.",
    }
    sources_data = {
        "sources": [
            {
                "name": "Robotics Trade Wire",
                "domain": "roboticswire.example",
                "url": "https://roboticswire.example",
                "feed_url": "https://roboticswire.example/rss.xml",
                "fetch_mode": "feed",
                "tier": 1,
                "role": "news",
                "locales": ["en-US", "pt-BR"],
                "topics": ["cobots", "predictive maintenance"],
                "enabled": True,
            },
            {
                "name": "Automation Research Portal",
                "domain": "autoresearch.example",
                "url": "https://autoresearch.example/news",
                "fetch_mode": "site_search",
                "tier": 2,
                "role": "data",
                "locales": ["en-US", "pt-BR"],
                "topics": ["manufacturing telemetry"],
                "enabled": True,
            },
        ]
    }
    (pack_dir / "profile.yaml").write_text(yaml.safe_dump(profile_data), encoding="utf-8")
    (pack_dir / "sources.yaml").write_text(yaml.safe_dump(sources_data), encoding="utf-8")
    for doc_name in ("audience", "challenges", "exclusions", "terminology"):
        (pack_dir / f"{doc_name}.md").write_text(
            f"# {doc_name.title()} — Industrial Robotics\n\nPlant engineers and integrators.\n",
            encoding="utf-8",
        )

    fixture_dir = root / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_payload = {
        "schema_version": 1,
        "voice": "Authoritative, engineering-led, grounded in plant telemetry",
        "positioning": "Private robotics intelligence platform for discrete manufacturing lines.",
        "capabilities": [
            "Cycle-time anomaly detection",
            "PLC telemetry normalization",
            "Predictive actuator diagnostics",
        ],
        "prohibited_claims": ["Zero unplanned downtime guaranteed"],
        "prohibited_phrases": [r"\b100% failure-proof\b"],
        "cta_rules": [
            "TOFU: Download benchmark checklist",
            "MOFU: Compare sensor deployment options",
            "BOFU: Schedule plant architecture review",
        ],
    }
    fixture_path = fixture_dir / fixture_name
    fixture_path.write_text(yaml.safe_dump(fixture_payload), encoding="utf-8")
    return fixture_path


def test_private_context_overlay_and_snapshot_e2e_parity(
    db_conn: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify full lifecycle under MACY_CONTEXT_DIR / Pocket /data/context and snapshot fallback."""
    monkeypatch.delenv("MACY_PACKS_DIR", raising=False)
    monkeypatch.delenv("MACY_FIXTURES_DIR", raising=False)

    overlay_dir = tmp_path / "data" / "context"
    pack_slug = f"robotics-{uuid.uuid4().hex[:6]}"
    fixture_name = f"seed_robotics_{uuid.uuid4().hex[:6]}.yaml"
    _materialize_private_context(overlay_dir, pack_slug=pack_slug, fixture_name=fixture_name)

    monkeypatch.setenv("MACY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MACY_CONTEXT_DIR", str(overlay_dir))

    # Inherits built-in blog format pack while resolving private industry pack + fixture
    assert config.load_format_pack("blog").slug == "blog"
    assert pack_slug in config.list_available_industry_packs()
    assert fixture_name.removesuffix(".yaml") in config.list_available_brand_fixtures()

    ws_slug = f"overlay-smoke-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(
        db_conn,
        slug=ws_slug,
        name="Overlay Smoke Robotics",
        industry_pack=pack_slug,
        timezone="UTC",
    )
    ws_id = ws["workspace_id"]
    owner = services.add_workspace_owner(
        db_conn,
        workspace_slug=ws_slug,
        email=f"overlay-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = owner["user"]["user_id"]

    brand_res = services.seed_brand(db_conn, workspace_slug=ws_slug, fixture_path=fixture_name)
    assert brand_res["id"] is not None

    seeded_sources = services.seed_sources(db_conn, industry_pack=pack_slug)
    assert len(seeded_sources) == 2

    verified = services.verify_sources(db_conn, industry_pack=pack_slug, days=30, offline=True)
    assert len(verified) == 2

    campaign = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        locale="en-US",
        market="US",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("10.00"),
        name="Robotics Weekly",
    )

    week_res = services.run_week(
        db_conn,
        workspace_identifier=ws_slug,
        campaign_identifier=str(campaign["id"]),
        user_id=user_id,
        offline=True,
        window_days=7,
    )
    assert week_res["status"] == "completed"
    run_id = uuid.UUID(str(week_res["id"]))
    opps = db.list_opportunities(db_conn, workspace_id=ws_id, run_id=run_id)
    assert len(opps) >= 1

    # Remove external context completely and verify diskless DB snapshot parity through evaluation
    shutil.rmtree(overlay_dir)
    monkeypatch.delenv("MACY_CONTEXT_DIR")
    monkeypatch.delenv("MACY_DATA_DIR")
    assert pack_slug not in config.list_available_industry_packs()

    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=run_id)
    assert run_row is not None
    rescored = score_mod.score_run(db_conn, run_row, llm_adapter=FakeLLM())
    assert len(rescored) >= 1

    opp_id = uuid.UUID(str(opps[0]["id"]))
    decision = services.decide_topic(
        db_conn,
        workspace_id=ws_id,
        opportunity_id=opp_id,
        decision="approved",
        user_id=user_id,
        note="Smoke approval after overlay unmount",
        auto_brief=True,
        offline=True,
    )
    assert decision["status"] == "approved"
    brief_row = decision["brief"]
    assert brief_row is not None
    draft_row = services.write_draft(
        db_conn,
        workspace_id=ws_id,
        brief_id=uuid.UUID(str(brief_row["id"])),
        user_id=user_id,
        offline=True,
    )
    eval_res = services.evaluate_draft(
        db_conn,
        workspace_id=ws_id,
        draft_id=uuid.UUID(str(draft_row["id"])),
        user_id=user_id,
        offline=True,
    )
    assert eval_res["report"].verdict in {"PASS", "REVISION", "REJECT"}
