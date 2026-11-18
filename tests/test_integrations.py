import uuid
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet, InvalidToken

from engine import db
from engine.integrations.registry import IntegrationMissing


def test_ports_and_registry_importable() -> None:
    from engine.integrations import ports, registry

    assert hasattr(ports, "LLMPort")
    assert hasattr(ports, "FeedSourcePort")
    assert hasattr(ports, "NewsSearchPort")
    assert hasattr(ports, "ExtractorPort")
    assert hasattr(ports, "KeywordProviderPort")
    assert hasattr(ports, "PublisherPort")
    assert hasattr(ports, "AnalyticsSourcePort")
    assert hasattr(registry, "discover_plugins")
    assert hasattr(registry, "IntegrationMissing")


def test_integration_missing_names_port() -> None:
    exc = IntegrationMissing("publisher")
    assert exc.port == "publisher"
    assert "publisher" in str(exc)


def test_discover_plugins_finds_all_fakes() -> None:
    from engine.integrations.registry import discover_plugins

    plugins = discover_plugins()
    for port in (
        "llm",
        "feed_source",
        "news_search",
        "extractor",
        "keyword_provider",
        "publisher",
        "analytics_source",
    ):
        assert port in plugins, f"Port '{port}' missing from discovered plugins"
        assert "fake" in plugins[port], f"Fake plugin missing for port '{port}'"


def test_cli_plugins_list() -> None:
    from engine.cli import list_plugins_command

    plugins = list_plugins_command()
    assert len(plugins) >= 7
    ports_found = {p["port"] for p in plugins}
    assert "llm" in ports_found
    assert "publisher" in ports_found


def _create_user(conn: psycopg.Connection[Any]) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, ui_locale)
            VALUES (%(email)s, %(name)s, %(ui_locale)s)
            RETURNING id
            """,
            {
                "email": f"user-{uuid.uuid4().hex[:8]}@example.com",
                "name": "Test User",
                "ui_locale": "pt-BR",
            },
        )
        row = cur.fetchone()
        assert row is not None
        user_id: uuid.UUID = row[0]
        return user_id


def test_encrypt_decrypt_and_rotate_secrets() -> None:
    from engine.integrations.config import (
        decrypt_secrets,
        encrypt_secrets,
        rotate_secrets,
    )

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()

    raw_secrets = {"api_key": "sk-secret-token", "account_id": 42}
    encrypted = encrypt_secrets(raw_secrets, k1)

    assert isinstance(encrypted, bytes)
    assert b"sk-secret-token" not in encrypted

    # Decrypt with k1
    decrypted = decrypt_secrets(encrypted, [k1])
    assert decrypted == raw_secrets

    # Rotate from k1 to k2
    rotated = rotate_secrets(encrypted, current_keys=[k1], new_key=k2)
    assert isinstance(rotated, bytes)
    assert b"sk-secret-token" not in rotated

    # Decrypt with k2 succeeds
    assert decrypt_secrets(rotated, [k2]) == raw_secrets

    # Decrypt rotated with only k1 fails
    with pytest.raises(InvalidToken):
        decrypt_secrets(rotated, [k1])


def test_mask_secrets() -> None:
    from engine.integrations.config import mask_secrets

    raw = {"api_key": "sk-sensitive", "token": "topsecret"}
    masked = mask_secrets(raw)
    assert masked == {"api_key": "********", "token": "********"}
    assert "sk-sensitive" not in str(masked)


def test_plugin_config_unconfigured_port_raises_integration_missing(
    db_conn: psycopg.Connection[Any],
) -> None:
    from engine.integrations.config import plugin_config

    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-empty-{uuid.uuid4().hex[:8]}",
        name="Empty Integrations WS",
        industry_pack="general",
    )
    with pytest.raises(IntegrationMissing) as exc_info:
        plugin_config(db_conn, workspace_id=ws_id, port="llm")
    assert exc_info.value.port == "llm"


def test_set_and_get_workspace_integration(db_conn: psycopg.Connection[Any]) -> None:
    from engine.integrations.config import plugin_config, set_workspace_integration
    from engine.integrations.fakes import FakeLLM

    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-cfg-{uuid.uuid4().hex[:8]}",
        name="Configured Integrations WS",
        industry_pack="general",
    )
    user_id = _create_user(db_conn)
    key = Fernet.generate_key().decode()

    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="llm",
        plugin="fake",
        settings={"model_name": "custom-fake-model"},
        secrets={"default_response": {"custom": "hello"}},
        updated_by=user_id,
        encryption_key=key,
    )

    # Verify secrets in database are encrypted
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT secrets FROM workspace_integrations WHERE workspace_id = %s AND port = %s",
            (ws_id, "llm"),
        )
        row = cur.fetchone()
        assert row is not None
        encrypted_bytes = row[0]
        assert isinstance(encrypted_bytes, (bytes, memoryview))
        assert b"custom" not in bytes(encrypted_bytes)

    # plugin_config loads and validates settings
    cls, settings = plugin_config(db_conn, workspace_id=ws_id, port="llm", encryption_key=key)
    assert cls is FakeLLM
    assert isinstance(settings, FakeLLM.Settings)
    assert settings.model_name == "custom-fake-model"
    assert settings.default_response == {"custom": "hello"}


def test_rotate_workspace_integration_keys(db_conn: psycopg.Connection[Any]) -> None:
    from engine.integrations.config import (
        plugin_config,
        rotate_workspace_integration_keys,
        set_workspace_integration,
    )
    from engine.integrations.fakes import FakeFeedSource

    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-rot-{uuid.uuid4().hex[:8]}",
        name="Key Rotation WS",
        industry_pack="general",
    )
    user_id = _create_user(db_conn)
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()

    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="feed_source",
        plugin="fake",
        settings={"default_items_count": 3},
        secrets={"auth_token": "feed-token-123"},
        updated_by=user_id,
        encryption_key=key1,
    )

    # Rotate keys in DB
    rotated_count = rotate_workspace_integration_keys(db_conn, current_keys=[key1], new_key=key2)
    assert rotated_count >= 1

    # Now decryption with key2 works
    cls, settings = plugin_config(
        db_conn, workspace_id=ws_id, port="feed_source", encryption_key=key2
    )
    assert isinstance(settings, FakeFeedSource.Settings)
    assert settings.default_items_count == 3


def test_cli_integrations_set_and_rotate_key(
    db_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine import cli

    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-cli-{uuid.uuid4().hex[:8]}",
        name="CLI WS",
        industry_pack="general",
    )
    user_id = _create_user(db_conn)
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()

    monkeypatch.setenv("APP_ENCRYPTION_KEY", key1)

    # Test integrations set via CLI
    code = cli.main(
        [
            "integrations",
            "set",
            "--workspace-id",
            str(ws_id),
            "--port",
            "extractor",
            "--plugin",
            "fake",
            "--settings",
            '{"user_agent": "CustomAgent/2.0"}',
            "--secrets",
            '{"api_key": "extr-secret"}',
            "--user-id",
            str(user_id),
        ]
    )
    assert code == 0

    # Test rotate-key via CLI (use '=' form in case base64 key begins with '-')
    code_rot = cli.main(
        [
            "rotate-key",
            f"--current-keys={key1}",
            f"--new-key={key2}",
        ]
    )
    assert code_rot == 0


def test_get_plugin_missing_raises_integration_missing() -> None:
    from engine.integrations.registry import get_plugin

    with pytest.raises(IntegrationMissing) as exc_info:
        get_plugin("llm", "nonexistent-plugin")
    assert exc_info.value.port == "llm"


def test_set_and_get_workspace_integration_without_secrets(
    db_conn: psycopg.Connection[Any],
) -> None:
    from engine.integrations.config import plugin_config, set_workspace_integration
    from engine.integrations.fakes import FakeFeedSource

    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-nosec-{uuid.uuid4().hex[:8]}",
        name="No Secrets WS",
        industry_pack="general",
    )
    user_id = _create_user(db_conn)

    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="feed_source",
        plugin="fake",
        settings={"default_items_count": 4},
        secrets=None,
        updated_by=user_id,
    )

    cls, settings = plugin_config(db_conn, workspace_id=ws_id, port="feed_source")
    assert cls is FakeFeedSource
    assert isinstance(settings, FakeFeedSource.Settings)
    assert settings.default_items_count == 4


def test_resolve_keys_without_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.integrations.config import _resolve_keys

    monkeypatch.delenv("APP_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ValueError, match="APP_ENCRYPTION_KEY"):
        _resolve_keys(None)


def test_rotate_workspace_integration_keys_handles_invalid_token(
    db_conn: psycopg.Connection[Any],
) -> None:
    from engine.integrations.config import (
        rotate_workspace_integration_keys,
        set_workspace_integration,
    )

    ws_id = db.create_workspace(
        db_conn,
        slug=f"ws-inv-{uuid.uuid4().hex[:8]}",
        name="Invalid Token WS",
        industry_pack="general",
    )
    user_id = _create_user(db_conn)
    key_other = Fernet.generate_key().decode()
    key_target = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Insert row with key_other
    set_workspace_integration(
        db_conn,
        workspace_id=ws_id,
        port="llm",
        plugin="fake",
        secrets={"api_key": "other-secret"},
        updated_by=user_id,
        encryption_key=key_other,
    )

    # Rotating with key_target will catch InvalidToken and skip the row
    rotated_count = rotate_workspace_integration_keys(
        db_conn,
        current_keys=[key_target],
        new_key=new_key,
        workspace_id=ws_id,
    )
    assert rotated_count == 0


def test_cli_branches() -> None:
    from engine import cli

    # Plugins list command
    code_list = cli.main(["plugins", "list"])
    assert code_list == 0

    # Empty args
    code_empty = cli.main([])
    assert code_empty == 0

    # Incomplete subcommand returns 1
    code_sub = cli.main(["plugins"])
    assert code_sub == 1


def test_decrypt_secrets_single_key_string() -> None:
    from engine.integrations.config import decrypt_secrets, encrypt_secrets

    k = Fernet.generate_key().decode()
    enc = encrypt_secrets({"test": 1}, k)
    dec = decrypt_secrets(enc, k)
    assert dec == {"test": 1}


def test_discover_plugins_handles_entrypoint_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.metadata

    from engine.integrations.registry import discover_plugins

    class BrokenEP:
        name = "broken"

        def load(self) -> None:
            raise RuntimeError("Corrupted plugin")

    def mock_entry_points(*, group: str) -> list[Any]:
        if group == "content_engine.llm":
            return [BrokenEP()]
        return []

    monkeypatch.setattr(importlib.metadata, "entry_points", mock_entry_points)
    discovered = discover_plugins("llm")
    assert "broken" not in discovered["llm"]


def test_openrouter_plugin_discovery_and_settings() -> None:
    from engine.integrations.openrouter import OpenRouterLLM
    from engine.integrations.ports import LLMPort
    from engine.integrations.registry import get_plugin

    cls = get_plugin("llm", "openrouter")
    assert hasattr(cls, "Settings")
    settings = cls.Settings(
        api_key="sk-or-test-key",
        models_by_stage={"brief": "anthropic/claude-opus-5.5"},
        data_policy="deny",
    )
    adapter = cls(settings=settings)
    assert isinstance(adapter, LLMPort)
    assert isinstance(adapter, OpenRouterLLM)
    assert adapter.settings.api_key is not None
    assert adapter.settings.api_key.get_secret_value() == "sk-or-test-key"
    assert adapter.settings.models_by_stage["brief"] == "anthropic/claude-opus-5.5"
    assert adapter.settings.data_policy == "deny"


def test_schema_to_widget_mapping_for_mvp_plugin_field_types() -> None:
    from typing import Literal

    from pydantic import BaseModel, Field, SecretStr

    from engine import services
    from engine.integrations.fakes import (
        FakeFeedSource,
        FakeKeywordProvider,
        FakeLLM,
        FakePublisher,
    )
    from engine.integrations.openrouter import OpenRouterLLM

    or_specs = {f["name"]: f for f in services.extract_settings_field_specs(OpenRouterLLM.Settings)}
    assert or_specs["api_key"]["widget"] == "password"
    assert or_specs["api_key"]["is_secret"] is True
    assert or_specs["default_model"]["widget"] == "text"
    assert or_specs["default_model"]["is_secret"] is False
    assert or_specs["models_by_stage"]["widget"] == "stage_models"
    assert or_specs["data_policy"]["widget"] == "select"
    assert or_specs["data_policy"]["options"] == ["deny", "allow"]
    assert or_specs["base_url"]["widget"] == "text"

    feed_specs = {
        f["name"]: f for f in services.extract_settings_field_specs(FakeFeedSource.Settings)
    }
    assert feed_specs["default_items_count"]["widget"] == "number"
    assert feed_specs["default_items_count"]["field_type"] == "integer"
    assert feed_specs["auth_token"]["widget"] == "password"
    assert feed_specs["auth_token"]["is_secret"] is True

    kw_specs = {
        f["name"]: f for f in services.extract_settings_field_specs(FakeKeywordProvider.Settings)
    }
    assert kw_specs["default_volume"]["widget"] == "number"
    assert kw_specs["default_cpc"]["widget"] == "number"
    assert kw_specs["default_cpc"]["field_type"] == "number"
    assert kw_specs["api_key"]["widget"] == "password"

    llm_specs = {f["name"]: f for f in services.extract_settings_field_specs(FakeLLM.Settings)}
    assert llm_specs["default_response"]["widget"] == "json"
    assert llm_specs["model_name"]["widget"] == "text"

    pub_specs = {
        f["name"]: f for f in services.extract_settings_field_specs(FakePublisher.Settings)
    }
    assert pub_specs["endpoint_url"]["widget"] == "text"
    assert pub_specs["api_key"]["widget"] == "password"

    class CustomSettings(BaseModel):
        secret_token: SecretStr = SecretStr("default-secret")
        enabled_flag: bool = True
        mode: Literal["draft", "live"] = Field(default="draft")

    c_inst = CustomSettings()
    assert c_inst.secret_token.get_secret_value() == "default-secret"
    assert c_inst.enabled_flag is True

    custom_specs = {f["name"]: f for f in services.extract_settings_field_specs(CustomSettings)}
    assert custom_specs["secret_token"]["widget"] == "password"
    assert custom_specs["enabled_flag"]["widget"] == "checkbox"
    assert custom_specs["mode"]["widget"] == "select"
    assert custom_specs["mode"]["options"] == ["draft", "live"]
    assert "research" in services.LLM_STAGES
    port_plugins = services.list_port_plugins()
    assert "llm" in port_plugins
    assert any(p["plugin"] == "openrouter" for p in port_plugins["llm"])
