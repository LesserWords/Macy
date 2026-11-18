"""Tests for M7-02 D1 zero-.env secret bootstrap and Python SQL migration runner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg import sql


def test_ensure_pocket_secrets_generates_persists_and_reuses_keys(tmp_path: Path) -> None:
    """D1: Pocket mode generates valid persistent keys once and reuses them across boots."""
    from engine.bootstrap import SECRETS_FILENAME, ensure_pocket_secrets

    env: dict[str, str] = {"POCKET_MODE": "true"}
    first = ensure_pocket_secrets(data_dir=tmp_path, env=env)

    secrets_file = tmp_path / SECRETS_FILENAME
    assert secrets_file.is_file()
    Fernet(first["APP_ENCRYPTION_KEY"].encode("ascii"))
    assert len(first["AUTH_COOKIE_SECRET"]) == 64
    assert env["APP_ENCRYPTION_KEY"] == first["APP_ENCRYPTION_KEY"]
    assert env["AUTH_COOKIE_SECRET"] == first["AUTH_COOKIE_SECRET"]

    if os.name != "nt":
        assert (secrets_file.stat().st_mode & 0o777) == 0o600

    # Second boot with fresh env reloads exact same keys
    env_second: dict[str, str] = {"POCKET_MODE": "true"}
    second = ensure_pocket_secrets(data_dir=tmp_path, env=env_second)
    assert second == first
    assert env_second["APP_ENCRYPTION_KEY"] == first["APP_ENCRYPTION_KEY"]


def test_ensure_pocket_secrets_skips_when_non_pocket_or_preset(tmp_path: Path) -> None:
    """D1: Non-pocket mode never auto-generates secrets; explicit env vars win."""
    from engine.bootstrap import SECRETS_FILENAME, ensure_pocket_secrets

    non_pocket_env: dict[str, str] = {}
    assert ensure_pocket_secrets(data_dir=tmp_path, env=non_pocket_env) == {}
    assert not (tmp_path / SECRETS_FILENAME).exists()

    explicit_key = Fernet.generate_key().decode("ascii")
    env_preset: dict[str, str] = {
        "POCKET_MODE": "true",
        "APP_ENCRYPTION_KEY": explicit_key,
        "AUTH_COOKIE_SECRET": "b" * 64,
    }
    res = ensure_pocket_secrets(data_dir=tmp_path, env=env_preset)
    assert res["APP_ENCRYPTION_KEY"] == explicit_key
    assert res["AUTH_COOKIE_SECRET"] == "b" * 64
    persisted = json.loads((tmp_path / SECRETS_FILENAME).read_text(encoding="utf-8"))
    assert persisted["APP_ENCRYPTION_KEY"] == explicit_key

    # Non-dict secrets file falls back to regenerating keys safely
    (tmp_path / SECRETS_FILENAME).write_text("[]", encoding="utf-8")
    recovered = ensure_pocket_secrets(data_dir=tmp_path, env={"POCKET_MODE": "true"})
    assert recovered["APP_ENCRYPTION_KEY"] != ""


def test_python_migration_runner_applies_idempotently(tmp_path: Path) -> None:
    """D1: Python migration runner executes -- migrate:up blocks once and handles errors."""
    from engine.migrations import (
        apply_pending_migrations,
        parse_migration_up,
        run_startup_migrations,
    )

    owner_url = os.getenv(
        "OWNER_DATABASE_URL",
        "postgres://owner:secret@localhost:5432/macy?sslmode=disable",
    )

    sql_sample = (
        "-- migrate:up\n"
        "CREATE TEMP TABLE m7_mig_test (id int);\n"
        "-- migrate:down\n"
        "DROP TABLE m7_mig_test;\n"
    )
    assert "CREATE TEMP TABLE" in parse_migration_up(sql_sample)
    assert "DROP TABLE" not in parse_migration_up(sql_sample)

    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    table_name = f"m7_mig_ok_{os.getpid()}"
    (mig_dir / "99990101000001_create_tmp.sql").write_text(
        f"-- migrate:up\nCREATE TEMP TABLE {table_name} (id int);\n"
        f"-- migrate:down\nDROP TABLE {table_name};\n",
        encoding="utf-8",
    )

    with psycopg.connect(owner_url, autocommit=True) as owner_conn:
        try:
            applied = apply_pending_migrations(owner_conn, migrations_dir=mig_dir)
            assert applied == ["99990101000001"]

            # Second run via run_startup_migrations is a no-op
            assert (
                run_startup_migrations(
                    env={"AUTO_MIGRATE": "true", "MIGRATION_DATABASE_URL": owner_url},
                    migrations_dir=mig_dir,
                )
                == []
            )
            assert run_startup_migrations(env={"AUTO_MIGRATE": "false"}) == []
            assert run_startup_migrations(env={"AUTO_MIGRATE": "true"}) == []

            # Failing migration rolls back cleanly and is not recorded
            (mig_dir / "99990101000002_bad.sql").write_text(
                "-- migrate:up\nSELECT * FROM nonexistent_table_xyz_123;\n",
                encoding="utf-8",
            )
            with pytest.raises(psycopg.Error):
                apply_pending_migrations(owner_conn, migrations_dir=mig_dir)
        finally:
            with owner_conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM schema_migrations WHERE version IN (%s, %s)",
                    ("99990101000001", "99990101000002"),
                )
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name)))


def test_ensure_migrations_table_creates_when_missing() -> None:
    """D1: _ensure_migrations_table executes CREATE TABLE when to_regclass returns None."""
    from unittest.mock import MagicMock

    from engine.migrations import _ensure_migrations_table

    fake_conn = MagicMock()
    fake_cur = fake_conn.cursor.return_value.__enter__.return_value
    fake_cur.fetchone.return_value = (None,)
    _ensure_migrations_table(fake_conn)
    assert fake_cur.execute.call_count == 2
