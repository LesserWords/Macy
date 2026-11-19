"""Service layer for the Content Intelligence Engine (ADR-009, ADR-014).

Sole entry point for UI, CLI and future API. Enforces authorization
and emits audit events.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
import yaml
from pydantic import BaseModel, SecretStr

from engine import budget, capabilities, config, db, events, passwords, safe_http, sitemap, webhooks
from engine.audit import audit_url as _run_url_audit_core
from engine.contracts import (
    BrandProfile,
    CampaignConfig,
    ContentDraft,
    CrawlConfig,
    DraftClaim,
    PublishedContent,
    PublishError,
    PublishRequest,
    ResearchRecord,
)
from engine.crawl import crawl_site as _run_site_crawl_core
from engine.integrations.config import (
    _get_fernet_instance,
    _resolve_keys,
    decrypt_secrets,
    encrypt_secrets,
    mask_secrets,
    plugin_config,
)
from engine.integrations.ports import (
    ExtractorPort,
    FeedSourcePort,
    KeywordProviderPort,
    LLMPort,
    NewsSearchPort,
    PublisherPort,
)
from engine.integrations.registry import (
    DEFAULT_SUPPORTED_LOCALES,
    PORTS,
    IntegrationMissing,
    UnsupportedLocale,
    discover_plugins,
    get_plugin,
)


def _execute_require_member(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str:
    membership = db.get_membership(conn, workspace_id=workspace_id, user_id=user_id)
    if membership is None:
        msg = f"User {user_id} is not a member of workspace {workspace_id}"
        raise PermissionError(msg)
    return str(membership["role"])


def require_member(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str:
    """Ensure user is a member of the workspace, returning their role.

    Raises PermissionError if the user has no membership in the workspace.
    """
    if conn is not None:
        return _execute_require_member(conn, workspace_id=workspace_id, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)


def get_user_by_email(
    conn: psycopg.Connection[Any] | None = None,
    *,
    email: str,
) -> dict[str, Any] | None:
    """Retrieve user record by email address."""
    if conn is not None:
        return db.get_user_by_email(conn, email=email)
    with db.get_connection() as managed_conn:
        return db.get_user_by_email(managed_conn, email=email)


def get_user_by_id(
    conn: psycopg.Connection[Any] | None = None,
    *,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve user record by UUID."""
    if conn is not None:
        return db.get_user_by_id(conn, user_id=user_id)
    with db.get_connection() as managed_conn:
        return db.get_user_by_id(managed_conn, user_id=user_id)


def list_available_industry_packs() -> list[str]:
    """Return available industry pack slugs merged across layered context dirs."""
    return config.list_available_industry_packs()


def get_default_industry_pack() -> str:
    """Return configured default industry pack slug."""
    return config.get_default_industry_pack()


def list_available_brand_fixtures() -> dict[str, Path]:
    """Return discovered brand fixture YAML files merged across layered context dirs."""
    return config.list_available_brand_fixtures()


def check_database_connection() -> str:
    """Verify live database connectivity using existing database role inspection."""
    with db.get_connection() as managed_conn:
        return db.get_current_db_role(managed_conn)


def list_user_workspaces(
    conn: psycopg.Connection[Any] | None = None,
    *,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all workspace memberships for a user."""
    if conn is not None:
        return db.list_user_memberships(conn, user_id=user_id)
    with db.get_connection() as managed_conn:
        return db.list_user_memberships(managed_conn, user_id=user_id)


def _select_primary_campaign(campaigns: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not campaigns:
        return None
    return next((c for c in campaigns if c.get("status") == "active"), campaigns[0])


def _is_non_local_environment() -> bool:
    env_val = os.environ.get("APP_ENV") or os.environ.get("ENV") or "development"
    return env_val.strip().lower() in ("staging", "production", "prod")


def _execute_list_accessible_workspaces(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    memberships = db.list_user_memberships(conn, user_id=user_id)
    result: list[dict[str, Any]] = []
    for member in memberships:
        ws_id = uuid.UUID(str(member["workspace_id"]))
        campaigns = db.list_campaigns(conn, workspace_id=ws_id)
        primary = _select_primary_campaign(campaigns)
        result.append(
            {
                "workspace_id": ws_id,
                "slug": member["slug"],
                "name": member["name"],
                "industry_pack": member["industry_pack"],
                "timezone": member["timezone"],
                "role": member["role"],
                "campaign_count": len(campaigns),
                "campaign_id": primary["id"] if primary is not None else None,
                "campaign_name": primary["name"] if primary is not None else None,
                "locale": primary["locale"] if primary is not None else None,
                "market": primary["market"] if primary is not None else None,
                "monthly_cap_usd": (
                    Decimal(str(primary["monthly_cap_usd"])).quantize(Decimal("0.01"))
                    if primary is not None
                    else None
                ),
                "run_cap_usd": (
                    Decimal(str(primary["run_cap_usd"])).quantize(Decimal("0.01"))
                    if primary is not None
                    else None
                ),
            }
        )
    return result


def list_accessible_workspaces(
    conn: psycopg.Connection[Any] | None = None,
    *,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List workspaces where user_id holds membership, enriched with campaign caps."""
    if conn is not None:
        return _execute_list_accessible_workspaces(conn, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_list_accessible_workspaces(managed_conn, user_id=user_id)


def _execute_get_workspace_owner_identity(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any] | None:
    if _is_non_local_environment():
        msg = "Workspace owner fallback is forbidden in staging or production environments"
        raise PermissionError(msg)
    owner = db.get_workspace_owner(conn, workspace_id=workspace_id)
    if owner is None:
        return None
    owner_user = db.get_user_by_id(conn, user_id=uuid.UUID(str(owner["user_id"])))
    if owner_user is None:  # pragma: no cover - defensive FK invariant
        return None
    return {
        "workspace_id": workspace_id,
        "user_id": owner_user["user_id"],
        "email": owner_user["email"],
        "name": owner_user.get("name"),
        "role": owner["role"],
    }


def get_workspace_owner_identity(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Resolve owner user_id/email for a workspace in non-production environments (ADR-027)."""
    if conn is not None:
        return _execute_get_workspace_owner_identity(conn, workspace_id=workspace_id)
    with db.get_connection() as managed_conn:
        return _execute_get_workspace_owner_identity(managed_conn, workspace_id=workspace_id)


def _summarize_latest_run(run_row: dict[str, Any] | None) -> dict[str, Any] | None:
    if run_row is None:
        return None
    return {
        "id": run_row["id"],
        "campaign_id": run_row["campaign_id"],
        "status": run_row["status"],
        "stage": run_row.get("stage"),
        "cost_usd": run_row.get("cost_usd"),
        "created_at": run_row["created_at"],
        "finished_at": run_row.get("finished_at"),
    }


def _execute_get_workspace_status(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    ws = _execute_get_workspace_for_member(conn, workspace_id=workspace_id, user_id=user_id)
    campaigns = db.list_campaigns(conn, workspace_id=workspace_id)
    primary = _select_primary_campaign(campaigns)
    monthly_spend = db.get_monthly_spend(conn, workspace_id=workspace_id).quantize(
        Decimal("0.000001")
    )
    monthly_cap = (
        Decimal(str(primary["monthly_cap_usd"])).quantize(Decimal("0.01"))
        if primary is not None
        else None
    )
    monthly_remaining = (
        max(Decimal("0.000000"), monthly_cap - monthly_spend) if monthly_cap is not None else None
    )
    sources = db.list_sources(
        conn,
        workspace_id=workspace_id,
        industry_pack=str(ws["industry_pack"]),
        include_shared=True,
    )
    integrations = db.list_workspace_integrations(conn, workspace_id=workspace_id)
    awaiting_topics = db.list_opportunities(conn, workspace_id=workspace_id, status="scored")
    awaiting_drafts = db.list_drafts_for_review(conn, workspace_id=workspace_id, statuses=("pass",))
    runs = db.list_runs(conn, workspace_id=workspace_id)
    return {
        "workspace_id": uuid.UUID(str(ws["workspace_id"])),
        "slug": ws["slug"],
        "name": ws["name"],
        "industry_pack": ws["industry_pack"],
        "timezone": ws["timezone"],
        "locale": primary["locale"] if primary is not None else None,
        "market": primary["market"] if primary is not None else None,
        "monthly_spend_usd": monthly_spend,
        "monthly_cap_usd": monthly_cap,
        "monthly_remaining_usd": monthly_remaining,
        "run_cap_usd": (
            Decimal(str(primary["run_cap_usd"])).quantize(Decimal("0.01"))
            if primary is not None
            else None
        ),
        "active_sources_count": sum(1 for s in sources if s.get("enabled")),
        "total_sources_count": len(sources),
        "configured_integrations_count": len(integrations),
        "configured_ports": [str(row["port"]) for row in integrations],
        "gate1_awaiting_count": len(awaiting_topics),
        "gate2_awaiting_count": len(awaiting_drafts),
        "latest_run": _summarize_latest_run(runs[0] if runs else None),
    }


def get_workspace_status(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Summarize workspace spend, pending approvals, sources, integrations, and latest run."""
    if conn is not None:
        return _execute_get_workspace_status(conn, workspace_id=workspace_id, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_get_workspace_status(
            managed_conn, workspace_id=workspace_id, user_id=user_id
        )


WORKSPACE_SLUG_RE = re.compile(r"^[a-z0-9-]{3,40}$")


def create_workspace(
    conn: psycopg.Connection[Any] | None = None,
    *,
    slug: str,
    name: str,
    industry_pack: str | None = None,
    timezone: str = "America/Sao_Paulo",
) -> dict[str, Any]:
    """Validate inputs and provision a workspace row (F01, M5-01)."""
    resolved_pack = industry_pack or config.get_default_industry_pack()
    if conn is not None:
        return _execute_create_workspace(
            conn,
            slug=slug,
            name=name,
            industry_pack=resolved_pack,
            timezone=timezone,
        )
    with db.get_connection() as managed_conn:
        return _execute_create_workspace(
            managed_conn,
            slug=slug,
            name=name,
            industry_pack=resolved_pack,
            timezone=timezone,
        )


def _execute_create_workspace(
    conn: psycopg.Connection[Any],
    *,
    slug: str,
    name: str,
    industry_pack: str,
    timezone: str,
) -> dict[str, Any]:
    clean_slug = slug.strip()
    if not WORKSPACE_SLUG_RE.match(clean_slug):
        msg = f"Invalid workspace slug '{slug}': must be 3-40 lowercase letters, digits, or hyphens"
        raise ValueError(msg)

    clean_name = name.strip()
    if not clean_name:
        msg = "Workspace name must not be empty"
        raise ValueError(msg)

    clean_pack = industry_pack.strip()
    try:
        loaded_pack = config.load_industry_pack(clean_pack)
    except config.PackValidationError as exc:
        raise ValueError(str(exc)) from exc

    clean_tz = timezone.strip()
    try:
        ZoneInfo(clean_tz)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        msg = f"Invalid IANA timezone '{timezone}'"
        raise ValueError(msg) from exc

    try:
        ws_id = db.create_workspace(
            conn,
            slug=clean_slug,
            name=clean_name,
            industry_pack=clean_pack,
            timezone=clean_tz,
        )
    except psycopg.errors.UniqueViolation as exc:
        msg = f"Workspace with slug '{clean_slug}' already exists"
        raise ValueError(msg) from exc

    db.update_workspace_config(
        conn,
        workspace_id=ws_id,
        config={"industry_pack_snapshot": loaded_pack.model_dump(mode="json")},
    )

    return {
        "workspace_id": ws_id,
        "slug": clean_slug,
        "name": clean_name,
        "industry_pack": clean_pack,
        "timezone": clean_tz,
    }


def add_workspace_owner(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_slug: str,
    email: str,
    actor_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Provision an owner membership in a workspace and emit audit event (F01 AC5)."""
    if conn is not None:
        return _execute_add_workspace_owner(
            conn,
            workspace_slug=workspace_slug,
            email=email,
            actor_user_id=actor_user_id,
        )
    with db.get_connection() as managed_conn:
        return _execute_add_workspace_owner(
            managed_conn,
            workspace_slug=workspace_slug,
            email=email,
            actor_user_id=actor_user_id,
        )


def _execute_add_workspace_owner(
    conn: psycopg.Connection[Any],
    *,
    workspace_slug: str,
    email: str,
    actor_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    workspace = db.get_workspace_by_slug(conn, slug=workspace_slug)
    if workspace is None:
        raise ValueError(f"Workspace with slug '{workspace_slug}' does not exist")

    clean_email = email.strip().lower()
    user = db.create_or_get_user(conn, email=clean_email)
    membership = db.create_membership(
        conn,
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
        role="owner",
    )

    events.emit(
        conn,
        workspace_id=workspace["workspace_id"],
        event_type="membership.created",
        entity_type="membership",
        entity_id=user["user_id"],
        payload={
            "user_id": str(user["user_id"]),
            "email": clean_email,
            "role": "owner",
        },
        actor_user_id=actor_user_id or user["user_id"],
    )

    return {
        "workspace": workspace,
        "user": user,
        "membership": membership,
    }


def get_workspace_for_member(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Retrieve workspace details ensuring caller holds membership (F01 AC3)."""
    if conn is not None:
        return _execute_get_workspace_for_member(conn, workspace_id=workspace_id, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_get_workspace_for_member(
            managed_conn, workspace_id=workspace_id, user_id=user_id
        )


def _execute_get_workspace_for_member(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    workspace = db.get_workspace(conn, workspace_id=workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace {workspace_id} not found")
    return workspace


SUPPORTED_UI_LOCALES = frozenset({"pt-BR", "en-US"})


def set_user_ui_locale(
    conn: psycopg.Connection[Any] | None = None,
    *,
    user_id: uuid.UUID,
    ui_locale: str,
    workspace_id: uuid.UUID | None = None,
) -> None:
    """Update preferred UI locale for a user and record audit event if in workspace."""
    if ui_locale not in SUPPORTED_UI_LOCALES:
        msg = (
            f"Unsupported UI locale '{ui_locale}'. "
            f"Supported locales: {sorted(SUPPORTED_UI_LOCALES)}"
        )
        raise ValueError(msg)
    if conn is not None:
        _execute_set_user_ui_locale(
            conn, user_id=user_id, ui_locale=ui_locale, workspace_id=workspace_id
        )
        return
    with db.get_connection() as managed_conn:
        _execute_set_user_ui_locale(
            managed_conn, user_id=user_id, ui_locale=ui_locale, workspace_id=workspace_id
        )


def _execute_set_user_ui_locale(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
    ui_locale: str,
    workspace_id: uuid.UUID | None = None,
) -> None:
    db.update_user_ui_locale(conn, user_id=user_id, ui_locale=ui_locale)
    if workspace_id is not None:
        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type="user.locale_updated",
            entity_type="user",
            entity_id=user_id,
            payload={"ui_locale": ui_locale},
            actor_user_id=user_id,
        )


LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
MARKET_RE = re.compile(r"^[A-Z]{2}$")
CRON_FIELD_LIMITS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 7),
)
DEFAULT_PER_ARTICLE_MONTHLY_USD = Decimal("6.00")
DEFAULT_FIXED_RESEARCH_MONTHLY_USD = Decimal("10.00")


def estimate_monthly_cost(
    articles_per_week: int,
    *,
    per_article_monthly_usd: Decimal = DEFAULT_PER_ARTICLE_MONTHLY_USD,
    fixed_research_monthly_usd: Decimal = DEFAULT_FIXED_RESEARCH_MONTHLY_USD,
) -> Decimal:
    """Estimate monthly cost in USD: volume * per_article_estimate + fixed_research (F02 AC6)."""
    if (
        isinstance(articles_per_week, bool)
        or not isinstance(articles_per_week, int)
        or not (1 <= articles_per_week <= 20)
    ):
        msg = "articles_per_week must be an integer between 1 and 20"
        raise ValueError(msg)
    total = Decimal(articles_per_week) * per_article_monthly_usd + fixed_research_monthly_usd
    return total.quantize(Decimal("0.01"))


def _validate_cron_atom(atom: str, field_name: str, min_val: int, max_val: int) -> None:
    base = atom
    if "/" in atom:
        base, step_str = atom.split("/", 1)
        if not step_str.isdigit() or int(step_str) < 1:
            msg = f"Invalid cron step in {field_name}: '{atom}'"
            raise ValueError(msg)

    if base == "*":
        return

    if "-" in base:
        start_s, end_s = base.split("-", 1)
        if not (start_s.isdigit() and end_s.isdigit()):
            msg = f"Invalid cron range in {field_name}: '{atom}'"
            raise ValueError(msg)
        start_v, end_v = int(start_s), int(end_s)
        if start_v > end_v or start_v < min_val or end_v > max_val:
            msg = f"Out-of-bounds cron range in {field_name}: '{atom}'"
            raise ValueError(msg)
        return

    if not base.isdigit() or not (min_val <= int(base) <= max_val):
        msg = f"Invalid cron value in {field_name}: '{atom}'"
        raise ValueError(msg)


def validate_cron_expression(cron_expr: str) -> str:
    """Validate a standard 5-field cron schedule expression."""
    cleaned = cron_expr.strip()
    parts = cleaned.split()
    if len(parts) != 5:
        msg = f"Invalid cron schedule '{cron_expr}': expected 5 fields"
        raise ValueError(msg)

    for part, (field_name, min_val, max_val) in zip(parts, CRON_FIELD_LIMITS, strict=True):
        for atom in part.split(","):
            _validate_cron_atom(atom, field_name, min_val, max_val)

    return cleaned


def _parse_positive_cap(value: Decimal | float | int | str | None, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        msg = f"Missing required spend cap: {label}"
        raise ValueError(msg)
    raw_str = str(value).strip()
    if not raw_str:
        msg = f"Missing required spend cap: {label}"
        raise ValueError(msg)
    try:
        dec = Decimal(raw_str)
    except InvalidOperation as exc:
        msg = f"Invalid spend cap for {label}: '{value}'"
        raise ValueError(msg) from exc
    if not dec.is_finite() or dec <= Decimal("0"):
        msg = f"Spend cap {label} must be greater than 0"
        raise ValueError(msg)
    return dec.quantize(Decimal("0.01"))


def _validate_spend_caps(
    monthly_cap_usd: Decimal | float | int | str | None,
    run_cap_usd: Decimal | float | int | str | None,
) -> tuple[Decimal, Decimal]:
    monthly = _parse_positive_cap(monthly_cap_usd, "monthly_cap_usd")
    run_cap = _parse_positive_cap(run_cap_usd, "run_cap_usd")
    if run_cap > monthly:
        msg = "run_cap_usd must be less than or equal to monthly_cap_usd"
        raise ValueError(msg)
    return monthly, run_cap


def _validate_locale_with_plugins(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    locale: str,
    market: str,
) -> None:
    integrations = db.list_workspace_integrations(conn, workspace_id=workspace_id)
    targets: list[tuple[str, str, dict[str, Any]]] = [
        (str(row["port"]), str(row["plugin"]), dict(row.get("settings") or {}))
        for row in integrations
    ]
    if not targets:
        targets = [("llm", "openrouter", {})]

    for port, plugin_name, settings_dict in targets:
        plugin_cls = get_plugin(port, plugin_name)
        configured_locales = settings_dict.get("supported_locales")
        if isinstance(configured_locales, list):
            supported = tuple(str(x) for x in configured_locales)
        else:
            supported = getattr(plugin_cls, "SUPPORTED_LOCALES", DEFAULT_SUPPORTED_LOCALES)
        if locale not in supported:
            raise UnsupportedLocale(plugin=plugin_name, locale=locale, market=market)


def _campaign_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "locale": str(row["locale"]),
        "market": str(row["market"]),
        "articles_per_week": int(row["articles_per_week"]),
        "schedule_cron": str(row["schedule_cron"]),
        "monthly_cap_usd": f"{Decimal(str(row['monthly_cap_usd'])):.2f}",
        "run_cap_usd": f"{Decimal(str(row['run_cap_usd'])):.2f}",
        "config": dict(row["config"]),
    }


def get_active_campaign(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the active campaign for a workspace, verifying membership."""
    if conn is not None:
        return _execute_get_active_campaign(conn, workspace_id=workspace_id, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_get_active_campaign(
            managed_conn, workspace_id=workspace_id, user_id=user_id
        )


def _execute_get_active_campaign(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    campaigns = db.list_campaigns(conn, workspace_id=workspace_id)
    return campaigns[0] if campaigns else None


def save_campaign(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    locale: str,
    market: str,
    articles_per_week: int,
    schedule_cron: str,
    monthly_cap_usd: Decimal | float | int | str | None,
    run_cap_usd: Decimal | float | int | str | None,
    config: CampaignConfig | dict[str, Any] | None = None,
    name: str = "Default Campaign",
    campaign_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create or update a campaign with full validation and audit event (F02 AC1-AC5)."""
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "locale": locale,
        "market": market,
        "articles_per_week": articles_per_week,
        "schedule_cron": schedule_cron,
        "monthly_cap_usd": monthly_cap_usd,
        "run_cap_usd": run_cap_usd,
        "config": config,
        "name": name,
        "campaign_id": campaign_id,
    }
    if conn is not None:
        return _execute_save_campaign(conn, payload)
    with db.get_connection() as managed_conn:
        return _execute_save_campaign(managed_conn, payload)


def _resolve_existing_campaign(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
) -> dict[str, Any] | None:
    if campaign_id is not None:
        existing = db.get_campaign(conn, workspace_id=workspace_id, campaign_id=campaign_id)
        if existing is None:
            msg = f"Campaign {campaign_id} not found in workspace {workspace_id}"
            raise ValueError(msg)
        return existing

    campaigns = db.list_campaigns(conn, workspace_id=workspace_id)
    return campaigns[0] if campaigns else None


def _execute_save_campaign(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    locale = str(params["locale"])
    market = str(params["market"])
    articles_per_week: int = params["articles_per_week"]
    schedule_cron = str(params["schedule_cron"])
    config = params["config"]
    campaign_id: uuid.UUID | None = params["campaign_id"]

    require_member(conn, workspace_id=workspace_id, user_id=user_id)

    clean_name = str(params["name"]).strip()
    if not clean_name:
        msg = "Campaign name must not be empty"
        raise ValueError(msg)
    if not LOCALE_RE.match(locale):
        msg = f"Invalid locale format '{locale}': expected BCP-47 ll-CC (e.g. pt-BR, en-US)"
        raise ValueError(msg)
    if not MARKET_RE.match(market):
        msg = f"Invalid market format '{market}': expected ISO-3166 2-letter code (e.g. BR, US)"
        raise ValueError(msg)

    _validate_locale_with_plugins(conn, workspace_id=workspace_id, locale=locale, market=market)
    estimate_monthly_cost(articles_per_week)
    valid_cron = validate_cron_expression(schedule_cron)
    monthly_dec, run_dec = _validate_spend_caps(params["monthly_cap_usd"], params["run_cap_usd"])

    validated_cfg = (
        config
        if isinstance(config, CampaignConfig)
        else CampaignConfig.model_validate(config)
        if config is not None
        else CampaignConfig()
    )
    cfg_dict = validated_cfg.model_dump()

    existing = _resolve_existing_campaign(conn, workspace_id=workspace_id, campaign_id=campaign_id)

    if existing is None:
        target_id = db.create_campaign(
            conn,
            workspace_id=workspace_id,
            name=clean_name,
            locale=locale,
            market=market,
            articles_per_week=articles_per_week,
            schedule_cron=valid_cron,
            monthly_cap_usd=monthly_dec,
            run_cap_usd=run_dec,
            config=cfg_dict,
            config_schema_version=validated_cfg.schema_version,
            created_by=user_id,
        )
    else:
        target_id = uuid.UUID(str(existing["id"]))
        db.update_campaign(
            conn,
            workspace_id=workspace_id,
            campaign_id=target_id,
            name=clean_name,
            locale=locale,
            market=market,
            articles_per_week=articles_per_week,
            schedule_cron=valid_cron,
            monthly_cap_usd=monthly_dec,
            run_cap_usd=run_dec,
            config=cfg_dict,
            config_schema_version=validated_cfg.schema_version,
            updated_by=user_id,
        )

    saved = db.get_campaign(conn, workspace_id=workspace_id, campaign_id=target_id)
    if saved is None:
        msg = f"Failed to load saved campaign {target_id}"
        raise RuntimeError(msg)

    events.emit(
        conn,
        workspace_id=workspace_id,
        event_type="settings.changed",
        entity_type="campaign",
        entity_id=target_id,
        payload={
            "before": _campaign_snapshot(existing) if existing is not None else None,
            "after": _campaign_snapshot(saved),
        },
        actor_user_id=user_id,
    )
    return saved


def _brand_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "version": int(row["version"]),
        "is_current": bool(row["is_current"]),
        "schema_version": int(row["schema_version"]),
        "payload": dict(row["payload"]),
    }


def current_brand(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the current brand profile for a workspace, verifying membership (F03 AC1)."""
    if conn is not None:
        return _execute_current_brand(conn, workspace_id=workspace_id, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_current_brand(managed_conn, workspace_id=workspace_id, user_id=user_id)


def _execute_current_brand(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    return db.get_current_brand_profile(conn, workspace_id=workspace_id)


def save_brand(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    profile: BrandProfile | dict[str, Any],
) -> dict[str, Any]:
    """Validate and save a new brand profile version in one transaction (F03 AC1, AC3)."""
    if conn is not None:
        return _execute_save_brand(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            profile=profile,
        )
    with db.get_connection() as managed_conn:
        return _execute_save_brand(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            profile=profile,
        )


def _execute_save_brand(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    profile: BrandProfile | dict[str, Any],
) -> dict[str, Any]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    validated = (
        profile if isinstance(profile, BrandProfile) else BrandProfile.model_validate(profile)
    )
    payload_dict = validated.model_dump()

    with conn.transaction():
        previous = db.get_current_brand_profile(conn, workspace_id=workspace_id)
        saved = db.save_brand_profile(
            conn,
            workspace_id=workspace_id,
            payload=payload_dict,
            schema_version=validated.schema_version,
            created_by=user_id,
        )
        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type="settings.changed",
            entity_type="brand_profile",
            entity_id=uuid.UUID(str(saved["id"])),
            payload={
                "before": _brand_snapshot(previous) if previous is not None else None,
                "after": _brand_snapshot(saved),
            },
            actor_user_id=user_id,
        )
    return saved


def _parse_brand_fixture_yaml(yaml_text: str) -> BrandProfile:
    try:
        raw_data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        msg = f"Invalid brand fixture YAML: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(raw_data, dict):
        msg = "Brand fixture must be a YAML mapping"
        raise ValueError(msg)
    return BrandProfile.model_validate(raw_data)


def import_brand_fixture_yaml(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    yaml_text: str,
) -> dict[str, Any]:
    """Validate a BrandProfile YAML string and persist a new brand version (ADR-032)."""
    profile = _parse_brand_fixture_yaml(yaml_text)
    return save_brand(conn, workspace_id=workspace_id, user_id=user_id, profile=profile)


def export_brand_fixture_yaml(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str:
    """Export the current workspace BrandProfile as formatted YAML (ADR-032)."""
    current = current_brand(conn, workspace_id=workspace_id, user_id=user_id)
    if current is None:
        msg = "No brand profile is saved for this workspace"
        raise ValueError(msg)
    profile = BrandProfile.model_validate(current["payload"])
    return str(yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False))


def seed_brand(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_slug: str,
    fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    """Seed brand profile from a YAML fixture file for a workspace (F03 AC4)."""
    if conn is not None:
        return _execute_seed_brand(
            conn,
            workspace_slug=workspace_slug,
            fixture_path=fixture_path,
        )
    with db.get_connection() as managed_conn:
        return _execute_seed_brand(
            managed_conn,
            workspace_slug=workspace_slug,
            fixture_path=fixture_path,
        )


def _execute_seed_brand(
    conn: psycopg.Connection[Any],
    *,
    workspace_slug: str,
    fixture_path: str | Path | None,
) -> dict[str, Any]:
    workspace = db.get_workspace_by_slug(conn, slug=workspace_slug)
    if workspace is None:
        msg = f"Workspace with slug '{workspace_slug}' does not exist"
        raise ValueError(msg)

    ws_id = uuid.UUID(str(workspace["workspace_id"]))
    owner = db.get_workspace_owner(conn, workspace_id=ws_id)
    if owner is None:
        msg = f"Workspace '{workspace_slug}' has no owner membership; run add-owner first"
        raise ValueError(msg)

    resolved_path = config.resolve_brand_fixture_path(fixture_path)
    if not resolved_path.is_file():
        msg = f"Fixture file not found: {resolved_path}"
        raise ValueError(msg)

    profile = _parse_brand_fixture_yaml(resolved_path.read_text(encoding="utf-8"))
    return _execute_save_brand(
        conn,
        workspace_id=ws_id,
        user_id=uuid.UUID(str(owner["user_id"])),
        profile=profile,
    )


DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+$")
URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
VALID_FETCH_MODES = frozenset({"feed", "site_search"})
VALID_SOURCE_ROLES = frozenset({"news", "company", "data", "management"})
VALID_SOURCE_TIERS = frozenset({1, 2, 3})
VALID_FEED_ROOT_TAGS = frozenset({"rss", "feed", "rdf", "urlset", "sitemapindex"})


def _validate_source_domain(domain: str | None) -> str:
    clean = (domain or "").strip().lower()
    if not clean or not DOMAIN_RE.match(clean):
        msg = f"Invalid or missing source domain '{domain}': expected bare lowercase domain"
        raise ValueError(msg)
    return clean


def _validate_source_url(url: str | None) -> str:
    clean = (url or "").strip()
    if not clean or not URL_RE.match(clean):
        msg = f"Source URL must be a valid http(s) URL (got '{url}')"
        raise ValueError(msg)
    return clean


def _validate_source_tier_and_role(tier: int | None, role: str | None) -> tuple[int, str]:
    if isinstance(tier, bool) or not isinstance(tier, int) or tier not in VALID_SOURCE_TIERS:
        msg = "Source tier is required and must be 1, 2, or 3"
        raise ValueError(msg)
    clean_role = (role or "").strip().lower()
    if clean_role not in VALID_SOURCE_ROLES:
        msg = f"Source role is required and must be one of {sorted(VALID_SOURCE_ROLES)}"
        raise ValueError(msg)
    return tier, clean_role


def _validate_source_locales(locales: list[str] | None) -> list[str]:
    if not locales:
        msg = "Source locales must not be empty"
        raise ValueError(msg)
    cleaned: list[str] = []
    for loc in locales:
        s = str(loc).strip()
        if not LOCALE_RE.match(s):
            msg = f"Invalid source locale '{loc}': expected BCP-47 ll-CC"
            raise ValueError(msg)
        cleaned.append(s)
    return cleaned


def _validate_and_fetch_feed(
    feed_url: str | None,
    *,
    should_fetch: bool,
) -> tuple[str, bool | None, datetime | None]:
    clean_feed = (feed_url or "").strip()
    if not clean_feed or not URL_RE.match(clean_feed):
        msg = "Valid feed_url is required when fetch_mode is 'feed'"
        raise ValueError(msg)
    if not should_fetch:
        return clean_feed, None, None

    try:
        resp = safe_http.safe_request("GET", clean_feed)
    except (safe_http.SafeHTTPError, OSError) as exc:
        msg = f"Feed fetch failed for '{clean_feed}': {exc}"
        raise ValueError(msg) from exc

    if resp.status_code >= 400:
        msg = f"Feed URL '{clean_feed}' returned HTTP {resp.status_code}"
        raise ValueError(msg)

    try:
        root = ET.fromstring(resp.text.strip())  # noqa: S314
    except ET.ParseError as exc:
        msg = f"Invalid feed XML at '{clean_feed}': {exc}"
        raise ValueError(msg) from exc

    root_tag = root.tag.split("}")[-1].lower()
    if root_tag not in VALID_FEED_ROOT_TAGS:
        msg = f"Unrecognized feed root element '<{root_tag}>' at '{clean_feed}'"
        raise ValueError(msg)

    return clean_feed, True, datetime.now(UTC)


def _validate_source_fields(
    params: dict[str, Any],
    *,
    fetch_feed: bool = True,
) -> dict[str, Any]:
    clean_name = str(params.get("name") or "").strip()
    if not clean_name:
        msg = "Source name must not be empty"
        raise ValueError(msg)

    fetch_mode = str(params.get("fetch_mode") or "").strip()
    if fetch_mode not in VALID_FETCH_MODES:
        msg = f"Invalid fetch_mode '{fetch_mode}': expected 'feed' or 'site_search'"
        raise ValueError(msg)

    clean_domain = _validate_source_domain(params.get("domain"))
    clean_url = _validate_source_url(params.get("url"))
    valid_tier, valid_role = _validate_source_tier_and_role(params.get("tier"), params.get("role"))
    valid_locales = _validate_source_locales(params.get("locales"))
    enabled = bool(params.get("enabled", True))

    feed_url: str | None = None
    last_fetch_ok: bool | None = None
    last_fetched_at: datetime | None = None
    if fetch_mode == "feed":
        feed_url, last_fetch_ok, last_fetched_at = _validate_and_fetch_feed(
            params.get("feed_url"),
            should_fetch=enabled and fetch_feed,
        )

    raw_topics = params.get("topics") or []
    clean_topics = [str(t).strip() for t in raw_topics if str(t).strip()]

    return {
        "name": clean_name,
        "domain": clean_domain,
        "url": clean_url,
        "feed_url": feed_url,
        "fetch_mode": fetch_mode,
        "tier": valid_tier,
        "role": valid_role,
        "locales": valid_locales,
        "topics": clean_topics,
        "enabled": enabled,
        "last_fetch_ok": last_fetch_ok,
        "last_fetched_at": last_fetched_at,
    }


def _source_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]) if row.get("workspace_id") is not None else None,
        "name": str(row["name"]),
        "domain": str(row["domain"]),
        "url": str(row["url"]),
        "feed_url": str(row["feed_url"]) if row.get("feed_url") is not None else None,
        "fetch_mode": str(row["fetch_mode"]),
        "tier": int(row["tier"]),
        "role": str(row["role"]),
        "locales": list(row["locales"]),
        "topics": list(row["topics"]),
        "enabled": bool(row["enabled"]),
    }


def list_sources(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    include_shared: bool = True,
) -> list[dict[str, Any]]:
    """List sources visible to a workspace, verifying membership (F04)."""
    if conn is not None:
        return _execute_list_sources(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            include_shared=include_shared,
        )
    with db.get_connection() as managed_conn:
        return _execute_list_sources(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            include_shared=include_shared,
        )


def _execute_list_sources(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    include_shared: bool,
) -> list[dict[str, Any]]:
    workspace = _execute_get_workspace_for_member(conn, workspace_id=workspace_id, user_id=user_id)
    return db.list_sources(
        conn,
        workspace_id=workspace_id,
        industry_pack=str(workspace["industry_pack"]),
        include_shared=include_shared,
    )


def save_source(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    domain: str,
    url: str,
    fetch_mode: str,
    tier: int | None,
    role: str | None,
    locales: list[str],
    feed_url: str | None = None,
    topics: list[str] | None = None,
    enabled: bool = True,
    source_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create or update a source with feed/domain/role/tier validation (F04 AC1-AC4, AC7)."""
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "name": name,
        "domain": domain,
        "url": url,
        "fetch_mode": fetch_mode,
        "tier": tier,
        "role": role,
        "locales": locales,
        "feed_url": feed_url,
        "topics": topics or [],
        "enabled": enabled,
        "source_id": source_id,
    }
    if conn is not None:
        return _execute_save_source(conn, payload)
    with db.get_connection() as managed_conn:
        return _execute_save_source(managed_conn, payload)


def _execute_save_source(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    source_id: uuid.UUID | None = params["source_id"]

    workspace = _execute_get_workspace_for_member(conn, workspace_id=workspace_id, user_id=user_id)
    validated = _validate_source_fields(params)

    existing: dict[str, Any] | None = None
    if source_id is not None:
        existing = db.get_source(conn, workspace_id=workspace_id, source_id=source_id)
        if existing is None:
            msg = f"Source {source_id} not found in workspace {workspace_id}"
            raise ValueError(msg)

    try:
        with conn.transaction():
            if existing is None:
                saved = db.create_source(
                    conn,
                    workspace_id=workspace_id,
                    industry_pack=str(workspace["industry_pack"]),
                    added_by=user_id,
                    **validated,
                )
            else:
                updated = db.update_source(
                    conn,
                    workspace_id=workspace_id,
                    source_id=source_id,  # type: ignore[arg-type]
                    **validated,
                )
                if updated is None:
                    msg = f"Failed to update source {source_id}"
                    raise RuntimeError(msg)
                saved = updated

            events.emit(
                conn,
                workspace_id=workspace_id,
                event_type="settings.changed",
                entity_type="source",
                entity_id=uuid.UUID(str(saved["id"])),
                payload={
                    "before": _source_snapshot(existing) if existing is not None else None,
                    "after": _source_snapshot(saved),
                },
                actor_user_id=user_id,
            )
    except psycopg.errors.UniqueViolation as exc:
        msg = f"Duplicate source domain '{validated['domain']}' already exists"
        raise ValueError(msg) from exc

    return saved


def set_source_enabled(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    enabled: bool,
) -> dict[str, Any]:
    """Enable or disable a source and record a settings.changed audit event (F04 AC4)."""
    if conn is not None:
        return _execute_set_source_enabled(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            source_id=source_id,
            enabled=enabled,
        )
    with db.get_connection() as managed_conn:
        return _execute_set_source_enabled(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            source_id=source_id,
            enabled=enabled,
        )


def _execute_set_source_enabled(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    enabled: bool,
) -> dict[str, Any]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    existing = db.get_source(conn, workspace_id=workspace_id, source_id=source_id)
    if existing is None:
        msg = f"Source {source_id} not found in workspace {workspace_id}"
        raise ValueError(msg)

    with conn.transaction():
        updated = db.update_source(
            conn,
            workspace_id=workspace_id,
            source_id=source_id,
            name=str(existing["name"]),
            domain=str(existing["domain"]),
            url=str(existing["url"]),
            feed_url=existing.get("feed_url"),
            fetch_mode=str(existing["fetch_mode"]),
            tier=int(existing["tier"]),
            role=str(existing["role"]),
            locales=list(existing["locales"]),
            topics=list(existing["topics"]),
            enabled=enabled,
        )
        if updated is None:
            msg = f"Failed to update source {source_id}"
            raise RuntimeError(msg)

        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type="settings.changed",
            entity_type="source",
            entity_id=source_id,
            payload={
                "before": _source_snapshot(existing),
                "after": _source_snapshot(updated),
            },
            actor_user_id=user_id,
        )
    return updated


def seed_sources(
    conn: psycopg.Connection[Any] | None = None,
    *,
    industry_pack: str | None = None,
    packs_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Seed shared pack sources from an industry pack's sources.yaml (F04 AC5)."""
    resolved_pack = industry_pack or config.get_default_industry_pack()
    if conn is not None:
        return _execute_seed_sources(conn, industry_pack=resolved_pack, packs_dir=packs_dir)
    with db.get_connection() as managed_conn:
        return _execute_seed_sources(managed_conn, industry_pack=resolved_pack, packs_dir=packs_dir)


def _execute_seed_sources(
    conn: psycopg.Connection[Any],
    *,
    industry_pack: str,
    packs_dir: Path | str | None,
) -> list[dict[str, Any]]:
    try:
        pack = config.load_industry_pack(industry_pack, base_dir=packs_dir)
    except config.PackValidationError as exc:
        raise ValueError(str(exc)) from exc

    seeded: list[dict[str, Any]] = []
    with conn.transaction():
        for raw_src in pack.sources:
            validated = _validate_source_fields(raw_src, fetch_feed=False)
            row = db.upsert_pack_source(
                conn,
                industry_pack=pack.slug,
                name=validated["name"],
                domain=validated["domain"],
                url=validated["url"],
                feed_url=validated["feed_url"],
                fetch_mode=validated["fetch_mode"],
                tier=validated["tier"],
                role=validated["role"],
                locales=validated["locales"],
                topics=validated["topics"],
                enabled=validated["enabled"],
            )
            seeded.append(row)
    return seeded


def _fetch_verification_records(  # noqa: PLR0913
    src: dict[str, Any],
    fetch_mode: str,
    domain: str,
    since_dt: datetime,
    since_date: date,
    feed_adapter: FeedSourcePort,
    search_adapter: NewsSearchPort,
) -> list[ResearchRecord]:
    if fetch_mode == "feed":
        feed_url = str(src.get("feed_url") or "")
        return feed_adapter.fetch_feed(feed_url, since=since_dt)

    locales = src.get("locales") or ["pt-BR"]
    primary_locale = str(locales[0])
    market = "br" if primary_locale == "pt-BR" else "us"
    raw_topics = src.get("topics") or []
    topics = [str(t).strip() for t in raw_topics if str(t).strip()]
    domain_query = f"site:{domain} {' '.join(topics)}".strip()
    return search_adapter.search(
        domain_query,
        locale=primary_locale,
        market=market,
        since=since_date,
    )


def _compute_dated_records(records: list[ResearchRecord]) -> tuple[int, int, float, str]:
    records_found = len(records)
    if records_found == 0:
        return 0, 0, 0.0, "no_records"

    dated_count = sum(
        1
        for r in records
        if r.published_at is not None
        and getattr(r, "verification_status", None) in ("verified", "inferred")
    )
    dated_pct = round((dated_count / records_found * 100.0), 1)
    status = "ok" if dated_count > 0 else "no_records"
    return records_found, dated_count, dated_pct, status


def _verify_single_source(
    src: dict[str, Any],
    since_dt: datetime,
    since_date: date,
    feed_adapter: FeedSourcePort,
    search_adapter: NewsSearchPort,
) -> dict[str, Any]:
    name = str(src.get("name") or src.get("domain") or "")
    domain = str(src.get("domain") or "")
    fetch_mode = str(src.get("fetch_mode") or "site_search")
    enabled = bool(src.get("enabled", True))
    notes = src.get("notes")

    if not enabled:
        return {
            "name": name,
            "domain": domain,
            "fetch_mode": fetch_mode,
            "enabled": False,
            "records_found": 0,
            "dated_records_count": 0,
            "dated_pct": 0.0,
            "status": "disabled",
            "error": None,
            "notes": notes,
        }

    try:
        records = _fetch_verification_records(
            src, fetch_mode, domain, since_dt, since_date, feed_adapter, search_adapter
        )
        records_found, dated_count, dated_pct, status = _compute_dated_records(records)
        error = None
    except Exception as exc:
        records_found, dated_count, dated_pct = 0, 0, 0.0
        status = "error"
        error = str(exc)

    return {
        "name": name,
        "domain": domain,
        "fetch_mode": fetch_mode,
        "enabled": enabled,
        "records_found": records_found,
        "dated_records_count": dated_count,
        "dated_pct": dated_pct,
        "status": status,
        "error": error,
        "notes": notes,
    }


def verify_sources(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    industry_pack: str | None = None,
    days: int = 30,
    offline: bool = False,
    feed_plugin: FeedSourcePort | None = None,
    search_plugin: NewsSearchPort | None = None,
    packs_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Verify shared seed sources across feed and site_search modes (F04 AC6, M1-12)."""
    del conn
    resolved_pack = industry_pack or config.get_default_industry_pack()
    try:
        pack = config.load_industry_pack(resolved_pack, base_dir=packs_dir)
    except config.PackValidationError as exc:
        raise ValueError(str(exc)) from exc

    if offline:
        from engine.integrations.fakes import FakeFeedSource, FakeNewsSearch

        feed_adapter: FeedSourcePort = feed_plugin or FakeFeedSource()
        search_adapter: NewsSearchPort = search_plugin or FakeNewsSearch()
    else:
        from engine.integrations.brave import BraveNewsSearch
        from engine.integrations.rss import RSSFeedSource

        feed_adapter = feed_plugin or RSSFeedSource()
        search_adapter = search_plugin or BraveNewsSearch()

    since_dt = datetime.now(UTC) - timedelta(days=days)
    since_date = since_dt.date()

    results: list[dict[str, Any]] = []
    for src in pack.sources:
        res = _verify_single_source(src, since_dt, since_date, feed_adapter, search_adapter)
        results.append(res)
    return results


LLM_STAGES: tuple[str, ...] = ("research", "score", "brief", "write", "evaluate")


def _unwrap_schema_prop(prop: dict[str, Any]) -> dict[str, Any]:
    """Unwrap nullable anyOf/oneOf JSON Schema property into its primary branch."""
    for union_key in ("anyOf", "oneOf"):
        branches = prop.get(union_key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict) and branch.get("type") != "null":
                    return {**prop, **branch}
    return prop


def _classify_field_widget(
    field_name: str,
    unwrapped: dict[str, Any],
) -> tuple[str, str, bool, list[Any] | None]:
    """Classify JSON schema property into (widget, field_type, is_secret, options)."""
    fmt = str(unwrapped.get("format") or "")
    prop_type = str(unwrapped.get("type") or "string")
    enum_vals = unwrapped.get("enum")

    if fmt == "password":
        return "password", "string", True, None
    if field_name == "models_by_stage":
        return "stage_models", "object", False, None
    if isinstance(enum_vals, list) and enum_vals:
        return "select", prop_type, False, list(enum_vals)
    if prop_type == "boolean":
        return "checkbox", "boolean", False, None
    if prop_type in ("integer", "number"):
        return "number", prop_type, False, None
    if prop_type == "object":
        return "json", "object", False, None
    return "text", prop_type, False, None


def extract_settings_field_specs(settings_cls: type[BaseModel]) -> list[dict[str, Any]]:
    """Extract UI widget specifications from a plugin Settings Pydantic model JSON Schema."""
    schema = settings_cls.model_json_schema()
    properties: dict[str, Any] = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    default_instance = settings_cls()

    specs: list[dict[str, Any]] = []
    for field_name, raw_prop in properties.items():
        unwrapped = _unwrap_schema_prop(raw_prop)
        widget, field_type, is_secret, options = _classify_field_widget(field_name, unwrapped)
        default_val = None if is_secret else getattr(default_instance, field_name, None)
        specs.append(
            {
                "name": field_name,
                "title": str(raw_prop.get("title") or field_name),
                "widget": widget,
                "field_type": field_type,
                "is_secret": is_secret,
                "required": field_name in required_set,
                "default": default_val,
                "options": options,
            }
        )
    return specs


def list_port_plugins() -> dict[str, list[dict[str, Any]]]:
    """Return discovered plugins grouped by port with their field specifications (F05 AC1)."""
    discovered = discover_plugins()
    result: dict[str, list[dict[str, Any]]] = {p: [] for p in PORTS}
    for port in PORTS:
        for plugin_name, plugin_cls in sorted(discovered.get(port, {}).items()):
            settings_cls = getattr(plugin_cls, "Settings", None)
            specs = extract_settings_field_specs(settings_cls) if settings_cls else []
            result[port].append(
                {
                    "plugin": plugin_name,
                    "fields": specs,
                }
            )
    return result


_LOCAL_LLM_PLUGINS = frozenset({"ollama", "openai_compat"})


def _validate_single_integration_url(key: str, clean: str, *, allow_local: bool) -> None:
    if allow_local:
        from engine.integrations.openai_compat import validate_llm_base_url

        try:
            validate_llm_base_url(clean)
        except ValueError as exc:
            msg = f"Unsafe URL in setting '{key}': {exc}"
            raise ValueError(msg) from exc
        return
    try:
        safe_http.validate_safe_url(clean)
    except safe_http.SafeHTTPError as exc:
        msg = f"Unsafe URL in setting '{key}': {exc}"
        raise ValueError(msg) from exc


def _validate_integration_urls(
    settings_dict: dict[str, Any],
    *,
    allow_local: bool = False,
) -> None:
    """Validate URL-like settings against SSRF rules via safe_http (F05 AC7)."""
    for key, val in settings_dict.items():
        if not isinstance(val, str) or not val.strip():
            continue
        clean = val.strip()
        is_url_key = key.lower().endswith("url") or "url" in key.lower()
        if is_url_key or clean.lower().startswith(("http://", "https://")):
            _validate_single_integration_url(key, clean, allow_local=allow_local)


def _resolve_integration_masked_secrets(
    row: dict[str, Any] | None,
    encryption_key: str | list[str] | None = None,
) -> dict[str, str]:
    """Return masked secret dictionary for an integration row without exposing raw values."""
    if row is None or not row.get("secrets"):
        return {}
    try:
        keys = _resolve_keys(encryption_key)
        decrypted = decrypt_secrets(row["secrets"], keys)
        return mask_secrets(decrypted)
    except Exception:
        return {}


def _format_integration_row(
    row: dict[str, Any],
    masked: dict[str, str],
) -> dict[str, Any]:
    """Format a workspace_integrations row with masked secrets and no raw bytea."""
    return {
        "workspace_id": row["workspace_id"],
        "port": str(row["port"]),
        "plugin": str(row["plugin"]),
        "settings": dict(row.get("settings") or {}),
        "masked_secrets": masked,
        "key_version": int(row.get("key_version") or 1),
        "last_test_ok": row.get("last_test_ok"),
        "last_tested_at": row.get("last_tested_at"),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
    }


def _integration_snapshot(row: dict[str, Any], masked: dict[str, str]) -> dict[str, Any]:
    return {
        "port": str(row["port"]),
        "plugin": str(row["plugin"]),
        "settings": dict(row.get("settings") or {}),
        "masked_secrets": dict(masked),
    }


def list_integrations(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    encryption_key: str | list[str] | None = None,
) -> list[dict[str, Any]]:
    """List configured integrations for a workspace with masked secrets (F05 AC2)."""
    if conn is not None:
        return _execute_list_integrations(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            encryption_key=encryption_key,
        )
    with db.get_connection() as managed_conn:
        return _execute_list_integrations(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            encryption_key=encryption_key,
        )


def _execute_list_integrations(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    encryption_key: str | list[str] | None,
) -> list[dict[str, Any]]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    rows = db.list_workspace_integrations(conn, workspace_id=workspace_id)
    results: list[dict[str, Any]] = []
    for row in rows:
        full_row = db.get_workspace_integration(
            conn, workspace_id=workspace_id, port=str(row["port"])
        )
        masked = _resolve_integration_masked_secrets(full_row, encryption_key)
        results.append(_format_integration_row(row, masked))
    return results


def _merge_secret_inputs(
    raw_secrets: dict[str, Any],
    existing_secrets: dict[str, Any],
) -> dict[str, Any]:
    """Merge non-empty secret inputs over existing secrets."""
    merged = dict(existing_secrets)
    for k, v in raw_secrets.items():
        if v is not None and str(v).strip() != "":
            merged[k] = v
    return merged


def _merge_and_split_integration_inputs(
    plugin_cls: type[Any],
    raw_settings: dict[str, Any],
    raw_secrets: dict[str, Any],
    existing_secrets: dict[str, Any],
    *,
    allow_local: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate combined plugin settings and split into non-secret settings and raw secrets."""
    merged_secrets = _merge_secret_inputs(raw_secrets, existing_secrets)
    validated = plugin_cls.Settings.model_validate({**raw_settings, **merged_secrets})
    json_safe_settings = validated.model_dump(mode="json")
    specs = {f["name"]: f for f in extract_settings_field_specs(plugin_cls.Settings)}

    clean_settings: dict[str, Any] = {}
    clean_secrets: dict[str, Any] = {}
    for field_name, spec in specs.items():
        val = getattr(validated, field_name, None)
        if spec["is_secret"]:
            raw_val = val.get_secret_value() if isinstance(val, SecretStr) else val
            if field_name in merged_secrets and raw_val is not None:
                clean_secrets[field_name] = raw_val
        else:
            clean_settings[field_name] = json_safe_settings.get(field_name, val)

    _validate_integration_urls(clean_settings, allow_local=allow_local)
    return clean_settings, clean_secrets


def save_integration(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    port: str,
    plugin: str,
    settings: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
    encryption_key: str | None = None,
) -> dict[str, Any]:
    """Validate, encrypt secrets, and save a workspace integration (F05 AC1, AC2, AC5, AC7)."""
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "port": port,
        "plugin": plugin,
        "settings": settings or {},
        "secrets": secrets or {},
        "encryption_key": encryption_key,
    }
    if conn is not None:
        return _execute_save_integration(conn, payload)
    with db.get_connection() as managed_conn:
        return _execute_save_integration(managed_conn, payload)


def _upsert_integration_inside_tx(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    port: str = params["port"]
    plugin: str = params["plugin"]
    encryption_key: str | None = params["encryption_key"]

    if port not in PORTS:
        msg = f"Invalid integration port '{port}'"
        raise ValueError(msg)
    plugin_cls = get_plugin(port, plugin)

    existing = db.get_workspace_integration(conn, workspace_id=workspace_id, port=port)
    existing_secrets: dict[str, Any] = {}
    if (
        existing is not None
        and existing.get("plugin") == plugin
        and existing.get("secrets") is not None
    ):
        keys = _resolve_keys(encryption_key)
        existing_secrets = decrypt_secrets(existing["secrets"], keys)

    clean_settings, clean_secrets = _merge_and_split_integration_inputs(
        plugin_cls,
        params["settings"],
        params["secrets"],
        existing_secrets,
        allow_local=plugin in _LOCAL_LLM_PLUGINS,
    )

    encrypted_bytes: bytes | None = None
    if clean_secrets:
        keys = _resolve_keys(encryption_key)
        encrypted_bytes = encrypt_secrets(clean_secrets, keys[0])

    masked_before = mask_secrets(existing_secrets) if existing is not None else {}
    masked_after = mask_secrets(clean_secrets)

    saved = db.upsert_workspace_integration(
        conn,
        workspace_id=workspace_id,
        port=port,
        plugin=plugin,
        settings=clean_settings,
        secrets=encrypted_bytes,
        updated_by=user_id,
    )
    entity_id = uuid.uuid5(workspace_id, f"integration:{port}")
    events.emit(
        conn,
        workspace_id=workspace_id,
        event_type="settings.changed",
        entity_type="integration",
        entity_id=entity_id,
        payload={
            "before": (
                _integration_snapshot(existing, masked_before) if existing is not None else None
            ),
            "after": _integration_snapshot(saved, masked_after),
        },
        actor_user_id=user_id,
    )
    return _format_integration_row(saved, masked_after)


def _execute_save_integration(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    require_member(conn, workspace_id=params["workspace_id"], user_id=params["user_id"])
    with conn.transaction():
        return _upsert_integration_inside_tx(conn, params)


def list_integration_presets() -> list[dict[str, Any]]:
    """Return all predefined integration stack presets (ADR-031)."""
    from engine.integrations.presets import list_presets

    return list_presets()


def apply_integration_preset(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    preset_name: str,
    port_overrides: dict[str, dict[str, Any]] | None = None,
    encryption_key: str | None = None,
) -> list[dict[str, Any]]:
    """Atomically apply a stack preset across all its ports and emit integration.preset_applied."""
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "preset_name": preset_name,
        "port_overrides": port_overrides or {},
        "encryption_key": encryption_key,
    }
    if conn is not None:
        return _execute_apply_integration_preset(conn, payload)
    with db.get_connection() as managed_conn:
        return _execute_apply_integration_preset(managed_conn, payload)


def _execute_apply_integration_preset(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    from engine.integrations.presets import get_preset

    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    preset = get_preset(str(params["preset_name"]))
    overrides: dict[str, dict[str, Any]] = params["port_overrides"]

    results: list[dict[str, Any]] = []
    with conn.transaction():
        for port, plugin in preset["ports"].items():
            override = overrides.get(port, {})
            results.append(
                _upsert_integration_inside_tx(
                    conn,
                    {
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "port": port,
                        "plugin": plugin,
                        "settings": override.get("settings") or {},
                        "secrets": override.get("secrets") or {},
                        "encryption_key": params["encryption_key"],
                    },
                )
            )
        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type="integration.preset_applied",
            entity_type="workspace",
            entity_id=workspace_id,
            payload={"preset": preset["name"], "ports": preset["ports"]},
            actor_user_id=user_id,
        )
    return results


def test_integration(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    port: str,
    encryption_key: str | list[str] | None = None,
) -> dict[str, Any]:
    """Execute plugin healthcheck() and record last_test_ok / last_tested_at (F05 AC3, AC7)."""
    if conn is not None:
        return _execute_test_integration(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            port=port,
            encryption_key=encryption_key,
        )
    with db.get_connection() as managed_conn:
        return _execute_test_integration(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            port=port,
            encryption_key=encryption_key,
        )


def _execute_test_integration(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    port: str,
    encryption_key: str | list[str] | None,
) -> dict[str, Any]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    plugin_cls, settings_instance = plugin_config(
        conn,
        workspace_id=workspace_id,
        port=port,
        encryption_key=encryption_key,
    )
    now = datetime.now(UTC)
    try:
        _validate_integration_urls(settings_instance.model_dump())
        adapter = plugin_cls(settings=settings_instance)
        adapter.healthcheck()
    except Exception as exc:
        db.update_workspace_integration_test_status(
            conn,
            workspace_id=workspace_id,
            port=port,
            last_test_ok=False,
            last_tested_at=now,
        )
        raise ValueError(str(exc)) from exc

    updated = db.update_workspace_integration_test_status(
        conn,
        workspace_id=workspace_id,
        port=port,
        last_test_ok=True,
        last_tested_at=now,
    )
    if updated is None:
        msg = f"Failed to update test status for port '{port}'"
        raise RuntimeError(msg)
    return {
        "ok": True,
        "port": port,
        "plugin": str(updated["plugin"]),
        "last_test_ok": True,
        "last_tested_at": updated["last_tested_at"],
    }


def run_now(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Trigger manual 'Run now' creating a queued run row (F18 AC2)."""
    from engine import scheduler

    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return scheduler.trigger_campaign_run(
            conn,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            trigger="manual",
            created_by=user_id,
        )

    with db.get_connection() as new_conn:
        require_member(new_conn, workspace_id=workspace_id, user_id=user_id)
        return scheduler.trigger_campaign_run(
            new_conn,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            trigger="manual",
            created_by=user_id,
        )


def list_runs(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List runs for a workspace visible to member."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_runs(conn, workspace_id=workspace_id, campaign_id=campaign_id)

    with db.get_connection() as new_conn:
        require_member(new_conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_runs(new_conn, workspace_id=workspace_id, campaign_id=campaign_id)


def execute_run_pipeline(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Execute stages for a run."""
    from engine import pipeline

    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return pipeline.execute_run(conn, run_id, workspace_id=workspace_id, offline=offline)

    with db.get_connection() as new_conn:
        require_member(new_conn, workspace_id=workspace_id, user_id=user_id)
        return pipeline.execute_run(new_conn, run_id, workspace_id=workspace_id, offline=offline)


def get_run(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve run details for a member (F18)."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
    with db.get_connection() as new_conn:
        require_member(new_conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_run(new_conn, workspace_id=workspace_id, run_id=run_id)


def resolve_workspace(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_identifier: str | None = None,
) -> dict[str, Any]:
    """Resolve a workspace by UUID or slug (or first workspace if omitted)."""

    def _resolve(c: psycopg.Connection[Any]) -> dict[str, Any]:
        ws = None
        if not workspace_identifier:
            all_ws = db.list_workspaces(c)
            if not all_ws:
                msg = "No workspaces found"
                raise ValueError(msg)
            ws = all_ws[0]
        else:
            try:
                ws_id = uuid.UUID(workspace_identifier)
                ws = db.get_workspace(c, workspace_id=ws_id)
            except ValueError:
                ws = db.get_workspace_by_slug(c, slug=workspace_identifier)

        if not ws:
            msg = f"Workspace '{workspace_identifier}' not found"
            raise ValueError(msg)

        ws_id = uuid.UUID(str(ws["workspace_id"]))
        owner = db.get_workspace_owner(c, workspace_id=ws_id)
        owner_id = uuid.UUID(str(owner["user_id"])) if owner else None
        res = dict(ws)
        res["owner_user_id"] = owner_id
        return res

    if conn is not None:
        return _resolve(conn)
    with db.get_connection() as managed_conn:
        return _resolve(managed_conn)


def _find_campaign(
    campaigns: list[dict[str, Any]], campaign_identifier: str | None
) -> dict[str, Any] | None:
    if campaign_identifier is None:
        return campaigns[0] if campaigns else None

    try:
        c_id = uuid.UUID(campaign_identifier)
        for camp in campaigns:
            if camp["id"] == c_id:
                return camp
    except ValueError:
        pass

    for camp in campaigns:
        if camp.get("name") == campaign_identifier:
            return camp

    return None


def resolve_campaign(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    campaign_identifier: str | None = None,
) -> dict[str, Any]:
    """Resolve a campaign by UUID or name within a workspace, defaulting to the first campaign."""

    def _resolve(c: psycopg.Connection[Any]) -> dict[str, Any]:
        campaigns = db.list_campaigns(c, workspace_id=workspace_id)
        if not campaigns:
            msg = f"No campaigns found for workspace {workspace_id}"
            raise ValueError(msg)

        found = _find_campaign(campaigns, campaign_identifier)
        if found is not None:
            return found

        msg = f"Campaign '{campaign_identifier}' not found in workspace {workspace_id}"
        raise ValueError(msg)

    if conn is not None:
        return _resolve(conn)
    with db.get_connection() as managed_conn:
        return _resolve(managed_conn)


def run_week(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_identifier: str,
    campaign_identifier: str | None = None,
    user_id: uuid.UUID | None = None,
    offline: bool = False,
    window_days: int = 7,
) -> dict[str, Any]:
    """Trigger and execute a weekly run for a campaign (F18 AC1-AC2)."""
    from engine import pipeline, scheduler

    def _exec(c: psycopg.Connection[Any]) -> dict[str, Any]:
        ws = resolve_workspace(c, workspace_identifier=workspace_identifier)
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        actor_id = user_id or ws.get("owner_user_id")
        if not actor_id:
            msg = f"No user or owner found for workspace '{workspace_identifier}'"
            raise ValueError(msg)

        camp = resolve_campaign(c, workspace_id=ws_id, campaign_identifier=campaign_identifier)
        camp_id = uuid.UUID(str(camp["id"]))

        run_id = scheduler.trigger_campaign_run(
            c,
            workspace_id=ws_id,
            campaign_id=camp_id,
            trigger="manual",
            window_days=window_days,
            created_by=actor_id,
        )

        adapters: dict[str, Any] = {}
        if offline:
            from engine.integrations.fakes import (
                FakeExtractor,
                FakeFeedSource,
                FakeLLM,
                FakeNewsSearch,
            )

            adapters = {
                "feed_adapter": FakeFeedSource(),
                "news_search_adapter": FakeNewsSearch(),
                "extractor_adapter": FakeExtractor(),
                "llm_adapter": FakeLLM(),
            }

        result_run = pipeline.execute_run(
            c,
            run_id,
            workspace_id=ws_id,
            offline=offline,
            **adapters,
        )
        if result_run.get("status") == "completed":
            report(c, workspace_id=ws_id, run_id=run_id)
        return result_run

    if conn is not None:
        return _exec(conn)
    with db.get_connection() as managed_conn:
        return _exec(managed_conn)


def _run_stage_research_record(
    c: psycopg.Connection[Any],
    *,
    rec_ws: uuid.UUID,
    target_id: uuid.UUID,
    offline: bool,
) -> dict[str, Any]:
    from engine import research

    adapters: dict[str, Any] = {}
    if offline:
        from engine.integrations.fakes import FakeExtractor, FakeLLM

        adapters = {"extractor_adapter": FakeExtractor(), "llm_adapter": FakeLLM()}
    re_run_rec = research.re_run_failed_record(
        c,
        workspace_id=rec_ws,
        record_id=target_id,
        **adapters,
    )
    return {"record": re_run_rec, "type": "research_record"}


def _run_stage_pipeline_run(  # noqa: PLR0913
    c: psycopg.Connection[Any],
    *,
    run_ws: uuid.UUID,
    target_id: uuid.UUID,
    run_row: dict[str, Any],
    stage: str | None,
    offline: bool,
) -> dict[str, Any]:
    from engine import pipeline

    target_stage = stage or str(run_row.get("stage") or "research")
    adapters_pipeline: dict[str, Any] = {}
    if offline:
        from engine.integrations.fakes import (
            FakeExtractor,
            FakeFeedSource,
            FakeLLM,
            FakeNewsSearch,
        )

        adapters_pipeline = {
            "feed_adapter": FakeFeedSource(),
            "news_search_adapter": FakeNewsSearch(),
            "extractor_adapter": FakeExtractor(),
            "llm_adapter": FakeLLM(),
        }

    re_run_items = pipeline.re_run_failed_stage_items(
        c,
        workspace_id=run_ws,
        run_id=target_id,
        stage=target_stage,
        offline=offline,
        extractor_adapter=adapters_pipeline.get("extractor_adapter"),
        llm_adapter=adapters_pipeline.get("llm_adapter"),
    )

    if run_row.get("status") in ("blocked", "failed", "queued"):
        result_run = pipeline.execute_run(
            c,
            target_id,
            workspace_id=run_ws,
            offline=offline,
            **adapters_pipeline,
        )
    else:
        result_run = db.get_run(c, workspace_id=run_ws, run_id=target_id) or run_row

    return {
        "run": result_run,
        "re_run_items_count": len(re_run_items),
        "type": "run",
    }


def run_stage(
    conn: psycopg.Connection[Any] | None = None,
    *,
    stage_or_record_id: str | uuid.UUID,
    stage: str | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Re-run a failed record or re-run/resume a stage in a run (F18 AC3, AC4)."""

    def _exec(c: psycopg.Connection[Any]) -> dict[str, Any]:
        target_id = (
            uuid.UUID(stage_or_record_id)
            if isinstance(stage_or_record_id, str)
            else stage_or_record_id
        )

        rec_ws = db.get_research_record_workspace_id(c, record_id=target_id)
        if rec_ws is not None:
            return _run_stage_research_record(
                c, rec_ws=rec_ws, target_id=target_id, offline=offline
            )

        run_ws = db.get_run_workspace_id(c, run_id=target_id)
        if run_ws is None:
            msg = f"No run or research record found with ID '{stage_or_record_id}'"
            raise ValueError(msg)

        run_row = db.get_run(c, workspace_id=run_ws, run_id=target_id)
        if not run_row:
            msg = f"Run '{target_id}' not found"
            raise ValueError(msg)

        return _run_stage_pipeline_run(
            c,
            run_ws=run_ws,
            target_id=target_id,
            run_row=run_row,
            stage=stage,
            offline=offline,
        )

    if conn is not None:
        return _exec(conn)
    with db.get_connection() as managed_conn:
        return _exec(managed_conn)


def generate_crontab(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID | None = None,
) -> str:
    """Generate Supercronic crontab for scheduled runs (F18 AC1)."""
    from engine import scheduler

    if conn is not None:
        return scheduler.generate_crontab(conn, workspace_id=workspace_id)
    with db.get_connection() as managed_conn:
        return scheduler.generate_crontab(managed_conn, workspace_id=workspace_id)


def trigger_scheduled_runs(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Trigger any scheduled runs for active campaigns (F18 AC1)."""
    from engine import scheduler

    if conn is not None:
        return scheduler.trigger_due_scheduled_runs(conn, workspace_id=workspace_id)
    with db.get_connection() as managed_conn:
        return scheduler.trigger_due_scheduled_runs(managed_conn, workspace_id=workspace_id)


def score_run(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    offline: bool = False,
) -> list[dict[str, Any]]:
    """Score verified research records for a run (F09 AC1)."""

    def _exec(c: psycopg.Connection[Any]) -> list[dict[str, Any]]:
        from engine.strategy import score

        run_row = db.get_run(c, workspace_id=workspace_id, run_id=run_id)
        if not run_row:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)
        llm_adapter = None
        if offline:
            from engine.integrations.fakes import FakeLLM

            llm_adapter = FakeLLM()
        return score.score_run(c, run_row, llm_adapter=llm_adapter)

    if conn is not None:
        return _exec(conn)
    with db.get_connection() as managed_conn:
        return _exec(managed_conn)


def _format_report_item(
    item: dict[str, Any],
    idx: int,
    is_recommended: bool,
    *,
    already_covered: bool = False,
    attachments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format single opportunity row into report item contract (F09 AC3, AC4, F10 AC5)."""
    payload = item.get("record_payload") or {}
    headline = payload.get("headline") or item.get("document_title") or "Opportunity"
    summary = payload.get("summary") or ""
    date_status = str(item.get("date_status") or "unverified").lower()
    pub_at = item.get("published_at")
    extra = attachments or {}

    return {
        "id": item["id"],
        "rank": item.get("rank") or (idx + 1),
        "title": headline,
        "summary": summary,
        "angle": item.get("angle", ""),
        "funnel_stage": item.get("funnel_stage", "mofu"),
        "confidence": item.get("confidence", "medium"),
        "total": float(item.get("total") or 0.0),
        "scores": item.get("scores") or {},
        "original_url": item.get("original_url", ""),
        "published_at": pub_at,
        "date_status": date_status,
        "is_verified_date": date_status == "verified",
        "is_recommended": is_recommended,
        "already_covered": already_covered,
        "status": item.get("status", "scored"),
        "decision_note": item.get("decision_note"),
        "decided_by": item.get("decided_by"),
        "decided_at": item.get("decided_at"),
        "is_rejected_story": bool(item.get("is_rejected_story")),
        "latest_brief": extra.get("latest_brief"),
        "latest_draft": extra.get("latest_draft"),
    }


def _emit_awaiting_approval_if_needed(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    camp_id: uuid.UUID | None,
    items: list[dict[str, Any]],
    creator_id: uuid.UUID | None,
) -> None:
    """Emit topics.awaiting_approval event idempotently if opportunities exist (F09 AC5)."""
    if not items:
        return
    existing = db.list_events(
        conn,
        workspace_id=workspace_id,
        event_type="topics.awaiting_approval",
        entity_id=run_id,
    )
    if existing:
        return
    rec_ids = [str(it["id"]) for it in items if it["is_recommended"]]
    events.emit(
        conn,
        workspace_id=workspace_id,
        event_type="topics.awaiting_approval",
        entity_type="run",
        entity_id=run_id,
        payload={
            "run_id": str(run_id),
            "campaign_id": str(camp_id) if camp_id else None,
            "recommended_count": len(rec_ids),
            "total_opportunities": len(items),
            "top_opportunity_ids": rec_ids,
        },
        actor_user_id=creator_id,
    )


def _build_report_items_for_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    raw_items: list[dict[str, Any]],
    articles_per_week: int,
) -> tuple[list[dict[str, Any]], int]:
    """Format ranked report items and compute recommendation eligibility."""
    rec_cap = max(3, articles_per_week)
    latest_briefs = db.list_latest_briefs_for_run(conn, workspace_id=workspace_id, run_id=run_id)
    latest_drafts = db.list_latest_drafts_for_run(conn, workspace_id=workspace_id, run_id=run_id)
    items: list[dict[str, Any]] = []
    rec_count = 0
    for idx, it in enumerate(raw_items):
        payload = it.get("record_payload") or {}
        headline = payload.get("headline") or it.get("document_title") or ""
        similar_pub = db.find_similar_publication(conn, workspace_id=workspace_id, title=headline)
        is_covered = similar_pub is not None
        is_rejected = bool(it.get("is_rejected_story")) or it.get("status") == "rejected"
        is_eligible = not is_rejected and not is_covered and it.get("status") == "scored"
        is_rec = is_eligible and rec_count < rec_cap
        if is_rec:
            rec_count += 1
        opp_uuid = uuid.UUID(str(it["id"]))
        items.append(
            _format_report_item(
                it,
                idx,
                is_rec,
                already_covered=is_covered,
                attachments={
                    "latest_brief": latest_briefs.get(opp_uuid),
                    "latest_draft": latest_drafts.get(opp_uuid),
                },
            )
        )
    return items, rec_count


def _execute_report(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Execute weekly report query, recommendation capping, and event emission."""
    run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
    if not run_row:
        msg = f"Run {run_id} not found"
        raise ValueError(msg)

    camp_id = run_row.get("campaign_id")
    camp_row = (
        db.get_campaign(conn, workspace_id=workspace_id, campaign_id=camp_id) if camp_id else None
    )
    camp_name = camp_row.get("name", "Campaign") if camp_row else "Campaign"
    articles_per_week = int(camp_row.get("articles_per_week", 1)) if camp_row else 1

    raw_items = db.get_opportunity_report_items(
        conn, workspace_id=workspace_id, run_id=run_id, limit=10
    )
    items, rec_count = _build_report_items_for_run(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        raw_items=raw_items,
        articles_per_week=articles_per_week,
    )

    _emit_awaiting_approval_if_needed(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        camp_id=camp_id,
        items=items,
        creator_id=run_row.get("created_by"),
    )

    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "campaign_id": camp_id,
        "campaign_name": camp_name,
        "articles_per_week": articles_per_week,
        "recommended_count": rec_count,
        "total_count": len(items),
        "items": items,
    }


def report(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Compile weekly opportunity report with recommendation capping and event emission (F09)."""
    if conn is not None:
        return _execute_report(conn, workspace_id=workspace_id, run_id=run_id)
    with db.get_connection() as managed_conn:
        return _execute_report(managed_conn, workspace_id=workspace_id, run_id=run_id)


def _normalize_decision_value(
    decision: str,
    *,
    positive: tuple[str, ...],
    positive_target: str,
    negative: tuple[str, ...],
    negative_target: str,
) -> str:
    """Normalize gate decision string to canonical target values."""
    norm = decision.strip().lower()
    if norm in positive:
        return positive_target
    if norm in negative:
        return negative_target
    msg = f"Invalid decision: '{decision}', expected '{positive_target}' or '{negative_target}'"
    raise ValueError(msg)


def _normalize_topic_decision(decision: str) -> str:
    """Normalize and validate topic decision string."""
    return _normalize_decision_value(
        decision,
        positive=("approve", "approved"),
        positive_target="approved",
        negative=("reject", "rejected"),
        negative_target="rejected",
    )


def _normalize_draft_decision(decision: str) -> str:
    """Normalize and validate Gate 2 draft decision string."""
    return _normalize_decision_value(
        decision,
        positive=("approve", "approved"),
        positive_target="approved",
        negative=("decline", "declined"),
        negative_target="declined",
    )


def _validate_topic_approver(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> dict[str, Any]:
    """Verify user is an owner and matches campaign creator (ADR-013, F10 AC1, AC2)."""
    role = require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if role != "owner":
        msg = f"User {user_id} with role '{role}' is forbidden from deciding topics"
        raise PermissionError(msg)

    campaign = db.get_campaign(conn, workspace_id=workspace_id, campaign_id=campaign_id)
    if not campaign:
        msg = f"Campaign {campaign_id} not found"
        raise ValueError(msg)

    creator_id = campaign.get("created_by")
    if str(user_id) != str(creator_id):
        msg = (
            f"User {user_id} is forbidden from deciding topics for campaign {campaign_id}; "
            f"only campaign creator {creator_id} is authorized"
        )
        raise PermissionError(msg)
    return campaign


def _execute_decide_topic(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    decision: str,
    user_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any]:
    """Execute topic approval or rejection with guarded transition, locks, and events."""
    norm_decision = _normalize_topic_decision(decision)

    opp = db.get_opportunity(conn, workspace_id=workspace_id, opportunity_id=opportunity_id)
    if not opp:
        msg = f"Opportunity {opportunity_id} not found"
        raise ValueError(msg)

    current_status = opp.get("status")
    if current_status != "scored":
        msg = f"Opportunity {opportunity_id} is in status '{current_status}', expected 'scored'"
        raise ValueError(msg)

    run_id = opp["run_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT campaign_id FROM runs
            WHERE workspace_id = %(workspace_id)s AND id = %(run_id)s
            FOR UPDATE
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        run_row = cur.fetchone()
        if not run_row:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)
        campaign_id = run_row[0]

    campaign = _validate_topic_approver(
        conn,
        workspace_id=workspace_id,
        user_id=user_id,
        campaign_id=campaign_id,
    )

    if norm_decision == "approved":
        articles_per_week = int(campaign.get("articles_per_week", 1))
        approved_count = db.count_approved_opportunities(
            conn, workspace_id=workspace_id, run_id=run_id
        )
        if approved_count >= articles_per_week:
            msg = (
                f"Approval limit reached: campaign allows {articles_per_week} "
                f"approved topic(s) per run"
            )
            raise ValueError(msg)

    decided_at = datetime.now(UTC)
    updated_opp = db.decide_opportunity(
        conn,
        workspace_id=workspace_id,
        opportunity_id=opportunity_id,
        decision=norm_decision,
        user_id=user_id,
        decided_at=decided_at,
        note=note,
    )
    if updated_opp is None:
        msg = (
            f"Opportunity {opportunity_id} could not be updated "
            "(concurrent update or status changed)"
        )
        raise ValueError(msg)

    events.emit(
        conn,
        workspace_id=workspace_id,
        event_type="topic.decided",
        entity_type="opportunity",
        entity_id=opportunity_id,
        payload={
            "opportunity_id": str(opportunity_id),
            "run_id": str(run_id),
            "campaign_id": str(campaign_id),
            "decision": norm_decision,
            "note": note,
            "rank": opp.get("rank"),
            "total": float(opp.get("total") or 0.0),
            "brief_queued": norm_decision == "approved",
        },
        actor_user_id=user_id,
    )
    return updated_opp


def decide_topic(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    decision: str,
    user_id: uuid.UUID,
    note: str | None = None,
    auto_brief: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    """Approve or reject a scored opportunity and optionally generate its brief (F10, F12)."""

    def _run(c: psycopg.Connection[Any]) -> dict[str, Any]:
        updated = _execute_decide_topic(
            c,
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            decision=decision,
            user_id=user_id,
            note=note,
        )
        if auto_brief and updated.get("status") == "approved":
            updated["brief"] = _execute_generate_brief(
                c,
                {
                    "workspace_id": workspace_id,
                    "opportunity_id": opportunity_id,
                    "user_id": user_id,
                    "offline": offline,
                },
            )
        return updated

    if conn is not None:
        return _run(conn)
    with db.get_connection() as managed_conn:
        return _run(managed_conn)


def _execute_generate_brief(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from engine import strategy

    workspace_id: uuid.UUID = params["workspace_id"]
    opportunity_id: uuid.UUID = params["opportunity_id"]
    user_id: uuid.UUID = params["user_id"]
    require_member(conn, workspace_id=workspace_id, user_id=user_id)

    return strategy.brief(
        opportunity_id,
        conn=conn,
        workspace_id=workspace_id,
        revision_findings=params.get("revision_findings"),
        actor_user_id=user_id,
        offline=bool(params.get("offline", False)),
        llm_adapter=params.get("llm_adapter"),
        keyword_provider=params.get("keyword_provider"),
        news_search_adapter=params.get("news_search_adapter"),
        extractor_adapter=params.get("extractor_adapter"),
    )


def generate_brief(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    user_id: uuid.UUID,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    keyword_provider: KeywordProviderPort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
) -> dict[str, Any]:
    """Generate a grounded, brand-pinned editorial brief for an approved topic (F12 AC1-AC3)."""
    params = {
        "workspace_id": workspace_id,
        "opportunity_id": opportunity_id,
        "user_id": user_id,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "keyword_provider": keyword_provider,
        "news_search_adapter": news_search_adapter,
        "extractor_adapter": extractor_adapter,
    }
    if conn is not None:
        return _execute_generate_brief(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_generate_brief(managed_conn, params)


def _execute_revise_brief(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    opportunity_id: uuid.UUID = params["opportunity_id"]
    user_id: uuid.UUID = params["user_id"]
    findings: list[dict[str, Any] | str] = list(params.get("findings") or [])

    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if not findings:
        msg = "Revision findings cannot be empty when routing REVISION to brief"
        raise ValueError(msg)

    existing = db.get_latest_brief_for_opportunity(
        conn, workspace_id=workspace_id, opportunity_id=opportunity_id
    )
    if existing is None:
        msg = f"Cannot revise brief for opportunity {opportunity_id}: no prior brief version exists"
        raise ValueError(msg)

    params_with_findings = {**params, "revision_findings": findings}
    return _execute_generate_brief(conn, params_with_findings)


def revise_brief(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    user_id: uuid.UUID,
    findings: list[dict[str, Any] | str],
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    keyword_provider: KeywordProviderPort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
) -> dict[str, Any]:
    """Create a new brief version (v+1) incorporating evaluator REVISION findings (F12 AC4)."""
    params = {
        "workspace_id": workspace_id,
        "opportunity_id": opportunity_id,
        "user_id": user_id,
        "findings": findings,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "keyword_provider": keyword_provider,
        "news_search_adapter": news_search_adapter,
        "extractor_adapter": extractor_adapter,
    }
    if conn is not None:
        return _execute_revise_brief(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_revise_brief(managed_conn, params)


def get_latest_brief(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return latest brief version for an opportunity visible to workspace member."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_latest_brief_for_opportunity(
            conn, workspace_id=workspace_id, opportunity_id=opportunity_id
        )
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_latest_brief_for_opportunity(
            managed_conn, workspace_id=workspace_id, opportunity_id=opportunity_id
        )


def list_briefs(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    opportunity_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List brief versions for a workspace (or single opportunity), verifying membership."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_briefs(conn, workspace_id=workspace_id, opportunity_id=opportunity_id)
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_briefs(
            managed_conn, workspace_id=workspace_id, opportunity_id=opportunity_id
        )


def _execute_write_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from engine.production import writer as writer_module

    workspace_id: uuid.UUID = params["workspace_id"]
    brief_id: uuid.UUID = params["brief_id"]
    user_id: uuid.UUID = params["user_id"]
    require_member(conn, workspace_id=workspace_id, user_id=user_id)

    return writer_module.write(
        brief_id,
        params.get("findings"),
        conn=conn,
        workspace_id=workspace_id,
        actor_user_id=user_id,
        offline=bool(params.get("offline", False)),
        llm_adapter=params.get("llm_adapter"),
        model=params.get("model"),
    )


def write_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
    user_id: uuid.UUID,
    findings: list[Any] | None = None,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate and persist a sourced article draft and claims for a brief (F13 AC1-AC3, AC5)."""
    params = {
        "workspace_id": workspace_id,
        "brief_id": brief_id,
        "user_id": user_id,
        "findings": findings,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "model": model,
    }
    if conn is not None:
        return _execute_write_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_write_draft(managed_conn, params)


def _execute_revise_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    brief_id: uuid.UUID = params["brief_id"]
    user_id: uuid.UUID = params["user_id"]
    findings: list[Any] = list(params.get("findings") or [])

    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if not findings:
        msg = "Revision findings cannot be empty when revising a draft"
        raise ValueError(msg)

    existing = db.get_latest_draft_for_brief(conn, workspace_id=workspace_id, brief_id=brief_id)
    if existing is None:
        msg = f"Cannot revise draft for brief {brief_id}: no prior draft revision exists"
        raise ValueError(msg)

    return _execute_write_draft(conn, {**params, "findings": findings})


def revise_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
    user_id: uuid.UUID,
    findings: list[Any],
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Create a new draft revision (revision+1, max 2) with evaluator findings (F13 AC3)."""
    params = {
        "workspace_id": workspace_id,
        "brief_id": brief_id,
        "user_id": user_id,
        "findings": findings,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "model": model,
    }
    if conn is not None:
        return _execute_revise_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_revise_draft(managed_conn, params)


def get_draft(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return a draft row by ID visible to a workspace member."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_draft(conn, workspace_id=workspace_id, draft_id=draft_id)
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_draft(managed_conn, workspace_id=workspace_id, draft_id=draft_id)


def get_latest_draft(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the latest draft revision for a brief visible to a workspace member."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_latest_draft_for_brief(conn, workspace_id=workspace_id, brief_id=brief_id)
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.get_latest_draft_for_brief(
            managed_conn, workspace_id=workspace_id, brief_id=brief_id
        )


def list_drafts(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    brief_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List draft revisions for a workspace (or single brief), verifying membership."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_drafts(conn, workspace_id=workspace_id, brief_id=brief_id)
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_drafts(managed_conn, workspace_id=workspace_id, brief_id=brief_id)


def list_draft_claims(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List persisted draft_claims with source document metadata, verifying membership."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_draft_claims(conn, workspace_id=workspace_id, draft_id=draft_id)
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_draft_claims(managed_conn, workspace_id=workspace_id, draft_id=draft_id)


def _execute_evaluate_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from engine.quality import EvalExecutionSpec, evaluate_persisted_draft

    ws_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    require_member(conn, workspace_id=ws_id, user_id=user_id)
    spec = EvalExecutionSpec(
        workspace_id=ws_id,
        draft_id=params["draft_id"],
        offline=bool(params.get("offline", False)),
        llm_adapter=params.get("llm_adapter"),
        llm_evaluator=params.get("llm_evaluator"),
        model=params.get("model"),
        actor_user_id=user_id,
        options=params.get("options"),
        deterministic_only=bool(params.get("deterministic_only", False)),
    )
    return evaluate_persisted_draft(conn, spec)


def evaluate_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    llm_evaluator: Any = None,
    model: str | None = None,
    options: Any = None,
    deterministic_only: bool = False,
) -> dict[str, Any]:
    """Run deterministic and LLM rubric evaluation on a draft revision (F14 AC1-AC2, AC6)."""
    params = {
        "workspace_id": workspace_id,
        "draft_id": draft_id,
        "user_id": user_id,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "llm_evaluator": llm_evaluator,
        "model": model,
        "options": options,
        "deterministic_only": deterministic_only,
    }
    if conn is not None:
        return _execute_evaluate_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_evaluate_draft(managed_conn, params)


def _execute_evaluate_and_route(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    ws_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    require_member(conn, workspace_id=ws_id, user_id=user_id)

    current_draft_id: uuid.UUID = params["draft_id"]
    history: list[dict[str, Any]] = []
    while True:
        outcome = _execute_evaluate_draft(conn, {**params, "draft_id": current_draft_id})
        history.append(outcome)
        route = outcome["route"]
        if route in {"pass", "research"} or outcome["surfaced_to_owner"]:
            final_verdict = (
                "REJECT" if outcome["topic_returned_to_pool"] else outcome["report"].verdict
            )
            return {
                "verdict": final_verdict,
                "status": outcome["draft"]["status"],
                "final_draft_id": current_draft_id,
                "report": outcome["report"],
                "quality_report": outcome["quality_report"],
                "surfaced_to_owner": outcome["surfaced_to_owner"],
                "topic_returned_to_pool": outcome["topic_returned_to_pool"],
                "history": history,
            }

        ctx = outcome["context"]
        finding_dicts = [f.model_dump(mode="json") for f in outcome["report"].findings]
        if route == "brief":
            next_brief = _execute_revise_brief(
                conn,
                {
                    **params,
                    "opportunity_id": uuid.UUID(str(ctx["opportunity_id"])),
                    "findings": finding_dicts,
                },
            )
            next_draft = _execute_write_draft(
                conn,
                {
                    **params,
                    "brief_id": uuid.UUID(str(next_brief["id"])),
                    "findings": finding_dicts,
                },
            )
        else:
            next_draft = _execute_revise_draft(
                conn,
                {
                    **params,
                    "brief_id": uuid.UUID(str(ctx["brief_id"])),
                    "findings": finding_dicts,
                },
            )
        current_draft_id = uuid.UUID(str(next_draft["id"]))


def evaluate_and_route_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    llm_evaluator: Any = None,
    keyword_provider: KeywordProviderPort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Evaluate draft and execute bounded revision routing (<=2 loops) to brief/write/pool."""
    params = {
        "workspace_id": workspace_id,
        "draft_id": draft_id,
        "user_id": user_id,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "llm_evaluator": llm_evaluator,
        "keyword_provider": keyword_provider,
        "news_search_adapter": news_search_adapter,
        "extractor_adapter": extractor_adapter,
        "model": model,
    }
    if conn is not None:
        return _execute_evaluate_and_route(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_evaluate_and_route(managed_conn, params)


def _execute_produce_and_evaluate_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    written = _execute_write_draft(conn, params)
    return _execute_evaluate_and_route(
        conn,
        {**params, "draft_id": uuid.UUID(str(written["id"]))},
    )


def produce_and_evaluate_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
    user_id: uuid.UUID,
    offline: bool = False,
    llm_adapter: LLMPort | None = None,
    llm_evaluator: Any = None,
    keyword_provider: KeywordProviderPort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Write a draft from a brief and run bounded quality evaluation routing (F13, F14, F15)."""
    params = {
        "workspace_id": workspace_id,
        "brief_id": brief_id,
        "user_id": user_id,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "llm_evaluator": llm_evaluator,
        "keyword_provider": keyword_provider,
        "news_search_adapter": news_search_adapter,
        "extractor_adapter": extractor_adapter,
        "model": model,
    }
    if conn is not None:
        return _execute_produce_and_evaluate_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_produce_and_evaluate_draft(managed_conn, params)


def _is_surfaced_after_max_revisions(
    *,
    status: str | None,
    revision: int | None,
    quality_verdict: str | None,
) -> bool:
    """Return True only when a draft at revision >= 2 was surfaced despite REVISION verdict."""
    return (
        status == "pass"
        and int(revision or 0) >= 2
        and str(quality_verdict or "").lower() == "revision"
    )


def _ensure_draft_awaiting_approval(
    conn: psycopg.Connection[Any],
    *,
    meta: dict[str, Any],
    actor_user_id: uuid.UUID | None,
) -> None:
    """Emit draft.awaiting_approval idempotently for drafts awaiting Gate 2 review (F15 AC1)."""
    workspace_id = uuid.UUID(str(meta["workspace_id"]))
    draft_id = uuid.UUID(str(meta["draft_id"]))
    existing = db.list_events(
        conn,
        workspace_id=workspace_id,
        event_type="draft.awaiting_approval",
        entity_id=draft_id,
    )
    if existing:
        return
    revision = int(meta["revision"])
    quality_verdict = meta.get("quality_verdict")
    quality_score = meta.get("quality_score")
    verdict_upper = str(quality_verdict or "pass").upper()
    score_val = float(quality_score) if quality_score is not None else None
    surfaced = _is_surfaced_after_max_revisions(
        status="pass",
        revision=revision,
        quality_verdict=quality_verdict,
    )
    events.emit(
        conn,
        workspace_id=workspace_id,
        event_type="draft.awaiting_approval",
        entity_type="draft",
        entity_id=draft_id,
        payload={
            "draft_id": str(draft_id),
            "brief_id": str(meta["brief_id"]),
            "opportunity_id": str(meta["opportunity_id"]),
            "run_id": str(meta["run_id"]),
            "revision": revision,
            "verdict": verdict_upper,
            "score": score_val,
            "surfaced_after_max_revisions": surfaced,
        },
        actor_user_id=actor_user_id,
    )


def _execute_list_draft_reviews(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    include_decided: bool,
) -> list[dict[str, Any]]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    statuses: tuple[str, ...] = (
        ("pass", "approved", "declined", "published") if include_decided else ("pass",)
    )
    rows = db.list_drafts_for_review(conn, workspace_id=workspace_id, statuses=statuses)
    for row in rows:
        if row["status"] == "pass":
            _ensure_draft_awaiting_approval(
                conn,
                meta={
                    "workspace_id": workspace_id,
                    "draft_id": row["id"],
                    "brief_id": row["brief_id"],
                    "opportunity_id": row["opportunity_id"],
                    "run_id": row["run_id"],
                    "revision": row["revision"],
                    "quality_verdict": row.get("quality_verdict"),
                    "quality_score": row.get("quality_score"),
                },
                actor_user_id=user_id,
            )
        row["surfaced_after_max_revisions"] = _is_surfaced_after_max_revisions(
            status=row.get("status"),
            revision=row.get("revision"),
            quality_verdict=row.get("quality_verdict"),
        )
    return rows


def list_draft_reviews(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    include_decided: bool = True,
) -> list[dict[str, Any]]:
    """List drafts in the Gate 2 review queue and recent decisions (F15 AC1)."""
    if conn is not None:
        return _execute_list_draft_reviews(
            conn, workspace_id=workspace_id, user_id=user_id, include_decided=include_decided
        )
    with db.get_connection() as managed_conn:
        return _execute_list_draft_reviews(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            include_decided=include_decided,
        )


def _execute_get_draft_review_bundle(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    draft_row = db.get_draft(conn, workspace_id=workspace_id, draft_id=draft_id)
    ctx = db.get_draft_evaluation_context(conn, workspace_id=workspace_id, draft_id=draft_id)
    if draft_row is None or ctx is None:
        msg = f"Draft {draft_id} not found"
        raise ValueError(msg)

    opp_id = uuid.UUID(str(ctx["opportunity_id"]))
    run_id = uuid.UUID(str(ctx["run_id"]))
    quality_report = db.get_quality_report(conn, workspace_id=workspace_id, draft_id=draft_id)
    claims = db.list_draft_claims(conn, workspace_id=workspace_id, draft_id=draft_id)
    revision_history = db.list_draft_revision_history_for_opportunity(
        conn, workspace_id=workspace_id, opportunity_id=opp_id
    )
    cost_breakdown = db.get_run_usage_breakdown(conn, workspace_id=workspace_id, run_id=run_id)
    total_run_cost = sum(
        (Decimal(str(item["cost_usd"])) for item in cost_breakdown),
        Decimal("0.000000"),
    )

    if draft_row["status"] == "pass":
        _ensure_draft_awaiting_approval(
            conn,
            meta={
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "brief_id": ctx["brief_id"],
                "opportunity_id": opp_id,
                "run_id": run_id,
                "revision": draft_row["revision"],
                "quality_verdict": quality_report.get("verdict") if quality_report else None,
                "quality_score": quality_report.get("score") if quality_report else None,
            },
            actor_user_id=user_id,
        )

    publication = db.get_publication_for_draft(conn, workspace_id=workspace_id, draft_id=draft_id)
    publications = db.list_publications_for_draft(
        conn, workspace_id=workspace_id, draft_id=draft_id
    )
    has_publisher = db.get_workspace_integration(conn, workspace_id=workspace_id, port="publisher")
    surfaced = _is_surfaced_after_max_revisions(
        status=draft_row["status"],
        revision=draft_row["revision"],
        quality_verdict=quality_report.get("verdict") if quality_report else None,
    )
    return {
        "draft": draft_row,
        "context": ctx,
        "claims": claims,
        "quality_report": quality_report,
        "revision_history": revision_history,
        "cost_breakdown": cost_breakdown,
        "total_run_cost_usd": total_run_cost,
        "surfaced_after_max_revisions": surfaced,
        "publication": publication,
        "publications": publications,
        "has_publisher": has_publisher is not None,
    }


def get_draft_review_bundle(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Load complete Gate 2 review bundle (draft, claims+sources, report, history, cost)."""
    if conn is not None:
        return _execute_get_draft_review_bundle(
            conn, workspace_id=workspace_id, draft_id=draft_id, user_id=user_id
        )
    with db.get_connection() as managed_conn:
        return _execute_get_draft_review_bundle(
            managed_conn, workspace_id=workspace_id, draft_id=draft_id, user_id=user_id
        )


def _execute_decide_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    decision: str,
    user_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any]:
    norm_decision = _normalize_draft_decision(decision)
    clean_note = note.strip() if isinstance(note, str) and note.strip() else None

    with conn.transaction():
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        ctx = db.get_draft_evaluation_context(conn, workspace_id=workspace_id, draft_id=draft_id)
        if ctx is None:
            msg = f"Draft {draft_id} not found"
            raise ValueError(msg)

        campaign_id = uuid.UUID(str(ctx["campaign_id"]))
        _validate_topic_approver(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            campaign_id=campaign_id,
        )

        current_status = str(ctx.get("status") or "")
        if current_status != "pass":
            msg = f"Draft {draft_id} is in status '{current_status}', expected 'pass'"
            raise ValueError(msg)

        updated = db.decide_draft(
            conn,
            workspace_id=workspace_id,
            draft_id=draft_id,
            decision=norm_decision,
            user_id=user_id,
            decided_at=datetime.now(UTC),
            note=clean_note,
        )
        if updated is None:
            msg = f"Draft {draft_id} could not be updated (concurrent update or status changed)"
            raise ValueError(msg)

        publish_queued = norm_decision == "approved"
        events.emit(
            conn,
            workspace_id=workspace_id,
            event_type="draft.decided",
            entity_type="draft",
            entity_id=draft_id,
            payload={
                "draft_id": str(draft_id),
                "brief_id": str(ctx["brief_id"]),
                "opportunity_id": str(ctx["opportunity_id"]),
                "run_id": str(ctx["run_id"]),
                "campaign_id": str(campaign_id),
                "revision": int(ctx["revision"]),
                "decision": norm_decision,
                "note": clean_note,
                "publish_queued": publish_queued,
            },
            actor_user_id=user_id,
        )

    publication: dict[str, Any] | None = None
    if publish_queued and (
        db.get_workspace_integration(conn, workspace_id=workspace_id, port="publisher") is not None
    ):
        publication = _execute_publish_draft(
            conn,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "user_id": user_id,
            },
        )
        updated = db.get_draft(conn, workspace_id=workspace_id, draft_id=draft_id) or updated

    return {**updated, "publish_queued": publish_queued, "publication": publication}


def decide_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    decision: str,
    user_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve or decline an evaluated draft with creator authorization and event (F15)."""
    if conn is not None:
        return _execute_decide_draft(
            conn,
            workspace_id=workspace_id,
            draft_id=draft_id,
            decision=decision,
            user_id=user_id,
            note=note,
        )
    with db.get_connection() as managed_conn:
        return _execute_decide_draft(
            managed_conn,
            workspace_id=workspace_id,
            draft_id=draft_id,
            decision=decision,
            user_id=user_id,
            note=note,
        )


def _is_staging_environment() -> bool:
    env_val = os.environ.get("APP_ENV") or os.environ.get("ENV") or ""
    return env_val.strip().lower() == "staging"


def _resolve_publish_mode(*, requested_mode: str | None, settings_obj: Any) -> str:
    if _is_staging_environment():
        return "draft"
    raw = requested_mode or getattr(settings_obj, "default_mode", "draft")
    return str(raw or "draft").strip().lower()


def _normalize_cta_for_publish(raw_cta: Any) -> dict[str, str]:
    if isinstance(raw_cta, dict):
        return {str(k): str(v) for k, v in raw_cta.items() if v is not None}
    text = str(raw_cta or "").strip()
    return {"text": text} if text else {}


def _normalize_faq_for_publish(raw_faq: Any) -> list[dict[str, str]]:
    if not isinstance(raw_faq, list):
        return []
    return [
        {str(k): str(v) for k, v in item.items() if v is not None}
        for item in raw_faq
        if isinstance(item, dict)
    ]


def _normalize_claims_for_publish(
    claim_rows: list[dict[str, Any]],
    payload_claims: Any,
) -> list[DraftClaim]:
    if claim_rows:
        return [
            DraftClaim(
                text=str(row.get("text") or ""),
                kind=str(row.get("kind") or "analysis"),
                source_id=str(row["document_id"]) if row.get("document_id") is not None else None,
            )
            for row in claim_rows
        ]
    return [
        DraftClaim(
            text=str(item.get("text") or ""),
            kind=str(item.get("kind") or ("fact" if item.get("source_id") else "analysis")),
            source_id=item.get("source_id"),
        )
        for item in (payload_claims or [])
        if isinstance(item, dict)
    ]


def _build_content_draft_for_publish(
    draft_row: dict[str, Any],
    claim_rows: list[dict[str, Any]],
) -> ContentDraft:
    payload = draft_row.get("payload") or {}
    return ContentDraft(
        schema_version=int(payload.get("schema_version") or 1),
        title=str(payload.get("title") or "Untitled Draft"),
        meta_description=str(payload.get("meta_description") or ""),
        slug=str(payload.get("slug") or f"draft-{draft_row['id']}"),
        body_md=str(payload.get("body_md") or ""),
        faq=_normalize_faq_for_publish(payload.get("faq")),
        cta=_normalize_cta_for_publish(payload.get("cta")),
        claims=_normalize_claims_for_publish(claim_rows, payload.get("claims")),
    )


def _validate_publish_outcome(raw_result: Any, *, mode: str) -> PublishedContent:
    published = (
        raw_result
        if isinstance(raw_result, PublishedContent)
        else PublishedContent.model_validate(raw_result)
    )
    if published.status == "failed":
        detail = (published.raw or {}).get("error") or "Publisher returned status 'failed'"
        raise PublishError(str(detail))
    raw_meta = published.raw or {}
    if mode == "draft" and (
        raw_meta.get("is_live") is True
        or raw_meta.get("live") is True
        or raw_meta.get("public") is True
    ):
        msg = "Destination reported content live while mode='draft'"
        raise PublishError(msg)
    return published


def _load_configured_publisher(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    encryption_key: str | list[str] | None,
) -> tuple[str, PublisherPort, Any]:
    row = db.get_workspace_integration(conn, workspace_id=workspace_id, port="publisher")
    plugin_cls, settings_obj = plugin_config(
        conn,
        workspace_id=workspace_id,
        port="publisher",
        encryption_key=encryption_key,
    )
    plugin_name = str(row["plugin"]) if row is not None else "publisher"
    publisher: PublisherPort = plugin_cls(settings_obj)
    return plugin_name, publisher, settings_obj


def _record_failed_publication(
    conn: psycopg.Connection[Any],
    *,
    meta: dict[str, Any],
    error_msg: str,
) -> dict[str, Any]:
    trimmed_error = error_msg.strip()[:1000] or "Publish failed"
    with conn.transaction():
        return db.upsert_draft_publication(
            conn,
            workspace_id=meta["workspace_id"],
            draft_id=meta["draft_id"],
            destination=meta["destination"],
            mode=meta["mode"],
            status="failed",
            title=meta["title"],
            error=trimmed_error,
            response={"mode": meta["mode"], "error": trimmed_error},
        )


def _record_successful_publication(
    conn: psycopg.Connection[Any],
    *,
    meta: dict[str, Any],
    published: PublishedContent,
) -> dict[str, Any]:
    workspace_id: uuid.UUID = meta["workspace_id"]
    draft_id: uuid.UUID = meta["draft_id"]
    destination: str = meta["destination"]
    ctx: dict[str, Any] = meta["ctx"]

    with conn.transaction():
        existing_pub = db.get_publication_for_draft(
            conn, workspace_id=workspace_id, draft_id=draft_id, destination=destination
        )
        pub_row = db.upsert_draft_publication(
            conn,
            workspace_id=workspace_id,
            draft_id=draft_id,
            destination=destination,
            mode=meta["mode"],
            status=published.status,
            title=meta["title"],
            external_id=published.external_id,
            url=published.url,
            published_at=published.published_at,
            error=None,
            response=published.raw,
        )
        db.mark_draft_published(conn, workspace_id=workspace_id, draft_id=draft_id)
        if existing_pub is None or existing_pub.get("status") != "published":
            events.emit(
                conn,
                workspace_id=workspace_id,
                event_type="article.published",
                entity_type="draft",
                entity_id=draft_id,
                payload={
                    "draft_id": str(draft_id),
                    "brief_id": str(ctx["brief_id"]),
                    "opportunity_id": str(ctx["opportunity_id"]),
                    "run_id": str(ctx["run_id"]),
                    "campaign_id": str(meta["campaign_id"]),
                    "destination": destination,
                    "mode": meta["mode"],
                    "external_id": published.external_id,
                    "url": published.url,
                    "status": published.status,
                },
                actor_user_id=meta["user_id"],
            )
    return pub_row


def _execute_publish_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    draft_id: uuid.UUID = params["draft_id"]
    user_id: uuid.UUID = params["user_id"]

    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    ctx = db.get_draft_evaluation_context(conn, workspace_id=workspace_id, draft_id=draft_id)
    draft_row = db.get_draft(conn, workspace_id=workspace_id, draft_id=draft_id)
    if draft_row is None or ctx is None:
        msg = f"Draft {draft_id} not found"
        raise ValueError(msg)

    campaign_id = uuid.UUID(str(ctx["campaign_id"]))
    _validate_topic_approver(
        conn,
        workspace_id=workspace_id,
        user_id=user_id,
        campaign_id=campaign_id,
    )

    current_status = str(draft_row.get("status") or "")
    if current_status not in ("approved", "published"):
        msg = (
            f"Draft {draft_id} is in status '{current_status}', expected 'approved' or 'published'"
        )
        raise ValueError(msg)

    plugin_name, publisher, settings_obj = _load_configured_publisher(
        conn, workspace_id=workspace_id, encryption_key=params.get("encryption_key")
    )
    destination = str(params.get("destination") or plugin_name).strip() or plugin_name
    mode = _resolve_publish_mode(requested_mode=params.get("mode"), settings_obj=settings_obj)
    claim_rows = db.list_draft_claims(conn, workspace_id=workspace_id, draft_id=draft_id)
    content_draft = _build_content_draft_for_publish(draft_row, claim_rows)
    request = PublishRequest(
        draft_id=draft_id,
        draft=content_draft,
        locale=str(ctx.get("campaign_locale") or "pt-BR"),
        destination=destination,
        mode=mode,
        scheduled_at=params.get("scheduled_at"),
    )
    meta: dict[str, Any] = {
        "workspace_id": workspace_id,
        "draft_id": draft_id,
        "destination": destination,
        "mode": mode,
        "title": content_draft.title,
        "user_id": user_id,
        "ctx": ctx,
        "campaign_id": campaign_id,
    }

    try:
        raw_result = publisher.publish(request)
        published = _validate_publish_outcome(raw_result, mode=mode)
    except (PublishError, safe_http.SafeHTTPError, ValueError, OSError, RuntimeError) as exc:
        return _record_failed_publication(conn, meta=meta, error_msg=str(exc))

    return _record_successful_publication(conn, meta=meta, published=published)


def publish_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    mode: str | None = None,
    destination: str | None = None,
    scheduled_at: datetime | None = None,
    encryption_key: str | list[str] | None = None,
) -> dict[str, Any]:
    """Publish an approved or previously published draft through the Publisher port (F16)."""
    params = {
        "workspace_id": workspace_id,
        "draft_id": draft_id,
        "user_id": user_id,
        "mode": mode,
        "destination": destination,
        "scheduled_at": scheduled_at,
        "encryption_key": encryption_key,
    }
    if conn is not None:
        return _execute_publish_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_publish_draft(managed_conn, params)


def retry_publication(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    destination: str | None = None,
    encryption_key: str | list[str] | None = None,
) -> dict[str, Any]:
    """Retry publishing an approved or failed draft idempotently (F16 AC2, AC4)."""
    return publish_draft(
        conn,
        workspace_id=workspace_id,
        draft_id=draft_id,
        user_id=user_id,
        destination=destination,
        encryption_key=encryption_key,
    )


def save_subscription(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    url: str,
    event_types: list[str],
    enabled: bool = True,
    subscription_id: uuid.UUID | None = None,
    encryption_key: str | list[str] | None = None,
    is_production: bool | None = None,
) -> dict[str, Any]:
    """Validate, encrypt secret, and save a webhook subscription (F06 AC1, AC5, AC7)."""
    params = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "url": url,
        "event_types": event_types,
        "enabled": enabled,
        "subscription_id": subscription_id,
        "encryption_key": encryption_key,
        "is_production": is_production,
    }
    if conn is not None:
        return _execute_save_subscription(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_save_subscription(managed_conn, params)


def _execute_save_subscription(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    url: str = str(params["url"]).strip()
    event_types: list[str] = list(params["event_types"])
    enabled: bool = bool(params["enabled"])
    subscription_id: uuid.UUID | None = params["subscription_id"]
    encryption_key: str | list[str] | None = params["encryption_key"]
    is_production: bool | None = params["is_production"]

    role = require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if role != "owner":
        msg = f"User {user_id} must have owner role to manage webhooks"
        raise PermissionError(msg)

    if not url:
        msg = "Webhook URL cannot be empty"
        raise ValueError(msg)

    safe_http.validate_safe_url(url, is_production=is_production)

    if not event_types:
        msg = "Webhook subscription must include at least one event type"
        raise ValueError(msg)

    if subscription_id is not None:
        updated = db.update_webhook_subscription(
            conn,
            workspace_id=workspace_id,
            subscription_id=subscription_id,
            url=url,
            event_types=event_types,
            enabled=enabled,
        )
        if updated is None:
            msg = f"Webhook subscription {subscription_id} not found"
            raise ValueError(msg)
        return {**updated, "secret": None}

    token_bytes = secrets.token_bytes(24)
    secret_token = "whsec_" + base64.b64encode(token_bytes).decode("ascii")

    keys = _resolve_keys(encryption_key)
    mf = _get_fernet_instance(keys)
    encrypted_secret = mf.encrypt(secret_token.encode("utf-8"))

    created = db.create_webhook_subscription(
        conn,
        workspace_id=workspace_id,
        url=url,
        event_types=event_types,
        secret=encrypted_secret,
        created_by=user_id,
        enabled=enabled,
    )
    return {**created, "secret": secret_token}


def list_subscriptions(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List webhook subscriptions for workspace with secrets omitted."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_webhook_subscriptions(conn, workspace_id=workspace_id)
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_webhook_subscriptions(managed_conn, workspace_id=workspace_id)


def delete_subscription(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Delete a webhook subscription and its delivery history."""
    if conn is not None:
        role = require_member(conn, workspace_id=workspace_id, user_id=user_id)
        if role != "owner":
            msg = f"User {user_id} must have owner role to delete webhooks"
            raise PermissionError(msg)
        return db.delete_webhook_subscription(
            conn, workspace_id=workspace_id, subscription_id=subscription_id
        )
    with db.get_connection() as managed_conn:
        role = require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        if role != "owner":
            msg = f"User {user_id} must have owner role to delete webhooks"
            raise PermissionError(msg)
        return db.delete_webhook_subscription(
            managed_conn, workspace_id=workspace_id, subscription_id=subscription_id
        )


def list_deliveries(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 50,
    subscription_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List webhook deliveries with joined subscription URL and event metadata."""
    if conn is not None:
        require_member(conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_webhook_deliveries(
            conn, workspace_id=workspace_id, limit=limit, subscription_id=subscription_id
        )
    with db.get_connection() as managed_conn:
        require_member(managed_conn, workspace_id=workspace_id, user_id=user_id)
        return db.list_webhook_deliveries(
            managed_conn, workspace_id=workspace_id, limit=limit, subscription_id=subscription_id
        )


def resend(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    execute_immediately: bool = False,
    encryption_key: str | list[str] | None = None,
    timeout: float = 10.0,
    is_production: bool | None = None,
) -> dict[str, Any]:
    """Reset a delivery to pending status and optionally execute immediately (F06 AC4)."""
    params = {
        "workspace_id": workspace_id,
        "subscription_id": subscription_id,
        "event_id": event_id,
        "user_id": user_id,
        "execute_immediately": execute_immediately,
        "encryption_key": encryption_key,
        "timeout": timeout,
        "is_production": is_production,
    }
    if conn is not None:
        return _execute_resend(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_resend(managed_conn, params)


def _execute_resend(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    subscription_id: uuid.UUID = params["subscription_id"]
    event_id: uuid.UUID = params["event_id"]
    user_id: uuid.UUID = params["user_id"]
    execute_immediately: bool = params["execute_immediately"]
    encryption_key: str | list[str] | None = params["encryption_key"]
    timeout: float = params["timeout"]
    is_production: bool | None = params["is_production"]

    role = require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if role != "owner":
        msg = f"User {user_id} must have owner role to re-send webhooks"
        raise PermissionError(msg)

    reset_row = db.reset_webhook_delivery_for_resend(
        conn,
        workspace_id=workspace_id,
        subscription_id=subscription_id,
        event_id=event_id,
    )
    if reset_row is None:
        msg = f"Delivery {subscription_id}/{event_id} not found"
        raise ValueError(msg)

    if not execute_immediately:
        return reset_row

    delivery_item = db.get_webhook_delivery(
        conn,
        workspace_id=workspace_id,
        subscription_id=subscription_id,
        event_id=event_id,
    )
    if delivery_item is None:
        return reset_row

    return webhooks.deliver_single_delivery(
        conn,
        delivery_item,
        encryption_key=encryption_key,
        timeout=timeout,
        is_production=is_production,
    )


def deliver_webhooks(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID | None = None,
    batch_size: int = 10,
    encryption_key: str | list[str] | None = None,
    timeout: float = 10.0,
    is_production: bool | None = None,
) -> list[dict[str, Any]]:
    """Dispatch due pending/retrying webhook deliveries (F06 AC2, AC3)."""
    return webhooks.deliver_due(
        conn=conn,
        workspace_id=workspace_id,
        batch_size=batch_size,
        encryption_key=encryption_key,
        timeout=timeout,
        is_production=is_production,
    )


def import_sitemap(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    sitemap_url: str,
    user_id: uuid.UUID,
    fetch_titles: bool = False,
    timeout: float = 10.0,
    is_production: bool | None = None,
) -> dict[str, Any]:
    """Import articles from a sitemap URL into workspace publications (F08 AC3)."""
    params = {
        "workspace_id": workspace_id,
        "sitemap_url": sitemap_url,
        "user_id": user_id,
        "fetch_titles": fetch_titles,
        "timeout": timeout,
        "is_production": is_production,
    }
    if conn is not None:
        return _execute_import_sitemap(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_import_sitemap(managed_conn, params)


def _execute_import_sitemap(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    sitemap_url: str = str(params["sitemap_url"]).strip()
    user_id: uuid.UUID = params["user_id"]
    fetch_titles: bool = bool(params["fetch_titles"])
    timeout: float = float(params["timeout"])
    is_production: bool | None = params["is_production"]

    role = require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if role != "owner":
        msg = f"User {user_id} must have owner role to import sitemaps"
        raise PermissionError(msg)

    if not sitemap_url:
        msg = "Sitemap URL cannot be empty"
        raise ValueError(msg)

    articles = sitemap.fetch_sitemap_articles(
        sitemap_url,
        timeout=timeout,
        is_production=is_production,
        fetch_titles=fetch_titles,
    )

    inserted_count = 0
    updated_count = 0
    with conn.transaction():
        for art in articles:
            res = db.create_publication(
                conn,
                workspace_id=workspace_id,
                url=art["url"],
                title=art["title"],
                published_at=art["published_at"],
                mode="live",
                status="published",
                destination="sitemap",
                response=art.get("raw"),
            )
            if res.get("is_new"):
                inserted_count += 1
            else:
                updated_count += 1

    return {
        "total": len(articles),
        "inserted": inserted_count,
        "updated": updated_count,
    }


DEFAULT_RETENTION_DATABASE_URL = (
    "postgres://retention:retention_secret@localhost:5432/macy?sslmode=disable"
)


def get_retention_connection(database_url: str | None = None) -> psycopg.Connection[Any]:
    """Open a database connection for the least-privilege `retention` role."""
    url = (
        database_url
        or os.environ.get("RETENTION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DEFAULT_RETENTION_DATABASE_URL
    )
    return db.get_connection(url)


def purge_expired_data(
    conn: psycopg.Connection[Any] | None = None,
    *,
    now: datetime | None = None,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Execute data retention purge under the `retention` role (F20 AC7, ADR-024)."""
    ref_now = now if now is not None else datetime.now(UTC)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=UTC)

    if conn is not None:
        return _execute_purge_expired_data(conn, now=ref_now, workspace_id=workspace_id)
    with get_retention_connection() as managed_conn:
        return _execute_purge_expired_data(managed_conn, now=ref_now, workspace_id=workspace_id)


def _execute_purge_expired_data(
    conn: psycopg.Connection[Any],
    *,
    now: datetime,
    workspace_id: uuid.UUID | None,
) -> dict[str, int]:
    role = db.get_current_db_role(conn)
    if role != "retention":
        msg = f"Purge job must run as database role 'retention' (current role: '{role}')"
        raise PermissionError(msg)
    return db.purge_expired_data(conn, now=now, workspace_id=workspace_id)


def anonymise_user(
    conn: psycopg.Connection[Any] | None = None,
    *,
    email: str,
) -> dict[str, Any]:
    """Anonymise a user's personal data (LGPD Art. 18) while keeping opaque event IDs (F20 AC8)."""
    clean_email = email.strip().lower()
    if not clean_email:
        msg = "User email must not be empty"
        raise ValueError(msg)

    if conn is not None:
        return _execute_anonymise_user(conn, email=clean_email)
    with db.get_connection() as managed_conn:
        return _execute_anonymise_user(managed_conn, email=clean_email)


def _execute_anonymise_user(
    conn: psycopg.Connection[Any],
    *,
    email: str,
) -> dict[str, Any]:
    user = db.get_user_by_email(conn, email=email)
    if user is None:
        msg = f"User with email '{email}' not found"
        raise ValueError(msg)
    user_id = uuid.UUID(str(user["user_id"]))
    return db.anonymise_user_record(conn, user_id=user_id)


PILOT_ARTICLE_COST_TARGET_USD = Decimal("5.00")


def cost_summary(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Return spend-to-date vs caps, cost per run, and cost per published article (F17 AC5, AC6)."""
    if conn is not None:
        return _execute_cost_summary(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            campaign_id=campaign_id,
            reference_date=reference_date,
        )
    with db.get_connection() as managed_conn:
        return _execute_cost_summary(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            campaign_id=campaign_id,
            reference_date=reference_date,
        )


def _resolve_cost_summary_campaign(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
) -> dict[str, Any] | None:
    if campaign_id is not None:
        return db.get_campaign(conn, workspace_id=workspace_id, campaign_id=campaign_id)
    campaigns = db.list_campaigns(conn, workspace_id=workspace_id)
    return campaigns[0] if campaigns else None


def _build_published_articles_with_costs(
    raw_articles: list[dict[str, Any]],
    runs_by_id: dict[uuid.UUID, dict[str, Any]],
) -> list[dict[str, Any]]:
    published_articles: list[dict[str, Any]] = []
    for art in raw_articles:
        art_run_id = uuid.UUID(str(art["run_id"]))
        run_info = runs_by_id.get(art_run_id)
        if run_info is not None:
            pub_count = max(1, int(run_info["published_articles_count"]))
            art_cost = (Decimal(str(run_info["cost_usd"])) / Decimal(pub_count)).quantize(
                Decimal("0.000001")
            )
        else:  # pragma: no cover
            art_cost = Decimal("0.000000")
        item = dict(art)
        item["cost_usd"] = art_cost
        item["under_pilot_target"] = art_cost < PILOT_ARTICLE_COST_TARGET_USD
        published_articles.append(item)

    published_articles.sort(
        key=lambda a: str(a.get("published_at") or a.get("created_at") or ""),
        reverse=True,
    )
    return published_articles


def _execute_cost_summary(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
    reference_date: datetime | None,
) -> dict[str, Any]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    campaign = _resolve_cost_summary_campaign(
        conn, workspace_id=workspace_id, campaign_id=campaign_id
    )

    monthly_cap = (
        Decimal(str(campaign["monthly_cap_usd"])).quantize(Decimal("0.01"))
        if campaign is not None
        else Decimal("0.00")
    )
    run_cap = (
        Decimal(str(campaign["run_cap_usd"])).quantize(Decimal("0.01"))
        if campaign is not None
        else Decimal("0.00")
    )
    monthly_spend = db.get_monthly_spend(
        conn, workspace_id=workspace_id, reference_date=reference_date
    ).quantize(Decimal("0.000001"))
    monthly_remaining = max(Decimal("0.000000"), monthly_cap - monthly_spend)

    runs = db.list_runs_with_usage_summary(conn, workspace_id=workspace_id, campaign_id=campaign_id)
    runs_by_id = {uuid.UUID(str(r["run_id"])): r for r in runs}
    total_spend = sum((r["cost_usd"] for r in runs), Decimal("0.000000")).quantize(
        Decimal("0.000001")
    )
    avg_run_cost = (
        (total_spend / Decimal(len(runs))).quantize(Decimal("0.000001"))
        if runs
        else Decimal("0.000000")
    )

    raw_articles = db.list_published_articles_with_costs(
        conn, workspace_id=workspace_id, campaign_id=campaign_id
    )
    published_articles = _build_published_articles_with_costs(raw_articles, runs_by_id)
    total_article_spend = sum((a["cost_usd"] for a in published_articles), Decimal("0.000000"))
    avg_article_cost = (
        (total_article_spend / Decimal(len(published_articles))).quantize(Decimal("0.000001"))
        if published_articles
        else Decimal("0.000000")
    )
    within_target = all(bool(a["under_pilot_target"]) for a in published_articles)

    return {
        "campaign_id": campaign["id"] if campaign is not None else None,
        "campaign_name": str(campaign["name"]) if campaign is not None else None,
        "monthly_spend_usd": monthly_spend,
        "monthly_cap_usd": monthly_cap,
        "monthly_remaining_usd": monthly_remaining,
        "run_cap_usd": run_cap,
        "total_spend_usd": total_spend,
        "runs": runs,
        "average_cost_per_run_usd": avg_run_cost,
        "published_articles": published_articles,
        "average_cost_per_published_article_usd": avg_article_cost,
        "pilot_target_usd": PILOT_ARTICLE_COST_TARGET_USD,
        "within_pilot_target": within_target,
    }


MCP_BUDGET_EXCEPTIONS: tuple[type[BaseException], ...] = (
    budget.BudgetCapReached,
    budget.MonthlyCapReached,
)
MCP_STATE_EXCEPTIONS: tuple[type[BaseException], ...] = (IntegrationMissing,)


def _execute_preflight_mcp_spend(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None,
    estimated_cost: Decimal,
) -> dict[str, Any]:
    campaign = _select_primary_campaign(db.list_campaigns(conn, workspace_id=workspace_id))
    monthly_spend = Decimal("0.000000")
    if campaign is not None:
        monthly_cap = Decimal(str(campaign["monthly_cap_usd"]))
        monthly_spend = budget.check_monthly_budget(
            conn, workspace_id=workspace_id, monthly_cap_usd=monthly_cap
        )
        if monthly_spend + estimated_cost > monthly_cap:
            raise budget.MonthlyCapReached(
                workspace_id, monthly_cap, monthly_spend + estimated_cost
            )

    if run_id is not None:
        run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
        if run_row is not None:
            eff_cost = estimated_cost if estimated_cost > Decimal("0") else Decimal("0.000001")
            budget.check_run_budget(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                run_cap_usd=Decimal(str(run_row["run_cap_usd"])),
                estimated_cost=eff_cost,
            )
    return {"workspace_id": workspace_id, "run_id": run_id, "monthly_spend_usd": monthly_spend}


def preflight_mcp_spend(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    estimated_cost: Decimal = Decimal("0.000000"),
) -> dict[str, Any]:
    """Enforce monthly and per-run spend caps before any MCP generative operation (ADR-016)."""
    if conn is not None:
        return _execute_preflight_mcp_spend(
            conn, workspace_id=workspace_id, run_id=run_id, estimated_cost=estimated_cost
        )
    with db.get_connection() as managed_conn:
        return _execute_preflight_mcp_spend(
            managed_conn, workspace_id=workspace_id, run_id=run_id, estimated_cost=estimated_cost
        )


def _resolve_mcp_llm(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    offline: bool | None,
    llm_adapter: LLMPort | None,
    run_row: dict[str, Any] | None = None,
) -> tuple[LLMPort, bool]:
    from engine import pipeline
    from engine.integrations.fakes import FakeLLM

    try:
        resolved_llm: LLMPort = pipeline._resolve_port_adapter(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            port="llm",
            explicit=llm_adapter,
            offline=offline,
            fallback_factory=FakeLLM,
            required_in_live=True,
        )
    except IntegrationMissing as exc:
        if run_row is not None:
            pipeline._mark_integration_missing(conn, workspace_id, run_id, run_row, exc)
        msg = f"Missing required integration '{exc.port}' for workspace {workspace_id}"
        raise ValueError(msg) from exc
    is_fake = offline is True or resolved_llm.__class__.__name__.startswith("Fake")
    return resolved_llm, bool(is_fake and offline is not False)


def _ensure_mcp_offline_allowed(offline: bool | None) -> None:
    if offline is True and _is_non_local_environment():
        msg = "Offline fake adapters are forbidden in staging or production"
        raise PermissionError(msg)


def _resolve_approval_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None,
) -> dict[str, Any] | None:
    if run_id is not None:
        run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
        if run_row is None:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)
        return run_row

    scored = db.list_opportunities(conn, workspace_id=workspace_id, status="scored")
    if not scored:
        return None
    scored_run_ids = {uuid.UUID(str(opp["run_id"])) for opp in scored}
    runs = db.list_runs(conn, workspace_id=workspace_id)
    return next(
        (r for r in runs if uuid.UUID(str(r["id"])) in scored_run_ids),
        None,
    )


def _execute_list_topics_for_approval(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID | None,
    limit: int,
) -> list[dict[str, Any]]:
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    run_row = _resolve_approval_run(conn, workspace_id=workspace_id, run_id=run_id)
    if run_row is None:
        return []

    target_run_id = uuid.UUID(str(run_row["id"]))
    camp_id = run_row.get("campaign_id")
    camp_row = (
        db.get_campaign(conn, workspace_id=workspace_id, campaign_id=camp_id) if camp_id else None
    )
    articles_per_week = int(camp_row.get("articles_per_week", 1)) if camp_row else 1
    raw_items = db.get_opportunity_report_items(
        conn, workspace_id=workspace_id, run_id=target_run_id, limit=max(limit, 100)
    )
    scored_raw = [it for it in raw_items if it.get("status") == "scored"][:limit]
    items, _ = _build_report_items_for_run(
        conn,
        workspace_id=workspace_id,
        run_id=target_run_id,
        raw_items=scored_raw,
        articles_per_week=articles_per_week,
    )
    for item in items:
        item["run_id"] = target_run_id
    return items


def list_topics_for_approval(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List Gate 1 opportunities strictly in status 'scored' for a workspace (M5-04)."""
    if conn is not None:
        return _execute_list_topics_for_approval(
            conn, workspace_id=workspace_id, user_id=user_id, run_id=run_id, limit=limit
        )
    with db.get_connection() as managed_conn:
        return _execute_list_topics_for_approval(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            limit=limit,
        )


def _resolve_mcp_research_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID | None,
    window_days: int,
) -> dict[str, Any]:
    from engine import pipeline, scheduler

    if run_id is not None:
        run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
        if run_row is None:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)
    else:
        existing_runs = db.list_runs(conn, workspace_id=workspace_id, campaign_id=campaign_id)
        active = next(
            (r for r in existing_runs if r.get("status") in ("queued", "running", "blocked")),
            None,
        )
        if active is not None:
            run_row = active
        else:
            new_run_id = scheduler.trigger_campaign_run(
                conn,
                workspace_id=workspace_id,
                campaign_id=campaign_id,
                trigger="manual",
                window_days=window_days,
                created_by=user_id,
            )
            loaded = db.get_run(conn, workspace_id=workspace_id, run_id=new_run_id)
            if loaded is None:  # pragma: no cover - FK guaranteed
                msg = f"Run {new_run_id} not found"
                raise ValueError(msg)
            run_row = loaded

    target_run_id = uuid.UUID(str(run_row["id"]))
    transitioned = pipeline._transition_initial_run_status(
        conn, workspace_id, target_run_id, run_row
    )
    if transitioned.get("status") != "running":
        msg = f"Run {target_run_id} is in status '{transitioned.get('status')}', expected active"
        raise ValueError(msg)
    return transitioned


def _format_mcp_research_documents(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    records = db.list_research_records(conn, workspace_id=workspace_id, run_id=run_id)
    docs_out: list[dict[str, Any]] = []
    for rec in records:
        doc_id = uuid.UUID(str(rec["document_id"]))
        doc = db.get_document(conn, workspace_id=workspace_id, document_id=doc_id)
        if doc is None:  # pragma: no cover - FK guaranteed
            continue
        payload = rec.get("payload") or {}
        text = str(doc.get("extracted_text") or payload.get("summary") or "")
        docs_out.append(
            {
                "document_id": doc_id,
                "record_id": uuid.UUID(str(rec["id"])),
                "record_status": str(rec.get("status") or "extracted"),
                "url": str(doc["url"]),
                "canonical_url": str(doc["canonical_url"]),
                "title": str(doc["title"]),
                "published_at": doc.get("published_at"),
                "date_status": str(doc["date_status"]),
                "fetch_status": str(doc["fetch_status"]),
                "extracted_passage": text[:1200],
                "summary": str(payload.get("summary") or ""),
                "facts": list(payload.get("facts") or []),
            }
        )
    return docs_out


def _execute_run_mcp_research(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from engine import pipeline, research

    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    offline: bool | None = params.get("offline")
    _ensure_mcp_offline_allowed(offline)
    require_member(conn, workspace_id=workspace_id, user_id=user_id)

    campaign = _select_primary_campaign(db.list_campaigns(conn, workspace_id=workspace_id))
    if campaign is None:
        msg = f"Workspace {workspace_id} has no configured campaign"
        raise ValueError(msg)

    camp_id = uuid.UUID(str(campaign["id"]))
    run_row = _resolve_mcp_research_run(
        conn,
        workspace_id=workspace_id,
        campaign_id=camp_id,
        user_id=user_id,
        run_id=params.get("run_id"),
        window_days=int(params.get("window_days", 7)),
    )
    target_run_id = uuid.UUID(str(run_row["id"]))
    budget.check_run_budget(
        conn,
        workspace_id=workspace_id,
        run_id=target_run_id,
        run_cap_usd=Decimal(str(run_row["run_cap_usd"])),
        estimated_cost=Decimal("0.000000"),
    )
    try:
        f_adapter, ns_adapter, ext_adapter, l_adapter = pipeline._resolve_run_adapters(
            conn,
            ws_id=workspace_id,
            run_id=target_run_id,
            offline=offline,
            feed_adapter=params.get("feed_adapter"),
            news_search_adapter=params.get("news_search_adapter"),
            extractor_adapter=params.get("extractor_adapter"),
            llm_adapter=params.get("llm_adapter"),
        )
    except IntegrationMissing as exc:
        pipeline._mark_integration_missing(conn, workspace_id, target_run_id, run_row, exc)
        msg = f"Missing required integration '{exc.port}' for workspace {workspace_id}"
        raise ValueError(msg) from exc

    research.collect(
        conn,
        run_row,
        feed_adapter=f_adapter,
        news_search_adapter=ns_adapter,
        extractor_adapter=ext_adapter,
        llm_adapter=l_adapter,
    )
    clean_query = str(params.get("query") or "").strip()
    if clean_query:
        window_start = run_row.get("window_start")
        since_date = window_start.date() if isinstance(window_start, datetime) else None
        found = ns_adapter.search(
            clean_query,
            locale=str(campaign.get("locale") or "pt-BR"),
            market=str(campaign.get("market") or "BR"),
            since=since_date,
        )
        research._process_collected_items(
            conn,
            workspace_id=workspace_id,
            run_id=target_run_id,
            source_row={"id": None, "name": "topic-search", "role": "news"},
            items=found,
            seen_canonicals=set(),
            extractor_adapter=ext_adapter,
            llm_adapter=l_adapter,
        )
        research.dedup(conn, run_row)

    documents = _format_mcp_research_documents(
        conn, workspace_id=workspace_id, run_id=target_run_id
    )
    return {
        "workspace_id": workspace_id,
        "run_id": target_run_id,
        "query": clean_query or None,
        "document_count": len(documents),
        "documents": documents,
    }


def run_mcp_research(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str | None = None,
    run_id: uuid.UUID | None = None,
    window_days: int = 7,
    offline: bool | None = None,
    feed_adapter: FeedSourcePort | None = None,
    news_search_adapter: NewsSearchPort | None = None,
    extractor_adapter: ExtractorPort | None = None,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any]:
    """Run membership-checked research collection for MCP without silent prod fakes (M5-04)."""
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "query": query,
        "run_id": run_id,
        "window_days": window_days,
        "offline": offline,
        "feed_adapter": feed_adapter,
        "news_search_adapter": news_search_adapter,
        "extractor_adapter": extractor_adapter,
        "llm_adapter": llm_adapter,
    }
    if conn is not None:
        return _execute_run_mcp_research(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_run_mcp_research(managed_conn, params)


def _execute_score_mcp_topics(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID | None,
    offline: bool | None,
    llm_adapter: LLMPort | None,
) -> dict[str, Any]:
    from engine import pipeline
    from engine.strategy import score

    _ensure_mcp_offline_allowed(offline)
    require_member(conn, workspace_id=workspace_id, user_id=user_id)

    if run_id is not None:
        run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
        if run_row is None:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)
    else:
        runs = db.list_runs(conn, workspace_id=workspace_id)
        if not runs:
            msg = f"No runs found for workspace {workspace_id}; run research first"
            raise ValueError(msg)
        run_row = next((r for r in runs if r.get("status") in ("running", "queued")), runs[0])

    target_run_id = uuid.UUID(str(run_row["id"]))
    _execute_preflight_mcp_spend(
        conn,
        workspace_id=workspace_id,
        run_id=target_run_id,
        estimated_cost=Decimal("0.000000"),
    )
    resolved_llm, _ = _resolve_mcp_llm(
        conn,
        workspace_id=workspace_id,
        run_id=target_run_id,
        offline=offline,
        llm_adapter=llm_adapter,
        run_row=run_row,
    )

    run_row = pipeline._transition_initial_run_status(conn, workspace_id, target_run_id, run_row)
    if run_row.get("status") == "running":
        db.update_run_status(
            conn,
            workspace_id=workspace_id,
            run_id=target_run_id,
            expected_status="running",
            new_status="running",
            stage="score",
        )
    score.score_run(conn, run_row, llm_adapter=resolved_llm)
    if run_row.get("status") == "running":
        db.update_run_status(
            conn,
            workspace_id=workspace_id,
            run_id=target_run_id,
            expected_status="running",
            new_status="running",
            stage="report",
        )
        pipeline._finish_run(
            conn, workspace_id, target_run_id, run_row["campaign_id"], status="completed"
        )
    return _execute_report(conn, workspace_id=workspace_id, run_id=target_run_id)


def score_mcp_topics(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    offline: bool | None = None,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any]:
    """Score verified research records for a workspace run and return ranked report (M5-04)."""
    if conn is not None:
        return _execute_score_mcp_topics(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            offline=offline,
            llm_adapter=llm_adapter,
        )
    with db.get_connection() as managed_conn:
        return _execute_score_mcp_topics(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            offline=offline,
            llm_adapter=llm_adapter,
        )


def _collect_doc_passages(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    doc_ids: list[uuid.UUID],
) -> list[str]:
    passages: list[str] = []
    docs = [
        doc
        for doc_id in dict.fromkeys(doc_ids)
        if (doc := db.get_document(conn, workspace_id=workspace_id, document_id=doc_id)) is not None
    ]
    for doc in docs:
        raw_meta = doc.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        parts = [
            str(doc.get("extracted_text") or "").strip(),
            str(meta.get("summary") or "").strip(),
            *[str(f).strip() for f in (meta.get("facts") or [])],
        ]
        joined = "\n".join(p for p in parts if p)
        if joined:
            passages.append(joined)
    return passages


def _load_draft_claim_texts(
    draft_row: dict[str, Any],
    claim_rows: list[dict[str, Any]],
) -> list[str]:
    raw_claims = [row.get("text") for row in claim_rows]
    if not raw_claims:
        payload_claims = (draft_row.get("payload") or {}).get("claims") or []
        raw_claims = [c.get("text") for c in payload_claims if isinstance(c, dict)]
    return [str(t).strip() for t in raw_claims if str(t or "").strip()]


def _load_draft_passages(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    claim_rows: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> list[str]:
    doc_ids = [
        uuid.UUID(str(row["document_id"]))
        for row in claim_rows
        if row.get("document_id") is not None
    ]
    if ctx.get("primary_document_id") is not None:
        doc_ids.append(uuid.UUID(str(ctx["primary_document_id"])))
    return _collect_doc_passages(conn, workspace_id=workspace_id, doc_ids=doc_ids)


def _resolve_verify_claims_inputs(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> tuple[uuid.UUID | None, list[str], list[str]]:
    workspace_id: uuid.UUID = params["workspace_id"]
    draft_id: uuid.UUID | None = params.get("draft_id")
    run_id: uuid.UUID | None = params.get("run_id")
    claim_texts = [str(c).strip() for c in (params.get("claims") or ()) if str(c).strip()]
    passages = [str(p).strip() for p in (params.get("passages") or ()) if str(p).strip()]

    if draft_id is None:
        if not claim_texts:
            msg = "claims or draft_id with persisted claims is required"
            raise ValueError(msg)
        return run_id, claim_texts, passages

    draft_row = db.get_draft(conn, workspace_id=workspace_id, draft_id=draft_id)
    ctx = db.get_draft_evaluation_context(conn, workspace_id=workspace_id, draft_id=draft_id)
    if draft_row is None or ctx is None:
        msg = f"Draft {draft_id} not found"
        raise ValueError(msg)

    target_run_id = run_id or uuid.UUID(str(ctx["run_id"]))
    claim_rows = db.list_draft_claims(conn, workspace_id=workspace_id, draft_id=draft_id)
    if not claim_texts:
        claim_texts = _load_draft_claim_texts(draft_row, claim_rows)
    if not claim_texts:
        msg = f"Draft {draft_id} has no factual claims to verify"
        raise ValueError(msg)
    if not passages:
        passages = _load_draft_passages(
            conn, workspace_id=workspace_id, claim_rows=claim_rows, ctx=ctx
        )
    return target_run_id, claim_texts, passages


def _record_verify_claims_usage(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None,
    draft_id: uuid.UUID | None,
    results: Sequence[Any],
    jev_api_key: str | None,
    jev_model: str,
) -> Decimal:
    total_cost = sum((r.cost_usd for r in results), Decimal("0.000000")).quantize(
        Decimal("0.000001")
    )
    jev_used = bool(jev_api_key) or any(r.judge == "jev" for r in results)
    db.record_usage(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        stage="verify_claims",
        kind="llm",
        vendor="openrouter" if jev_used else "deterministic",
        model=jev_model if jev_used else "deterministic:v1",
        units=Decimal(len(results)),
        cost_usd=total_cost,
        entity_type="draft" if draft_id is not None else None,
        entity_id=draft_id,
    )
    return total_cost


def _execute_verify_claims(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from engine.quality import claims as claims_mod

    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    draft_id: uuid.UUID | None = params.get("draft_id")
    offline: bool | None = params.get("offline")
    jev_api_key: str | None = params.get("jev_api_key")
    resolved_llm: LLMPort | None = params.get("llm_adapter")

    _ensure_mcp_offline_allowed(offline)
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    target_run_id, claim_texts, passages = _resolve_verify_claims_inputs(conn, params)
    _execute_preflight_mcp_spend(
        conn, workspace_id=workspace_id, run_id=target_run_id, estimated_cost=Decimal("0.000000")
    )

    if resolved_llm is None and not jev_api_key:
        resolved_llm, _ = _resolve_mcp_llm(
            conn,
            workspace_id=workspace_id,
            run_id=target_run_id or uuid.UUID(int=0),
            offline=offline,
            llm_adapter=None,
        )

    results = claims_mod.verify_claims_against_sources(
        claim_texts, passages, llm=resolved_llm, jev_api_key=jev_api_key
    )
    total_cost = _record_verify_claims_usage(
        conn,
        workspace_id=workspace_id,
        run_id=target_run_id,
        draft_id=draft_id,
        results=results,
        jev_api_key=jev_api_key,
        jev_model=claims_mod.JEV_MODEL,
    )

    serialized = [
        {
            "claim": r.claim,
            "verdict": r.verdict,
            "confidence": r.confidence,
            "evidence_quote": r.evidence_quote,
            "cost_usd": r.cost_usd,
            "judge": r.judge,
        }
        for r in results
    ]
    return {
        "workspace_id": workspace_id,
        "draft_id": draft_id,
        "run_id": target_run_id,
        "verified_count": len(serialized),
        "supported_count": sum(1 for r in results if r.verdict == "supported"),
        "refuted_count": sum(1 for r in results if r.verdict == "refuted"),
        "unverifiable_count": sum(1 for r in results if r.verdict == "unverifiable"),
        "total_cost_usd": total_cost,
        "results": serialized,
    }


def verify_claims(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    draft_id: uuid.UUID | None = None,
    claims: Sequence[str] | None = None,
    passages: Sequence[str] | None = None,
    run_id: uuid.UUID | None = None,
    offline: bool | None = None,
    llm_adapter: LLMPort | None = None,
    jev_api_key: str | None = None,
) -> dict[str, Any]:
    """Verify factual claims at runtime without evals/ and record usage (ADR-026, M5-05)."""
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "draft_id": draft_id,
        "claims": claims,
        "passages": passages,
        "run_id": run_id,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "jev_api_key": jev_api_key,
    }
    if conn is not None:
        return _execute_verify_claims(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_verify_claims(managed_conn, params)


def get_draft_workspace_id(
    conn: psycopg.Connection[Any] | None = None,
    *,
    draft_id: uuid.UUID,
) -> uuid.UUID | None:
    """Resolve owning workspace UUID for a draft ID without trusting caller hints."""
    if conn is not None:
        return db.get_draft_workspace_id(conn, draft_id=draft_id)
    with db.get_connection() as managed_conn:
        return db.get_draft_workspace_id(managed_conn, draft_id=draft_id)


def _resolve_brief_cited_documents(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_row: dict[str, Any],
) -> list[dict[str, Any]]:
    payload = brief_row.get("payload") or {}
    doc_ids = [
        uuid.UUID(str(rf["source_id"]))
        for rf in (payload.get("required_facts") or [])
        if isinstance(rf, dict) and rf.get("source_id")
    ]
    cited: list[dict[str, Any]] = []
    for doc_id in dict.fromkeys(doc_ids):
        doc = db.get_document(conn, workspace_id=workspace_id, document_id=doc_id)
        if doc is not None:
            cited.append(
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "url": doc["url"],
                    "canonical_url": doc["canonical_url"],
                    "published_at": doc.get("published_at"),
                    "date_status": doc.get("date_status"),
                }
            )
    return cited


def _execute_get_or_create_mcp_brief(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    opportunity_id: uuid.UUID = params["opportunity_id"]
    user_id: uuid.UUID = params["user_id"]
    offline: bool | None = params.get("offline")

    _ensure_mcp_offline_allowed(offline)
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    opp_ctx = db.get_opportunity_context(
        conn, workspace_id=workspace_id, opportunity_id=opportunity_id
    )
    if opp_ctx is None:
        msg = f"Opportunity {opportunity_id} not found"
        raise ValueError(msg)

    run_id = uuid.UUID(str(opp_ctx["run_id"]))
    existing = db.get_latest_brief_for_opportunity(
        conn, workspace_id=workspace_id, opportunity_id=opportunity_id
    )
    created = False
    if existing is None or params.get("force_regenerate"):
        _execute_preflight_mcp_spend(
            conn, workspace_id=workspace_id, run_id=run_id, estimated_cost=Decimal("0.000000")
        )
        resolved_llm, eff_offline = _resolve_mcp_llm(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            offline=offline,
            llm_adapter=params.get("llm_adapter"),
        )
        brief_row = _execute_generate_brief(
            conn,
            {
                "workspace_id": workspace_id,
                "opportunity_id": opportunity_id,
                "user_id": user_id,
                "offline": eff_offline,
                "llm_adapter": resolved_llm,
                "keyword_provider": params.get("keyword_provider"),
                "news_search_adapter": params.get("news_search_adapter"),
                "extractor_adapter": params.get("extractor_adapter"),
            },
        )
        created = True
    else:
        brief_row = existing

    payload = brief_row.get("payload") or {}
    return {
        **brief_row,
        "run_id": run_id,
        "created": created,
        "primary_keyword": payload.get("primary_keyword"),
        "outline": payload.get("outline"),
        "cited_documents": _resolve_brief_cited_documents(
            conn, workspace_id=workspace_id, brief_row=brief_row
        ),
    }


def get_or_create_mcp_brief(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    user_id: uuid.UUID,
    force_regenerate: bool = False,
    offline: bool | None = None,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any]:
    """Return latest brief or generate a budget-preflighted editorial brief via MCP (M5-05)."""
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "opportunity_id": opportunity_id,
        "user_id": user_id,
        "force_regenerate": force_regenerate,
        "offline": offline,
        "llm_adapter": llm_adapter,
    }
    if conn is not None:
        return _execute_get_or_create_mcp_brief(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_get_or_create_mcp_brief(managed_conn, params)


def _execute_generate_mcp_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    brief_id: uuid.UUID = params["brief_id"]
    user_id: uuid.UUID = params["user_id"]
    offline: bool | None = params.get("offline")

    _ensure_mcp_offline_allowed(offline)
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    brief_ctx = db.get_brief_writing_context(conn, workspace_id=workspace_id, brief_id=brief_id)
    if brief_ctx is None:
        msg = f"Brief {brief_id} not found"
        raise ValueError(msg)

    run_id = uuid.UUID(str(brief_ctx["run_id"]))
    _execute_preflight_mcp_spend(
        conn, workspace_id=workspace_id, run_id=run_id, estimated_cost=Decimal("0.000000")
    )
    resolved_llm, eff_offline = _resolve_mcp_llm(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        offline=offline,
        llm_adapter=params.get("llm_adapter"),
    )
    draft_row = _execute_write_draft(
        conn,
        {
            "workspace_id": workspace_id,
            "brief_id": brief_id,
            "user_id": user_id,
            "findings": params.get("findings"),
            "offline": eff_offline,
            "llm_adapter": resolved_llm,
            "model": params.get("model"),
        },
    )
    claims = db.list_draft_claims(
        conn, workspace_id=workspace_id, draft_id=uuid.UUID(str(draft_row["id"]))
    )
    return {**draft_row, "run_id": run_id, "claims": claims}


def generate_mcp_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
    user_id: uuid.UUID,
    findings: list[Any] | None = None,
    offline: bool | None = None,
    llm_adapter: LLMPort | None = None,
) -> dict[str, Any]:
    """Generate a spend-guarded FK-grounded draft and return persisted claims (M5-05)."""
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "brief_id": brief_id,
        "user_id": user_id,
        "findings": findings,
        "offline": offline,
        "llm_adapter": llm_adapter,
    }
    if conn is not None:
        return _execute_generate_mcp_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_generate_mcp_draft(managed_conn, params)


def _execute_evaluate_mcp_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    workspace_id: uuid.UUID = params["workspace_id"]
    draft_id: uuid.UUID = params["draft_id"]
    user_id: uuid.UUID = params["user_id"]
    offline: bool | None = params.get("offline")

    _ensure_mcp_offline_allowed(offline)
    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    ctx = db.get_draft_evaluation_context(conn, workspace_id=workspace_id, draft_id=draft_id)
    if ctx is None:
        msg = f"Draft {draft_id} not found"
        raise ValueError(msg)

    run_id = uuid.UUID(str(ctx["run_id"]))
    _execute_preflight_mcp_spend(
        conn, workspace_id=workspace_id, run_id=run_id, estimated_cost=Decimal("0.000000")
    )
    resolved_llm: LLMPort | None = params.get("llm_adapter")
    eff_offline = bool(offline)
    det_only = bool(params.get("deterministic_only", False))
    if not det_only and params.get("llm_evaluator") is None:
        resolved_llm, eff_offline = _resolve_mcp_llm(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            offline=offline,
            llm_adapter=resolved_llm,
        )

    outcome = _execute_evaluate_draft(
        conn,
        {
            "workspace_id": workspace_id,
            "draft_id": draft_id,
            "user_id": user_id,
            "offline": eff_offline,
            "llm_adapter": resolved_llm,
            "llm_evaluator": params.get("llm_evaluator"),
            "model": params.get("model"),
            "options": params.get("options"),
            "deterministic_only": det_only,
        },
    )
    report_obj = outcome["report"]
    draft_out = outcome.get("draft") or {}
    return {
        "draft_id": draft_id,
        "run_id": run_id,
        "status": draft_out.get("status"),
        "revision": int(draft_out.get("revision") or ctx["revision"]),
        "verdict": report_obj.verdict,
        "score": report_obj.score,
        "findings": [f.model_dump(mode="json") for f in report_obj.findings],
        "route": outcome["route"],
        "surfaced_to_owner": outcome["surfaced_to_owner"],
        "topic_returned_to_pool": outcome["topic_returned_to_pool"],
        "quality_report": outcome["quality_report"],
    }


def evaluate_mcp_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    offline: bool | None = None,
    llm_adapter: LLMPort | None = None,
    llm_evaluator: Any = None,
    deterministic_only: bool = False,
) -> dict[str, Any]:
    """Run spend-preflighted deterministic and rubric draft evaluation via MCP (M5-05)."""
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "draft_id": draft_id,
        "user_id": user_id,
        "offline": offline,
        "llm_adapter": llm_adapter,
        "llm_evaluator": llm_evaluator,
        "deterministic_only": deterministic_only,
    }
    if conn is not None:
        return _execute_evaluate_mcp_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_evaluate_mcp_draft(managed_conn, params)


def get_weekly_report_resource(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Return latest weekly report with Gate 1 topics and Gate 2 awaiting queue (M5-05)."""

    def _exec(c: psycopg.Connection[Any]) -> dict[str, Any]:
        require_member(c, workspace_id=workspace_id, user_id=user_id)
        runs = db.list_runs(c, workspace_id=workspace_id)
        report_data = (
            _execute_report(c, workspace_id=workspace_id, run_id=uuid.UUID(str(runs[0]["id"])))
            if runs
            else {"workspace_id": workspace_id, "run_id": None, "total_count": 0, "items": []}
        )
        awaiting_topics = _execute_list_topics_for_approval(
            c, workspace_id=workspace_id, user_id=user_id, run_id=None, limit=50
        )
        awaiting_drafts = _execute_list_draft_reviews(
            c, workspace_id=workspace_id, user_id=user_id, include_decided=False
        )
        return {
            **report_data,
            "awaiting_topics": awaiting_topics,
            "awaiting_drafts_count": len(awaiting_drafts),
        }

    if conn is not None:
        return _exec(conn)
    with db.get_connection() as managed_conn:
        return _exec(managed_conn)


def _create_completed_adhoc_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Create a completed ad-hoc run without colliding with runs_one_active."""
    campaigns = db.list_campaigns(conn, workspace_id=workspace_id)
    if not campaigns:
        msg = f"Workspace {workspace_id} has no campaign configured."
        raise ValueError(msg)
    camp = campaigns[0]
    now = datetime.now(UTC)
    return db.create_run(
        conn,
        workspace_id=workspace_id,
        campaign_id=uuid.UUID(str(camp["id"])),
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=Decimal(str(camp.get("run_cap_usd") or "10.00")),
        code_version="m5-07",
        pack_version="v1",
        created_by=user_id,
        status="completed",
    )


def _persist_external_doc_and_record(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    doc_spec: dict[str, Any],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Persist an external document and its research_record row."""
    title = str(doc_spec.get("title") or "External Research Document").strip()
    raw_url = str(doc_spec.get("url") or "").strip()
    if raw_url:
        safe_http.validate_safe_url(raw_url)
        url = raw_url
    else:
        url = f"https://external.macy.local/doc/{uuid.uuid4()}"
    published_at = doc_spec.get("published_at") or datetime.now(UTC)
    summary = str(doc_spec.get("summary") or title)
    doc_row = db.upsert_document(
        conn,
        workspace_id=workspace_id,
        url=url,
        canonical_url=url,
        title=title,
        published_at=published_at,
        date_status="verified",
        fetch_status="ok",
        extracted_text=summary,
    )
    doc_id = uuid.UUID(str(doc_row["id"]))
    rec_row = db.upsert_research_record(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        document_id=doc_id,
        status="extracted",
        schema_version=1,
        payload={
            "headline": title,
            "url": url,
            "source": str(doc_spec.get("source_name") or "external"),
            "summary": summary,
            "facts": list(doc_spec.get("facts") or []),
            "published_at": str(published_at),
        },
    )
    return doc_id, uuid.UUID(str(rec_row["id"]))


def ingest_external_research(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    topic: str,
    documents: Sequence[dict[str, Any]],
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Persist external research documents and records into a workspace (M5-07)."""

    def _exec(c: psycopg.Connection[Any]) -> dict[str, Any]:
        require_member(c, workspace_id=workspace_id, user_id=user_id)
        resolved_run_id = run_id or _create_completed_adhoc_run(
            c, workspace_id=workspace_id, user_id=user_id
        )
        doc_ids: list[uuid.UUID] = []
        rec_ids: list[uuid.UUID] = []
        for doc_spec in documents:
            doc_id, rec_id = _persist_external_doc_and_record(
                c, workspace_id=workspace_id, run_id=resolved_run_id, doc_spec=doc_spec
            )
            doc_ids.append(doc_id)
            rec_ids.append(rec_id)
        return {
            "run_id": resolved_run_id,
            "topic": topic,
            "document_ids": doc_ids,
            "record_ids": rec_ids,
        }

    if conn is not None:
        return _exec(conn)
    with db.get_connection() as managed_conn:
        return _exec(managed_conn)


def _ensure_external_brief(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    draft_model: ContentDraft,
    format_slug: str,
    topic: str | None,
    primary_keyword: str | None,
    sources: Sequence[dict[str, Any]] | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create ad-hoc run, anchor record, opportunity, and brief for external draft import."""
    from engine.contracts import EditorialBrief

    run_id = _create_completed_adhoc_run(conn, workspace_id=workspace_id, user_id=user_id)
    seed_docs = (
        list(sources)
        if sources
        else [
            {"title": f"External Draft Anchor: {draft_model.title}", "summary": draft_model.title}
        ]
    )
    anchor_rec_id: uuid.UUID | None = None
    for doc_spec in seed_docs:
        _doc_id, rec_id = _persist_external_doc_and_record(
            conn, workspace_id=workspace_id, run_id=run_id, doc_spec=doc_spec
        )
        if anchor_rec_id is None:
            anchor_rec_id = rec_id

    if anchor_rec_id is None:
        msg = "Anchor record was not persisted."
        raise RuntimeError(msg)
    opp = db.upsert_opportunity(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        record_id=anchor_rec_id,
        scores={"external": 1},
        total=Decimal("80.00"),
        angle=topic or draft_model.title,
        funnel_stage="mofu",
        confidence="medium",
        status="approved",
        decided_by=user_id,
        decided_at=datetime.now(UTC),
    )
    brand = db.get_current_brand_profile(conn, workspace_id=workspace_id)
    if brand is None:
        msg = f"Workspace {workspace_id} has no active brand profile."
        raise ValueError(msg)

    resolved_topic = topic or draft_model.title
    resolved_kw = primary_keyword or draft_model.title
    brief_model = EditorialBrief(
        brand_profile_id=uuid.UUID(str(brand["id"])),
        topic=resolved_topic,
        target_audience="General",
        primary_keyword=resolved_kw,
        format=format_slug,
        angle=resolved_topic,
        outline=["Introduction"],
        cta_goal=str(draft_model.cta.get("headline") or "Saiba mais"),
    )
    brief_row = db.create_next_brief_version(
        conn,
        workspace_id=workspace_id,
        opportunity_id=uuid.UUID(str(opp["id"])),
        brand_profile_id=uuid.UUID(str(brand["id"])),
        prompt_version="external-import:v1",
        keywords=[{"keyword": resolved_kw, "role": "primary"}],
        schema_version=brief_model.schema_version,
        payload=brief_model.model_dump(mode="json"),
    )
    return uuid.UUID(str(brief_row["id"])), run_id


def _execute_import_external_draft(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from engine.production import writer

    workspace_id: uuid.UUID = params["workspace_id"]
    user_id: uuid.UUID = params["user_id"]
    draft_model: ContentDraft = params["draft_model"]
    brief_id: uuid.UUID | None = params.get("brief_id")
    require_member(conn, workspace_id=workspace_id, user_id=user_id)

    if brief_id is None:
        resolved_brief_id, resolved_run_id = _ensure_external_brief(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            draft_model=draft_model,
            format_slug=str(params.get("format_slug") or "blog"),
            topic=params.get("topic"),
            primary_keyword=params.get("primary_keyword"),
            sources=params.get("sources"),
        )
    else:
        brief_ctx = db.get_brief_writing_context(conn, workspace_id=workspace_id, brief_id=brief_id)
        if brief_ctx is None:
            msg = f"Brief {brief_id} not found in workspace {workspace_id}"
            raise ValueError(msg)
        resolved_brief_id = brief_id
        resolved_run_id = uuid.UUID(str(brief_ctx["run_id"]))

    latest = db.get_latest_draft_for_brief(
        conn, workspace_id=workspace_id, brief_id=resolved_brief_id
    )
    next_revision = 0 if latest is None else int(latest["revision"]) + 1
    if next_revision > writer.MAX_DRAFT_REVISION:
        msg = f"Brief {resolved_brief_id} already reached max revision {writer.MAX_DRAFT_REVISION}."
        raise writer.DraftRevisionLimitReachedError(msg)

    claim_rows = writer.validate_draft_claim_provenance(
        draft_model, conn=conn, workspace_id=workspace_id
    )
    draft_row = db.create_next_draft_revision(
        conn,
        workspace_id=workspace_id,
        brief_id=resolved_brief_id,
        prompt_version="external-import:v1",
        schema_version=draft_model.schema_version,
        payload=draft_model.model_dump(mode="json"),
        claims=claim_rows,
        revision=next_revision,
    )
    draft_id = uuid.UUID(str(draft_row["id"]))
    eval_res = _execute_evaluate_draft(
        conn,
        {
            "workspace_id": workspace_id,
            "draft_id": draft_id,
            "user_id": user_id,
            "offline": bool(params.get("offline", False)),
            "deterministic_only": bool(params.get("deterministic_only", True)),
        },
    )
    report_obj = eval_res["report"]
    return {
        "draft_id": draft_id,
        "brief_id": resolved_brief_id,
        "run_id": resolved_run_id,
        "revision": next_revision,
        "status": eval_res["draft"]["status"],
        "verdict": report_obj.verdict,
        "score": report_obj.score,
        "findings": [f.model_dump(mode="json") for f in report_obj.findings],
    }


def _load_draft_model(
    *,
    file_path: Path | str | None = None,
    markdown_text: str | None = None,
    topic: str | None = None,
) -> ContentDraft:
    from engine.production import writer

    if file_path is not None:
        raw_md = writer.extract_file_to_markdown(file_path)
    elif markdown_text is not None:
        raw_md = markdown_text
    else:
        msg = "Either file_path or markdown_text is required."
        raise ValueError(msg)
    return writer.parse_markdown_draft(raw_md, default_title=topic)


def check_draft_stateless(  # noqa: PLR0913
    *,
    file_path: Path | str | None = None,
    markdown_text: str | None = None,
    format_slug: str = "blog",
    topic: str | None = None,
    primary_keyword: str | None = None,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Run deterministic draft compliance + JSON-LD generation without DB or LLM (Path B)."""
    from engine.production import schema as schema_mod
    from engine.quality import evaluate as evaluate_fn

    draft_model = _load_draft_model(
        file_path=file_path,
        markdown_text=markdown_text,
        topic=topic,
    )
    resolved_kw = primary_keyword or draft_model.title
    report_obj = evaluate_fn(
        draft_model,
        format_slug=format_slug,
        deterministic_only=True,
        primary_keyword=resolved_kw,
        locale=locale,
    )
    schema_json_ld = schema_mod.generate_schema(
        "Article",
        draft=draft_model,
        locale=locale,
    )
    return {
        "stateless": True,
        "title": draft_model.title,
        "format_slug": format_slug,
        "verdict": report_obj.verdict,
        "score": report_obj.score,
        "findings": [f.model_dump(mode="json") for f in report_obj.findings],
        "schema_json_ld": schema_json_ld,
    }


def import_external_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    file_path: Path | str | None = None,
    markdown_text: str | None = None,
    brief_id: uuid.UUID | None = None,
    format_slug: str = "blog",
    topic: str | None = None,
    primary_keyword: str | None = None,
    sources: Sequence[dict[str, Any]] | None = None,
    deterministic_only: bool = True,
    offline: bool = False,
) -> dict[str, Any]:
    """Import an external .md/.pdf or Markdown draft and evaluate compliance (M5-07)."""
    draft_model = _load_draft_model(
        file_path=file_path,
        markdown_text=markdown_text,
        topic=topic,
    )
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "draft_model": draft_model,
        "brief_id": brief_id,
        "format_slug": format_slug,
        "topic": topic,
        "primary_keyword": primary_keyword,
        "sources": sources,
        "deterministic_only": deterministic_only,
        "offline": offline,
    }
    if conn is not None:
        return _execute_import_external_draft(conn, params)
    with db.get_connection() as managed_conn:
        return _execute_import_external_draft(managed_conn, params)


def _persist_url_audit(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    report: Any,
) -> uuid.UUID:
    if user_id is not None:
        _execute_require_member(conn, workspace_id=workspace_id, user_id=user_id)
    return db.insert_audit_page(conn, workspace_id=workspace_id, page=report)


def run_url_audit(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    url: str,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    crawl_config: CrawlConfig | None = None,
    check_external: bool = False,
) -> dict[str, Any]:
    """Run a single-URL SEO + GEO audit and optionally persist to Postgres (M6-02 D4)."""
    report = _run_url_audit_core(
        url,
        crawl_config=crawl_config,
        check_external=check_external,
    )
    audit_id: uuid.UUID | None = None
    if workspace_id is not None:
        if conn is not None:
            audit_id = _persist_url_audit(
                conn, workspace_id=workspace_id, user_id=user_id, report=report
            )
        else:
            with db.get_connection() as managed_conn:
                audit_id = _persist_url_audit(
                    managed_conn, workspace_id=workspace_id, user_id=user_id, report=report
                )
    return {**report.model_dump(mode="json"), "audit_id": audit_id}


def _persist_site_crawl(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    report: Any,
) -> uuid.UUID:
    if user_id is not None:
        _execute_require_member(conn, workspace_id=workspace_id, user_id=user_id)
    site_audit_id = db.insert_site_audit(conn, workspace_id=workspace_id, report=report)
    for page in report.pages:
        db.insert_audit_page(
            conn,
            workspace_id=workspace_id,
            site_audit_id=site_audit_id,
            page=page,
        )
    return site_audit_id


def run_site_crawl(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    start_url: str,
    config: CrawlConfig | None = None,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    sitemap_url: str | None = None,
) -> dict[str, Any]:
    """Run a bounded site crawl and optionally persist summary + page audits (M6-02 D4)."""
    report = _run_site_crawl_core(start_url, config=config, sitemap_url=sitemap_url)
    site_audit_id: uuid.UUID | None = None
    if workspace_id is not None:
        if conn is not None:
            site_audit_id = _persist_site_crawl(
                conn, workspace_id=workspace_id, user_id=user_id, report=report
            )
        else:
            with db.get_connection() as managed_conn:
                site_audit_id = _persist_site_crawl(
                    managed_conn, workspace_id=workspace_id, user_id=user_id, report=report
                )
    return {**report.model_dump(mode="json"), "site_audit_id": site_audit_id}


def set_local_password(
    conn: psycopg.Connection[Any] | None = None,
    *,
    user_id: uuid.UUID,
    password: str,
) -> bool:
    """Hash and persist a PBKDF2-HMAC-SHA256 local password for a user (ADR-030)."""
    pw_hash = passwords.hash_password(password)
    if conn is not None:
        return db.set_user_password_hash(conn, user_id=user_id, password_hash=pw_hash)
    with db.get_connection() as managed_conn:
        return db.set_user_password_hash(managed_conn, user_id=user_id, password_hash=pw_hash)


def _execute_authenticate_local_user(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    password: str,
) -> dict[str, Any]:
    clean_email = email.strip().lower()
    passwords.check_local_rate_limit(clean_email)
    row = db.get_user_auth_by_email(conn, email=clean_email)
    if row is None or not passwords.verify_password(password, row.get("password_hash")):
        passwords.record_local_login_failure(clean_email)
        msg = "Invalid email or password"
        raise passwords.AuthError(msg)
    passwords.clear_local_login_failures(clean_email)
    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "name": row.get("name"),
        "ui_locale": row.get("ui_locale"),
    }


def authenticate_local_user(
    conn: psycopg.Connection[Any] | None = None,
    *,
    email: str,
    password: str,
) -> dict[str, Any]:
    """Authenticate a local user with PBKDF2 verification and rate limiting (ADR-030)."""
    if conn is not None:
        return _execute_authenticate_local_user(conn, email=email, password=password)
    with db.get_connection() as managed_conn:
        return _execute_authenticate_local_user(managed_conn, email=email, password=password)


def _execute_get_enabled_capabilities(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> list[str]:
    if user_id is not None:
        _execute_require_member(conn, workspace_id=workspace_id, user_id=user_id)
    ws_config = db.get_workspace_config(conn, workspace_id=workspace_id)
    if ws_config is None:
        msg = f"Workspace {workspace_id} not found"
        raise ValueError(msg)
    raw = ws_config.get("enabled_capabilities")
    return capabilities.normalize_enabled_capabilities(raw if isinstance(raw, list) else None)


def get_enabled_capabilities(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> list[str]:
    """Return active capability slugs for a workspace (defaults to all built-in plugins)."""
    if conn is not None:
        return _execute_get_enabled_capabilities(conn, workspace_id=workspace_id, user_id=user_id)
    with db.get_connection() as managed_conn:
        return _execute_get_enabled_capabilities(
            managed_conn, workspace_id=workspace_id, user_id=user_id
        )


def _execute_set_enabled_capabilities(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled_capabilities: Sequence[str],
) -> list[str]:
    role = _execute_require_member(conn, workspace_id=workspace_id, user_id=user_id)
    if role != "owner":
        msg = f"User {user_id} with role '{role}' is forbidden from updating capabilities"
        raise PermissionError(msg)
    normalized = capabilities.normalize_enabled_capabilities(enabled_capabilities)
    existing = db.get_workspace_config(conn, workspace_id=workspace_id) or {}
    updated_config = {**existing, "enabled_capabilities": normalized}
    db.update_workspace_config(conn, workspace_id=workspace_id, config=updated_config)
    events.emit(
        conn,
        workspace_id=workspace_id,
        event_type="workspace.capabilities_updated",
        entity_type="workspace",
        entity_id=workspace_id,
        payload={"enabled_capabilities": normalized},
        actor_user_id=user_id,
    )
    return normalized


def set_enabled_capabilities(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    enabled_capabilities: Sequence[str],
) -> list[str]:
    """Persist workspace capability switchboard selection and emit audit event (ADR-029)."""
    if conn is not None:
        return _execute_set_enabled_capabilities(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            enabled_capabilities=enabled_capabilities,
        )
    with db.get_connection() as managed_conn:
        return _execute_set_enabled_capabilities(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            enabled_capabilities=enabled_capabilities,
        )


def needs_first_boot_wizard(conn: psycopg.Connection[Any] | None = None) -> bool:
    """Return True when the database contains zero workspaces and needs onboarding."""
    if conn is not None:
        return len(db.list_workspaces(conn)) == 0
    with db.get_connection() as managed_conn:
        return len(db.list_workspaces(managed_conn)) == 0


def _execute_bootstrap_onboarding(
    conn: psycopg.Connection[Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    with conn.transaction():
        if db.list_workspaces(conn):
            msg = "Initial onboarding is disabled because a workspace already exists."
            raise PermissionError(msg)

        ws = _execute_create_workspace(
            conn,
            slug=str(params["workspace_slug"]),
            name=str(params["workspace_name"]),
            industry_pack=str(params["industry_pack"]),
            timezone=str(params["timezone"]),
        )
        ws_id = uuid.UUID(str(ws["workspace_id"]))
        owner = _execute_add_workspace_owner(
            conn,
            workspace_slug=ws["slug"],
            email=str(params["email"]),
        )
        user = owner["user"]
        user_id = uuid.UUID(str(user["user_id"]))

        password: str | None = params.get("password")
        if password is not None:
            set_local_password(conn, user_id=user_id, password=password)

        raw_caps = params.get("enabled_capabilities")
        target_caps = list(raw_caps) if raw_caps is not None else ["core-compliance", "seo-audit"]
        caps = _execute_set_enabled_capabilities(
            conn,
            workspace_id=ws_id,
            user_id=user_id,
            enabled_capabilities=target_caps,
        )
        integrations = _execute_apply_integration_preset(
            conn,
            {
                "workspace_id": ws_id,
                "user_id": user_id,
                "preset_name": str(params.get("preset_name") or "pocket_free"),
                "port_overrides": params.get("port_overrides") or {},
                "encryption_key": None,
            },
        )
        events.emit(
            conn,
            workspace_id=ws_id,
            event_type="onboarding.bootstrapped",
            entity_type="workspace",
            entity_id=ws_id,
            payload={
                "workspace_slug": ws["slug"],
                "preset": str(params.get("preset_name") or "pocket_free"),
                "enabled_capabilities": caps,
            },
            actor_user_id=user_id,
        )

    return {
        "workspace": ws,
        "user": user,
        "enabled_capabilities": caps,
        "integrations": integrations,
    }


def bootstrap_onboarding(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    email: str,
    password: str | None = None,
    workspace_slug: str,
    workspace_name: str,
    industry_pack: str | None = None,
    timezone: str = "America/Sao_Paulo",
    enabled_capabilities: Sequence[str] | None = None,
    preset_name: str = "pocket_free",
    port_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically provision first owner, workspace, capabilities, and integration preset."""
    resolved_pack = industry_pack or config.get_default_industry_pack()
    payload: dict[str, Any] = {
        "email": email,
        "password": password,
        "workspace_slug": workspace_slug,
        "workspace_name": workspace_name,
        "industry_pack": resolved_pack,
        "timezone": timezone,
        "enabled_capabilities": list(enabled_capabilities)
        if enabled_capabilities is not None
        else None,
        "preset_name": preset_name,
        "port_overrides": port_overrides or {},
    }
    if conn is not None:
        return _execute_bootstrap_onboarding(conn, payload)
    with db.get_connection() as managed_conn:
        return _execute_bootstrap_onboarding(managed_conn, payload)


def _execute_autoseed_from_url(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    site_url: str,
    timeout: float,
) -> dict[str, Any]:
    from engine.onboarding import extract_site_seed

    require_member(conn, workspace_id=workspace_id, user_id=user_id)
    extracted = extract_site_seed(site_url, timeout=timeout)
    brand_row = _execute_save_brand(
        conn,
        workspace_id=workspace_id,
        user_id=user_id,
        profile=extracted["brand_profile"],
    )
    src_meta = extracted["source"]
    source_row = _execute_save_source(
        conn,
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "name": src_meta["name"],
            "domain": src_meta["domain"],
            "url": src_meta["url"],
            "fetch_mode": "site_search",
            "tier": 1,
            "role": "company",
            "locales": [src_meta["locale"]],
            "feed_url": None,
            "topics": [],
            "enabled": True,
            "source_id": None,
        },
    )

    sitemap_imported = 0
    try:
        sitemap_res = _execute_import_sitemap(
            conn,
            {
                "workspace_id": workspace_id,
                "sitemap_url": extracted["sitemap_url"],
                "user_id": user_id,
                "fetch_titles": False,
                "timeout": timeout,
                "is_production": None,
            },
        )
        sitemap_imported = int(sitemap_res.get("total", 0))
    except (ValueError, RuntimeError, safe_http.SafeHTTPError):
        sitemap_imported = 0

    return {
        "brand_profile": brand_row,
        "source": source_row,
        "sitemap_imported": sitemap_imported,
        "sitemap_url": extracted["sitemap_url"],
    }


def autoseed_from_url(
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    site_url: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Auto-seed brand, company source, and optional sitemap from a website URL (ADR-031)."""
    if conn is not None:
        return _execute_autoseed_from_url(
            conn,
            workspace_id=workspace_id,
            user_id=user_id,
            site_url=site_url,
            timeout=timeout,
        )
    with db.get_connection() as managed_conn:
        return _execute_autoseed_from_url(
            managed_conn,
            workspace_id=workspace_id,
            user_id=user_id,
            site_url=site_url,
            timeout=timeout,
        )
