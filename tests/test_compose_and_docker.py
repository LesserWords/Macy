from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"


def test_dockerfile_structure_and_security() -> None:
    dockerfile_path = INFRA / "Dockerfile"
    assert dockerfile_path.is_file(), "Dockerfile must exist at infra/Dockerfile"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "ghcr.io/astral-sh/uv:" in content or "FROM python:" in content
    assert "USER appuser" in content or "USER nobody" in content or "useradd" in content
    assert '"-m"' in content or "streamlit" in content
    assert "app.py" in content
    assert ".streamlit" in content
    assert "fixtures" in content
    assert "locales" in content
    assert "packs" in content
    assert "prompts" in content

    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "streamlit[auth]" in pyproject_text

    st_config_path = ROOT / ".streamlit" / "config.toml"
    assert st_config_path.is_file(), ".streamlit/config.toml must exist"
    st_config_text = st_config_path.read_text(encoding="utf-8")
    assert "[secrets]" in st_config_text
    assert "/tmp/macy/secrets.toml" in st_config_text  # noqa: S108
    assert ".streamlit/secrets.toml" in st_config_text

    dockerignore_text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".streamlit/secrets.toml" in dockerignore_text


def test_compose_files_exist_and_define_services() -> None:
    compose_files = [
        INFRA / "compose.yaml",
        INFRA / "compose.override.yaml",
        INFRA / "compose.e2e.yaml",
        INFRA / "compose.prod.yaml",
    ]
    for file_path in compose_files:
        assert file_path.is_file(), f"{file_path.name} must exist"

    base_yaml = (INFRA / "compose.yaml").read_text(encoding="utf-8")
    for service in ("db:", "migrate:", "app:", "worker:"):
        assert service in base_yaml, f"Base compose.yaml must define {service}"
    assert "postgres:" in base_yaml
    assert "dbmate" in base_yaml

    prod_yaml = (INFRA / "compose.prod.yaml").read_text(encoding="utf-8")
    assert "caddy:" in prod_yaml
    assert "backup:" in prod_yaml
    # DB port 5432 should NOT be exposed in compose.prod.yaml
    assert '"5432:5432"' not in prod_yaml
    assert '"8501:8501"' not in prod_yaml


def test_compose_prod_healthchecks_and_no_test_identities() -> None:
    base_yaml = (INFRA / "compose.yaml").read_text(encoding="utf-8")
    assert "python -m engine.auth" in base_yaml
    assert "GOOGLE_CLIENT_ID" in base_yaml

    prod_yaml = (INFRA / "compose.prod.yaml").read_text(encoding="utf-8")
    assert "healthcheck:" in prod_yaml
    assert "MACY_TEST_USER_EMAIL" not in prod_yaml
    assert "AUTH_COOKIE_SECRET:" in prod_yaml
    assert "GOOGLE_CLIENT_ID:" in prod_yaml
    assert "GOOGLE_CLIENT_SECRET:" in prod_yaml
    # Worker service must not inherit Google OAuth client secrets via x-app-env
    x_app_env_block = prod_yaml.split("services:", maxsplit=1)[0]
    assert "GOOGLE_CLIENT_SECRET" not in x_app_env_block
    assert "AUTH_COOKIE_SECRET" not in x_app_env_block
    # No mandatory host secrets.toml bind-mount (Docker would create a directory if missing)
    assert ":/app/.streamlit/secrets.toml" not in prod_yaml


def test_baselines_and_runbook_exist() -> None:
    assert (ROOT / ".env.example").is_file()
    assert (ROOT / ".env.staging.example").is_file()
    assert (ROOT / ".env.prod.example").is_file()
    assert (INFRA / "deploy" / "staging" / "secrets.toml.example").is_file()
    assert (INFRA / "deploy" / "prod" / "secrets.toml.example").is_file()
    assert (ROOT / "scripts" / "backup.sh").is_file()
    assert (ROOT / "docs" / "runbook.md").is_file()

    staging_env_text = (ROOT / ".env.staging.example").read_text(encoding="utf-8")
    assert "AUTH_COOKIE_SECRET=" in staging_env_text
    assert "GOOGLE_CLIENT_ID=" in staging_env_text
    assert "GOOGLE_CLIENT_SECRET=" in staging_env_text
    assert "BACKUP_HEARTBEAT_URL=" in staging_env_text
    assert "BACKUP_ALERT_URL=" in staging_env_text

    prod_env_text = (ROOT / ".env.prod.example").read_text(encoding="utf-8")
    assert "APP_ENV=production" in prod_env_text
    assert "COMPOSE_PROJECT_NAME=macy-prod" in prod_env_text
    assert "AUTH_COOKIE_SECRET=" in prod_env_text
    assert "GOOGLE_CLIENT_ID=" in prod_env_text
    assert "GOOGLE_CLIENT_SECRET=" in prod_env_text
    assert "RETENTION_DB_PASSWORD=" in prod_env_text
    assert "BACKUP_HEARTBEAT_URL=" in prod_env_text
    assert "BACKUP_ALERT_URL=" in prod_env_text

    prod_secrets_text = (INFRA / "deploy" / "prod" / "secrets.toml.example").read_text(
        encoding="utf-8"
    )
    assert "redirect_uri" in prod_secrets_text
    assert "oauth2callback" in prod_secrets_text

    prod_yaml = (INFRA / "compose.prod.yaml").read_text(encoding="utf-8")
    assert "scripts/backup.sh" in prod_yaml
    assert "BACKUP_RETENTION_DAYS" in prod_yaml
    assert "BACKUP_HEARTBEAT_URL" in prod_yaml
    assert "BACKUP_ALERT_URL" in prod_yaml
    assert "RETENTION_DATABASE_URL" in prod_yaml

    runbook_text = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    assert "First-time VPS preparation" in runbook_text
    assert "Deploy" in runbook_text
    assert "Rollback" in runbook_text
    assert "Logs" in runbook_text
    assert "Restart" in runbook_text
    assert "Verify exposure" in runbook_text
    assert "Backups and restore" in runbook_text
    assert "APP_ENCRYPTION_KEY" in runbook_text


def test_deploy_workflow_structure() -> None:
    import yaml

    deploy_yml = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "STAGING_DEPLOY_ENABLED" in deploy_yml
    assert "PROD_DEPLOY_ENABLED" in deploy_yml
    assert "ghcr.io" in deploy_yml
    assert "StrictHostKeyChecking yes" in deploy_yml
    assert "scripts/deploy.sh" in deploy_yml
    assert "scripts/backup.sh" in deploy_yml
    assert "scripts/post_deploy_smoke.py" in deploy_yml

    workflow = yaml.safe_load(deploy_yml)
    triggers = workflow.get("on") or workflow.get(True) or {}
    assert "v*" in triggers.get("push", {}).get("tags", [])

    jobs = workflow.get("jobs", {})
    assert "build-prod" in jobs
    assert "deploy-prod" in jobs
    prod_job = jobs["deploy-prod"]
    assert prod_job.get("environment") == "production"
    assert prod_job.get("env", {}).get("REMOTE_DIR") == "/opt/macy/prod"
    step_runs = "\n".join(str(step.get("run", "")) for step in prod_job.get("steps", []))
    assert "post_deploy_smoke.py" in step_runs
    assert any(
        "failure()" in str(step.get("if", "")) and "scripts/deploy.sh" in str(step.get("run", ""))
        for step in prod_job.get("steps", [])
    )


def test_squawk_and_compose_config_configured() -> None:
    check_sh = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "squawk" in check_sh, "squawk must be invoked in scripts/check.sh"

    pre_commit = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "squawk" in pre_commit, "squawk hook must be present in .pre-commit-config.yaml"

    ci_yml = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "compose-config:" in ci_yml, "ci.yml must have compose-config job"
    assert "squawk:" in ci_yml or "squawk" in ci_yml, "ci.yml must run squawk"


def test_compose_prod_mcp_isolation_and_healthcheck() -> None:
    """Verify opt-in MCP topology, Caddy /mcp* routing, and prod hardening invariants (M5-06)."""
    import yaml

    base_text = (INFRA / "compose.yaml").read_text(encoding="utf-8")
    base_doc = yaml.safe_load(base_text)
    mcp_svc = base_doc["services"]["mcp"]
    assert mcp_svc["profiles"] == ["mcp"]
    cmd_text = " ".join(str(part) for part in mcp_svc["command"])
    assert "mcp" in cmd_text
    assert "--transport streamable-http" in cmd_text
    assert "--port 8765" in cmd_text
    hc_text = " ".join(str(part) for part in mcp_svc["healthcheck"]["test"])
    assert "8765/healthz" in hc_text
    assert "MCP_AUTH_TOKEN" in mcp_svc["environment"]
    assert "MCP_ACTOR_EMAIL" in mcp_svc["environment"]

    override_doc = yaml.safe_load((INFRA / "compose.override.yaml").read_text(encoding="utf-8"))
    override_mcp = override_doc["services"]["mcp"]
    assert "../engine:/app/engine:ro" in override_mcp["volumes"]
    assert "127.0.0.1:8765:8765" in override_mcp["ports"]

    e2e_doc = yaml.safe_load((INFRA / "compose.e2e.yaml").read_text(encoding="utf-8"))
    e2e_mcp = e2e_doc["services"]["mcp"]
    assert e2e_mcp["environment"]["APP_ENV"] == "test"
    assert e2e_mcp["environment"]["MCP_ACTOR_EMAIL"]
    assert e2e_mcp["environment"]["MCP_AUTH_TOKEN"]
    assert e2e_mcp["environment"]["SAFE_HTTP_TEST_ALLOW_HOSTS"] == "webhook-receiver"
    assert "127.0.0.1:8765:8765" in e2e_mcp["ports"]

    prod_yaml = (INFRA / "compose.prod.yaml").read_text(encoding="utf-8")
    assert '"5432:5432"' not in prod_yaml
    assert '"8501:8501"' not in prod_yaml
    assert '"8765:8765"' not in prod_yaml
    assert ":8765" not in prod_yaml
    assert "MACY_TEST_USER_EMAIL" not in prod_yaml

    x_app_env_block, services_block = prod_yaml.split("services:", maxsplit=1)
    assert "MCP_AUTH_TOKEN" not in x_app_env_block
    assert "MCP_ACTOR_EMAIL" not in x_app_env_block
    assert "mcp:" in services_block
    mcp_prod_section = services_block.split("mcp:", maxsplit=1)[1]
    assert "build: !reset null" in mcp_prod_section
    assert "MCP_AUTH_TOKEN:" in mcp_prod_section
    assert "MCP_ACTOR_EMAIL:" in mcp_prod_section
    assert "ports:" not in mcp_prod_section.split("caddy:", maxsplit=1)[0]

    caddyfile = (INFRA / "Caddyfile").read_text(encoding="utf-8")
    assert "/mcp*" in caddyfile
    assert "reverse_proxy mcp:8765" in caddyfile
    assert caddyfile.index("/mcp*") < caddyfile.index("reverse_proxy app:8501")

    for env_name in (".env.staging.example", ".env.prod.example"):
        env_text = (ROOT / env_name).read_text(encoding="utf-8")
        assert "MCP_ENABLED=false" in env_text
        assert "COMPOSE_PROFILES=" in env_text
        assert "MCP_AUTH_TOKEN=CHANGE_ME" in env_text
        assert "MCP_ACTOR_EMAIL=" in env_text


def test_compose_pocket_zero_env_stack() -> None:
    """M7-02 D4: compose.pocket.yaml defines zero-.env db + macy single-app runtime."""
    import shutil
    import subprocess

    import yaml

    pocket_path = INFRA / "compose.pocket.yaml"
    assert pocket_path.is_file(), "compose.pocket.yaml must exist"
    doc = yaml.safe_load(pocket_path.read_text(encoding="utf-8"))
    svcs = doc["services"]
    assert set(svcs.keys()) == {"db", "macy"}

    macy_svc = svcs["macy"]
    env = macy_svc["environment"]
    assert env["POCKET_MODE"] == "true"
    assert env["AUTO_MIGRATE"] == "true"
    assert env["EMBEDDED_WORKER"] == "true"
    assert "auto" in str(env["AUTH_MODE"])
    assert "healthcheck" in macy_svc
    assert "healthcheck" in svcs["db"]
    assert any(str(v).endswith(":/data") for v in macy_svc["volumes"])
    assert env.get("MACY_CONTEXT_DIR") == "/data/context"
    assert any(str(v).endswith(":/data/context:ro") for v in macy_svc["volumes"])

    context_readme = ROOT / "context" / "README.md"
    assert context_readme.is_file(), "context/README.md must exist"

    context_example = INFRA / "compose.context.example.yaml"
    assert context_example.is_file(), "infra/compose.context.example.yaml must exist"

    docker_bin = shutil.which("docker") or "docker"
    proc = subprocess.run(  # noqa: S603
        [docker_bin, "compose", "-f", str(pocket_path), "config", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    proc_ctx = subprocess.run(  # noqa: S603
        [
            docker_bin,
            "compose",
            "-f",
            str(INFRA / "compose.yaml"),
            "-f",
            str(context_example),
            "config",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_ctx.returncode == 0, proc_ctx.stderr
