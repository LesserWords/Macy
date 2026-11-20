"""Database access layer for Schema v1 with strict tenant isolation (ADR-009, ADR-010)."""

import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DEFAULT_DATABASE_URL = "postgres://app:app_secret@localhost:5432/macy?sslmode=disable"


def get_connection(database_url: str | None = None) -> psycopg.Connection[Any]:
    """Open a new database connection with autocommit enabled."""
    url: str = database_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    return psycopg.connect(url, autocommit=True)


def create_workspace(
    conn: psycopg.Connection[Any],
    *,
    slug: str,
    name: str,
    industry_pack: str,
    timezone: str = "America/Sao_Paulo",
) -> uuid.UUID:
    """Create a new workspace and return its ID."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO workspaces (slug, name, industry_pack, timezone)
            VALUES (%(slug)s, %(name)s, %(industry_pack)s, %(timezone)s)
            RETURNING id AS workspace_id
            """,
            {
                "slug": slug,
                "name": name,
                "industry_pack": industry_pack,
                "timezone": timezone,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert workspace"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def get_workspace(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve workspace details by ID."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id AS workspace_id, slug, name, industry_pack, timezone,
                   COALESCE(config, '{}'::jsonb) AS config, created_at
            FROM workspaces
            WHERE id = %(workspace_id)s
            """,
            {"workspace_id": workspace_id},
        )
        return cur.fetchone()


def list_workspaces(
    conn: psycopg.Connection[Any],
) -> list[dict[str, Any]]:
    """List all workspaces in the system."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id AS workspace_id, slug, name, industry_pack, timezone,
                   COALESCE(config, '{}'::jsonb) AS config, created_at
            FROM workspaces
            ORDER BY created_at ASC
            """
        )
        return list(cur.fetchall())


def get_workspace_config(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve JSON config dictionary for a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT COALESCE(config, '{}'::jsonb) AS config
            FROM workspaces
            WHERE id = %(workspace_id)s
            """,
            {"workspace_id": workspace_id},
        )
        row = cur.fetchone()
        return dict(row["config"]) if row is not None else None


def update_workspace_config(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Persist JSON config dictionary for a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE workspaces
            SET config = %(config)s
            WHERE id = %(workspace_id)s
            RETURNING COALESCE(config, '{}'::jsonb) AS config
            """,
            {"workspace_id": workspace_id, "config": Jsonb(config)},
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Workspace {workspace_id} not found"
            raise ValueError(msg)
        return dict(row["config"])


def get_membership(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve membership of a user in a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, user_id, role, created_at
            FROM memberships
            WHERE workspace_id = %(workspace_id)s AND user_id = %(user_id)s
            """,
            {"workspace_id": workspace_id, "user_id": user_id},
        )
        return cur.fetchone()


def create_campaign(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    name: str,
    locale: str,
    market: str,
    articles_per_week: int,
    schedule_cron: str,
    monthly_cap_usd: Decimal,
    run_cap_usd: Decimal,
    config: dict[str, Any],
    config_schema_version: int,
    created_by: uuid.UUID,
) -> uuid.UUID:
    """Create a campaign scoped to a workspace."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO campaigns (
                workspace_id, name, locale, market, articles_per_week,
                schedule_cron, monthly_cap_usd, run_cap_usd, config,
                config_schema_version, created_by
            )
            VALUES (
                %(workspace_id)s, %(name)s, %(locale)s, %(market)s, %(articles_per_week)s,
                %(schedule_cron)s, %(monthly_cap_usd)s, %(run_cap_usd)s, %(config)s,
                %(config_schema_version)s, %(created_by)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "name": name,
                "locale": locale,
                "market": market,
                "articles_per_week": articles_per_week,
                "schedule_cron": schedule_cron,
                "monthly_cap_usd": monthly_cap_usd,
                "run_cap_usd": run_cap_usd,
                "config": Jsonb(config),
                "config_schema_version": config_schema_version,
                "created_by": created_by,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert campaign"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def get_campaign(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a campaign ensuring workspace boundary."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, name, locale, market, articles_per_week,
                   schedule_cron, monthly_cap_usd, run_cap_usd, config,
                   config_schema_version, status, created_by, created_at,
                   updated_by, updated_at
            FROM campaigns
            WHERE workspace_id = %(workspace_id)s AND id = %(campaign_id)s
            """,
            {"workspace_id": workspace_id, "campaign_id": campaign_id},
        )
        return cur.fetchone()


def list_campaigns(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all campaigns belonging to a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, name, locale, market, articles_per_week,
                   schedule_cron, monthly_cap_usd, run_cap_usd, config,
                   config_schema_version, status, created_by, created_at,
                   updated_by, updated_at
            FROM campaigns
            WHERE workspace_id = %(workspace_id)s
            ORDER BY created_at DESC
            """,
            {"workspace_id": workspace_id},
        )
        return list(cur.fetchall())


def update_campaign(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    name: str,
    locale: str,
    market: str,
    articles_per_week: int,
    schedule_cron: str,
    monthly_cap_usd: Decimal,
    run_cap_usd: Decimal,
    config: dict[str, Any],
    config_schema_version: int,
    updated_by: uuid.UUID,
) -> bool:
    """Modify an existing campaign within a workspace."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaigns
            SET name = %(name)s,
                locale = %(locale)s,
                market = %(market)s,
                articles_per_week = %(articles_per_week)s,
                schedule_cron = %(schedule_cron)s,
                monthly_cap_usd = %(monthly_cap_usd)s,
                run_cap_usd = %(run_cap_usd)s,
                config = %(config)s,
                config_schema_version = %(config_schema_version)s,
                updated_by = %(updated_by)s,
                updated_at = now()
            WHERE workspace_id = %(workspace_id)s AND id = %(campaign_id)s
            """,
            {
                "workspace_id": workspace_id,
                "campaign_id": campaign_id,
                "name": name,
                "locale": locale,
                "market": market,
                "articles_per_week": articles_per_week,
                "schedule_cron": schedule_cron,
                "monthly_cap_usd": monthly_cap_usd,
                "run_cap_usd": run_cap_usd,
                "config": Jsonb(config),
                "config_schema_version": config_schema_version,
                "updated_by": updated_by,
            },
        )
        return cur.rowcount > 0


def list_workspace_integrations(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List configured integration plugins for a workspace (without raw secrets)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, port, plugin, settings, key_version,
                   last_test_ok, last_tested_at, updated_by, updated_at
            FROM workspace_integrations
            WHERE workspace_id = %(workspace_id)s
            ORDER BY port
            """,
            {"workspace_id": workspace_id},
        )
        return list(cur.fetchall())


def get_workspace_integration(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    port: str,
) -> dict[str, Any] | None:
    """Retrieve a single workspace integration row by workspace and port."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, port, plugin, settings, secrets, key_version,
                   last_test_ok, last_tested_at, updated_by, updated_at
            FROM workspace_integrations
            WHERE workspace_id = %(workspace_id)s AND port = %(port)s
            """,
            {"workspace_id": workspace_id, "port": port},
        )
        return cur.fetchone()


def upsert_workspace_integration(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    port: str,
    plugin: str,
    settings: dict[str, Any],
    secrets: bytes | None,
    updated_by: uuid.UUID,
) -> dict[str, Any]:
    """Create or replace a workspace integration row (without returning raw secrets)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO workspace_integrations (
                workspace_id, port, plugin, settings, secrets, key_version, updated_by, updated_at
            )
            VALUES (
                %(workspace_id)s, %(port)s, %(plugin)s, %(settings)s,
                %(secrets)s, 1, %(updated_by)s, now()
            )
            ON CONFLICT (workspace_id, port) DO UPDATE
            SET plugin = EXCLUDED.plugin,
                settings = EXCLUDED.settings,
                secrets = EXCLUDED.secrets,
                key_version = EXCLUDED.key_version,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            RETURNING workspace_id, port, plugin, settings, key_version,
                      last_test_ok, last_tested_at, updated_by, updated_at
            """,
            {
                "workspace_id": workspace_id,
                "port": port,
                "plugin": plugin,
                "settings": Jsonb(settings),
                "secrets": secrets,
                "updated_by": updated_by,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to upsert workspace integration for port '{port}'"
            raise RuntimeError(msg)
        return row


def update_workspace_integration_test_status(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    port: str,
    last_test_ok: bool,
    last_tested_at: datetime,
) -> dict[str, Any] | None:
    """Record healthcheck result (last_test_ok and last_tested_at) for a workspace integration."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE workspace_integrations
            SET last_test_ok = %(last_test_ok)s,
                last_tested_at = %(last_tested_at)s
            WHERE workspace_id = %(workspace_id)s AND port = %(port)s
            RETURNING workspace_id, port, plugin, settings, key_version,
                      last_test_ok, last_tested_at, updated_by, updated_at
            """,
            {
                "workspace_id": workspace_id,
                "port": port,
                "last_test_ok": last_test_ok,
                "last_tested_at": last_tested_at,
            },
        )
        return cur.fetchone()


def create_run(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    trigger: str,
    window_start: datetime,
    window_end: datetime,
    run_cap_usd: Decimal,
    code_version: str,
    pack_version: str,
    created_by: uuid.UUID | None = None,
    status: str = "queued",
) -> uuid.UUID:
    """Create a new run belonging to a campaign and workspace."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (
                workspace_id, campaign_id, trigger, window_start,
                window_end, run_cap_usd, code_version, pack_version, created_by, status
            )
            VALUES (
                %(workspace_id)s, %(campaign_id)s, %(trigger)s, %(window_start)s,
                %(window_end)s, %(run_cap_usd)s, %(code_version)s, %(pack_version)s,
                %(created_by)s, %(status)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "campaign_id": campaign_id,
                "trigger": trigger,
                "window_start": window_start,
                "window_end": window_end,
                "run_cap_usd": run_cap_usd,
                "code_version": code_version,
                "pack_version": pack_version,
                "created_by": created_by,
                "status": status,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert run"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def get_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a run scoped to a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, campaign_id, trigger, status, stage,
                   window_start, window_end, run_cap_usd, cost_usd,
                   code_version, pack_version, blocked_reason, error,
                   created_by, started_at, finished_at, created_at
            FROM runs
            WHERE workspace_id = %(workspace_id)s AND id = %(run_id)s
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        return cur.fetchone()


def list_runs(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List runs for a workspace, optionally filtered by campaign."""
    with conn.cursor(row_factory=dict_row) as cur:
        if campaign_id is not None:
            cur.execute(
                """
                SELECT workspace_id, id, campaign_id, trigger, status, stage,
                       window_start, window_end, run_cap_usd, cost_usd,
                       code_version, pack_version, blocked_reason, error,
                       created_by, started_at, finished_at, created_at
                FROM runs
                WHERE workspace_id = %(workspace_id)s AND campaign_id = %(campaign_id)s
                ORDER BY created_at DESC
                """,
                {"workspace_id": workspace_id, "campaign_id": campaign_id},
            )
        else:
            cur.execute(
                """
                SELECT workspace_id, id, campaign_id, trigger, status, stage,
                       window_start, window_end, run_cap_usd, cost_usd,
                       code_version, pack_version, blocked_reason, error,
                       created_by, started_at, finished_at, created_at
                FROM runs
                WHERE workspace_id = %(workspace_id)s
                ORDER BY created_at DESC
                """,
                {"workspace_id": workspace_id},
            )
        return list(cur.fetchall())


def update_run_status(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    expected_status: str | None,
    new_status: str,
    stage: str | None = None,
    blocked_reason: str | None = None,
    error: dict[str, Any] | None = None,
    cost_usd: Decimal | None = None,
    run_cap_usd: Decimal | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> bool:
    """Guarded state transition on run status within a workspace."""
    with conn.cursor() as cur:
        query = """
            UPDATE runs
            SET status = %(new_status)s,
                stage = COALESCE(%(stage)s, stage),
                blocked_reason = CASE
                    WHEN %(new_status)s = 'blocked' THEN
                        COALESCE(%(blocked_reason)s, blocked_reason)
                    WHEN %(new_status)s IN ('running', 'completed') THEN NULL
                    ELSE blocked_reason
                END,
                error = COALESCE(%(error)s, error),
                cost_usd = COALESCE(%(cost_usd)s, cost_usd),
                run_cap_usd = COALESCE(%(run_cap_usd)s, run_cap_usd),
                started_at = COALESCE(%(started_at)s, started_at),
                finished_at = COALESCE(%(finished_at)s, finished_at)
            WHERE workspace_id = %(workspace_id)s
              AND id = %(run_id)s
        """
        params: dict[str, Any] = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "new_status": new_status,
            "stage": stage,
            "blocked_reason": blocked_reason,
            "error": Jsonb(error) if error is not None else None,
            "cost_usd": cost_usd,
            "run_cap_usd": run_cap_usd,
            "started_at": started_at,
            "finished_at": finished_at,
            "expected_status": expected_status,
        }
        if expected_status is not None:
            query += " AND status = %(expected_status)s"

        cur.execute(query, params)
        return cur.rowcount > 0


def claim_next_queued_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Claim the next queued run for execution using FOR UPDATE SKIP LOCKED."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, campaign_id, trigger, status, stage,
                   window_start, window_end, run_cap_usd, cost_usd,
                   code_version, pack_version, blocked_reason, error,
                   created_by, started_at, finished_at, created_at
            FROM runs
            WHERE status = 'queued'
              AND (%(workspace_id)s::uuid IS NULL OR workspace_id = %(workspace_id)s)
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            {"workspace_id": workspace_id},
        )
        return cur.fetchone()


def get_run_total_cost(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Decimal:
    """Sum total cost in usage_ledger for a given run."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0.000000)
            FROM usage_ledger
            WHERE workspace_id = %(workspace_id)s AND run_id = %(run_id)s
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        row = cur.fetchone()
        return Decimal(str(row[0])) if row and row[0] is not None else Decimal("0.000000")


def get_monthly_spend(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    reference_date: datetime | None = None,
) -> Decimal:
    """Calculate cumulative spend for the workspace in the current calendar month."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0.000000)
            FROM usage_ledger
            WHERE workspace_id = %(workspace_id)s
              AND created_at >= date_trunc('month', COALESCE(%(ref_date)s, now()))
              AND created_at < (
                  date_trunc('month', COALESCE(%(ref_date)s, now())) + interval '1 month'
              )
            """,
            {"workspace_id": workspace_id, "ref_date": reference_date},
        )
        row = cur.fetchone()
        return Decimal(str(row[0])) if row and row[0] is not None else Decimal("0.000000")


def insert_event(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    event_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Append-only insert into events for a workspace."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (
                workspace_id, type, entity_type, entity_id, payload, actor_user_id
            )
            VALUES (
                %(workspace_id)s, %(type)s, %(entity_type)s,
                %(entity_id)s, %(payload)s, %(actor_user_id)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": Jsonb(payload),
                "actor_user_id": actor_user_id,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert event"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def list_events(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    limit: int = 50,
    event_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List recent events for a workspace, optionally filtered by event_type and entity_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, type, actor_user_id, entity_type,
                   entity_id, payload, occurred_at
            FROM events
            WHERE workspace_id = %(workspace_id)s
              AND (%(event_type)s::text IS NULL OR type = %(event_type)s)
              AND (%(entity_id)s::uuid IS NULL OR entity_id = %(entity_id)s)
            ORDER BY occurred_at DESC
            LIMIT %(limit)s
            """,
            {
                "workspace_id": workspace_id,
                "limit": limit,
                "event_type": event_type,
                "entity_id": entity_id,
            },
        )
        return list(cur.fetchall())


def record_usage(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    stage: str,
    kind: str,
    vendor: str,
    cost_usd: Decimal,
    run_id: uuid.UUID | None = None,
    model: str | None = None,
    generation_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    units: Decimal | None = None,
    latency_ms: int | None = None,
    prompt_version: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record LLM or API consumption in usage ledger."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO usage_ledger (
                workspace_id, run_id, stage, kind, vendor, model,
                generation_id, input_tokens, output_tokens, cached_tokens,
                units, cost_usd, latency_ms, prompt_version, entity_type, entity_id
            )
            VALUES (
                %(workspace_id)s, %(run_id)s, %(stage)s, %(kind)s, %(vendor)s, %(model)s,
                %(generation_id)s, %(input_tokens)s, %(output_tokens)s, %(cached_tokens)s,
                %(units)s, %(cost_usd)s, %(latency_ms)s, %(prompt_version)s,
                %(entity_type)s, %(entity_id)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "stage": stage,
                "kind": kind,
                "vendor": vendor,
                "model": model,
                "generation_id": generation_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "units": units,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "prompt_version": prompt_version,
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert usage ledger entry"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def record_llm_payload(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    usage_id: uuid.UUID,
    request: dict[str, Any],
    response: dict[str, Any],
) -> None:
    """Store LLM request and response JSON payloads linked to usage ledger."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_payloads (usage_id, request, response)
            SELECT %(usage_id)s, %(request)s, %(response)s
            FROM usage_ledger
            WHERE workspace_id = %(workspace_id)s AND id = %(usage_id)s
            """,
            {
                "workspace_id": workspace_id,
                "usage_id": usage_id,
                "request": Jsonb(request),
                "response": Jsonb(response),
            },
        )


def get_llm_payload(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    usage_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve LLM payload ensuring workspace tenancy boundary."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT p.usage_id, p.request, p.response
            FROM llm_payloads p
            JOIN usage_ledger u ON p.usage_id = u.id
            WHERE u.workspace_id = %(workspace_id)s AND p.usage_id = %(usage_id)s
            """,
            {"workspace_id": workspace_id, "usage_id": usage_id},
        )
        return cur.fetchone()


def list_usage(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Query usage records for a workspace, optionally filtered by run."""
    with conn.cursor(row_factory=dict_row) as cur:
        if run_id is not None:
            cur.execute(
                """
                SELECT workspace_id, id, run_id, stage, kind, vendor, model,
                       generation_id, input_tokens, output_tokens, cached_tokens,
                       units, cost_usd, latency_ms, prompt_version, entity_type,
                       entity_id, created_at
                FROM usage_ledger
                WHERE workspace_id = %(workspace_id)s AND run_id = %(run_id)s
                ORDER BY created_at DESC
                """,
                {"workspace_id": workspace_id, "run_id": run_id},
            )
        else:
            cur.execute(
                """
                SELECT workspace_id, id, run_id, stage, kind, vendor, model,
                       generation_id, input_tokens, output_tokens, cached_tokens,
                       units, cost_usd, latency_ms, prompt_version, entity_type,
                       entity_id, created_at
                FROM usage_ledger
                WHERE workspace_id = %(workspace_id)s
                ORDER BY created_at DESC
                """,
                {"workspace_id": workspace_id},
            )
        return list(cur.fetchall())


def get_workspace_by_slug(
    conn: psycopg.Connection[Any],
    *,
    slug: str,
) -> dict[str, Any] | None:
    """Retrieve workspace details by unique slug."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id AS workspace_id, slug, name, industry_pack, timezone, created_at
            FROM workspaces
            WHERE slug = %(slug)s
            """,
            {"slug": slug},
        )
        return cur.fetchone()


def get_user_by_email(
    conn: psycopg.Connection[Any],
    *,
    email: str,
) -> dict[str, Any] | None:
    """Retrieve user by email address (case-insensitive)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id AS user_id, email, name, ui_locale, created_at, last_login_at
            FROM users
            WHERE lower(email) = lower(%(email)s)
            """,
            {"email": email},
        )
        return cur.fetchone()


def get_user_auth_by_email(
    conn: psycopg.Connection[Any],
    *,
    email: str,
) -> dict[str, Any] | None:
    """Retrieve user record including password_hash for local authentication."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id AS user_id, email, name, ui_locale, password_hash
            FROM users
            WHERE lower(email) = lower(%(email)s)
            """,
            {"email": email},
        )
        return cur.fetchone()


def set_user_password_hash(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
    password_hash: str,
) -> bool:
    """Persist PBKDF2 password hash for a user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET password_hash = %(password_hash)s
            WHERE id = %(user_id)s
            """,
            {"user_id": user_id, "password_hash": password_hash},
        )
        return cur.rowcount > 0


def get_user_by_id(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve user by UUID."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id AS user_id, email, name, ui_locale, created_at, last_login_at
            FROM users
            WHERE id = %(user_id)s
            """,
            {"user_id": user_id},
        )
        return cur.fetchone()


def create_or_get_user(
    conn: psycopg.Connection[Any],
    *,
    email: str,
    name: str | None = None,
    ui_locale: str = "pt-BR",
) -> dict[str, Any]:
    """Create a new user or return existing user by email."""
    existing = get_user_by_email(conn, email=email)
    if existing is not None:
        return existing

    clean_email = email.strip().lower()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%(email)s, %(name)s, %(ui_locale)s)
            ON CONFLICT ((lower(email))) DO UPDATE
            SET name = COALESCE(EXCLUDED.name, users.name)
            RETURNING id AS user_id, email, name, ui_locale, created_at, last_login_at
            """,
            {"email": clean_email, "name": name, "ui_locale": ui_locale},
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to create user {clean_email}"
            raise RuntimeError(msg)
        return row


def update_user_ui_locale(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
    ui_locale: str,
) -> None:
    """Update preferred UI locale for a user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET ui_locale = %(ui_locale)s
            WHERE id = %(user_id)s
            """,
            {"user_id": user_id, "ui_locale": ui_locale},
        )


def create_membership(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "owner",
) -> dict[str, Any]:
    """Create or return a membership for a user in a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO memberships (workspace_id, user_id, role)
            VALUES (%(workspace_id)s, %(user_id)s, %(role)s)
            ON CONFLICT (workspace_id, user_id) DO UPDATE
            SET role = EXCLUDED.role
            RETURNING workspace_id, user_id, role, created_at
            """,
            {"workspace_id": workspace_id, "user_id": user_id, "role": role},
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to create membership for user {user_id}"
            raise RuntimeError(msg)
        return row


def list_user_memberships(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all workspace memberships for a user."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT m.workspace_id, m.user_id, m.role, m.created_at,
                   w.slug, w.name,
                   w.slug AS workspace_slug, w.name AS workspace_name,
                   w.industry_pack, w.timezone
            FROM memberships m
            JOIN workspaces w ON w.id = m.workspace_id
            WHERE m.user_id = %(user_id)s
            ORDER BY w.name
            """,
            {"user_id": user_id},
        )
        return list(cur.fetchall())


def get_current_brand_profile(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve the active (is_current = true) brand profile for a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, version, is_current, schema_version,
                   payload, created_by, created_at
            FROM brand_profiles
            WHERE workspace_id = %(workspace_id)s AND is_current = true
            """,
            {"workspace_id": workspace_id},
        )
        return cur.fetchone()


def get_brand_profile(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brand_profile_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a specific brand profile version by ID within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, version, is_current, schema_version,
                   payload, created_by, created_at
            FROM brand_profiles
            WHERE workspace_id = %(workspace_id)s AND id = %(brand_profile_id)s
            """,
            {"workspace_id": workspace_id, "brand_profile_id": brand_profile_id},
        )
        return cur.fetchone()


def list_brand_profiles(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all brand profile versions for a workspace ordered newest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, version, is_current, schema_version,
                   payload, created_by, created_at
            FROM brand_profiles
            WHERE workspace_id = %(workspace_id)s
            ORDER BY version DESC
            """,
            {"workspace_id": workspace_id},
        )
        return list(cur.fetchall())


def save_brand_profile(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    payload: dict[str, Any],
    schema_version: int,
    created_by: uuid.UUID,
) -> dict[str, Any]:
    """Demote prior current version and insert next brand profile version."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE brand_profiles
            SET is_current = false
            WHERE workspace_id = %(workspace_id)s AND is_current = true
            """,
            {"workspace_id": workspace_id},
        )
        cur.execute(
            """
            INSERT INTO brand_profiles (
                workspace_id, version, is_current, schema_version, payload, created_by
            )
            VALUES (
                %(workspace_id)s,
                (
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM brand_profiles
                    WHERE workspace_id = %(workspace_id)s
                ),
                true,
                %(schema_version)s,
                %(payload)s,
                %(created_by)s
            )
            RETURNING workspace_id, id, version, is_current, schema_version,
                      payload, created_by, created_at
            """,
            {
                "workspace_id": workspace_id,
                "schema_version": schema_version,
                "payload": Jsonb(payload),
                "created_by": created_by,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert brand profile"
            raise RuntimeError(msg)
        return row


def get_workspace_owner(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve the primary owner membership for a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, user_id, role, created_at
            FROM memberships
            WHERE workspace_id = %(workspace_id)s AND role = 'owner'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            {"workspace_id": workspace_id},
        )
        return cur.fetchone()


def create_source(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None,
    industry_pack: str,
    name: str,
    domain: str,
    url: str,
    feed_url: str | None,
    fetch_mode: str,
    tier: int,
    role: str,
    locales: list[str],
    topics: list[str],
    enabled: bool = True,
    last_fetch_ok: bool | None = None,
    last_fetched_at: datetime | None = None,
    added_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create a new source scoped to a workspace (or NULL for shared pack sources)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO sources (
                workspace_id, industry_pack, name, domain, url, feed_url,
                fetch_mode, tier, role, locales, topics, enabled,
                last_fetch_ok, last_fetched_at, added_by
            )
            VALUES (
                %(workspace_id)s, %(industry_pack)s, %(name)s, %(domain)s,
                %(url)s, %(feed_url)s, %(fetch_mode)s, %(tier)s, %(role)s,
                %(locales)s, %(topics)s, %(enabled)s, %(last_fetch_ok)s,
                %(last_fetched_at)s, %(added_by)s
            )
            RETURNING id, workspace_id, industry_pack, name, domain, url,
                      feed_url, fetch_mode, tier, role, locales, topics,
                      enabled, last_fetch_ok, last_fetched_at, added_by, created_at
            """,
            {
                "workspace_id": workspace_id,
                "industry_pack": industry_pack,
                "name": name,
                "domain": domain,
                "url": url,
                "feed_url": feed_url,
                "fetch_mode": fetch_mode,
                "tier": tier,
                "role": role,
                "locales": locales,
                "topics": topics,
                "enabled": enabled,
                "last_fetch_ok": last_fetch_ok,
                "last_fetched_at": last_fetched_at,
                "added_by": added_by,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert source"
            raise RuntimeError(msg)
        return row


def update_source(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    name: str,
    domain: str,
    url: str,
    feed_url: str | None,
    fetch_mode: str,
    tier: int,
    role: str,
    locales: list[str],
    topics: list[str],
    enabled: bool,
    last_fetch_ok: bool | None = None,
    last_fetched_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Modify an existing workspace or shared source visible to the workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE sources
            SET name = %(name)s,
                domain = %(domain)s,
                url = %(url)s,
                feed_url = %(feed_url)s,
                fetch_mode = %(fetch_mode)s,
                tier = %(tier)s,
                role = %(role)s,
                locales = %(locales)s,
                topics = %(topics)s,
                enabled = %(enabled)s,
                last_fetch_ok = COALESCE(%(last_fetch_ok)s, last_fetch_ok),
                last_fetched_at = COALESCE(%(last_fetched_at)s, last_fetched_at)
            WHERE id = %(source_id)s
              AND (workspace_id = %(workspace_id)s OR workspace_id IS NULL)
            RETURNING id, workspace_id, industry_pack, name, domain, url,
                      feed_url, fetch_mode, tier, role, locales, topics,
                      enabled, last_fetch_ok, last_fetched_at, added_by, created_at
            """,
            {
                "workspace_id": workspace_id,
                "source_id": source_id,
                "name": name,
                "domain": domain,
                "url": url,
                "feed_url": feed_url,
                "fetch_mode": fetch_mode,
                "tier": tier,
                "role": role,
                "locales": locales,
                "topics": topics,
                "enabled": enabled,
                "last_fetch_ok": last_fetch_ok,
                "last_fetched_at": last_fetched_at,
            },
        )
        return cur.fetchone()


def get_source(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None,
    source_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a source by ID within a workspace or shared pack scope."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, industry_pack, name, domain, url,
                   feed_url, fetch_mode, tier, role, locales, topics,
                   enabled, last_fetch_ok, last_fetched_at, added_by, created_at
            FROM sources
            WHERE id = %(source_id)s
              AND (workspace_id = %(workspace_id)s OR workspace_id IS NULL)
            """,
            {"workspace_id": workspace_id, "source_id": source_id},
        )
        return cur.fetchone()


def list_sources(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None,
    industry_pack: str | None = None,
    include_shared: bool = True,
) -> list[dict[str, Any]]:
    """List workspace sources and optionally shared industry-pack sources."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, industry_pack, name, domain, url,
                   feed_url, fetch_mode, tier, role, locales, topics,
                   enabled, last_fetch_ok, last_fetched_at, added_by, created_at
            FROM sources
            WHERE (
                (%(workspace_id)s::uuid IS NOT NULL AND workspace_id = %(workspace_id)s)
                OR (
                    %(include_shared)s
                    AND workspace_id IS NULL
                    AND (%(industry_pack)s::text IS NULL OR industry_pack = %(industry_pack)s)
                )
            )
            ORDER BY tier ASC, name ASC
            """,
            {
                "workspace_id": workspace_id,
                "industry_pack": industry_pack,
                "include_shared": include_shared,
            },
        )
        return list(cur.fetchall())


def upsert_pack_source(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    industry_pack: str,
    name: str,
    domain: str,
    url: str,
    feed_url: str | None,
    fetch_mode: str,
    tier: int,
    role: str,
    locales: list[str],
    topics: list[str],
    enabled: bool = True,
) -> dict[str, Any]:
    """Idempotently insert or update a shared pack source (workspace_id IS NULL)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO sources (
                workspace_id, industry_pack, name, domain, url, feed_url,
                fetch_mode, tier, role, locales, topics, enabled
            )
            VALUES (
                NULL, %(industry_pack)s, %(name)s, %(domain)s, %(url)s,
                %(feed_url)s, %(fetch_mode)s, %(tier)s, %(role)s,
                %(locales)s, %(topics)s, %(enabled)s
            )
            ON CONFLICT (workspace_id, domain) DO UPDATE
            SET industry_pack = EXCLUDED.industry_pack,
                name = EXCLUDED.name,
                url = EXCLUDED.url,
                feed_url = EXCLUDED.feed_url,
                fetch_mode = EXCLUDED.fetch_mode,
                tier = EXCLUDED.tier,
                role = EXCLUDED.role,
                locales = EXCLUDED.locales,
                topics = EXCLUDED.topics,
                enabled = EXCLUDED.enabled
            RETURNING id, workspace_id, industry_pack, name, domain, url,
                      feed_url, fetch_mode, tier, role, locales, topics,
                      enabled, last_fetch_ok, last_fetched_at, added_by, created_at
            """,
            {
                "industry_pack": industry_pack,
                "name": name,
                "domain": domain,
                "url": url,
                "feed_url": feed_url,
                "fetch_mode": fetch_mode,
                "tier": tier,
                "role": role,
                "locales": locales,
                "topics": topics,
                "enabled": enabled,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to upsert shared source {domain}"
            raise RuntimeError(msg)
        return row


def upsert_document(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
    canonical_url: str,
    title: str,
    date_status: str,
    fetch_status: str,
    source_id: uuid.UUID | None = None,
    content_hash: bytes | None = None,
    lang: str | None = None,
    published_at: datetime | None = None,
    http_status: int | None = None,
    extracted_text: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert a document once per (workspace_id, canonical_url)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO documents (
                workspace_id, source_id, url, canonical_url, content_hash,
                title, lang, published_at, date_status, fetch_status,
                http_status, extracted_text, meta, fetched_at
            )
            VALUES (
                %(workspace_id)s, %(source_id)s, %(url)s, %(canonical_url)s,
                %(content_hash)s, %(title)s, %(lang)s, %(published_at)s,
                %(date_status)s, %(fetch_status)s, %(http_status)s,
                %(extracted_text)s, %(meta)s, now()
            )
            ON CONFLICT (workspace_id, canonical_url) DO UPDATE
            SET source_id = COALESCE(EXCLUDED.source_id, documents.source_id),
                url = EXCLUDED.url,
                content_hash = COALESCE(EXCLUDED.content_hash, documents.content_hash),
                title = EXCLUDED.title,
                lang = COALESCE(EXCLUDED.lang, documents.lang),
                published_at = COALESCE(EXCLUDED.published_at, documents.published_at),
                date_status = EXCLUDED.date_status,
                fetch_status = EXCLUDED.fetch_status,
                http_status = COALESCE(EXCLUDED.http_status, documents.http_status),
                extracted_text = COALESCE(EXCLUDED.extracted_text, documents.extracted_text),
                meta = EXCLUDED.meta,
                fetched_at = now()
            RETURNING workspace_id, id, source_id, url, canonical_url, content_hash,
                      title, lang, published_at, date_status, fetch_status,
                      http_status, extracted_text, text_purged_at, meta, fetched_at
            """,
            {
                "workspace_id": workspace_id,
                "source_id": source_id,
                "url": url,
                "canonical_url": canonical_url,
                "content_hash": content_hash,
                "title": title,
                "lang": lang,
                "published_at": published_at,
                "date_status": date_status,
                "fetch_status": fetch_status,
                "http_status": http_status,
                "extracted_text": extracted_text,
                "meta": Jsonb(meta or {}),
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to upsert document {canonical_url}"
            raise RuntimeError(msg)
        return row


def get_document_by_canonical_url(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    canonical_url: str,
) -> dict[str, Any] | None:
    """Retrieve a document by canonical_url within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, source_id, url, canonical_url, content_hash,
                   title, lang, published_at, date_status, fetch_status,
                   http_status, extracted_text, text_purged_at, meta, fetched_at
            FROM documents
            WHERE workspace_id = %(workspace_id)s AND canonical_url = %(canonical_url)s
            """,
            {"workspace_id": workspace_id, "canonical_url": canonical_url},
        )
        return cur.fetchone()


def get_document(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a document by ID within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, source_id, url, canonical_url, content_hash,
                   title, lang, published_at, date_status, fetch_status,
                   http_status, extracted_text, text_purged_at, meta, fetched_at
            FROM documents
            WHERE workspace_id = %(workspace_id)s AND id = %(document_id)s
            """,
            {"workspace_id": workspace_id, "document_id": document_id},
        )
        return cur.fetchone()


def upsert_research_record(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    document_id: uuid.UUID,
    status: str,
    schema_version: int,
    payload: dict[str, Any],
    duplicate_of: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Upsert a research_record for (run_id, document_id) within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO research_records (
                workspace_id, run_id, document_id, duplicate_of,
                status, schema_version, payload
            )
            VALUES (
                %(workspace_id)s, %(run_id)s, %(document_id)s, %(duplicate_of)s,
                %(status)s, %(schema_version)s, %(payload)s
            )
            ON CONFLICT (run_id, document_id) DO UPDATE
            SET duplicate_of = EXCLUDED.duplicate_of,
                status = EXCLUDED.status,
                schema_version = EXCLUDED.schema_version,
                payload = EXCLUDED.payload
            WHERE research_records.workspace_id = %(workspace_id)s
            RETURNING workspace_id, id, run_id, document_id, duplicate_of,
                      status, schema_version, payload, created_at
            """,
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "document_id": document_id,
                "duplicate_of": duplicate_of,
                "status": status,
                "schema_version": schema_version,
                "payload": Jsonb(payload),
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to upsert research record for document {document_id}"
            raise RuntimeError(msg)
        return row


def list_research_records(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List research records for a workspace, optionally filtered by run_id and status."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, run_id, document_id, duplicate_of,
                   status, schema_version, payload, created_at
            FROM research_records
            WHERE workspace_id = %(workspace_id)s
              AND (%(run_id)s::uuid IS NULL OR run_id = %(run_id)s)
              AND (%(status)s::text IS NULL OR status = %(status)s)
            ORDER BY created_at ASC
            """,
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "status": status,
            },
        )
        return list(cur.fetchall())


def find_earlier_near_duplicate_record(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    threshold: float = 0.6,
    window_days: int = 14,
) -> dict[str, Any] | None:
    """Find earlier research record within window_days having similarity >= threshold."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT prev_rr.id AS duplicate_of,
                   prev_d.title AS matched_title,
                   similarity(curr_d.title, prev_d.title) AS similarity
            FROM research_records curr_rr
            JOIN documents curr_d
              ON curr_d.workspace_id = curr_rr.workspace_id
             AND curr_d.id = curr_rr.document_id
            JOIN research_records prev_rr
              ON prev_rr.workspace_id = curr_rr.workspace_id
            JOIN documents prev_d
              ON prev_d.workspace_id = prev_rr.workspace_id
             AND prev_d.id = prev_rr.document_id
            WHERE curr_rr.workspace_id = %(workspace_id)s
              AND curr_rr.id = %(record_id)s
              AND prev_rr.id <> curr_rr.id
              AND prev_rr.status <> 'duplicate'
              AND (
                  prev_rr.created_at < curr_rr.created_at
                  OR (prev_rr.created_at = curr_rr.created_at AND prev_rr.id < curr_rr.id)
              )
              AND ABS(
                  EXTRACT(EPOCH FROM (
                      COALESCE(curr_d.published_at, curr_rr.created_at) -
                      COALESCE(prev_d.published_at, prev_rr.created_at)
                  ))
              ) <= (%(window_days)s * 86400)
              AND similarity(curr_d.title, prev_d.title) >= %(threshold)s
            ORDER BY similarity DESC, prev_rr.created_at ASC
            LIMIT 1
            """,
            {
                "workspace_id": workspace_id,
                "record_id": record_id,
                "threshold": threshold,
                "window_days": window_days,
            },
        )
        return cur.fetchone()


def mark_research_record_duplicate(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    duplicate_of: uuid.UUID,
) -> dict[str, Any] | None:
    """Mark a research record as duplicate pointing to duplicate_of within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE research_records
            SET status = 'duplicate',
                duplicate_of = %(duplicate_of)s
            WHERE workspace_id = %(workspace_id)s
              AND id = %(record_id)s
            RETURNING workspace_id, id, run_id, document_id, duplicate_of,
                      status, schema_version, payload, created_at
            """,
            {
                "workspace_id": workspace_id,
                "record_id": record_id,
                "duplicate_of": duplicate_of,
            },
        )
        return cur.fetchone()


def get_research_record(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a single research record by ID within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, run_id, document_id, duplicate_of,
                   status, schema_version, payload, created_at
            FROM research_records
            WHERE workspace_id = %(workspace_id)s
              AND id = %(record_id)s
            """,
            {"workspace_id": workspace_id, "record_id": record_id},
        )
        return cur.fetchone()


def get_research_record_workspace_id(
    conn: psycopg.Connection[Any],
    *,
    record_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find workspace_id for a research record ID."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workspace_id
            FROM research_records
            WHERE id = %(record_id)s
              AND workspace_id IS NOT NULL
            LIMIT 1
            """,
            {"record_id": record_id},
        )
        row = cur.fetchone()
        return uuid.UUID(str(row[0])) if row else None


def get_run_workspace_id(
    conn: psycopg.Connection[Any],
    *,
    run_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find workspace_id for a run ID."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workspace_id
            FROM runs
            WHERE id = %(run_id)s
              AND workspace_id IS NOT NULL
            LIMIT 1
            """,
            {"run_id": run_id},
        )
        row = cur.fetchone()
        return uuid.UUID(str(row[0])) if row else None


def upsert_opportunity(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    record_id: uuid.UUID,
    scores: dict[str, Any],
    total: Decimal | float,
    angle: str,
    funnel_stage: str,
    confidence: str,
    rank: int | None = None,
    status: str = "scored",
    decided_by: uuid.UUID | None = None,
    decided_at: datetime | None = None,
    decision_note: str | None = None,
) -> dict[str, Any]:
    """Upsert an opportunity for (run_id, record_id) within a workspace (DATA-MODEL §8)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO opportunities (
                workspace_id, run_id, record_id, scores, total,
                rank, funnel_stage, confidence, angle, status,
                decided_by, decided_at, decision_note
            )
            VALUES (
                %(workspace_id)s, %(run_id)s, %(record_id)s, %(scores)s, %(total)s,
                %(rank)s, %(funnel_stage)s, %(confidence)s, %(angle)s, %(status)s,
                %(decided_by)s, %(decided_at)s, %(decision_note)s
            )
            ON CONFLICT (run_id, record_id) DO UPDATE
            SET scores = EXCLUDED.scores,
                total = EXCLUDED.total,
                rank = EXCLUDED.rank,
                funnel_stage = EXCLUDED.funnel_stage,
                confidence = EXCLUDED.confidence,
                angle = EXCLUDED.angle,
                status = EXCLUDED.status,
                decided_by = EXCLUDED.decided_by,
                decided_at = EXCLUDED.decided_at,
                decision_note = EXCLUDED.decision_note
            WHERE opportunities.workspace_id = %(workspace_id)s
            RETURNING workspace_id, id, run_id, record_id, scores, total,
                      rank, funnel_stage, confidence, angle, status,
                      decided_by, decided_at, decision_note, created_at
            """,
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "record_id": record_id,
                "scores": Jsonb(scores),
                "total": total,
                "rank": rank,
                "funnel_stage": funnel_stage.lower(),
                "confidence": confidence.lower(),
                "angle": angle,
                "status": status,
                "decided_by": decided_by,
                "decided_at": decided_at,
                "decision_note": decision_note,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = f"Failed to upsert opportunity for record {record_id}"
            raise RuntimeError(msg)
        return row


def get_opportunity(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve an opportunity by ID within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, run_id, record_id, scores, total,
                   rank, funnel_stage, confidence, angle, status,
                   decided_by, decided_at, decision_note, created_at
            FROM opportunities
            WHERE workspace_id = %(workspace_id)s AND id = %(opportunity_id)s
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        return cur.fetchone()


def list_opportunities(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List opportunities for a workspace, optionally filtered by run_id and status."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, run_id, record_id, scores, total,
                   rank, funnel_stage, confidence, angle, status,
                   decided_by, decided_at, decision_note, created_at
            FROM opportunities
            WHERE workspace_id = %(workspace_id)s
              AND (%(run_id)s::uuid IS NULL OR run_id = %(run_id)s)
              AND (%(status)s::text IS NULL OR status = %(status)s)
            ORDER BY total DESC, created_at ASC
            """,
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "status": status,
            },
        )
        return list(cur.fetchall())


def update_opportunity_rank(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    rank: int,
) -> dict[str, Any] | None:
    """Set position rank for an opportunity."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET rank = %(rank)s
            WHERE workspace_id = %(workspace_id)s AND id = %(opportunity_id)s
            RETURNING workspace_id, id, run_id, record_id, scores, total,
                      rank, funnel_stage, confidence, angle, status,
                      decided_by, decided_at, decision_note, created_at
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id, "rank": rank},
        )
        return cur.fetchone()


def get_opportunity_workspace_id(
    conn: psycopg.Connection[Any],
    *,
    opportunity_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find workspace_id for an opportunity ID."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workspace_id
            FROM opportunities
            WHERE id = %(opportunity_id)s
              AND workspace_id IS NOT NULL
            LIMIT 1
            """,
            {"opportunity_id": opportunity_id},
        )
        row = cur.fetchone()
        return uuid.UUID(str(row[0])) if row else None


def get_opportunity_report_items(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve ranked opportunities with document provenance for report (F09 AC3, F10 AC5)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT o.workspace_id, o.id, o.run_id, o.record_id, o.scores, o.total,
                   o.rank, o.funnel_stage, o.confidence, o.angle, o.status,
                   o.decided_by, o.decided_at, o.decision_note,
                   d.url AS original_url,
                   d.published_at,
                   d.date_status,
                   d.title AS document_title,
                   r.payload AS record_payload,
                   EXISTS (
                       SELECT 1 FROM opportunities prev_o
                       JOIN research_records prev_r
                         ON prev_o.record_id = prev_r.id
                        AND prev_o.workspace_id = prev_r.workspace_id
                       WHERE prev_o.workspace_id = o.workspace_id
                         AND (
                             prev_o.status = 'rejected'
                             OR EXISTS (
                                 SELECT 1 FROM briefs prev_b
                                 JOIN drafts prev_d
                                   ON prev_d.workspace_id = prev_b.workspace_id
                                  AND prev_d.brief_id = prev_b.id
                                 WHERE prev_b.workspace_id = prev_o.workspace_id
                                   AND prev_b.opportunity_id = prev_o.id
                                   AND prev_d.status = 'declined'
                             )
                         )
                         AND (
                             prev_o.id = o.id
                             OR prev_r.document_id = r.document_id
                             OR (r.duplicate_of IS NOT NULL AND prev_r.id = r.duplicate_of)
                             OR (prev_r.duplicate_of IS NOT NULL AND prev_r.duplicate_of = r.id)
                             OR (r.duplicate_of IS NOT NULL
                                 AND prev_r.duplicate_of = r.duplicate_of)
                         )
                   ) AS is_rejected_story
            FROM opportunities o
            JOIN research_records r ON o.record_id = r.id AND o.workspace_id = r.workspace_id
            JOIN documents d ON r.document_id = d.id AND r.workspace_id = d.workspace_id
            WHERE o.workspace_id = %(workspace_id)s
              AND o.run_id = %(run_id)s
            ORDER BY o.rank ASC NULLS LAST, o.total DESC, o.created_at ASC
            LIMIT %(limit)s
            """,
            {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "limit": limit,
            },
        )
        return list(cur.fetchall())


def decide_opportunity(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    decision: str,
    user_id: uuid.UUID,
    decided_at: datetime,
    note: str | None = None,
) -> dict[str, Any] | None:
    """Atomically transition opportunity from 'scored' to 'approved'/'rejected' (F10 AC1, AC4)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET status = %(decision)s,
                decided_by = %(user_id)s,
                decided_at = %(decided_at)s,
                decision_note = %(note)s
            WHERE workspace_id = %(workspace_id)s
              AND id = %(opportunity_id)s
              AND status = 'scored'
            RETURNING workspace_id, id, run_id, record_id, scores, total,
                      rank, funnel_stage, confidence, angle, status,
                      decided_by, decided_at, decision_note, created_at
            """,
            {
                "workspace_id": workspace_id,
                "opportunity_id": opportunity_id,
                "decision": decision,
                "user_id": user_id,
                "decided_at": decided_at,
                "note": note,
            },
        )
        return cur.fetchone()


def count_approved_opportunities(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> int:
    """Count number of approved opportunities for a run (F10 AC3)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM opportunities
            WHERE workspace_id = %(workspace_id)s
              AND run_id = %(run_id)s
              AND status = 'approved'
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def expire_previous_opportunities(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID,
    current_run_id: uuid.UUID | None = None,
) -> int:
    """Expire still-scored opportunities from previous runs of the campaign (F10 AC6)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET status = 'expired'
            WHERE workspace_id = %(workspace_id)s
              AND status = 'scored'
              AND run_id IN (
                  SELECT id FROM runs
                  WHERE workspace_id = %(workspace_id)s
                    AND campaign_id = %(campaign_id)s
                    AND (%(current_run_id)s::uuid IS NULL OR id != %(current_run_id)s)
              )
            """,
            {
                "workspace_id": workspace_id,
                "campaign_id": campaign_id,
                "current_run_id": current_run_id,
            },
        )
        return int(cur.rowcount)


def insert_webhook_deliveries_for_event(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    event_id: uuid.UUID,
    event_type: str,
) -> int:
    """Schedule pending webhook delivery records for matching enabled subscriptions (F06 AC1)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO webhook_deliveries (
                workspace_id, subscription_id, event_id, status, attempts, next_attempt_at
            )
            SELECT
                s.workspace_id,
                s.id,
                %(event_id)s,
                'pending',
                0,
                now()
            FROM webhook_subscriptions s
            WHERE s.workspace_id = %(workspace_id)s
              AND s.enabled = true
              AND %(event_type)s = ANY(s.event_types)
            ON CONFLICT (subscription_id, event_id) DO NOTHING
            """,
            {"workspace_id": workspace_id, "event_id": event_id, "event_type": event_type},
        )
        return int(cur.rowcount)


def create_webhook_subscription(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
    event_types: list[str],
    secret: bytes,
    created_by: uuid.UUID,
    enabled: bool = True,
) -> dict[str, Any]:
    """Persist a new webhook subscription record (F06 AC1, AC7)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO webhook_subscriptions (
                workspace_id, url, event_types, secret, enabled, created_by
            )
            VALUES (
                %(workspace_id)s, %(url)s, %(event_types)s, %(secret)s, %(enabled)s, %(created_by)s
            )
            RETURNING id, workspace_id, url, event_types, enabled, created_by, created_at
            """,
            {
                "workspace_id": workspace_id,
                "url": url,
                "event_types": event_types,
                "secret": secret,
                "enabled": enabled,
                "created_by": created_by,
            },
        )
        row = cur.fetchone()
        if row is None:
            msg = "Failed to insert webhook subscription"
            raise RuntimeError(msg)
        return dict(row)


def get_webhook_subscription(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a single webhook subscription by workspace and id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, url, event_types, secret, enabled, created_by, created_at
            FROM webhook_subscriptions
            WHERE workspace_id = %(workspace_id)s AND id = %(subscription_id)s
            """,
            {"workspace_id": workspace_id, "subscription_id": subscription_id},
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_webhook_subscription(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
    url: str | None = None,
    event_types: list[str] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    """Modify fields on an existing webhook subscription."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE webhook_subscriptions
            SET url = COALESCE(%(url)s, url),
                event_types = COALESCE(%(event_types)s, event_types),
                enabled = COALESCE(%(enabled)s, enabled)
            WHERE workspace_id = %(workspace_id)s AND id = %(subscription_id)s
            RETURNING id, workspace_id, url, event_types, enabled, created_by, created_at
            """,
            {
                "workspace_id": workspace_id,
                "subscription_id": subscription_id,
                "url": url,
                "event_types": event_types,
                "enabled": enabled,
            },
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_webhook_subscriptions(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all webhook subscriptions for a workspace (secrets omitted)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, url, event_types, enabled, created_by, created_at
            FROM webhook_subscriptions
            WHERE workspace_id = %(workspace_id)s
            ORDER BY created_at DESC
            """,
            {"workspace_id": workspace_id},
        )
        return [dict(r) for r in cur.fetchall()]


def delete_webhook_subscription(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> bool:
    """Remove a webhook subscription and its associated deliveries."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM webhook_deliveries
            WHERE workspace_id = %(workspace_id)s AND subscription_id = %(subscription_id)s
            """,
            {"workspace_id": workspace_id, "subscription_id": subscription_id},
        )
        cur.execute(
            """
            DELETE FROM webhook_subscriptions
            WHERE workspace_id = %(workspace_id)s AND id = %(subscription_id)s
            """,
            {"workspace_id": workspace_id, "subscription_id": subscription_id},
        )
        return cur.rowcount > 0


def list_webhook_deliveries(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    limit: int = 50,
    subscription_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List webhook deliveries with joined subscription URL and event metadata."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                d.workspace_id,
                d.subscription_id,
                d.event_id,
                d.status,
                d.attempts,
                d.next_attempt_at,
                d.last_http_status,
                d.last_error,
                d.delivered_at,
                s.url AS subscription_url,
                e.type AS event_type,
                e.occurred_at AS event_occurred_at
            FROM webhook_deliveries d
            JOIN webhook_subscriptions s
              ON s.workspace_id = d.workspace_id AND s.id = d.subscription_id
            JOIN events e
              ON e.workspace_id = d.workspace_id AND e.id = d.event_id
            WHERE d.workspace_id = %(workspace_id)s
              AND (%(subscription_id)s::uuid IS NULL OR d.subscription_id = %(subscription_id)s)
            ORDER BY d.next_attempt_at DESC, e.occurred_at DESC
            LIMIT %(limit)s
            """,
            {"workspace_id": workspace_id, "subscription_id": subscription_id, "limit": limit},
        )
        return [dict(r) for r in cur.fetchall()]


def get_webhook_delivery(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a single webhook delivery with joined subscription URL and event metadata."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                d.workspace_id,
                d.subscription_id,
                d.event_id,
                d.status,
                d.attempts,
                d.next_attempt_at,
                d.last_http_status,
                d.last_error,
                d.delivered_at,
                s.url AS subscription_url,
                s.url,
                s.secret,
                s.enabled,
                e.type AS event_type,
                e.entity_type,
                e.entity_id,
                e.payload,
                e.occurred_at,
                e.occurred_at AS event_occurred_at
            FROM webhook_deliveries d
            JOIN webhook_subscriptions s
              ON s.workspace_id = d.workspace_id AND s.id = d.subscription_id
            JOIN events e
              ON e.workspace_id = d.workspace_id AND e.id = d.event_id
            WHERE d.workspace_id = %(workspace_id)s
              AND d.subscription_id = %(subscription_id)s
              AND d.event_id = %(event_id)s
            """,
            {
                "workspace_id": workspace_id,
                "subscription_id": subscription_id,
                "event_id": event_id,
            },
        )
        row = cur.fetchone()
        return dict(row) if row else None


def reset_webhook_delivery_for_resend(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Reset a delivery to pending status for manual re-send (F06 AC4)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE webhook_deliveries
            SET status = 'pending',
                next_attempt_at = now(),
                last_error = NULL
            WHERE workspace_id = %(workspace_id)s
              AND subscription_id = %(subscription_id)s
              AND event_id = %(event_id)s
            RETURNING workspace_id, subscription_id, event_id, status, attempts, next_attempt_at
            """,
            {
                "workspace_id": workspace_id,
                "subscription_id": subscription_id,
                "event_id": event_id,
            },
        )
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_due_webhook_deliveries(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID | None = None,
    batch_size: int = 10,
) -> list[dict[str, Any]]:
    """Fetch due pending/retrying deliveries with row lock (F06 AC2, AC3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                d.workspace_id,
                d.subscription_id,
                d.event_id,
                d.status,
                d.attempts,
                d.next_attempt_at,
                s.url,
                s.secret,
                s.enabled,
                e.type AS event_type,
                e.entity_type,
                e.entity_id,
                e.payload,
                e.occurred_at
            FROM webhook_deliveries d
            JOIN webhook_subscriptions s
              ON s.workspace_id = d.workspace_id AND s.id = d.subscription_id
            JOIN events e
              ON e.workspace_id = d.workspace_id AND e.id = d.event_id
            WHERE d.status IN ('pending', 'retrying')
              AND d.next_attempt_at <= now()
              AND (%(workspace_id)s::uuid IS NULL OR d.workspace_id = %(workspace_id)s)
            ORDER BY d.next_attempt_at ASC
            LIMIT %(batch_size)s
            FOR UPDATE OF d SKIP LOCKED
            """,
            {"batch_size": batch_size, "workspace_id": workspace_id},
        )
        return [dict(r) for r in cur.fetchall()]


def update_webhook_delivery_attempt(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: uuid.UUID,
    status: str,
    attempts: int,
    next_attempt_at: Any,
    last_http_status: int | None = None,
    last_error: str | None = None,
    delivered_at: Any = None,
) -> None:
    """Record delivery outcome following an attempt (F06 AC2, AC3)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE webhook_deliveries
            SET status = %(status)s,
                attempts = %(attempts)s,
                next_attempt_at = %(next_attempt_at)s,
                last_http_status = %(last_http_status)s,
                last_error = %(last_error)s,
                delivered_at = %(delivered_at)s
            WHERE workspace_id = %(workspace_id)s
              AND subscription_id = %(subscription_id)s
              AND event_id = %(event_id)s
            """,
            {
                "workspace_id": workspace_id,
                "subscription_id": subscription_id,
                "event_id": event_id,
                "status": status,
                "attempts": attempts,
                "next_attempt_at": next_attempt_at,
                "last_http_status": last_http_status,
                "last_error": last_error,
                "delivered_at": delivered_at,
            },
        )


def create_publication(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
    title: str,
    published_at: datetime | None = None,
    mode: str = "live",
    status: str = "published",
    draft_id: uuid.UUID | None = None,
    destination: str = "sitemap",
    external_id: str | None = None,
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a publication record idempotently for a workspace (F08 AC3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO publications (
                workspace_id, draft_id, destination, mode, status,
                external_id, url, title, published_at, response
            )
            VALUES (
                %(workspace_id)s, %(draft_id)s, %(destination)s, %(mode)s, %(status)s,
                %(external_id)s, %(url)s, %(title)s, %(published_at)s, %(response)s
            )
            ON CONFLICT (workspace_id, url) DO UPDATE
            SET title = EXCLUDED.title,
                published_at = COALESCE(EXCLUDED.published_at, publications.published_at),
                response = COALESCE(EXCLUDED.response, publications.response)
            RETURNING id, workspace_id, draft_id, destination, mode, status,
                      external_id, url, title, published_at, error, response, created_at,
                      (xmax = 0) AS is_new
            """,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "destination": destination,
                "mode": mode,
                "status": status,
                "external_id": external_id,
                "url": url,
                "title": title,
                "published_at": published_at,
                "response": Jsonb(response) if response is not None else None,
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = "Failed to insert publication"
            raise RuntimeError(msg)
        return dict(row)


def upsert_draft_publication(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    destination: str,
    mode: str,
    status: str,
    title: str,
    external_id: str | None = None,
    url: str | None = None,
    published_at: datetime | None = None,
    error: str | None = None,
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert a draft publication idempotently by (workspace_id, draft_id, destination) (F16)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO publications (
                workspace_id, draft_id, destination, mode, status,
                external_id, url, title, published_at, error, response
            )
            VALUES (
                %(workspace_id)s, %(draft_id)s, %(destination)s, %(mode)s, %(status)s,
                %(external_id)s, %(url)s, %(title)s, %(published_at)s, %(error)s, %(response)s
            )
            ON CONFLICT (workspace_id, draft_id, destination) DO UPDATE
            SET mode = EXCLUDED.mode,
                status = EXCLUDED.status,
                external_id = COALESCE(EXCLUDED.external_id, publications.external_id),
                url = COALESCE(EXCLUDED.url, publications.url),
                title = EXCLUDED.title,
                published_at = COALESCE(EXCLUDED.published_at, publications.published_at),
                error = EXCLUDED.error,
                response = COALESCE(EXCLUDED.response, publications.response)
            WHERE publications.workspace_id = %(workspace_id)s
            RETURNING id, workspace_id, draft_id, destination, mode, status,
                      external_id, url, title, published_at, error, response, created_at,
                      (xmax = 0) AS is_new
            """,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "destination": destination,
                "mode": mode,
                "status": status,
                "external_id": external_id,
                "url": url,
                "title": title,
                "published_at": published_at,
                "error": error,
                "response": Jsonb(response) if response is not None else None,
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = f"Failed to upsert publication for draft {draft_id}"
            raise RuntimeError(msg)
        return dict(row)


def get_publication(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    publication_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve publication record by ID for a workspace (F08 AC3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, draft_id, destination, mode, status,
                   external_id, url, title, published_at, error, response, created_at
            FROM publications
            WHERE workspace_id = %(workspace_id)s
              AND id = %(publication_id)s
            """,
            {"workspace_id": workspace_id, "publication_id": publication_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def get_publication_by_url(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
) -> dict[str, Any] | None:
    """Retrieve publication row by URL for a specific workspace (F08 AC3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, draft_id, destination, mode, status,
                   external_id, url, title, published_at, error, response, created_at
            FROM publications
            WHERE workspace_id = %(workspace_id)s
              AND url = %(url)s
            """,
            {"workspace_id": workspace_id, "url": url},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def get_publication_for_draft(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    destination: str | None = None,
) -> dict[str, Any] | None:
    """Retrieve latest publication record for a draft in a workspace (F16)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, draft_id, destination, mode, status,
                   external_id, url, title, published_at, error, response, created_at
            FROM publications
            WHERE workspace_id = %(workspace_id)s
              AND draft_id = %(draft_id)s
              AND (%(destination)s::text IS NULL OR destination = %(destination)s)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "destination": destination,
            },
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def list_publications_for_draft(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all publication records for a draft within a workspace (F16)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, draft_id, destination, mode, status,
                   external_id, url, title, published_at, error, response, created_at
            FROM publications
            WHERE workspace_id = %(workspace_id)s
              AND draft_id = %(draft_id)s
            ORDER BY created_at DESC
            """,
            {"workspace_id": workspace_id, "draft_id": draft_id},
        )
        return [dict(r) for r in cur.fetchall()]


def list_publications(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve list of publication rows for a workspace (F08 AC3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, draft_id, destination, mode, status,
                   external_id, url, title, published_at, error, response, created_at
            FROM publications
            WHERE workspace_id = %(workspace_id)s
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT %(limit)s
            """,
            {"workspace_id": workspace_id, "limit": limit},
        )
        return [dict(r) for r in cur.fetchall()]


def find_similar_publication(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    title: str,
    threshold: float = 0.60,
) -> dict[str, Any] | None:
    """Locate most similar publication meeting trigram similarity threshold (F08 AC4)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, title, url, published_at,
                   similarity(title, %(title)s) AS similarity
            FROM publications
            WHERE workspace_id = %(workspace_id)s
              AND similarity(title, %(title)s) >= %(threshold)s
            ORDER BY similarity DESC
            LIMIT 1
            """,
            {
                "workspace_id": workspace_id,
                "title": title,
                "threshold": threshold,
            },
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def get_fresh_keywords_from_cache(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    provider: str,
    locale: str,
    market: str,
    normalized_keywords: list[str],
    max_age_days: int = 30,
) -> dict[str, dict[str, Any]]:
    """Retrieve fresh keyword data from global keyword_cache (F11 AC2)."""
    if not normalized_keywords:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT keyword_norm, data, fetched_at
            FROM keyword_cache
            WHERE provider = %(provider)s
              AND locale = %(locale)s
              AND market = %(market)s
              AND keyword_norm = ANY(%(keywords)s)
              AND fetched_at >= NOW() - (%(max_age_days)s || ' days')::interval
            """,
            {
                "provider": provider,
                "locale": locale,
                "market": market.lower(),
                "keywords": normalized_keywords,
                "max_age_days": max_age_days,
            },
        )
        rows = cur.fetchall()
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            data = r["data"]
            if isinstance(data, dict):
                result[r["keyword_norm"]] = data
        return result


def upsert_keyword_cache(
    conn: psycopg.Connection[Any],
    *,
    provider: str,
    locale: str,
    market: str,
    keywords_data: list[dict[str, Any]],
) -> None:
    """Insert or update keyword data in global keyword_cache (F11 AC1, AC2)."""
    if not keywords_data:
        return
    with conn.cursor() as cur:
        for kw in keywords_data:
            keyword_norm = kw.get("keyword_norm") or kw.get("keyword", "")
            if not keyword_norm:
                continue
            cur.execute(
                """
                INSERT INTO keyword_cache (
                    provider, locale, market, keyword_norm, data, fetched_at
                )
                VALUES (%(provider)s, %(locale)s, %(market)s, %(keyword_norm)s, %(data)s, NOW())
                ON CONFLICT (provider, locale, market, keyword_norm)
                DO UPDATE SET
                    data = EXCLUDED.data,
                    fetched_at = EXCLUDED.fetched_at
                """,
                {
                    "provider": provider,
                    "locale": locale,
                    "market": market.lower(),
                    "keyword_norm": keyword_norm,
                    "data": Jsonb(kw.get("data") or kw),
                },
            )
    conn.commit()


def get_opportunity_context(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve approved/scored opportunity joined with source record, doc, run, and campaign."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT o.workspace_id, o.id, o.run_id, o.record_id, o.scores, o.total,
                   o.rank, o.funnel_stage, o.confidence, o.angle, o.status,
                   o.decided_by, o.decided_at, o.decision_note,
                   r.document_id AS primary_document_id,
                   r.payload AS record_payload,
                   d.url AS document_url,
                   d.canonical_url AS document_canonical_url,
                   d.title AS document_title,
                   d.extracted_text AS document_extracted_text,
                   d.published_at AS document_published_at,
                   d.date_status AS document_date_status,
                   d.fetch_status AS document_fetch_status,
                   d.meta AS document_meta,
                   s.name AS source_name,
                   s.role AS source_role,
                   run.campaign_id,
                   c.name AS campaign_name,
                   c.locale AS campaign_locale,
                   c.market AS campaign_market,
                   c.config AS campaign_config,
                   w.industry_pack AS industry_pack,
                   COALESCE(w.config, '{}'::jsonb) AS workspace_config
            FROM opportunities o
            JOIN research_records r
              ON r.workspace_id = o.workspace_id AND r.id = o.record_id
            JOIN documents d
              ON d.workspace_id = r.workspace_id AND d.id = r.document_id
            LEFT JOIN sources s
              ON s.id = d.source_id
             AND (s.workspace_id = d.workspace_id OR s.workspace_id IS NULL)
            JOIN runs run
              ON run.workspace_id = o.workspace_id AND run.id = o.run_id
            JOIN campaigns c
              ON c.workspace_id = run.workspace_id AND c.id = run.campaign_id
            JOIN workspaces w
              ON w.id = o.workspace_id
            WHERE o.workspace_id = %(workspace_id)s
              AND o.id = %(opportunity_id)s
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def list_evidence_documents(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    roles: tuple[str, ...] = ("data", "management"),
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List extracted documents in workspace sourced from evidence roles (data/management)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.workspace_id, d.id, d.source_id, d.url, d.canonical_url,
                   d.title, d.lang, d.published_at, d.date_status, d.fetch_status,
                   d.extracted_text, d.meta, s.name AS source_name, s.role AS source_role
            FROM documents d
            JOIN sources s
              ON s.id = d.source_id
             AND (s.workspace_id = d.workspace_id OR s.workspace_id IS NULL)
            WHERE d.workspace_id = %(workspace_id)s
              AND d.fetch_status = 'ok'
              AND s.role = ANY(%(roles)s)
            ORDER BY d.published_at DESC NULLS LAST, d.fetched_at DESC
            LIMIT %(limit)s
            """,
            {
                "workspace_id": workspace_id,
                "roles": list(roles),
                "limit": limit,
            },
        )
        return [dict(r) for r in cur.fetchall()]


def create_next_brief_version(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    brand_profile_id: uuid.UUID,
    prompt_version: str,
    keywords: list[dict[str, Any]],
    schema_version: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Atomically insert version = MAX(version) + 1 into briefs scoped by workspace."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id
            FROM opportunities
            WHERE workspace_id = %(workspace_id)s
              AND id = %(opportunity_id)s
            FOR UPDATE
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        if cur.fetchone() is None:
            msg = f"Opportunity {opportunity_id} not found in workspace {workspace_id}"
            raise ValueError(msg)

        cur.execute(
            """
            INSERT INTO briefs (
                workspace_id, opportunity_id, version, brand_profile_id,
                prompt_version, keywords, schema_version, payload
            )
            VALUES (
                %(workspace_id)s,
                %(opportunity_id)s,
                (
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM briefs
                    WHERE workspace_id = %(workspace_id)s
                      AND opportunity_id = %(opportunity_id)s
                ),
                %(brand_profile_id)s,
                %(prompt_version)s,
                %(keywords)s,
                %(schema_version)s,
                %(payload)s
            )
            RETURNING workspace_id, id, opportunity_id, version, brand_profile_id,
                      prompt_version, keywords, schema_version, payload, created_at
            """,
            {
                "workspace_id": workspace_id,
                "opportunity_id": opportunity_id,
                "brand_profile_id": brand_profile_id,
                "prompt_version": prompt_version,
                "keywords": Jsonb(keywords),
                "schema_version": schema_version,
                "payload": Jsonb(payload),
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = f"Failed to create brief for opportunity {opportunity_id}"
            raise RuntimeError(msg)
        return dict(row)


def get_brief(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a brief row by ID within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, opportunity_id, version, brand_profile_id,
                   prompt_version, keywords, schema_version, payload, created_at
            FROM briefs
            WHERE workspace_id = %(workspace_id)s
              AND id = %(brief_id)s
            """,
            {"workspace_id": workspace_id, "brief_id": brief_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def get_latest_brief_for_opportunity(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve the latest brief version for an opportunity within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, opportunity_id, version, brand_profile_id,
                   prompt_version, keywords, schema_version, payload, created_at
            FROM briefs
            WHERE workspace_id = %(workspace_id)s
              AND opportunity_id = %(opportunity_id)s
            ORDER BY version DESC
            LIMIT 1
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def list_briefs(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List brief versions in a workspace, optionally filtered by opportunity_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, opportunity_id, version, brand_profile_id,
                   prompt_version, keywords, schema_version, payload, created_at
            FROM briefs
            WHERE workspace_id = %(workspace_id)s
              AND (%(opportunity_id)s::uuid IS NULL OR opportunity_id = %(opportunity_id)s)
            ORDER BY version DESC, created_at DESC
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        return [dict(r) for r in cur.fetchall()]


def list_latest_briefs_for_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[uuid.UUID, dict[str, Any]]:
    """Retrieve the latest brief version keyed by opportunity_id for all opps in a run."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (b.opportunity_id)
                   b.workspace_id, b.id, b.opportunity_id, b.version,
                   b.brand_profile_id, b.prompt_version, b.keywords,
                   b.schema_version, b.payload, b.created_at
            FROM briefs b
            JOIN opportunities o
              ON o.workspace_id = b.workspace_id
             AND o.id = b.opportunity_id
            WHERE b.workspace_id = %(workspace_id)s
              AND o.workspace_id = %(workspace_id)s
              AND o.run_id = %(run_id)s
            ORDER BY b.opportunity_id, b.version DESC
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        return {uuid.UUID(str(r["opportunity_id"])): dict(r) for r in cur.fetchall()}


def get_brief_writing_context(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve brief joined with opportunity, primary document, brand, run, and campaign."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT b.workspace_id, b.id, b.opportunity_id, b.version AS brief_version,
                   b.brand_profile_id, b.prompt_version AS brief_prompt_version,
                   b.keywords, b.schema_version AS brief_schema_version,
                   b.payload AS brief_payload, b.created_at AS brief_created_at,
                   bp.version AS brand_version, bp.payload AS brand_payload,
                   o.run_id, o.record_id, o.funnel_stage, o.angle, o.status AS opportunity_status,
                   r.document_id AS primary_document_id, r.payload AS record_payload,
                   d.url AS document_url, d.canonical_url AS document_canonical_url,
                   d.title AS document_title, d.extracted_text AS document_extracted_text,
                   d.published_at AS document_published_at, d.date_status AS document_date_status,
                   d.meta AS document_meta,
                   run.campaign_id,
                   c.name AS campaign_name, c.locale AS campaign_locale,
                   c.market AS campaign_market, c.config AS campaign_config,
                   w.industry_pack AS industry_pack,
                   COALESCE(w.config, '{}'::jsonb) AS workspace_config
            FROM briefs b
            JOIN brand_profiles bp
              ON bp.workspace_id = b.workspace_id AND bp.id = b.brand_profile_id
            JOIN opportunities o
              ON o.workspace_id = b.workspace_id AND o.id = b.opportunity_id
            JOIN research_records r
              ON r.workspace_id = o.workspace_id AND r.id = o.record_id
            JOIN documents d
              ON d.workspace_id = r.workspace_id AND d.id = r.document_id
            JOIN runs run
              ON run.workspace_id = o.workspace_id AND run.id = o.run_id
            JOIN campaigns c
              ON c.workspace_id = run.workspace_id AND c.id = run.campaign_id
            JOIN workspaces w
              ON w.id = b.workspace_id
            WHERE b.workspace_id = %(workspace_id)s
              AND b.id = %(brief_id)s
            """,
            {"workspace_id": workspace_id, "brief_id": brief_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def create_next_draft_revision(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
    prompt_version: str,
    schema_version: int,
    payload: dict[str, Any],
    claims: list[dict[str, Any]],
    status: str = "draft",
    revision: int | None = None,
) -> dict[str, Any]:
    """Atomically insert draft revision into drafts and draft_claims."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id
            FROM briefs
            WHERE workspace_id = %(workspace_id)s
              AND id = %(brief_id)s
            FOR UPDATE
            """,
            {"workspace_id": workspace_id, "brief_id": brief_id},
        )
        if cur.fetchone() is None:
            msg = f"Brief {brief_id} not found in workspace {workspace_id}"
            raise ValueError(msg)

        cur.execute(
            """
            INSERT INTO drafts (
                workspace_id, brief_id, revision, status,
                prompt_version, schema_version, payload
            )
            VALUES (
                %(workspace_id)s,
                %(brief_id)s,
                COALESCE(
                    %(explicit_revision)s::bigint,
                    (
                        SELECT COALESCE(MAX(revision), -1) + 1
                        FROM drafts
                        WHERE workspace_id = %(workspace_id)s
                          AND brief_id = %(brief_id)s
                    )
                ),
                %(status)s,
                %(prompt_version)s,
                %(schema_version)s,
                %(payload)s
            )
            RETURNING workspace_id, id, brief_id, revision, status,
                      prompt_version, schema_version, payload,
                      decision_note, decided_by, decided_at, created_at
            """,
            {
                "workspace_id": workspace_id,
                "brief_id": brief_id,
                "explicit_revision": revision,
                "status": status,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "payload": Jsonb(payload),
            },
        )
        draft_row = cur.fetchone()
        if draft_row is None:  # pragma: no cover
            msg = f"Failed to create draft for brief {brief_id}"
            raise RuntimeError(msg)

        draft_id = uuid.UUID(str(draft_row["id"]))
        for idx, claim in enumerate(claims):
            raw_doc_id = claim.get("document_id") or claim.get("source_id")
            doc_uuid = uuid.UUID(str(raw_doc_id)) if raw_doc_id is not None else None
            cur.execute(
                """
                INSERT INTO draft_claims (
                    workspace_id, draft_id, idx, kind, text, document_id
                )
                VALUES (
                    %(workspace_id)s, %(draft_id)s, %(idx)s,
                    %(kind)s, %(text)s, %(document_id)s
                )
                """,
                {
                    "workspace_id": workspace_id,
                    "draft_id": draft_id,
                    "idx": idx,
                    "kind": str(claim.get("kind") or "analysis"),
                    "text": str(claim.get("text") or ""),
                    "document_id": doc_uuid,
                },
            )
        return dict(draft_row)


def get_draft(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve a draft row by ID within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, brief_id, revision, status,
                   prompt_version, schema_version, payload,
                   decision_note, decided_by, decided_at, created_at
            FROM drafts
            WHERE workspace_id = %(workspace_id)s
              AND id = %(draft_id)s
            """,
            {"workspace_id": workspace_id, "draft_id": draft_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def get_draft_workspace_id(
    conn: psycopg.Connection[Any],
    *,
    draft_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find workspace_id for a draft ID."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workspace_id
            FROM drafts
            WHERE id = %(draft_id)s
              AND workspace_id IS NOT NULL
            LIMIT 1
            """,
            {"draft_id": draft_id},
        )
        row = cur.fetchone()
        return uuid.UUID(str(row[0])) if row else None


def get_latest_draft_for_brief(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve the latest draft revision for a brief within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, brief_id, revision, status,
                   prompt_version, schema_version, payload,
                   decision_note, decided_by, decided_at, created_at
            FROM drafts
            WHERE workspace_id = %(workspace_id)s
              AND brief_id = %(brief_id)s
            ORDER BY revision DESC
            LIMIT 1
            """,
            {"workspace_id": workspace_id, "brief_id": brief_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def list_drafts(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List draft revisions in a workspace, optionally filtered by brief_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, id, brief_id, revision, status,
                   prompt_version, schema_version, payload,
                   decision_note, decided_by, decided_at, created_at
            FROM drafts
            WHERE workspace_id = %(workspace_id)s
              AND (%(brief_id)s::uuid IS NULL OR brief_id = %(brief_id)s)
            ORDER BY revision DESC, created_at DESC
            """,
            {"workspace_id": workspace_id, "brief_id": brief_id},
        )
        return [dict(r) for r in cur.fetchall()]


def list_draft_claims(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List persisted draft_claims rows ordered by idx, joined with source document metadata."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT dc.workspace_id, dc.draft_id, dc.idx, dc.kind, dc.text, dc.document_id,
                   d.title AS document_title, d.url AS document_url,
                   d.canonical_url AS document_canonical_url,
                   d.published_at AS document_published_at,
                   d.date_status AS document_date_status
            FROM draft_claims dc
            LEFT JOIN documents d
              ON d.workspace_id = dc.workspace_id
             AND d.id = dc.document_id
            WHERE dc.workspace_id = %(workspace_id)s
              AND dc.draft_id = %(draft_id)s
            ORDER BY dc.idx ASC
            """,
            {"workspace_id": workspace_id, "draft_id": draft_id},
        )
        return [dict(r) for r in cur.fetchall()]


def get_max_draft_revision_for_opportunity(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> int | None:
    """Return the highest draft revision across all brief versions for an opportunity."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(d.revision)
            FROM drafts d
            JOIN briefs b
              ON b.workspace_id = d.workspace_id
             AND b.id = d.brief_id
            WHERE d.workspace_id = %(workspace_id)s
              AND b.workspace_id = %(workspace_id)s
              AND b.opportunity_id = %(opportunity_id)s
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None


def get_draft_evaluation_context(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Load draft joined with brief, brand, opportunity, primary doc, run, and campaign."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.workspace_id, d.id, d.brief_id, d.revision, d.status,
                   d.prompt_version AS draft_prompt_version,
                   d.schema_version AS draft_schema_version,
                   d.payload AS draft_payload,
                   b.opportunity_id, b.version AS brief_version,
                   b.brand_profile_id, b.payload AS brief_payload,
                   bp.version AS brand_version, bp.payload AS brand_payload,
                   o.run_id, o.funnel_stage, o.status AS opportunity_status,
                   r.document_id AS primary_document_id,
                   run.campaign_id,
                   c.locale AS campaign_locale, c.market AS campaign_market,
                   c.config AS campaign_config,
                   w.industry_pack AS industry_pack,
                   COALESCE(w.config, '{}'::jsonb) AS workspace_config
            FROM drafts d
            JOIN briefs b
              ON b.workspace_id = d.workspace_id AND b.id = d.brief_id
            JOIN brand_profiles bp
              ON bp.workspace_id = b.workspace_id AND bp.id = b.brand_profile_id
            JOIN opportunities o
              ON o.workspace_id = b.workspace_id AND o.id = b.opportunity_id
            JOIN research_records r
              ON r.workspace_id = o.workspace_id AND r.id = o.record_id
            JOIN runs run
              ON run.workspace_id = o.workspace_id AND run.id = o.run_id
            JOIN campaigns c
              ON c.workspace_id = run.workspace_id AND c.id = run.campaign_id
            JOIN workspaces w
              ON w.id = d.workspace_id
            WHERE d.workspace_id = %(workspace_id)s
              AND d.id = %(draft_id)s
            """,
            {"workspace_id": workspace_id, "draft_id": draft_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def transition_draft_status(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_statuses: tuple[str, ...],
    new_status: str,
) -> dict[str, Any] | None:
    """Guarded atomic draft status transition; forbids revision 2 from moving to 'revision'."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE drafts
            SET status = %(new_status)s
            WHERE workspace_id = %(workspace_id)s
              AND id = %(draft_id)s
              AND status = ANY(%(expected_statuses)s)
              AND (%(new_status)s <> 'revision' OR revision < 2)
            RETURNING workspace_id, id, brief_id, revision, status,
                      prompt_version, schema_version, payload,
                      decision_note, decided_by, decided_at, created_at
            """,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "expected_statuses": list(expected_statuses),
                "new_status": new_status,
            },
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row is not None else None


def upsert_quality_report(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    verdict: str,
    score: float | Decimal | None,
    checks: dict[str, Any],
    findings: list[dict[str, Any]],
    evaluator_model: str,
    prompt_version: str,
) -> dict[str, Any]:
    """Persist 1:1 quality report for a draft revision scoped by workspace."""
    norm_score = Decimal(str(round(float(score), 2))) if score is not None else None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO quality_reports (
                workspace_id, draft_id, verdict, score, checks,
                findings, evaluator_model, prompt_version
            )
            VALUES (
                %(workspace_id)s, %(draft_id)s, %(verdict)s, %(score)s, %(checks)s,
                %(findings)s, %(evaluator_model)s, %(prompt_version)s
            )
            ON CONFLICT (draft_id) DO UPDATE
            SET verdict = EXCLUDED.verdict,
                score = EXCLUDED.score,
                checks = EXCLUDED.checks,
                findings = EXCLUDED.findings,
                evaluator_model = EXCLUDED.evaluator_model,
                prompt_version = EXCLUDED.prompt_version
            WHERE quality_reports.workspace_id = %(workspace_id)s
            RETURNING workspace_id, draft_id, verdict, score, checks,
                      findings, evaluator_model, prompt_version, created_at
            """,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "verdict": verdict,
                "score": norm_score,
                "checks": Jsonb(checks),
                "findings": Jsonb(findings),
                "evaluator_model": evaluator_model,
                "prompt_version": prompt_version,
            },
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:  # pragma: no cover
        msg = f"Failed to upsert quality_report for draft {draft_id}"
        raise RuntimeError(msg)
    return dict(row)


def get_quality_report(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Retrieve quality report for a draft within a workspace."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT workspace_id, draft_id, verdict, score, checks,
                   findings, evaluator_model, prompt_version, created_at
            FROM quality_reports
            WHERE workspace_id = %(workspace_id)s
              AND draft_id = %(draft_id)s
            """,
            {"workspace_id": workspace_id, "draft_id": draft_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def list_quality_reports(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    brief_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List quality reports in a workspace, optionally filtered by brief_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT qr.workspace_id, qr.draft_id, qr.verdict, qr.score, qr.checks,
                   qr.findings, qr.evaluator_model, qr.prompt_version, qr.created_at
            FROM quality_reports qr
            JOIN drafts d
              ON d.workspace_id = qr.workspace_id
             AND d.id = qr.draft_id
            WHERE qr.workspace_id = %(workspace_id)s
              AND (%(brief_id)s::uuid IS NULL OR d.brief_id = %(brief_id)s)
            ORDER BY d.revision DESC, qr.created_at DESC
            """,
            {"workspace_id": workspace_id, "brief_id": brief_id},
        )
        return [dict(r) for r in cur.fetchall()]


def return_opportunity_to_pool(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any] | None:
    """Return an approved opportunity back to 'scored' pool after research REJECT."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE opportunities
            SET status = 'scored',
                decided_by = NULL,
                decided_at = NULL,
                decision_note = COALESCE(%(note)s, decision_note)
            WHERE workspace_id = %(workspace_id)s
              AND id = %(opportunity_id)s
              AND status = 'approved'
            RETURNING workspace_id, id, run_id, record_id, scores, total, rank,
                      funnel_stage, confidence, angle, status, decided_by, decided_at,
                      decision_note, created_at
            """,
            {
                "workspace_id": workspace_id,
                "opportunity_id": opportunity_id,
                "note": note,
            },
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row is not None else None


def decide_draft(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
    decision: str,
    user_id: uuid.UUID,
    decided_at: datetime,
    note: str | None = None,
) -> dict[str, Any] | None:
    """Atomically transition draft from 'pass' to 'approved'/'declined' (F15 AC3, AC4)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE drafts
            SET status = %(decision)s,
                decided_by = %(user_id)s,
                decided_at = %(decided_at)s,
                decision_note = %(note)s
            WHERE workspace_id = %(workspace_id)s
              AND id = %(draft_id)s
              AND status = 'pass'
            RETURNING workspace_id, id, brief_id, revision, status,
                      prompt_version, schema_version, payload,
                      decision_note, decided_by, decided_at, created_at
            """,
            {
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "decision": decision,
                "user_id": user_id,
                "decided_at": decided_at,
                "note": note,
            },
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def mark_draft_published(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Transition an approved or published draft to 'published' within caller transaction."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE drafts
            SET status = 'published'
            WHERE workspace_id = %(workspace_id)s
              AND id = %(draft_id)s
              AND status IN ('approved', 'published')
            RETURNING workspace_id, id, brief_id, revision, status,
                      prompt_version, schema_version, payload,
                      decision_note, decided_by, decided_at, created_at
            """,
            {"workspace_id": workspace_id, "draft_id": draft_id},
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def list_drafts_for_review(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    statuses: tuple[str, ...] = ("pass", "approved", "declined", "published"),
) -> list[dict[str, Any]]:
    """List reviewable or decided drafts joined with brief, run, campaign, and quality."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.workspace_id, d.id, d.brief_id, d.revision, d.status,
                   d.prompt_version, d.schema_version, d.payload,
                   d.decision_note, d.decided_by, d.decided_at, d.created_at,
                   b.opportunity_id, b.version AS brief_version,
                   o.run_id,
                   run.campaign_id,
                   c.name AS campaign_name, c.locale AS campaign_locale,
                   qr.verdict AS quality_verdict, qr.score AS quality_score,
                   qr.findings AS quality_findings, qr.checks AS quality_checks,
                   qr.evaluator_model
            FROM drafts d
            JOIN briefs b
              ON b.workspace_id = d.workspace_id AND b.id = d.brief_id
            JOIN opportunities o
              ON o.workspace_id = b.workspace_id AND o.id = b.opportunity_id
            JOIN runs run
              ON run.workspace_id = o.workspace_id AND run.id = o.run_id
            JOIN campaigns c
              ON c.workspace_id = run.workspace_id AND c.id = run.campaign_id
            LEFT JOIN quality_reports qr
              ON qr.workspace_id = d.workspace_id AND qr.draft_id = d.id
            WHERE d.workspace_id = %(workspace_id)s
              AND d.status = ANY(%(statuses)s)
            ORDER BY (d.status = 'pass') DESC,
                     COALESCE(d.decided_at, d.created_at) DESC,
                     d.revision DESC
            """,
            {"workspace_id": workspace_id, "statuses": list(statuses)},
        )
        return [dict(r) for r in cur.fetchall()]


def list_latest_drafts_for_run(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> dict[uuid.UUID, dict[str, Any]]:
    """Return the latest draft revision keyed by opportunity_id for all opps in a run."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (b.opportunity_id)
                   b.opportunity_id,
                   d.workspace_id, d.id, d.brief_id, d.revision, d.status,
                   d.prompt_version, d.schema_version, d.payload,
                   d.decision_note, d.decided_by, d.decided_at, d.created_at
            FROM drafts d
            JOIN briefs b
              ON b.workspace_id = d.workspace_id AND b.id = d.brief_id
            JOIN opportunities o
              ON o.workspace_id = b.workspace_id AND o.id = b.opportunity_id
            WHERE d.workspace_id = %(workspace_id)s
              AND o.workspace_id = %(workspace_id)s
              AND o.run_id = %(run_id)s
            ORDER BY b.opportunity_id, d.revision DESC, d.created_at DESC
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        return {uuid.UUID(str(r["opportunity_id"])): dict(r) for r in cur.fetchall()}


def list_draft_revision_history_for_opportunity(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List all draft revisions and quality outcomes across brief versions for an opportunity."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.id, d.brief_id, d.revision, d.status,
                   d.prompt_version, d.created_at,
                   d.payload->>'title' AS title,
                   b.version AS brief_version,
                   qr.verdict AS quality_verdict, qr.score AS quality_score,
                   qr.findings AS quality_findings,
                   qr.evaluator_model
            FROM drafts d
            JOIN briefs b
              ON b.workspace_id = d.workspace_id AND b.id = d.brief_id
            LEFT JOIN quality_reports qr
              ON qr.workspace_id = d.workspace_id AND qr.draft_id = d.id
            WHERE d.workspace_id = %(workspace_id)s
              AND b.opportunity_id = %(opportunity_id)s
            ORDER BY d.revision ASC, d.created_at ASC
            """,
            {"workspace_id": workspace_id, "opportunity_id": opportunity_id},
        )
        return [dict(r) for r in cur.fetchall()]


def get_run_usage_breakdown(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Aggregate usage_ledger cost, calls, and tokens per stage for a run."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT stage,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                   COALESCE(SUM(units), 0) AS units,
                   COALESCE(SUM(cost_usd), 0.000000) AS cost_usd
            FROM usage_ledger
            WHERE workspace_id = %(workspace_id)s
              AND run_id = %(run_id)s
            GROUP BY stage
            ORDER BY MIN(created_at) ASC, stage ASC
            """,
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        rows: list[dict[str, Any]] = []
        for r in cur.fetchall():
            row = dict(r)
            row["cost_usd"] = Decimal(str(row["cost_usd"]))
            rows.append(row)
        return rows


def get_current_db_role(conn: psycopg.Connection[Any]) -> str:
    """Return the active PostgreSQL role name (`current_user`)."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
        return str(row[0]) if row is not None else ""


def purge_expired_data(
    conn: psycopg.Connection[Any],
    *,
    now: datetime,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Purge expired retention data per ADR-024 (F20 AC7).

    Windows relative to `now`:
      - `llm_payloads`: deleted after 90 days (`usage_ledger` metrics kept)
      - `documents.extracted_text`: set to NULL and `text_purged_at` stamped after 365 days
      - `webhook_deliveries` (`delivered` / `dead`): deleted after 30 days
      - `events`: deleted after 24 months (730 days)
    """
    cutoff_llm = now - timedelta(days=90)
    cutoff_docs = now - timedelta(days=365)
    cutoff_deliveries = now - timedelta(days=30)
    cutoff_events = now - timedelta(days=730)

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM llm_payloads p
            USING usage_ledger u
            WHERE p.usage_id = u.id
              AND (%(workspace_id)s::uuid IS NULL OR u.workspace_id = %(workspace_id)s)
              AND u.created_at < %(cutoff_llm)s
            """,
            {"workspace_id": workspace_id, "cutoff_llm": cutoff_llm},
        )
        llm_deleted = cur.rowcount

        cur.execute(
            """
            UPDATE documents
            SET extracted_text = NULL,
                text_purged_at = %(now)s
            WHERE (%(workspace_id)s::uuid IS NULL OR workspace_id = %(workspace_id)s)
              AND fetched_at < %(cutoff_docs)s
              AND extracted_text IS NOT NULL
            """,
            {"workspace_id": workspace_id, "cutoff_docs": cutoff_docs, "now": now},
        )
        docs_purged = cur.rowcount

        cur.execute(
            """
            DELETE FROM webhook_deliveries
            WHERE (%(workspace_id)s::uuid IS NULL OR workspace_id = %(workspace_id)s)
              AND status IN ('delivered', 'dead')
              AND COALESCE(delivered_at, next_attempt_at) < %(cutoff_deliveries)s
            """,
            {"workspace_id": workspace_id, "cutoff_deliveries": cutoff_deliveries},
        )
        deliveries_deleted = cur.rowcount

        # Delete any remaining webhook_deliveries referencing events older than 24 months
        cur.execute(
            """
            DELETE FROM webhook_deliveries wd
            USING events e
            WHERE wd.event_id = e.id
              AND (%(workspace_id)s::uuid IS NULL OR wd.workspace_id = %(workspace_id)s)
              AND e.occurred_at < %(cutoff_events)s
            """,
            {"workspace_id": workspace_id, "cutoff_events": cutoff_events},
        )
        deliveries_deleted += cur.rowcount

        cur.execute(
            """
            DELETE FROM events
            WHERE (%(workspace_id)s::uuid IS NULL OR workspace_id = %(workspace_id)s)
              AND occurred_at < %(cutoff_events)s
            """,
            {"workspace_id": workspace_id, "cutoff_events": cutoff_events},
        )
        events_deleted = cur.rowcount

    return {
        "llm_payloads_deleted": llm_deleted,
        "documents_text_purged": docs_purged,
        "webhook_deliveries_deleted": deliveries_deleted,
        "events_deleted": events_deleted,
    }


def anonymise_user_record(
    conn: psycopg.Connection[Any],
    *,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Anonymise a user's email and name, remove memberships, and scrub event PII (F20 AC8)."""
    anonymised_email = f"deleted-{user_id}@anonymised.invalid"
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            DELETE FROM memberships
            WHERE user_id = %(user_id)s AND workspace_id IS NOT NULL
            """,
            {"user_id": user_id},
        )
        memberships_removed = cur.rowcount

        cur.execute(
            """
            UPDATE users
            SET email = %(anonymised_email)s,
                name = NULL
            WHERE id = %(user_id)s
            RETURNING id AS user_id, email, name, ui_locale, created_at, last_login_at
            """,
            {"user_id": user_id, "anonymised_email": anonymised_email},
        )
        updated_user = cur.fetchone()
        if updated_user is None:  # pragma: no cover
            msg = f"User {user_id} not found"
            raise ValueError(msg)

        cur.execute(
            "SELECT anonymise_user_events(%(user_id)s) AS scrubbed",
            {"user_id": user_id},
        )
        scrub_row = cur.fetchone()
        events_scrubbed = int(scrub_row["scrubbed"]) if scrub_row else 0

    return {
        "user_id": user_id,
        "anonymised_email": str(updated_user["email"]),
        "memberships_removed": memberships_removed,
        "events_scrubbed": events_scrubbed,
    }


def list_runs_with_usage_summary(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List runs with aggregated usage_ledger cost, calls, tokens, and published article counts."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.workspace_id,
                   r.id AS run_id,
                   r.campaign_id,
                   r.trigger,
                   r.status,
                   r.stage,
                   r.run_cap_usd,
                   COALESCE(u.ledger_cost_usd, r.cost_usd, 0.000000) AS cost_usd,
                   COALESCE(u.calls, 0) AS calls,
                   COALESCE(u.input_tokens, 0) AS input_tokens,
                   COALESCE(u.output_tokens, 0) AS output_tokens,
                   COALESCE(u.cached_tokens, 0) AS cached_tokens,
                   COALESCE(p.published_articles_count, 0) AS published_articles_count,
                   r.created_at,
                   r.started_at,
                   r.finished_at
            FROM runs r
            LEFT JOIN (
                SELECT workspace_id,
                       run_id,
                       COUNT(*) AS calls,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cached_tokens) AS cached_tokens,
                       SUM(cost_usd) AS ledger_cost_usd
                FROM usage_ledger
                WHERE workspace_id = %(workspace_id)s
                  AND run_id IS NOT NULL
                GROUP BY workspace_id, run_id
            ) u ON u.workspace_id = r.workspace_id AND u.run_id = r.id
            LEFT JOIN (
                SELECT o.workspace_id,
                       o.run_id,
                       COUNT(DISTINCT pub.draft_id) AS published_articles_count
                FROM publications pub
                JOIN drafts d ON d.workspace_id = pub.workspace_id AND d.id = pub.draft_id
                JOIN briefs b ON b.workspace_id = d.workspace_id AND b.id = d.brief_id
                JOIN opportunities o ON o.workspace_id = b.workspace_id AND o.id = b.opportunity_id
                WHERE pub.workspace_id = %(workspace_id)s
                  AND pub.status = 'published'
                GROUP BY o.workspace_id, o.run_id
            ) p ON p.workspace_id = r.workspace_id AND p.run_id = r.id
            WHERE r.workspace_id = %(workspace_id)s
              AND (%(campaign_id)s::uuid IS NULL OR r.campaign_id = %(campaign_id)s)
            ORDER BY r.created_at DESC
            """,
            {"workspace_id": workspace_id, "campaign_id": campaign_id},
        )
        rows: list[dict[str, Any]] = []
        for r in cur.fetchall():
            row = dict(r)
            row["cost_usd"] = Decimal(str(row["cost_usd"])).quantize(Decimal("0.000001"))
            row["run_cap_usd"] = Decimal(str(row["run_cap_usd"])).quantize(Decimal("0.01"))
            row["calls"] = int(row["calls"])
            row["input_tokens"] = int(row["input_tokens"])
            row["output_tokens"] = int(row["output_tokens"])
            row["cached_tokens"] = int(row["cached_tokens"])
            row["published_articles_count"] = int(row["published_articles_count"])
            rows.append(row)
        return rows


def list_published_articles_with_costs(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """List published draft articles linked to their originating run (F17 AC5, AC6)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (pub.draft_id)
                   pub.workspace_id,
                   pub.id AS publication_id,
                   pub.draft_id,
                   o.run_id,
                   r.campaign_id,
                   COALESCE(d.payload->>'title', pub.title) AS title,
                   COALESCE(d.payload->>'slug', '') AS slug,
                   pub.destination,
                   pub.mode,
                   pub.external_id,
                   pub.url,
                   pub.published_at,
                   pub.created_at
            FROM publications pub
            JOIN drafts d ON d.workspace_id = pub.workspace_id AND d.id = pub.draft_id
            JOIN briefs b ON b.workspace_id = d.workspace_id AND b.id = d.brief_id
            JOIN opportunities o ON o.workspace_id = b.workspace_id AND o.id = b.opportunity_id
            JOIN runs r ON r.workspace_id = o.workspace_id AND r.id = o.run_id
            WHERE pub.workspace_id = %(workspace_id)s
              AND pub.status = 'published'
              AND (%(campaign_id)s::uuid IS NULL OR r.campaign_id = %(campaign_id)s)
            ORDER BY pub.draft_id, pub.published_at DESC NULLS LAST, pub.created_at DESC
            """,
            {"workspace_id": workspace_id, "campaign_id": campaign_id},
        )
        return [dict(r) for r in cur.fetchall()]


def _to_json_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return dict(item.model_dump(mode="json"))
    return dict(item)


def insert_site_audit(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    report: Any,
) -> uuid.UUID:
    """Persist a bounded crawl or site audit report scoped to workspace_id."""
    payload = _to_json_dict(report)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO site_audits (
                workspace_id, start_url, pages_crawled, max_pages, overall_score, grade, payload
            )
            VALUES (
                %(workspace_id)s, %(start_url)s, %(pages_crawled)s, %(max_pages)s,
                %(overall_score)s, %(grade)s, %(payload)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "start_url": str(payload.get("start_url", "")),
                "pages_crawled": int(payload.get("pages_crawled", 0)),
                "max_pages": int(payload.get("max_pages", 20)),
                "overall_score": Decimal(str(payload.get("overall_score", 0.0))),
                "grade": str(payload.get("grade", "F")),
                "payload": Jsonb(payload),
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = "Failed to insert site_audits row"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def insert_audit_page(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    page: Any,
    site_audit_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Persist a single PageAuditReport row scoped to workspace_id."""
    payload = _to_json_dict(page)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_pages (
                workspace_id, site_audit_id, url, status_code, overall_score, grade, payload
            )
            VALUES (
                %(workspace_id)s, %(site_audit_id)s, %(url)s, %(status_code)s,
                %(overall_score)s, %(grade)s, %(payload)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "site_audit_id": site_audit_id,
                "url": str(payload.get("url", "")),
                "status_code": int(payload.get("status_code", 0)),
                "overall_score": Decimal(str(payload.get("overall_score", 0.0))),
                "grade": str(payload.get("grade", "F")),
                "payload": Jsonb(payload),
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = "Failed to insert audit_pages row"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def list_site_audits(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent workspace site audits ordered newest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, start_url, pages_crawled, max_pages,
                   overall_score, grade, payload, created_at
            FROM site_audits
            WHERE workspace_id = %(workspace_id)s
            ORDER BY created_at DESC
            LIMIT %(limit)s
            """,
            {"workspace_id": workspace_id, "limit": limit},
        )
        return [dict(r) for r in cur.fetchall()]


def get_gsc_inspection_cache(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    site_url: str,
    inspected_url: str,
    max_age_hours: int = 12,
) -> dict[str, Any] | None:
    """Return cached Google URL Inspection response if fetched within max_age_hours."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT payload, fetched_at
            FROM gsc_url_inspection_cache
            WHERE workspace_id = %(workspace_id)s
              AND site_url = %(site_url)s
              AND inspected_url = %(inspected_url)s
              AND fetched_at >= NOW() - (%(max_age_hours)s || ' hours')::interval
            """,
            {
                "workspace_id": workspace_id,
                "site_url": site_url,
                "inspected_url": inspected_url,
                "max_age_hours": max_age_hours,
            },
        )
        row = cur.fetchone()
        if row is None:
            return None
        payload = row["payload"]
        return dict(payload) if isinstance(payload, dict) else None


def upsert_gsc_inspection_cache(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    site_url: str,
    inspected_url: str,
    payload: dict[str, Any],
) -> None:
    """Upsert workspace_id GSC URL inspection cache entry."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gsc_url_inspection_cache (
                workspace_id, site_url, inspected_url, payload, fetched_at
            )
            VALUES (%(workspace_id)s, %(site_url)s, %(inspected_url)s, %(payload)s, NOW())
            ON CONFLICT (workspace_id, site_url, inspected_url)
            DO UPDATE SET
                payload = EXCLUDED.payload,
                fetched_at = EXCLUDED.fetched_at
            """,
            {
                "workspace_id": workspace_id,
                "site_url": site_url,
                "inspected_url": inspected_url,
                "payload": Jsonb(payload),
            },
        )


def upsert_url_monitor(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    url: str,
    schedule_cron: str = "0 9 * * *",
    score_drop_threshold: float = 5.0,
    tls_expiry_warn_days: int = 14,
) -> uuid.UUID:
    """Create or refresh a workspace_id route monitor row and return its UUID."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO url_monitors (
                workspace_id, url, schedule_cron, score_drop_threshold, tls_expiry_warn_days
            )
            VALUES (
                %(workspace_id)s, %(url)s, %(schedule_cron)s,
                %(score_drop_threshold)s, %(tls_expiry_warn_days)s
            )
            ON CONFLICT (workspace_id, url)
            DO UPDATE SET
                schedule_cron = EXCLUDED.schedule_cron,
                score_drop_threshold = EXCLUDED.score_drop_threshold,
                tls_expiry_warn_days = EXCLUDED.tls_expiry_warn_days
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "url": url,
                "schedule_cron": schedule_cron,
                "score_drop_threshold": Decimal(str(score_drop_threshold)),
                "tls_expiry_warn_days": tls_expiry_warn_days,
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = "Failed to upsert url_monitors row"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def list_url_monitors(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """List scheduled route monitors for workspace_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, url, schedule_cron, score_drop_threshold,
                   tls_expiry_warn_days, last_score, last_checks,
                   last_tls_days_left, last_status, last_checked_at
            FROM url_monitors
            WHERE workspace_id = %(workspace_id)s
            ORDER BY created_at ASC
            """,
            {"workspace_id": workspace_id},
        )
        return [dict(r) for r in cur.fetchall()]


def update_url_monitor_result(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    monitor_id: uuid.UUID,
    last_score: float | None,
    last_checks: list[dict[str, Any]] | None,
    last_tls_days_left: int | None,
    last_status: str,
) -> None:
    """Save latest route monitor audit snapshot for workspace_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE url_monitors
            SET last_score = %(last_score)s,
                last_checks = %(last_checks)s,
                last_tls_days_left = %(last_tls_days_left)s,
                last_status = %(last_status)s,
                last_checked_at = NOW()
            WHERE workspace_id = %(workspace_id)s AND id = %(monitor_id)s
            """,
            {
                "workspace_id": workspace_id,
                "monitor_id": monitor_id,
                "last_score": Decimal(str(last_score)) if last_score is not None else None,
                "last_checks": Jsonb(last_checks) if last_checks is not None else None,
                "last_tls_days_left": last_tls_days_left,
                "last_status": last_status,
            },
        )


def insert_keyword_ranking(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    keyword: str,
    domain: str,
    locale: str = "pt-BR",
    market: str = "BR",
    position: int | None,
    previous_position: int | None,
    ranking_url: str | None,
    provider: str,
) -> uuid.UUID:
    """Record a keyword ranking history snapshot for workspace_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO keyword_rankings (
                workspace_id, keyword, domain, locale, market,
                position, previous_position, ranking_url, provider
            )
            VALUES (
                %(workspace_id)s, %(keyword)s, %(domain)s, %(locale)s, %(market)s,
                %(position)s, %(previous_position)s, %(ranking_url)s, %(provider)s
            )
            RETURNING id
            """,
            {
                "workspace_id": workspace_id,
                "keyword": keyword,
                "domain": domain,
                "locale": locale,
                "market": market,
                "position": position,
                "previous_position": previous_position,
                "ranking_url": ranking_url,
                "provider": provider,
            },
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover
            msg = "Failed to insert keyword_rankings row"
            raise RuntimeError(msg)
        return uuid.UUID(str(row[0]))


def list_keyword_rankings(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    domain: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch recent keyword ranking history snapshots for workspace_id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, workspace_id, keyword, domain, locale, market,
                   position, previous_position, ranking_url, provider, checked_at
            FROM keyword_rankings
            WHERE workspace_id = %(workspace_id)s
              AND (%(domain)s::text IS NULL OR domain = %(domain)s)
              AND (%(keyword)s::text IS NULL OR keyword = %(keyword)s)
            ORDER BY checked_at DESC
            LIMIT %(limit)s
            """,
            {
                "workspace_id": workspace_id,
                "domain": domain,
                "keyword": keyword,
                "limit": limit,
            },
        )
        return [dict(r) for r in cur.fetchall()]
