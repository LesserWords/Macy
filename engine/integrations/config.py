"""Integration configuration, secrets encryption with Fernet, and MultiFernet rotation (ADR-008)."""

import json
import os
import uuid
from collections.abc import Sequence
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import BaseModel

from engine.integrations.registry import IntegrationMissing, get_plugin


def _get_fernet_instance(keys: Sequence[str | bytes] | str | bytes) -> MultiFernet:
    key_list = [keys] if isinstance(keys, (str, bytes)) else list(keys)
    fernets = [Fernet(k.encode("utf-8") if isinstance(k, str) else k) for k in key_list]
    return MultiFernet(fernets)


def encrypt_secrets(secrets: dict[str, Any], key: str | bytes) -> bytes:
    """Encrypt secrets dictionary into bytes using Fernet."""
    f = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    payload = json.dumps(secrets).encode("utf-8")
    return f.encrypt(payload)


def decrypt_secrets(
    encrypted_secrets: bytes | memoryview,
    keys: Sequence[str | bytes] | str | bytes,
) -> dict[str, Any]:
    """Decrypt encrypted bytes into secrets dictionary using MultiFernet."""
    mf = _get_fernet_instance(keys)
    raw_bytes = bytes(encrypted_secrets)
    decrypted_bytes = mf.decrypt(raw_bytes)
    res: dict[str, Any] = json.loads(decrypted_bytes.decode("utf-8"))
    return res


def rotate_secrets(
    encrypted_secrets: bytes | memoryview,
    current_keys: Sequence[str | bytes],
    new_key: str | bytes,
) -> bytes:
    """Re-encrypt secrets under a new primary key using MultiFernet."""
    all_keys: list[str | bytes] = [new_key] + [k for k in current_keys if k != new_key]
    mf = _get_fernet_instance(all_keys)
    raw_bytes = bytes(encrypted_secrets)
    return mf.rotate(raw_bytes)


def mask_secrets(secrets: dict[str, Any]) -> dict[str, str]:
    """Return dictionary with all secret values masked."""
    return {k: "********" for k in secrets}


def _resolve_keys(key_arg: str | list[str] | None) -> list[str]:
    if key_arg is not None:
        return [key_arg] if isinstance(key_arg, str) else list(key_arg)
    env_key = os.environ.get("APP_ENCRYPTION_KEY")
    if not env_key:
        msg = "APP_ENCRYPTION_KEY environment variable is required to encrypt/decrypt secrets"
        raise ValueError(msg)
    return [k.strip() for k in env_key.split(",") if k.strip()]


def plugin_config(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    port: str,
    encryption_key: str | list[str] | None = None,
) -> tuple[type[Any], BaseModel]:
    """Fetch plugin class and validated Settings for a workspace and port."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plugin, settings, secrets
            FROM workspace_integrations
            WHERE workspace_id = %s AND port = %s
            """,
            (workspace_id, port),
        )
        row = cur.fetchone()

    if row is None:
        raise IntegrationMissing(port)

    plugin_name, settings_dict, secrets_bytes = row[0], row[1], row[2]
    plugin_cls = get_plugin(port, plugin_name)

    decrypted_secrets: dict[str, Any] = {}
    if secrets_bytes is not None:
        keys = _resolve_keys(encryption_key)
        decrypted_secrets = decrypt_secrets(secrets_bytes, keys)

    combined = {**settings_dict, **decrypted_secrets}
    settings_instance = plugin_cls.Settings.model_validate(combined)
    return plugin_cls, settings_instance


def _execute_set_workspace_integration(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    *,
    workspace_id: uuid.UUID,
    port: str,
    plugin: str,
    settings: dict[str, Any] | None,
    secrets: dict[str, Any] | None,
    updated_by: uuid.UUID,
    encryption_key: str | None,
) -> None:
    get_plugin(port, plugin)
    non_secret_settings = settings or {}
    encrypted_secrets: bytes | None = None

    if secrets:
        keys = _resolve_keys(encryption_key)
        encrypted_secrets = encrypt_secrets(secrets, keys[0])

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO workspace_integrations (
                workspace_id, port, plugin, settings, secrets, key_version, updated_by, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, 1, %s, now()
            )
            ON CONFLICT (workspace_id, port) DO UPDATE SET
                plugin = EXCLUDED.plugin,
                settings = EXCLUDED.settings,
                secrets = EXCLUDED.secrets,
                key_version = EXCLUDED.key_version,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """,
            (
                workspace_id,
                port,
                plugin,
                json.dumps(non_secret_settings),
                encrypted_secrets,
                updated_by,
            ),
        )
    conn.commit()


def set_workspace_integration(  # noqa: PLR0913
    conn: psycopg.Connection[Any] | None = None,
    *,
    workspace_id: uuid.UUID,
    port: str,
    plugin: str,
    settings: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
    updated_by: uuid.UUID,
    encryption_key: str | None = None,
) -> None:
    """Upsert an integration configuration for a workspace."""
    if conn is not None:
        _execute_set_workspace_integration(
            conn,
            workspace_id=workspace_id,
            port=port,
            plugin=plugin,
            settings=settings,
            secrets=secrets,
            updated_by=updated_by,
            encryption_key=encryption_key,
        )
        return

    from engine.db import get_connection

    with get_connection() as new_conn:
        _execute_set_workspace_integration(
            new_conn,
            workspace_id=workspace_id,
            port=port,
            plugin=plugin,
            settings=settings,
            secrets=secrets,
            updated_by=updated_by,
            encryption_key=encryption_key,
        )


def _execute_rotate_keys(
    conn: psycopg.Connection[Any],
    *,
    current_keys: list[str],
    new_key: str,
    workspace_id: uuid.UUID | None = None,
) -> int:
    with conn.cursor() as cur:
        query = (
            "SELECT workspace_id, port, secrets, key_version "
            "FROM workspace_integrations WHERE secrets IS NOT NULL"
        )
        params: list[Any] = []
        if workspace_id is not None:
            query += " AND workspace_id = %s"
            params.append(workspace_id)

        cur.execute(query, tuple(params))
        rows = cur.fetchall()

        count = 0
        for ws_id, port, old_secrets, key_version in rows:
            try:
                new_secrets = rotate_secrets(old_secrets, current_keys, new_key)
            except InvalidToken:
                continue
            cur.execute(
                """
                UPDATE workspace_integrations
                SET secrets = %s, key_version = %s, updated_at = now()
                WHERE workspace_id = %s AND port = %s
                """,
                (new_secrets, key_version + 1, ws_id, port),
            )
            count += 1

    conn.commit()
    return count


def rotate_workspace_integration_keys(
    conn: psycopg.Connection[Any] | None = None,
    *,
    current_keys: list[str],
    new_key: str,
    workspace_id: uuid.UUID | None = None,
) -> int:
    """Re-encrypt all secrets in workspace_integrations under new_key."""
    if conn is not None:
        return _execute_rotate_keys(
            conn, current_keys=current_keys, new_key=new_key, workspace_id=workspace_id
        )

    from engine.db import get_connection

    with get_connection() as new_conn:
        return _execute_rotate_keys(
            new_conn, current_keys=current_keys, new_key=new_key, workspace_id=workspace_id
        )
