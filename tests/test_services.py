import ast
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from engine import db, services


def test_cli_main_entrypoint() -> None:
    from engine import cli

    cli.main()


def test_require_member_success(db_conn: psycopg.Connection) -> None:
    ws_id = db.create_workspace(
        db_conn,
        slug=f"svc-{uuid.uuid4().hex[:8]}",
        name="Services Test",
        industry_pack="general",
    )

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%s, 'Member User', 'pt-BR')
            RETURNING id
            """,
            (f"member-{uuid.uuid4().hex[:8]}@example.com",),
        )
        row = cur.fetchone()
        assert row is not None
        user_id = row[0]

        cur.execute(
            """
            INSERT INTO memberships (workspace_id, user_id, role)
            VALUES (%s, %s, 'owner')
            """,
            (ws_id, user_id),
        )

    role = services.require_member(db_conn, workspace_id=ws_id, user_id=user_id)
    assert role == "owner"


def test_require_member_unauthorized_raises_permission_error(
    db_conn: psycopg.Connection,
) -> None:
    ws_id = db.create_workspace(
        db_conn,
        slug=f"unauth-{uuid.uuid4().hex[:8]}",
        name="Unauthorized Test",
        industry_pack="general",
    )
    non_member_id = uuid.uuid4()

    with pytest.raises(PermissionError, match="not a member"):
        services.require_member(db_conn, workspace_id=ws_id, user_id=non_member_id)


def check_boundary_ast(source: str) -> list[str]:
    """Inspects AST of UI or CLI code to ensure it only imports services and contracts."""
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("engine.db"):
                    violations.append(
                        f"Forbidden direct DB import: '{alias.name}' at line {node.lineno}"
                    )
        elif (
            isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("engine.db")
        ):
            violations.append(
                f"Forbidden direct DB import from '{node.module}' at line {node.lineno}"
            )

    return violations


def test_boundary_check_detects_planted_db_import() -> None:
    bad_code = "import engine.db\nfrom engine.db import get_workspace\n"
    violations = check_boundary_ast(bad_code)
    assert len(violations) == 2


def test_app_and_cli_obey_boundary() -> None:
    app_source = Path("app.py").read_text(encoding="utf-8")
    assert not check_boundary_ast(app_source), "app.py violates service layer boundary"

    cli_source = Path("engine/cli.py").read_text(encoding="utf-8")
    assert not check_boundary_ast(cli_source), "cli.py violates service layer boundary"


def test_import_linter_forbids_cli_direct_db_access() -> None:
    import shutil

    cli_path = Path("engine/cli.py")
    orig_content = cli_path.read_text(encoding="utf-8")

    lint_bin = shutil.which("lint-imports") or "lint-imports"
    try:
        cli_path.write_text("import engine.db\n", encoding="utf-8")
        result = subprocess.run(  # noqa: S603
            [lint_bin],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, "lint-imports must fail when engine/cli.py imports engine.db"
    finally:
        cli_path.write_text(orig_content, encoding="utf-8")


def test_add_workspace_owner_service_and_audit(db_conn: psycopg.Connection) -> None:
    slug = f"ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Owner Provisioning Test",
        industry_pack="general",
    )

    owner_email = f"owner-{uuid.uuid4().hex[:8]}@example.com"

    # Provision owner
    res = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=owner_email,
    )

    assert res["workspace"]["workspace_id"] == ws_id
    assert res["user"]["email"] == owner_email
    assert res["membership"]["role"] == "owner"

    # Verify event logged
    events = db.list_events(db_conn, workspace_id=ws_id)
    audit_events = [e for e in events if e["type"] == "membership.created"]
    assert len(audit_events) == 1
    assert audit_events[0]["payload"]["email"] == owner_email
    assert audit_events[0]["payload"]["role"] == "owner"

    # Verify get_user_by_email and list_user_workspaces
    user_record = services.get_user_by_email(db_conn, email=owner_email.upper())
    assert user_record is not None
    assert user_record["email"] == owner_email

    memberships = services.list_user_workspaces(db_conn, user_id=res["user"]["user_id"])
    assert len(memberships) == 1
    assert memberships[0]["workspace_id"] == ws_id
    assert memberships[0]["slug"] == slug
    assert memberships[0]["role"] == "owner"

    # Idempotent re-add of same user to same workspace
    res2 = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=owner_email,
    )
    assert res2["user"]["user_id"] == res["user"]["user_id"]


def test_add_workspace_owner_invalid_slug_raises_value_error(
    db_conn: psycopg.Connection,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        services.add_workspace_owner(
            db_conn,
            workspace_slug=f"nonexistent-{uuid.uuid4().hex[:6]}",
            email="fail@example.com",
        )


def test_services_managed_connection_path(db_conn: psycopg.Connection) -> None:
    slug = f"managed-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Managed Conn Test",
        industry_pack="general",
    )
    email = f"managed-{uuid.uuid4().hex[:8]}@example.com"

    # add_workspace_owner with conn=None
    res = services.add_workspace_owner(
        conn=None,
        workspace_slug=slug,
        email=email,
    )
    user_id = res["user"]["user_id"]

    # get_user_by_email with conn=None
    user = services.get_user_by_email(conn=None, email=email)
    assert user is not None
    assert user["user_id"] == user_id

    # list_user_workspaces with conn=None
    workspaces = services.list_user_workspaces(conn=None, user_id=user_id)
    assert len(workspaces) >= 1

    # get_workspace_for_member with conn=None
    ws = services.get_workspace_for_member(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert ws["workspace_id"] == ws_id


def test_get_workspace_for_member_authorization(db_conn: psycopg.Connection) -> None:
    slug = f"authz-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Authz Test",
        industry_pack="general",
    )
    email = f"authz-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # Member can access
    ws = services.get_workspace_for_member(db_conn, workspace_id=ws_id, user_id=user_id)
    assert ws["workspace_id"] == ws_id
    assert ws["name"] == "Authz Test"

    # Non-member rejected
    non_member_id = uuid.uuid4()
    with pytest.raises(PermissionError, match="not a member"):
        services.get_workspace_for_member(
            db_conn,
            workspace_id=ws_id,
            user_id=non_member_id,
        )

    # Missing workspace error when membership check is bypassed
    with (
        patch("engine.services.require_member", return_value="owner"),
        pytest.raises(ValueError, match="not found"),
    ):
        services.get_workspace_for_member(
            db_conn,
            workspace_id=uuid.uuid4(),
            user_id=user_id,
        )


def test_cli_add_owner_command(
    db_conn: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from engine import cli

    slug = f"cli-ws-{uuid.uuid4().hex[:8]}"
    db.create_workspace(
        db_conn,
        slug=slug,
        name="CLI Test Workspace",
        industry_pack="general",
    )
    email = f"cli-owner-{uuid.uuid4().hex[:8]}@example.com"

    # Success invocation
    ret = cli.main(["add-owner", slug, email])
    assert ret == 0
    captured = capsys.readouterr()
    assert f"Added owner '{email}' to workspace '{slug}'" in captured.out

    # Failure invocation with invalid slug
    ret_fail = cli.main(["add-owner", "nonexistent-workspace-xyz", email])
    assert ret_fail == 1
    captured_fail = capsys.readouterr()
    assert "Error:" in captured_fail.err


def test_set_user_ui_locale_service(db_conn: psycopg.Connection) -> None:
    slug = f"locale-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Locale Test WS",
        industry_pack="general",
    )
    email = f"locale-user-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # Initial locale is pt-BR
    user_before = services.get_user_by_email(db_conn, email=email)
    assert user_before is not None
    assert user_before["ui_locale"] == "pt-BR"

    # Switch to en-US with workspace_id
    services.set_user_ui_locale(
        db_conn,
        user_id=user_id,
        ui_locale="en-US",
        workspace_id=ws_id,
    )

    user_after = services.get_user_by_email(db_conn, email=email)
    assert user_after is not None
    assert user_after["ui_locale"] == "en-US"

    # Verify event logged
    events = db.list_events(db_conn, workspace_id=ws_id)
    locale_events = [e for e in events if e["type"] == "user.locale_updated"]
    assert len(locale_events) == 1
    assert locale_events[0]["payload"]["ui_locale"] == "en-US"

    # Switch back to pt-BR with conn=None (managed path) and no workspace_id
    services.set_user_ui_locale(
        conn=None,
        user_id=user_id,
        ui_locale="pt-BR",
        workspace_id=None,
    )
    user_pt = services.get_user_by_email(conn=None, email=email)
    assert user_pt is not None
    assert user_pt["ui_locale"] == "pt-BR"

    # Invalid locale raises ValueError
    with pytest.raises(ValueError, match="Unsupported UI locale"):
        services.set_user_ui_locale(
            db_conn,
            user_id=user_id,
            ui_locale="es-ES",
        )


def test_save_campaign_create_and_update_with_settings_changed_event(
    db_conn: psycopg.Connection,
) -> None:
    from decimal import Decimal

    from engine.contracts import CampaignConfig, ScoringWeights

    slug = f"camp-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Campaign Service WS",
        industry_pack="general",
    )
    email = f"camp-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # Initially no active campaign
    assert services.get_active_campaign(db_conn, workspace_id=ws_id, user_id=user_id) is None

    # 1. Create campaign (AC1)
    saved = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        name="Brazil Growth",
        locale="pt-BR",
        market="BR",
        articles_per_week=3,
        schedule_cron="*/15 9-17 1,15 1-6/2 1-5",
        monthly_cap_usd=Decimal("150.00"),
        run_cap_usd=Decimal("25.00"),
    )
    assert saved["locale"] == "pt-BR"
    assert saved["market"] == "BR"
    assert saved["articles_per_week"] == 3
    assert saved["monthly_cap_usd"] == Decimal("150.00")
    assert saved["run_cap_usd"] == Decimal("25.00")
    assert saved["config"]["scoring_weights"]["novelty"] == 30

    events = db.list_events(db_conn, workspace_id=ws_id)
    changed_events = [e for e in events if e["type"] == "settings.changed"]
    assert len(changed_events) == 1
    assert changed_events[0]["payload"]["before"] is None
    assert changed_events[0]["payload"]["after"]["locale"] == "pt-BR"
    assert changed_events[0]["payload"]["after"]["articles_per_week"] == 3
    assert changed_events[0]["payload"]["after"]["monthly_cap_usd"] == "150.00"
    assert changed_events[0]["payload"]["after"]["run_cap_usd"] == "25.00"

    # 2. Update existing campaign (AC1, AC4)
    custom_cfg = CampaignConfig(
        goal="lead-gen",
        scoring_weights=ScoringWeights(
            novelty=25,
            audience_fit=25,
            brand_fit=20,
            competitor_gap=15,
            trend=10,
            urgency=5,
        ),
    )
    updated = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        campaign_id=saved["id"],
        name="US Expansion",
        locale="en-US",
        market="US",
        articles_per_week=5,
        schedule_cron="0 10 * * 2",
        monthly_cap_usd="300.00",
        run_cap_usd="40.00",
        config=custom_cfg,
    )
    assert updated["id"] == saved["id"]
    assert updated["name"] == "US Expansion"
    assert updated["locale"] == "en-US"
    assert updated["market"] == "US"
    assert updated["articles_per_week"] == 5
    assert updated["monthly_cap_usd"] == Decimal("300.00")
    assert updated["run_cap_usd"] == Decimal("40.00")
    assert updated["config"]["goal"] == "lead-gen"
    assert updated["config"]["scoring_weights"]["novelty"] == 25

    events_after = db.list_events(db_conn, workspace_id=ws_id)
    changed_after = [e for e in events_after if e["type"] == "settings.changed"]
    assert len(changed_after) == 2
    latest_event = changed_after[0]
    assert latest_event["payload"]["before"]["locale"] == "pt-BR"
    assert latest_event["payload"]["before"]["articles_per_week"] == 3
    assert latest_event["payload"]["after"]["locale"] == "en-US"
    assert latest_event["payload"]["after"]["articles_per_week"] == 5

    # Managed connection paths (conn=None)
    active = services.get_active_campaign(conn=None, workspace_id=ws_id, user_id=user_id)
    assert active is not None
    assert active["id"] == saved["id"]

    updated_managed = services.save_campaign(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        name="US Expansion",
        locale="en-US",
        market="US",
        articles_per_week=6,
        schedule_cron="0 10 * * 2",
        monthly_cap_usd=Decimal("300.00"),
        run_cap_usd=Decimal("40.00"),
        config=custom_cfg.model_dump(),
    )
    assert updated_managed["articles_per_week"] == 6


def test_save_campaign_not_found_and_defensive_error(
    db_conn: psycopg.Connection,
) -> None:
    from decimal import Decimal

    slug = f"camp-err-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Campaign Error WS",
        industry_pack="general",
    )
    email = f"camp-err-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # Nonexistent campaign_id raises ValueError
    with pytest.raises(ValueError, match="not found"):
        services.save_campaign(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            campaign_id=uuid.uuid4(),
            locale="pt-BR",
            market="BR",
            articles_per_week=1,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("100.00"),
            run_cap_usd=Decimal("10.00"),
        )

    # Defensive RuntimeError when get_campaign returns None after save
    with (
        patch("engine.db.get_campaign", return_value=None),
        pytest.raises(RuntimeError, match="Failed to load saved campaign"),
    ):
        services.save_campaign(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            locale="pt-BR",
            market="BR",
            articles_per_week=1,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("100.00"),
            run_cap_usd=Decimal("10.00"),
        )


@pytest.mark.parametrize(
    ("overrides", "error_match"),
    [
        ({"name": "   "}, "Campaign name"),
        ({"locale": "pt_BR"}, "Invalid locale"),
        ({"locale": "pt"}, "Invalid locale"),
        ({"market": "br"}, "Invalid market"),
        ({"market": "BRA"}, "Invalid market"),
        ({"articles_per_week": 0}, "articles_per_week"),
        ({"articles_per_week": 21}, "articles_per_week"),
        ({"articles_per_week": True}, "articles_per_week"),
        ({"schedule_cron": ""}, "cron"),
        ({"schedule_cron": "0 9 * *"}, "cron"),
        ({"schedule_cron": "60 9 * * 1"}, "cron"),
        ({"schedule_cron": "0 24 * * 1"}, "cron"),
        ({"schedule_cron": "0 9 0 * 1"}, "cron"),
        ({"schedule_cron": "0 9 32 * 1"}, "cron"),
        ({"schedule_cron": "0 9 * 13 1"}, "cron"),
        ({"schedule_cron": "0 9 * * 8"}, "cron"),
        ({"schedule_cron": "*/0 9 * * 1"}, "cron"),
        ({"schedule_cron": "5-2 9 * * 1"}, "cron"),
        ({"schedule_cron": "1-10/0 9 * * 1"}, "cron"),
        ({"schedule_cron": "abc 9 * * 1"}, "cron"),
        ({"schedule_cron": "1-b 9 * * 1"}, "cron"),
        ({"monthly_cap_usd": None}, "spend cap"),
        ({"run_cap_usd": None}, "spend cap"),
        ({"monthly_cap_usd": ""}, "spend cap"),
        ({"monthly_cap_usd": "not-a-number"}, "spend cap"),
        ({"monthly_cap_usd": "0"}, "greater than 0"),
        ({"monthly_cap_usd": "-10.00"}, "greater than 0"),
        ({"run_cap_usd": "0"}, "greater than 0"),
        ({"run_cap_usd": "-5.00"}, "greater than 0"),
        ({"monthly_cap_usd": "50.00", "run_cap_usd": "50.01"}, "run_cap_usd"),
        (
            {
                "config": {
                    "scoring_weights": {
                        "novelty": 10,
                        "audience_fit": 10,
                        "brand_fit": 10,
                        "competitor_gap": 10,
                        "trend": 10,
                        "urgency": 10,
                    }
                }
            },
            "Weights must sum to 100",
        ),
    ],
)
def test_save_campaign_validation_matrix(
    db_conn: psycopg.Connection,
    overrides: dict[str, object],
    error_match: str,
) -> None:
    from decimal import Decimal

    slug = f"val-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Validation Matrix WS",
        industry_pack="general",
    )
    email = f"val-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    params: dict[str, object] = {
        "workspace_id": ws_id,
        "user_id": user_id,
        "name": "Matrix Campaign",
        "locale": "pt-BR",
        "market": "BR",
        "articles_per_week": 2,
        "schedule_cron": "0 9 * * 1",
        "monthly_cap_usd": Decimal("100.00"),
        "run_cap_usd": Decimal("20.00"),
    }
    params.update(overrides)

    with pytest.raises(ValueError, match=error_match):
        services.save_campaign(db_conn, **params)  # type: ignore[arg-type]


def test_save_campaign_unsupported_locale_names_active_plugin(
    db_conn: psycopg.Connection,
) -> None:
    from decimal import Decimal

    from engine.integrations.config import set_workspace_integration
    from engine.integrations.registry import UnsupportedLocale

    slug = f"plug-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Plugin Locale WS",
        industry_pack="general",
    )
    email = f"plug-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # 1. Default plugin check rejects unsupported locale 'fr-FR' naming 'openrouter'
    with pytest.raises(UnsupportedLocale, match="openrouter") as exc_info:
        services.save_campaign(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            locale="fr-FR",
            market="FR",
            articles_per_week=1,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("100.00"),
            run_cap_usd=Decimal("10.00"),
        )
    assert exc_info.value.plugin == "openrouter"
    assert exc_info.value.locale == "fr-FR"

    # 2. Active workspace plugin restricting supported_locales to pt-BR rejects en-US naming 'fake'
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="news_search",
        plugin="fake",
        settings={"supported_locales": ["pt-BR"]},
        updated_by=user_id,
    )
    with pytest.raises(UnsupportedLocale, match="fake") as exc_info2:
        services.save_campaign(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            locale="en-US",
            market="US",
            articles_per_week=1,
            schedule_cron="0 9 * * 1",
            monthly_cap_usd=Decimal("100.00"),
            run_cap_usd=Decimal("10.00"),
        )
    assert exc_info2.value.plugin == "fake"


def test_save_campaign_snapshot_semantics_for_runs(
    db_conn: psycopg.Connection,
) -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    slug = f"snap-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Snapshot WS",
        industry_pack="general",
    )
    email = f"snap-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("200.00"),
        run_cap_usd=Decimal("20.00"),
    )

    now = datetime.now(UTC)
    run1_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp["id"],
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=camp["run_cap_usd"],
        code_version="v1",
        pack_version="v1",
        created_by=user_id,
    )

    # Update campaign while run1 is in progress (AC5)
    updated_camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        campaign_id=camp["id"],
        locale="pt-BR",
        market="BR",
        articles_per_week=4,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("300.00"),
        run_cap_usd=Decimal("45.00"),
    )

    # Run 1 in progress retains snapshotted run_cap_usd (20.00)
    run1 = db.get_run(db_conn, workspace_id=ws_id, run_id=run1_id)
    assert run1 is not None
    assert run1["run_cap_usd"] == Decimal("20.00")

    # Complete run1 and start run2 -> run2 uses updated cap (45.00)
    db.update_run_status(
        db_conn,
        workspace_id=ws_id,
        run_id=run1_id,
        expected_status="queued",
        new_status="completed",
    )
    run2_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp["id"],
        trigger="manual",
        window_start=now,
        window_end=now + timedelta(days=7),
        run_cap_usd=updated_camp["run_cap_usd"],
        code_version="v1",
        pack_version="v1",
        created_by=user_id,
    )
    run2 = db.get_run(db_conn, workspace_id=ws_id, run_id=run2_id)
    assert run2 is not None
    assert run2["run_cap_usd"] == Decimal("45.00")


def test_estimate_monthly_cost_literals_and_properties() -> None:
    from decimal import Decimal

    from hypothesis import given
    from hypothesis import strategies as st

    assert services.estimate_monthly_cost(1) == Decimal("16.00")
    assert services.estimate_monthly_cost(2) == Decimal("22.00")
    assert services.estimate_monthly_cost(5) == Decimal("40.00")
    assert services.estimate_monthly_cost(10) == Decimal("70.00")
    assert services.estimate_monthly_cost(20) == Decimal("130.00")

    with pytest.raises(ValueError, match="articles_per_week"):
        services.estimate_monthly_cost(0)
    with pytest.raises(ValueError, match="articles_per_week"):
        services.estimate_monthly_cost(21)
    with pytest.raises(ValueError, match="articles_per_week"):
        services.estimate_monthly_cost(True)

    @given(st.integers(min_value=1, max_value=19))
    def check_linear_step(v: int) -> None:
        diff = services.estimate_monthly_cost(v + 1) - services.estimate_monthly_cost(v)
        assert diff == Decimal("6.00")

    check_linear_step()


def test_save_brand_and_current_brand_versioning_and_partial_unique_index(
    db_conn: psycopg.Connection,
) -> None:
    from pydantic import ValidationError

    from engine.contracts import BrandProfile

    slug = f"brand-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Brand Service WS",
        industry_pack="general",
    )
    email = f"brand-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    # Initially no current brand
    assert services.current_brand(db_conn, workspace_id=ws_id, user_id=user_id) is None

    # 1. Save brand v1 (AC1)
    v1 = services.save_brand(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        profile={
            "voice": "Consultivo e direto",
            "positioning": "ERP para óticas",
            "capabilities": ["Controle de estoque", "Ordens de serviço"],
            "prohibited_claims": ["Lucro garantido"],
            "prohibited_phrases": [r"\bmelhor do mundo\b"],
            "cta_rules": ["TOFU: educativo"],
        },
    )
    assert v1["version"] == 1
    assert v1["is_current"] is True
    assert v1["payload"]["voice"] == "Consultivo e direto"

    curr1 = services.current_brand(db_conn, workspace_id=ws_id, user_id=user_id)
    assert curr1 is not None
    assert curr1["id"] == v1["id"]
    assert curr1["version"] == 1

    # 2. Save brand v2 -> v1 demoted in same transaction, v1 stays readable (AC1)
    v2_profile = BrandProfile(
        voice="Consultivo, técnico e acessível",
        positioning="Sistema ERP líder para redes de óticas",
        capabilities=["Gestão de receituário", "Controle fiscal"],
        prohibited_claims=["Retorno financeiro garantido"],
        prohibited_phrases=[r"\bmelhor do mundo\b", r"\b100% sem falhas\b"],
        cta_rules=["TOFU: educativo", "BOFU: demonstração"],
    )
    v2 = services.save_brand(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        profile=v2_profile,
    )
    assert v2["version"] == 2
    assert v2["is_current"] is True
    assert v2["id"] != v1["id"]

    # Previous version remains readable and is_current = False
    v1_reloaded = db.get_brand_profile(db_conn, workspace_id=ws_id, brand_profile_id=v1["id"])
    assert v1_reloaded is not None
    assert v1_reloaded["version"] == 1
    assert v1_reloaded["is_current"] is False
    assert v1_reloaded["payload"]["voice"] == "Consultivo e direto"

    all_versions = db.list_brand_profiles(db_conn, workspace_id=ws_id)
    assert [row["version"] for row in all_versions] == [2, 1]
    assert [row["is_current"] for row in all_versions] == [True, False]

    # Audit events emitted with before/after snapshots
    brand_events = [
        e
        for e in db.list_events(db_conn, workspace_id=ws_id)
        if e["type"] == "settings.changed" and e["entity_type"] == "brand_profile"
    ]
    assert len(brand_events) == 2
    assert brand_events[0]["payload"]["before"]["version"] == 1
    assert brand_events[0]["payload"]["after"]["version"] == 2
    assert brand_events[1]["payload"]["before"] is None
    assert brand_events[1]["payload"]["after"]["version"] == 1

    # Managed connection paths (conn=None)
    curr_managed = services.current_brand(conn=None, workspace_id=ws_id, user_id=user_id)
    assert curr_managed is not None
    assert curr_managed["version"] == 2

    v3 = services.save_brand(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        profile=v2_profile,
    )
    assert v3["version"] == 3
    assert v3["is_current"] is True

    # 3. AC3: Invalid regex rejected naming the field, current version remains v3
    with pytest.raises(ValidationError) as exc_info:
        services.save_brand(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            profile={
                "voice": "Valid voice",
                "positioning": "Valid positioning",
                "prohibited_phrases": ["[unclosed"],
            },
        )
    assert "prohibited_phrases" in str(exc_info.value)
    curr_after_fail = services.current_brand(db_conn, workspace_id=ws_id, user_id=user_id)
    assert curr_after_fail is not None
    assert curr_after_fail["version"] == 3


def test_brand_profile_partial_unique_index_and_defensive_error(
    db_conn: psycopg.Connection,
) -> None:
    from engine.contracts import BrandProfile

    slug = f"brand-idx-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Brand Index WS",
        industry_pack="general",
    )
    email = f"brand-idx-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    profile = BrandProfile(voice="Voice 1", positioning="Pos 1")
    services.save_brand(db_conn, workspace_id=ws_id, user_id=user_id, profile=profile)
    services.save_brand(db_conn, workspace_id=ws_id, user_id=user_id, profile=profile)

    # Partial unique index brand_one_current prevents a second current version
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        db_conn.transaction(),
        db_conn.cursor() as cur,
    ):
        cur.execute(
            """
            UPDATE brand_profiles
            SET is_current = true
            WHERE workspace_id = %s AND version = 1
            """,
            (ws_id,),
        )

    # Defensive RuntimeError when save_brand_profile fetchone returns None
    mock_cur = MagicMock()
    mock_cur.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    with pytest.raises(RuntimeError, match="Failed to insert brand profile"):
        db.save_brand_profile(
            mock_conn,
            workspace_id=ws_id,
            payload=profile.model_dump(),
            schema_version=1,
            created_by=user_id,
        )


def test_seed_brand_service_and_cli_example_fixture(
    db_conn: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from engine import cli

    slug = f"acme-seed-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Acme Workspace",
        industry_pack="general",
    )

    # Workspace without an owner fails seed_brand
    with pytest.raises(ValueError, match="no owner"):
        services.seed_brand(db_conn, workspace_slug=slug)

    email = f"acme-owner-{uuid.uuid4().hex[:8]}@example.com"
    services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)

    # 1. CLI seed-brand creates version 1 from fixtures/seed_example.yaml for new workspace (AC4)
    ret = cli.main(["seed-brand", slug])
    assert ret == 0
    out = capsys.readouterr().out
    assert f"Seeded brand profile v1 for workspace '{slug}'" in out

    current = db.get_current_brand_profile(db_conn, workspace_id=ws_id)
    assert current is not None
    assert current["version"] == 1
    assert current["is_current"] is True
    assert "Acme Content OS" in current["payload"]["positioning"]
    assert len(current["payload"]["capabilities"]) >= 3
    assert len(current["payload"]["prohibited_phrases"]) >= 1
    assert len(current["payload"]["cta_rules"]) >= 3

    # 2. Direct service call with explicit fixture path creates v2
    seeded_v2 = services.seed_brand(
        db_conn,
        workspace_slug=slug,
        fixture_path="fixtures/seed_example.yaml",
    )
    assert seeded_v2["version"] == 2

    # 3. Error cases: nonexistent workspace, missing fixture file, non-dict YAML
    ret_err = cli.main(["seed-brand", "nonexistent-workspace-slug"])
    assert ret_err == 1
    assert "Error:" in capsys.readouterr().err

    with pytest.raises(ValueError, match="Fixture file not found"):
        services.seed_brand(
            db_conn,
            workspace_slug=slug,
            fixture_path=tmp_path / "missing.yaml",
        )

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        services.seed_brand(
            db_conn,
            workspace_slug=slug,
            fixture_path=bad_yaml,
        )


def test_save_source_and_list_sources_lifecycle_and_unique_domain(
    db_conn: psycopg.Connection,
) -> None:
    from engine.safe_http import SafeResponse

    slug = f"src-svc-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Sources Service WS",
        industry_pack="general",
    )
    email = f"src-owner-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    rss_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0"><channel><title>Optica Feed</title></channel></rss>'
    )
    fake_resp = SafeResponse(status_code=200, headers={}, content=rss_xml)

    feed_domain = f"feed-{uuid.uuid4().hex[:8]}.com.br"
    with patch("engine.safe_http.safe_request", return_value=fake_resp) as mock_http:
        feed_src = services.save_source(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            name="Portal Óptico",
            domain=feed_domain.upper(),
            url=f"https://{feed_domain}",
            fetch_mode="feed",
            feed_url=f"https://{feed_domain}/rss.xml",
            tier=1,
            role="news",
            locales=["pt-BR"],
            topics=["retail"],
        )
        mock_http.assert_called_once_with("GET", f"https://{feed_domain}/rss.xml")

    assert feed_src["domain"] == feed_domain
    assert feed_src["fetch_mode"] == "feed"
    assert feed_src["last_fetch_ok"] is True
    assert feed_src["last_fetched_at"] is not None
    assert feed_src["enabled"] is True

    # AC7: Duplicate domain in the same workspace is rejected
    with (
        patch("engine.safe_http.safe_request", return_value=fake_resp),
        pytest.raises(ValueError, match="Duplicate source domain"),
    ):
        services.save_source(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            name="Portal Óptico Dupe",
            domain=feed_domain,
            url=f"https://{feed_domain}",
            fetch_mode="feed",
            feed_url=f"https://{feed_domain}/rss.xml",
            tier=1,
            role="news",
            locales=["pt-BR"],
        )

    # Update source and disable it (AC4)
    updated_src = services.save_source(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        source_id=feed_src["id"],
        name="Portal Óptico Atualizado",
        domain=feed_domain,
        url=f"https://{feed_domain}",
        fetch_mode="site_search",
        feed_url=None,
        tier=2,
        role="company",
        locales=["pt-BR", "en-US"],
        enabled=False,
    )
    assert updated_src["id"] == feed_src["id"]
    assert updated_src["name"] == "Portal Óptico Atualizado"
    assert updated_src["fetch_mode"] == "site_search"
    assert updated_src["tier"] == 2
    assert updated_src["role"] == "company"
    assert updated_src["enabled"] is False

    # Re-enable via set_source_enabled
    reenabled = services.set_source_enabled(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        source_id=feed_src["id"],
        enabled=True,
    )
    assert reenabled["enabled"] is True

    sources = services.list_sources(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        include_shared=False,
    )
    assert len(sources) == 1
    assert sources[0]["id"] == feed_src["id"]


@pytest.mark.parametrize(
    ("overrides", "http_side_effect", "error_match"),
    [
        ({"name": "   "}, None, "Source name"),
        ({"fetch_mode": "site_search", "domain": ""}, None, "domain"),
        ({"domain": "not_a_valid_domain"}, None, "domain"),
        ({"url": "ftp://bad-scheme.com"}, None, "Source URL"),
        ({"fetch_mode": "invalid_mode"}, None, "fetch_mode"),
        ({"fetch_mode": "feed", "feed_url": None}, None, "feed_url"),
        ({"tier": None}, None, "tier"),
        ({"tier": 0}, None, "tier"),
        ({"tier": 4}, None, "tier"),
        ({"tier": True}, None, "tier"),
        ({"role": ""}, None, "role"),
        ({"role": "invalid_role"}, None, "role"),
        ({"locales": []}, None, "locales"),
        ({"locales": ["bad_locale"]}, None, "locale"),
        ({"fetch_mode": "feed"}, "http_404", "HTTP 404"),
        ({"fetch_mode": "feed"}, "bad_xml", "Invalid feed XML"),
        ({"fetch_mode": "feed"}, "html_root", "Unrecognized feed root"),
        ({"fetch_mode": "feed"}, "ssrf_err", "Feed fetch failed"),
    ],
)
def test_save_source_validation_matrix(
    db_conn: psycopg.Connection,
    overrides: dict[str, object],
    http_side_effect: str | None,
    error_match: str,
) -> None:
    from engine.safe_http import BlockedIPError, SafeResponse

    slug = f"src-val-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Source Matrix WS",
        industry_pack="general",
    )
    email = f"src-val-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    domain = f"valid-{uuid.uuid4().hex[:8]}.com.br"
    params: dict[str, object] = {
        "workspace_id": ws_id,
        "user_id": user_id,
        "name": "Valid Source",
        "domain": domain,
        "url": f"https://{domain}",
        "fetch_mode": "site_search",
        "feed_url": f"https://{domain}/feed.xml",
        "tier": 1,
        "role": "news",
        "locales": ["pt-BR"],
    }
    params.update(overrides)

    mock_target = "engine.safe_http.safe_request"
    if http_side_effect == "http_404":
        ctx = patch(mock_target, return_value=SafeResponse(404, {}, b"Not found"))
    elif http_side_effect == "bad_xml":
        ctx = patch(mock_target, return_value=SafeResponse(200, {}, b"<rss><unclosed"))
    elif http_side_effect == "html_root":
        ctx = patch(
            mock_target, return_value=SafeResponse(200, {}, b"<html><body>Hi</body></html>")
        )
    elif http_side_effect == "ssrf_err":
        ctx = patch(mock_target, side_effect=BlockedIPError("127.0.0.1 blocked"))
    else:
        ctx = patch(
            mock_target,
            return_value=SafeResponse(
                200, {}, b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            ),
        )

    with ctx, pytest.raises(ValueError, match=error_match):
        services.save_source(db_conn, **params)  # type: ignore[arg-type]


def test_seed_sources_cli_and_pack_literal_counts(
    db_conn: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from collections import Counter

    from engine import cli

    ret = cli.main(["seed-sources"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Seeded 16 shared source(s) for pack 'general'" in out

    # Re-running is idempotent
    seeded = services.seed_sources(db_conn, industry_pack="general")
    assert len(seeded) == 16

    shared_sources = [
        s
        for s in db.list_sources(
            db_conn,
            workspace_id=None,
            industry_pack="general",
            include_shared=True,
        )
        if s["workspace_id"] is None and len(s["domain"]) < 35
    ]
    seeded_domains = {s["domain"] for s in seeded}
    pack_rows = [s for s in shared_sources if s["domain"] in seeded_domains]
    assert len(pack_rows) == 16

    role_counts = Counter(str(s["role"]) for s in pack_rows)
    assert dict(role_counts) == {
        "news": 5,
        "company": 3,
        "data": 6,
        "management": 2,
    }

    tier_counts = Counter(int(s["tier"]) for s in pack_rows)
    assert dict(tier_counts) == {
        1: 7,
        2: 6,
        3: 3,
    }

    locale_counts = Counter(str(s["locales"][0]) for s in pack_rows)
    assert dict(locale_counts) == {
        "pt-BR": 7,
        "en-US": 9,
    }

    # Invalid pack fails CLI with exit code 1
    ret_err = cli.main(["seed-sources", "--pack", "nonexistent-pack-xyz"])
    assert ret_err == 1
    assert "Error:" in capsys.readouterr().err


def test_source_services_managed_connections_and_defensive_branches(
    db_conn: psycopg.Connection,
) -> None:
    slug = f"src-def-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Source Defensive WS",
        industry_pack="general",
    )
    email = f"src-def-{uuid.uuid4().hex[:8]}@example.com"
    res = services.add_workspace_owner(db_conn, workspace_slug=slug, email=email)
    user_id = res["user"]["user_id"]

    dom = f"managed-src-{uuid.uuid4().hex[:8]}.com.br"
    created = services.save_source(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        name="Managed Source",
        domain=dom,
        url=f"https://{dom}",
        fetch_mode="site_search",
        tier=2,
        role="data",
        locales=["pt-BR"],
        enabled=False,
    )
    assert created["domain"] == dom

    # Disabled feed source skips HTTP fetch
    disabled_feed_dom = f"dis-feed-{uuid.uuid4().hex[:8]}.com.br"
    dis_feed = services.save_source(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        name="Disabled Feed",
        domain=disabled_feed_dom,
        url=f"https://{disabled_feed_dom}",
        fetch_mode="feed",
        feed_url=f"https://{disabled_feed_dom}/rss.xml",
        tier=3,
        role="news",
        locales=["pt-BR"],
        enabled=False,
    )
    assert dis_feed["last_fetch_ok"] is None

    toggled = services.set_source_enabled(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        source_id=created["id"],
        enabled=True,
    )
    assert toggled["enabled"] is True

    listed = services.list_sources(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        include_shared=False,
    )
    assert any(s["id"] == created["id"] for s in listed)

    # Not found errors on save_source and set_source_enabled
    with pytest.raises(ValueError, match="not found"):
        services.save_source(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            source_id=uuid.uuid4(),
            name="Missing",
            domain=dom,
            url=f"https://{dom}",
            fetch_mode="site_search",
            tier=1,
            role="news",
            locales=["pt-BR"],
        )

    with pytest.raises(ValueError, match="not found"):
        services.set_source_enabled(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            source_id=uuid.uuid4(),
            enabled=False,
        )

    # Defensive RuntimeError branches
    with (
        patch("engine.db.update_source", return_value=None),
        pytest.raises(RuntimeError, match="Failed to update source"),
    ):
        services.save_source(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            source_id=created["id"],
            name="Managed Source",
            domain=dom,
            url=f"https://{dom}",
            fetch_mode="site_search",
            tier=2,
            role="data",
            locales=["pt-BR"],
        )

    with (
        patch("engine.db.update_source", return_value=None),
        pytest.raises(RuntimeError, match="Failed to update source"),
    ):
        services.set_source_enabled(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            source_id=created["id"],
            enabled=False,
        )

    mock_cur = MagicMock()
    mock_cur.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    with pytest.raises(RuntimeError, match="Failed to insert source"):
        db.create_source(
            mock_conn,
            workspace_id=ws_id,
            industry_pack="general",
            name="X",
            domain="x.com",
            url="https://x.com",
            feed_url=None,
            fetch_mode="site_search",
            tier=1,
            role="news",
            locales=["pt-BR"],
            topics=[],
        )
    with pytest.raises(RuntimeError, match="Failed to upsert shared source"):
        db.upsert_pack_source(
            mock_conn,
            industry_pack="general",
            name="X",
            domain="x.com",
            url="https://x.com",
            feed_url=None,
            fetch_mode="site_search",
            tier=1,
            role="news",
            locales=["pt-BR"],
            topics=[],
        )


def test_save_and_list_integrations_secret_hygiene_and_preserve(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)

    slug = f"int-ws-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Integrations WS",
        industry_pack="general",
    )
    res = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"int-owner-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = res["user"]["user_id"]

    planted_secret = "sk-planted-top-secret-987654"
    saved = services.save_integration(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        port="llm",
        plugin="fake",
        settings={
            "model_name": "fake-haiku",
            "models_by_stage": {"brief": "fake-opus-1"},
        },
        secrets={"api_key": planted_secret},
    )
    assert saved["port"] == "llm"
    assert saved["plugin"] == "fake"
    assert saved["settings"]["model_name"] == "fake-haiku"
    assert saved["masked_secrets"] == {"api_key": "********"}
    assert planted_secret not in str(saved)
    assert planted_secret not in caplog.text

    # Update settings with empty secret -> preserves existing encrypted secret
    updated = services.save_integration(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        port="llm",
        plugin="fake",
        settings={
            "model_name": "fake-sonnet",
            "models_by_stage": {"brief": "fake-opus-2"},
        },
        secrets={"api_key": ""},
    )
    assert updated["settings"]["model_name"] == "fake-sonnet"
    assert updated["masked_secrets"] == {"api_key": "********"}

    listed = services.list_integrations(db_conn, workspace_id=ws_id, user_id=user_id)
    assert len(listed) == 1
    assert listed[0]["masked_secrets"] == {"api_key": "********"}
    assert planted_secret not in str(listed)

    events = db.list_events(db_conn, workspace_id=ws_id)
    int_events = [e for e in events if e["entity_type"] == "integration"]
    assert len(int_events) == 2
    for ev in int_events:
        assert planted_secret not in str(ev)


def test_test_integration_healthcheck_and_stage_model_in_usage_ledger(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from cryptography.fernet import Fernet

    from engine.contracts import Opportunity
    from engine.llm import structured_call

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)

    slug = f"int-test-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Integration Test WS",
        industry_pack="general",
    )
    res = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"int-test-owner-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = res["user"]["user_id"]

    sub = {"score": 80.0, "evidence": "Ev"}
    opp_dict = {
        "title": "Test Topic",
        "angle": "Angle",
        "funnel_stage": "TOFU",
        "confidence": 0.85,
        "novelty": sub,
        "audience_fit": sub,
        "brand_fit": sub,
        "competitor_gap": sub,
        "trend": sub,
        "urgency": sub,
        "weighted_total": 80.0,
    }
    services.save_integration(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        port="llm",
        plugin="fake",
        settings={
            "model_name": "fake-default-v1",
            "models_by_stage": {"brief": "fake-stage-brief-v1"},
            "default_response": opp_dict,
        },
        secrets={"api_key": "sk-fake-key"},
    )

    # AC3: Test connection runs healthcheck() and stores last_test_ok / last_tested_at
    test_res = services.test_integration(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        port="llm",
    )
    assert test_res["ok"] is True
    assert test_res["last_test_ok"] is True
    assert test_res["last_tested_at"] is not None

    # AC5: Changing LLM model for a stage updates usage_ledger.model on next stage call
    camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        name="Model Stage Camp",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("10.00"),
    )
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp["id"],
        trigger="manual",
        window_start=datetime.now(UTC),
        window_end=datetime.now(UTC) + timedelta(days=7),
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_id,
    )
    structured_call(
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="brief",
        model_cls=Opportunity,
        prompt="Brief prompt",
        input={"topic": "T1"},
    )
    # Change brief stage model
    services.save_integration(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        port="llm",
        plugin="fake",
        settings={
            "model_name": "fake-default-v1",
            "models_by_stage": {"brief": "fake-stage-brief-v2-upgraded"},
            "default_response": opp_dict,
        },
    )
    structured_call(
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="brief",
        model_cls=Opportunity,
        prompt="Brief prompt",
        input={"topic": "T2"},
    )
    usage_rows = db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
    models_logged = [r["model"] for r in usage_rows]
    assert "fake-stage-brief-v1" in models_logged
    assert "fake-stage-brief-v2-upgraded" in models_logged


def test_integration_ssrf_guard_and_failure_branches(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)

    slug = f"int-ssrf-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="SSRF Guard WS",
        industry_pack="general",
    )
    res = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"int-ssrf-owner-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = res["user"]["user_id"]

    # AC7: Private/loopback/metadata IP URL refused on save_integration
    for unsafe_url in (
        "http://127.0.0.1:8080/webhook",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.10/api",
    ):
        with pytest.raises(ValueError, match="Unsafe URL"):
            services.save_integration(
                db_conn,
                workspace_id=ws_id,
                user_id=user_id,
                port="publisher",
                plugin="fake",
                settings={"platform_name": "webhook", "endpoint_url": unsafe_url},
            )

    # Save valid public URL (mocking DNS resolution)
    with patch("engine.safe_http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        services.save_integration(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            port="publisher",
            plugin="fake",
            settings={
                "platform_name": "webhook",
                "endpoint_url": "https://hooks.example.com/publish",
            },
        )

    # If stored URL resolves to private IP at test time, test_integration refuses it
    from engine.safe_http import BlockedIPError

    with (
        patch(
            "engine.safe_http.resolve_and_validate_host",
            side_effect=BlockedIPError("Blocked private IP"),
        ),
        pytest.raises(ValueError, match="Unsafe URL"),
    ):
        services.test_integration(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            port="publisher",
        )
    row = db.get_workspace_integration(db_conn, workspace_id=ws_id, port="publisher")
    assert row is not None
    assert row["last_test_ok"] is False

    # Healthcheck failure sets last_test_ok=False and re-raises ValueError
    with (
        patch("engine.safe_http.resolve_and_validate_host", return_value=["93.184.216.34"]),
        patch(
            "engine.integrations.fakes.FakePublisher.healthcheck",
            side_effect=RuntimeError("Connection refused by remote"),
        ),
        pytest.raises(ValueError, match="Connection refused by remote"),
    ):
        services.test_integration(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            port="publisher",
        )
    row_after = db.get_workspace_integration(db_conn, workspace_id=ws_id, port="publisher")
    assert row_after is not None
    assert row_after["last_test_ok"] is False


def test_integration_defensive_and_edge_branches(
    db_conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from cryptography.fernet import Fernet

    from engine.contracts import Opportunity
    from engine.llm import structured_call

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)

    slug = f"int-def-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="Defensive Integrations WS",
        industry_pack="general",
    )
    res = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"int-def-owner-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = res["user"]["user_id"]

    # 1. Invalid port raises ValueError
    with pytest.raises(ValueError, match="Invalid integration port"):
        services.save_integration(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            port="invalid_port",
            plugin="fake",
        )

    # 2. Save integration without secrets -> _resolve_integration_masked_secrets returns {}
    services.save_integration(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        port="feed_source",
        plugin="fake",
        settings={"default_items_count": 3},
        secrets=None,
    )
    listed_no_sec = services.list_integrations(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert len(listed_no_sec) == 1
    assert listed_no_sec[0]["masked_secrets"] == {}

    # 3. Managed connection test_integration + decrypt failure in masked secrets
    t_res = services.test_integration(
        conn=None,
        workspace_id=ws_id,
        user_id=user_id,
        port="feed_source",
    )
    assert t_res["ok"] is True

    services.save_integration(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        port="extractor",
        plugin="fake",
        secrets={"api_key": "secret-1"},
    )
    other_key = Fernet.generate_key().decode()
    listed_bad_key = services.list_integrations(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        encryption_key=other_key,
    )
    extr_row = next(r for r in listed_bad_key if r["port"] == "extractor")
    assert extr_row["masked_secrets"] == {}

    # 4. _unwrap_schema_prop with all-null anyOf branch
    unwrapped_null = services._unwrap_schema_prop({"anyOf": [{"type": "null"}]})
    assert unwrapped_null == {"anyOf": [{"type": "null"}]}

    # 5. Defensive RuntimeErrors on db integration helpers
    mock_cur = MagicMock()
    mock_cur.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    with pytest.raises(RuntimeError, match="Failed to upsert workspace integration"):
        db.upsert_workspace_integration(
            mock_conn,
            workspace_id=ws_id,
            port="llm",
            plugin="fake",
            settings={},
            secrets=None,
            updated_by=user_id,
        )

    with (
        patch("engine.db.update_workspace_integration_test_status", return_value=None),
        pytest.raises(RuntimeError, match="Failed to update test status"),
    ):
        services.test_integration(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            port="feed_source",
        )

    # 6. structured_call when llm port is not configured in workspace (IntegrationMissing fallback)
    # and explicit model argument on non-OpenRouter adapter
    camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        name="Def Camp",
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("50.00"),
        run_cap_usd=Decimal("10.00"),
    )
    run_id = db.create_run(
        db_conn,
        workspace_id=ws_id,
        campaign_id=camp["id"],
        trigger="manual",
        window_start=datetime.now(UTC),
        window_end=datetime.now(UTC) + timedelta(days=7),
        run_cap_usd=Decimal("10.00"),
        code_version="v1",
        pack_version="p1",
        created_by=user_id,
    )
    sub = {"score": 80.0, "evidence": "Ev"}
    opp_dict = {
        "title": "Explicit Model Topic",
        "angle": "Angle",
        "funnel_stage": "TOFU",
        "confidence": 0.85,
        "novelty": sub,
        "audience_fit": sub,
        "brand_fit": sub,
        "competitor_gap": sub,
        "trend": sub,
        "urgency": sub,
        "weighted_total": 80.0,
    }
    from engine.integrations.fakes import FakeLLM

    resolved_default = (
        services.llm_resolve_test_helper if hasattr(services, "llm_resolve_test_helper") else None
    )
    del resolved_default
    from engine.llm import _resolve_llm_adapter

    fallback_adapter = _resolve_llm_adapter(db_conn, ws_id, None)
    assert fallback_adapter.__class__.__name__ == "OpenRouterLLM"

    fake_adapter = FakeLLM(settings=FakeLLM.Settings(default_response=opp_dict))
    structured_call(
        conn=db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="score",
        model_cls=Opportunity,
        prompt="Score",
        input={"k": "v"},
        plugin=fake_adapter,
        model="explicit-override-model",
    )
    usage = db.list_usage(db_conn, workspace_id=ws_id, run_id=run_id)
    assert usage[0]["model"] == "explicit-override-model"


def test_verify_sources_offline_service() -> None:
    """services.verify_sources verifies shared starter pack sources (F04 AC6, M1-12)."""
    results = services.verify_sources(industry_pack="general", days=30, offline=True)
    assert len(results) == 16

    by_domain = {r["domain"]: r for r in results}
    assert "legacy-standards.example.org" in by_domain
    legacy = by_domain["legacy-standards.example.org"]
    assert legacy["enabled"] is False
    assert legacy["status"] == "disabled"
    assert legacy["records_found"] == 0
    assert "archived" in str(legacy["notes"]).lower()

    w3c = by_domain["w3.org"]
    assert w3c["enabled"] is True
    assert w3c["status"] == "ok"
    assert w3c["records_found"] > 0
    assert w3c["dated_pct"] == 100.0

    active_ok = [r for r in results if r["enabled"] and r["status"] == "ok"]
    assert len(active_ok) == 14
    for r in active_ok:
        assert r["records_found"] > 0
        assert r["dated_pct"] == 100.0


def test_verify_sources_custom_adapters_and_error_handling(
    db_conn: psycopg.Connection,
) -> None:
    """services.verify_sources handles custom adapters, empty queries, errors, and invalid packs."""
    from typing import Any

    with pytest.raises(ValueError, match="nonexistent-pack-xyz"):
        services.verify_sources(industry_pack="nonexistent-pack-xyz", offline=True)

    class FailingFeedAdapter:
        def healthcheck(self) -> None:
            pass

        def fetch_feed(self, url: str, since: Any = None) -> list[Any]:
            msg = f"Network timeout fetching {url}"
            raise ConnectionError(msg)

    class EmptySearchAdapter:
        def healthcheck(self) -> None:
            pass

        def search(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

    results = services.verify_sources(
        db_conn,
        industry_pack="general",
        feed_plugin=FailingFeedAdapter(),
        search_plugin=EmptySearchAdapter(),
        days=30,
    )
    assert len(results) == 16
    by_mode: dict[str, list[dict[str, Any]]] = {"feed": [], "site_search": []}
    for r in results:
        by_mode[r["fetch_mode"]].append(r)

    for feed_res in by_mode["feed"]:
        if feed_res["enabled"]:
            assert feed_res["status"] == "error"
            assert "Network timeout" in str(feed_res["error"])
        else:
            assert feed_res["status"] == "disabled"

    active_search = [r for r in by_mode["site_search"] if r["enabled"]]
    for search_res in active_search:
        assert search_res["status"] == "no_records"
        assert search_res["records_found"] == 0
        assert search_res["dated_pct"] == 0.0


def test_m1_exit_gate_14_day_run_and_20_records_sample(
    db_conn: psycopg.Connection,
) -> None:
    """M1 Exit Gate: 14-day research run for pt-BR & en-US, 20 sample records, dedup verified."""
    from datetime import UTC, datetime, timedelta

    from engine import research

    # Seed all 16 shared pack sources once (shared across workspaces)
    seeded = services.seed_sources(db_conn, industry_pack="general")
    assert len(seeded) == 16

    # 1. Setup pt-BR workspace & campaign and execute 14-day run
    slug_pt = f"m1-exit-pt-{uuid.uuid4().hex[:8]}"
    ws_pt_id = db.create_workspace(
        db_conn,
        slug=slug_pt,
        name="M1 Exit Gate Workspace PT",
        industry_pack="general",
    )
    email_pt = f"m1-owner-pt-{uuid.uuid4().hex[:8]}@example.com"
    owner_pt_res = services.add_workspace_owner(db_conn, workspace_slug=slug_pt, email=email_pt)
    user_pt_id = owner_pt_res["user"]["user_id"]

    pt_camp = services.save_campaign(
        db_conn,
        workspace_id=ws_pt_id,
        user_id=user_pt_id,
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 8 * * 1",
        monthly_cap_usd=50,
        run_cap_usd=10,
        name="Campanha Brasil 14D",
    )
    pt_run = services.run_week(
        db_conn,
        workspace_identifier=slug_pt,
        campaign_identifier=str(pt_camp["id"]),
        offline=True,
        window_days=14,
    )
    assert pt_run["status"] == "completed"

    # 2. Setup en-US workspace & campaign and execute 14-day run
    slug_en = f"m1-exit-en-{uuid.uuid4().hex[:8]}"
    ws_en_id = db.create_workspace(
        db_conn,
        slug=slug_en,
        name="M1 Exit Gate Workspace EN",
        industry_pack="general",
    )
    email_en = f"m1-owner-en-{uuid.uuid4().hex[:8]}@example.com"
    owner_en_res = services.add_workspace_owner(db_conn, workspace_slug=slug_en, email=email_en)
    user_en_id = owner_en_res["user"]["user_id"]

    en_camp = services.save_campaign(
        db_conn,
        workspace_id=ws_en_id,
        user_id=user_en_id,
        locale="en-US",
        market="US",
        articles_per_week=2,
        schedule_cron="0 8 * * 1",
        monthly_cap_usd=50,
        run_cap_usd=10,
        name="US Authority Campaign 14D",
    )
    en_run = services.run_week(
        db_conn,
        workspace_identifier=slug_en,
        campaign_identifier=str(en_camp["id"]),
        offline=True,
        window_days=14,
    )
    assert en_run["status"] == "completed"

    pt_records = db.list_research_records(db_conn, workspace_id=ws_pt_id, run_id=pt_run["id"])
    en_records = db.list_research_records(db_conn, workspace_id=ws_en_id, run_id=en_run["id"])
    assert len(pt_records) >= 30
    assert len(en_records) >= 30

    # 3. Near-duplicate trigram detection within 14-day window (F08 AC2)
    now = datetime.now(UTC)
    doc_orig = db.upsert_document(
        db_conn,
        workspace_id=ws_pt_id,
        url="https://opticanet.com.br/noticia/tendencias-mercado-2026",
        canonical_url="https://opticanet.com.br/noticia/tendencias-mercado-2026",
        title="Tendências do Mercado Óptico Brasileiro em 2026",
        published_at=now - timedelta(days=3),
        date_status="verified",
        fetch_status="ok",
    )
    rr_orig = db.upsert_research_record(
        db_conn,
        workspace_id=ws_pt_id,
        run_id=pt_run["id"],
        document_id=doc_orig["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": doc_orig["title"]},
    )
    doc_near = db.upsert_document(
        db_conn,
        workspace_id=ws_pt_id,
        url="https://abioptica.com.br/noticia/tendencias-mercado-optico-2026",
        canonical_url="https://abioptica.com.br/noticia/tendencias-mercado-optico-2026",
        title="Tendências do Mercado Óptico Brasileiro para 2026",
        published_at=now - timedelta(days=2),
        date_status="verified",
        fetch_status="ok",
    )
    rr_near = db.upsert_research_record(
        db_conn,
        workspace_id=ws_pt_id,
        run_id=pt_run["id"],
        document_id=doc_near["id"],
        status="extracted",
        schema_version=1,
        payload={"headline": doc_near["title"]},
    )
    updated = research.dedup(db_conn, pt_run)
    dup_recs = [r for r in updated if r.get("status") == "duplicate"]
    assert len(dup_recs) >= 1
    near_dup = next(r for r in dup_recs if r["id"] == rr_near["id"])
    assert near_dup["duplicate_of"] == rr_orig["id"]

    # 4. Sample and inspect 20 distinct records (10 pt-BR + 10 en-US)
    sampled_20 = pt_records[:10] + en_records[:10]
    assert len(sampled_20) == 20

    for rec in sampled_20:
        payload = rec["payload"]
        assert payload.get("headline")
        assert payload.get("url")
        assert payload.get("source")
        assert payload.get("verification_status") in ("verified", "inferred")
        assert payload.get("published_at") is not None
        assert payload.get("event_date") is not None


def test_ingest_external_research_and_import_external_draft_compliance(
    db_conn: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """Verify M5-07: external research ingestion and .md draft import + deterministic compliance."""
    slug = f"m5-07-ext-{uuid.uuid4().hex[:8]}"
    ws_id = db.create_workspace(
        db_conn,
        slug=slug,
        name="M5-07 Compliance Workspace",
        industry_pack="general",
    )
    owner = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"m507-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = owner["user"]["user_id"]
    services.save_brand(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        profile={
            "voice": "Consultivo",
            "positioning": "ERP para oticas",
            "prohibited_phrases": ["o melhor ERP do Brasil"],
        },
    )
    services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 8 * * 1",
        monthly_cap_usd=50,
        run_cap_usd=10,
        name="Campanha M5-07",
    )

    ingested = services.ingest_external_research(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        topic="Gestao de estoque em oticas",
        documents=[
            {
                "title": "Estudo de Giro de Armacoes 2026",
                "url": "https://example.org/estudo-giro-2026",
                "summary": "Dados setoriais de giro de estoque.",
                "facts": ["Giro medio subiu 18% com curva ABC."],
            }
        ],
    )
    assert len(ingested["document_ids"]) == 1
    run_row = db.get_run(db_conn, workspace_id=ws_id, run_id=ingested["run_id"])
    assert run_row is not None and run_row["status"] == "completed"

    body_words = " ".join(["otica"] * 450)
    md_file = tmp_path / "external_article.md"
    md_file.write_text(
        "---\n"
        "title: Gestao de estoque em oticas\n"
        "slug: gestao-de-estoque-em-oticas\n"
        "meta_description: Guia pratico de gestao de estoque para melhorar o giro "
        "e a margem operacional na sua otica.\n"
        "---\n\n"
        f"# Gestao de estoque em oticas\n\n{body_words}\n",
        encoding="utf-8",
    )

    imported = services.import_external_draft(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        file_path=md_file,
        format_slug="blog-short",
        topic="Gestao de estoque em oticas",
        primary_keyword="gestao de estoque",
        deterministic_only=True,
    )
    assert imported["revision"] == 0
    assert imported["verdict"] == "PASS"
    assert imported["score"] is None
    assert imported["brief_id"] is not None

    # Importing a second revision against the same brief_id increments revision to 1
    rev1 = services.import_external_draft(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        file_path=md_file,
        brief_id=imported["brief_id"],
        format_slug="blog-short",
        deterministic_only=True,
    )
    assert rev1["revision"] == 1
    assert rev1["brief_id"] == imported["brief_id"]
