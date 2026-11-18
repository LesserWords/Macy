"""Idempotent Python SQL migration runner for Pocket startup (ADR-030)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psycopg

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def parse_migration_up(sql_text: str) -> str:
    """Extract the `-- migrate:up` section from a dbmate-formatted SQL file."""
    lines: list[str] = []
    in_up = False
    for line in sql_text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("-- migrate:up"):
            in_up = True
            continue
        if stripped.startswith("-- migrate:down"):
            break
        if in_up:
            lines.append(line)
    return "\n".join(lines).strip()


def _migration_uses_transaction(sql_text: str) -> bool:
    for line in sql_text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("-- migrate:up"):
            return "transaction:false" not in stripped
    return True  # pragma: no cover


def _ensure_migrations_table(conn: psycopg.Connection[Any]) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        row = cur.fetchone()
        if row is not None and row[0] is not None:
            return
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version character varying NOT NULL PRIMARY KEY
            )
            """
        )


def _applied_versions(conn: psycopg.Connection[Any]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {str(row[0]) for row in cur.fetchall()}


def _apply_single_migration(
    conn: psycopg.Connection[Any],
    *,
    version: str,
    sql_text: str,
) -> None:
    up_sql = parse_migration_up(sql_text)
    if not up_sql:  # pragma: no cover
        return
    use_tx = _migration_uses_transaction(sql_text)
    if use_tx:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(up_sql.encode("utf-8"))
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
    else:  # pragma: no cover
        with conn.cursor() as cur:
            cur.execute(up_sql.encode("utf-8"))
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))


def apply_pending_migrations(
    conn: psycopg.Connection[Any],
    *,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply all pending `-- migrate:up` SQL files in sorted order."""
    target_dir = migrations_dir if migrations_dir is not None else DEFAULT_MIGRATIONS_DIR
    if not target_dir.is_dir():  # pragma: no cover
        return []

    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    newly_applied: list[str] = []

    for sql_file in sorted(target_dir.glob("*.sql")):
        version = sql_file.name.split("_", 1)[0]
        if version in applied:
            continue
        _apply_single_migration(
            conn,
            version=version,
            sql_text=sql_file.read_text(encoding="utf-8"),
        )
        newly_applied.append(version)

    return newly_applied


def run_startup_migrations(
    *,
    env: Mapping[str, str] | None = None,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Run pending SQL migrations at startup when AUTO_MIGRATE is enabled (ADR-030)."""
    source = os.environ if env is None else env
    auto = str(source.get("AUTO_MIGRATE", "")).strip().lower() in {"1", "true", "yes"}
    if not auto:
        return []
    db_url = (source.get("MIGRATION_DATABASE_URL") or source.get("DATABASE_URL") or "").strip()
    if not db_url:
        return []
    with psycopg.connect(db_url, autocommit=True) as conn:
        return apply_pending_migrations(conn, migrations_dir=migrations_dir)
