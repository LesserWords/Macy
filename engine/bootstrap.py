"""Zero-.env persistent secret bootstrap for Pocket single-container runtime (ADR-030)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from cryptography.fernet import Fernet

SECRETS_FILENAME = ".macy_secrets.json"


def is_pocket_mode(env: Mapping[str, str] | None = None) -> bool:
    """Return True when POCKET_MODE is enabled."""
    source = os.environ if env is None else env
    return str(source.get("POCKET_MODE", "")).strip().lower() in {"1", "true", "yes"}


def _atomic_write_secrets(target: Path, payload: dict[str, str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".macy-sec-", suffix=".tmp", dir=target.parent)
    tmp_path = Path(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    tmp_path.chmod(0o600)
    os.replace(tmp_path, target)


def _load_stored_secrets(secrets_file: Path) -> dict[str, str]:
    if not secrets_file.is_file():
        return {}
    loaded = json.loads(secrets_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items()}


def _resolve_secret(
    key: str,
    target_env: Mapping[str, str],
    stored: Mapping[str, str],
    default_value: str,
) -> str:
    return target_env.get(key, "").strip() or stored.get(key, "") or default_value


def ensure_pocket_secrets(
    *,
    data_dir: Path | str | None = None,
    env: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load or generate persistent APP_ENCRYPTION_KEY and AUTH_COOKIE_SECRET in Pocket mode."""
    target_env: MutableMapping[str, str] = os.environ if env is None else env
    if not is_pocket_mode(target_env):
        return {}

    resolved_dir = Path(
        data_dir if data_dir is not None else target_env.get("MACY_DATA_DIR") or "/data"
    )
    secrets_file = resolved_dir / SECRETS_FILENAME
    stored = _load_stored_secrets(secrets_file)

    resolved = {
        "APP_ENCRYPTION_KEY": _resolve_secret(
            "APP_ENCRYPTION_KEY",
            target_env,
            stored,
            Fernet.generate_key().decode("ascii"),
        ),
        "AUTH_COOKIE_SECRET": _resolve_secret(
            "AUTH_COOKIE_SECRET",
            target_env,
            stored,
            secrets.token_hex(32),
        ),
    }
    if stored != resolved:
        _atomic_write_secrets(secrets_file, resolved)

    target_env.update(resolved)
    return resolved
