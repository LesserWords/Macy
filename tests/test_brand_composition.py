"""Tests for M11-01: BrandProfile canonical identity fields and YAML extends composition."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from engine import config, db, services
from engine.contracts import BrandProfile, ContentDraft
from engine.quality.checks import check_no_prohibited_phrases


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_brand_profile_canonical_identity_fields_and_whitespace_validation() -> None:
    """BrandProfile accepts optional brand_name, target_persona, canonical_domain."""
    base = BrandProfile(
        brand_name=None,
        voice="Clear and consultative",
        positioning="Core ERP for retail",
        capabilities=["POS"],
    )
    assert base.brand_name is None
    assert base.target_persona is None
    assert base.canonical_domain is None

    rich = BrandProfile(
        brand_name="  Vertical Specialty ERP  ",
        target_persona="  Clinic and specialty retail owners  ",
        canonical_domain="  vertical.example.com  ",
        voice="Clear and consultative",
        positioning="Vertical Specialty ERP platform",
        capabilities=["POS"],
    )
    assert rich.brand_name == "Vertical Specialty ERP"
    assert rich.target_persona == "Clinic and specialty retail owners"
    assert rich.canonical_domain == "vertical.example.com"

    with pytest.raises(ValueError, match="brand_name"):
        BrandProfile(
            brand_name="   ",
            voice="Clear",
            positioning="Pos",
        )


def test_load_brand_fixture_dict_composes_extends_across_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load-time YAML extends merges capabilities/prohibitions and strips directives."""
    base_dir = tmp_path / "base_fixtures"
    overlay_dir = tmp_path / "overlay_fixtures"
    _write_yaml(
        base_dir / "_base_core.yaml",
        {
            "schema_version": 1,
            "voice": "Objective B2B tone",
            "positioning": "Core ERP suite",
            "capabilities": [
                "Unified POS and fiscal receipts",
                "Generic wholesale invoicing",
                "Inventory barcodes",
            ],
            "prohibited_claims": ["Guaranteed 100% revenue uplift"],
            "prohibited_phrases": [r"\bbest ERP ever\b"],
            "cta_rules": ["TOFU: invite to educational guide"],
        },
    )
    child_path = _write_yaml(
        overlay_dir / "seed_vertical.yaml",
        {
            "extends": "_base_core.yaml",
            "brand_name": "Vertical Specialty ERP",
            "target_persona": "Specialty clinic & lab managers",
            "canonical_domain": "vertical.example.com",
            "positioning": "Vertical Specialty ERP built for specialty clinics.",
            "capabilities_remove": ["Generic wholesale invoicing"],
            "capabilities_add": ["Specialty lab work-order tracking"],
            "prohibited_claims_add": ["Medical diagnosis guarantees"],
            "prohibited_phrases_add": [r"\bCore ERP\b"],
        },
    )

    monkeypatch.setenv("MACY_FIXTURES_DIR", str(overlay_dir))
    # Ensure child lookup can also fall back to base_dir via explicit search path monkeypatch
    monkeypatch.setattr(
        config,
        "get_fixtures_roots",
        lambda explicit_base=None: (
            [overlay_dir, base_dir] if explicit_base is None else [Path(explicit_base)]
        ),
    )

    merged = config.load_brand_fixture_dict(child_path)
    for directive in (
        "extends",
        "capabilities_add",
        "capabilities_remove",
        "prohibited_phrases_add",
        "prohibited_claims_add",
    ):
        assert directive not in merged

    profile = BrandProfile.model_validate(merged)
    assert profile.brand_name == "Vertical Specialty ERP"
    assert profile.target_persona == "Specialty clinic & lab managers"
    assert profile.canonical_domain == "vertical.example.com"
    assert profile.capabilities == [
        "Unified POS and fiscal receipts",
        "Inventory barcodes",
        "Specialty lab work-order tracking",
    ]
    assert profile.prohibited_phrases == [r"\bbest ERP ever\b", r"\bCore ERP\b"]
    assert profile.prohibited_claims == [
        "Guaranteed 100% revenue uplift",
        "Medical diagnosis guarantees",
    ]


def test_load_brand_fixture_dict_detects_cycles_and_missing_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Circular extends chains, missing parents, and invalid YAML mappings fail cleanly."""
    _write_yaml(tmp_path / "a.yaml", {"extends": "b.yaml", "voice": "v", "positioning": "p"})
    _write_yaml(tmp_path / "b.yaml", {"extends": "a.yaml", "voice": "v", "positioning": "p"})
    _write_yaml(
        tmp_path / "orphan.yaml",
        {"extends": "missing.yaml", "voice": "v", "positioning": "p"},
    )
    bad_list = tmp_path / "bad_list.yaml"
    bad_list.write_text("- not-a-mapping\n", encoding="utf-8")

    monkeypatch.setattr(
        config,
        "get_fixtures_roots",
        lambda explicit_base=None: [tmp_path] if explicit_base is None else [Path(explicit_base)],
    )

    with pytest.raises(ValueError, match="Circular brand fixture extends"):
        config.load_brand_fixture_dict(tmp_path / "a.yaml")

    with pytest.raises(ValueError, match="Parent brand fixture not found"):
        config.load_brand_fixture_dict(tmp_path / "orphan.yaml")

    with pytest.raises(ValueError, match="Fixture file not found"):
        config.load_brand_fixture_dict(tmp_path / "does_not_exist.yaml")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        config.load_brand_fixture_dict(bad_list)


def test_prohibited_phrases_regex_word_boundary_allows_extended_brand_name() -> None:
    """Banning bare \\bCore ERP\\b rejects 'Core ERP' while allowing 'Vertical Core ERP'."""
    profile = BrandProfile(
        brand_name="Core Specialty ERP",
        voice="Direct",
        positioning="Core Specialty ERP",
        capabilities=["POS"],
        prohibited_phrases=[r"\bCore ERP\b"],
    )
    bad_draft = ContentDraft(
        title="Fiscal guide",
        meta_description="m" * 125,
        slug="fiscal-guide",
        body_md="# Fiscal guide\n\nUse Core ERP for billing.",
        faq=[{"question": "Q1?", "answer": "A1"}, {"question": "Q2?", "answer": "A2"}],
        cta={"headline": "H", "body": "Try Core ERP", "button_text": "Go"},
        claims=[],
    )
    good_draft = bad_draft.model_copy(
        update={
            "body_md": "# Fiscal guide\n\nUse Core Specialty ERP for billing.",
            "cta": {"headline": "H", "body": "Try Core Specialty ERP", "button_text": "Go"},
        }
    )

    assert not check_no_prohibited_phrases(bad_draft, profile).passed
    assert check_no_prohibited_phrases(good_draft, profile).passed


def test_execute_seed_brand_uses_extends_loader_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit test covering services._execute_seed_brand and relative/override extends."""
    parent_file = _write_yaml(
        tmp_path / "shared" / "_parent.yaml",
        {
            "schema_version": 1,
            "voice": "Parent voice",
            "positioning": "Parent positioning",
            "capabilities": ["Shared A", "Shared B"],
            "prohibited_claims": ["No magic"],
            "prohibited_phrases": [],
            "cta_rules": ["TOFU: guide"],
        },
    )
    child_file = _write_yaml(
        tmp_path / "shared" / "child_rel.yaml",
        {
            "extends": "_parent.yaml",
            "brand_name": "Child ERP",
            "positioning": "Child ERP positioning",
            "cta_rules": ["MOFU: overridden CTA"],
        },
    )
    # Direct path parent resolution
    direct_child = _write_yaml(
        tmp_path / "direct_child.yaml",
        {
            "extends": str(parent_file),
            "positioning": "Direct positioning",
        },
    )
    direct_bp = BrandProfile.model_validate(config.load_brand_fixture_dict(direct_child))
    assert direct_bp.capabilities == ["Shared A", "Shared B"]

    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(db, "get_workspace_by_slug", lambda conn, slug: {"workspace_id": ws_id})
    monkeypatch.setattr(db, "get_workspace_owner", lambda conn, workspace_id: {"user_id": owner_id})
    monkeypatch.setattr(
        services,
        "_execute_save_brand",
        lambda conn, workspace_id, user_id, profile: (
            captured.update({"ws": workspace_id, "user": user_id, "profile": profile})
            or {"version": 1}
        ),
    )
    fake_conn: Any = object()
    res = services.seed_brand(fake_conn, workspace_slug="demo-ws", fixture_path=child_file)
    assert res == {"version": 1}
    assert captured["ws"] == ws_id
    assert captured["user"] == owner_id
    seeded_bp = captured["profile"]
    assert isinstance(seeded_bp, BrandProfile)
    assert seeded_bp.brand_name == "Child ERP"
    assert seeded_bp.cta_rules == ["MOFU: overridden CTA"]


def test_seed_brand_materializes_extended_fixture_into_db(
    db_conn: psycopg.Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeding a workspace from an extends YAML persists an independent snapshot row."""
    _write_yaml(
        tmp_path / "_base.yaml",
        {
            "schema_version": 1,
            "voice": "Practical",
            "positioning": "Base",
            "capabilities": ["Shared fiscal emission"],
            "prohibited_claims": [],
            "prohibited_phrases": [],
            "cta_rules": ["MOFU: offer practical checklist"],
        },
    )
    _write_yaml(
        tmp_path / "seed_child.yaml",
        {
            "extends": "_base.yaml",
            "brand_name": "Vertical Brand ERP",
            "target_persona": "Vertical managers",
            "canonical_domain": "vertical.example.test",
            "positioning": "Vertical Brand ERP for vertical managers.",
            "capabilities_add": ["Vertical workflow"],
        },
    )
    monkeypatch.setattr(
        config,
        "get_fixtures_roots",
        lambda explicit_base=None: [tmp_path] if explicit_base is None else [Path(explicit_base)],
    )

    slug = f"m11-brand-{uuid.uuid4().hex[:6]}"
    ws_id = db.create_workspace(db_conn, slug=slug, name="M11 Brand WS", industry_pack="general")
    owner = services.add_workspace_owner(db_conn, workspace_slug=slug, email=f"{slug}@example.com")
    owner_id = uuid.UUID(str(owner["user"]["user_id"]))

    saved = services.seed_brand(db_conn, workspace_slug=slug, fixture_path="seed_child.yaml")
    current = services.current_brand(db_conn, workspace_id=ws_id, user_id=owner_id)
    assert saved is not None
    assert current is not None
    payload = current["payload"]
    assert payload["brand_name"] == "Vertical Brand ERP"
    assert payload["target_persona"] == "Vertical managers"
    assert payload["canonical_domain"] == "vertical.example.test"
    assert payload["capabilities"] == ["Shared fiscal emission", "Vertical workflow"]
