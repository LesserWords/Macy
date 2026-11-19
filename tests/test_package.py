import configparser
import contextlib
import shutil
import subprocess
import tarfile
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import engine
from engine.__main__ import main, run_worker_loop, run_worker_tick


def test_package_exposes_version() -> None:
    from pathlib import Path

    assert engine.__version__ == "0.2.0"
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'version = "0.2.0"' in pyproject


def test_main_entrypoint_returns_zero() -> None:
    assert main() == 0


def test_main_entrypoint_with_keep_alive(monkeypatch: object) -> None:
    monkeypatch.setenv("KEEP_ALIVE", "true")  # type: ignore[attr-defined]
    assert main([]) == 0
    assert main(["--once"]) == 0


def test_main_entrypoint_worker_flag(monkeypatch: Any) -> None:
    with patch("engine.__main__.run_worker_loop") as mock_loop:
        assert main(["--worker"]) == 0
    mock_loop.assert_called_once()


def test_run_worker_tick_processes_queued_run_and_delivers_webhooks(
    db_conn: Any,
    monkeypatch: Any,
) -> None:
    """run_worker_tick executes queued runs, compiles report, and delivers webhooks."""
    from engine import db, services
    from engine.safe_http import SafeResponse

    slug = f"ws-wrk-{uuid.uuid4().hex[:8]}"
    ws = services.create_workspace(db_conn, slug=slug, name="Worker Test", timezone="UTC")
    ws_id = ws["workspace_id"]
    owner = services.add_workspace_owner(
        db_conn,
        workspace_slug=slug,
        email=f"wrk-{uuid.uuid4().hex[:8]}@example.com",
    )
    user_id = owner["user"]["user_id"]
    camp = services.save_campaign(
        db_conn,
        workspace_id=ws_id,
        user_id=user_id,
        locale="pt-BR",
        market="BR",
        articles_per_week=1,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=Decimal("100.00"),
        run_cap_usd=Decimal("10.00"),
        name="Worker Campaign",
    )
    db.create_source(
        db_conn,
        workspace_id=ws_id,
        name="Worker Feed",
        domain="workernews.example.com",
        industry_pack="general",
        url="https://workernews.example.com",
        fetch_mode="feed",
        feed_url="https://workernews.example.com/rss.xml",
        tier=1,
        role="news",
        locales=["pt-BR"],
        topics=["trends"],
        enabled=True,
    )
    from cryptography.fernet import Fernet

    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    with patch("engine.safe_http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        services.save_subscription(
            db_conn,
            workspace_id=ws_id,
            user_id=user_id,
            url="https://example.com/topics",
            event_types=["topics.awaiting_approval"],
        )
        run_id = services.run_now(
            db_conn,
            workspace_id=ws_id,
            campaign_id=camp["id"],
            user_id=user_id,
        )

        with patch(
            "engine.webhooks.safe_http.safe_request",
            return_value=SafeResponse(status_code=200, headers={}, content=b"ok"),
        ):
            tick_res = run_worker_tick(db_conn, workspace_id=ws_id)

    assert tick_res["run"] is not None
    assert tick_res["run"]["id"] == run_id
    assert tick_res["run"]["status"] == "completed"
    assert any(d["status"] == "delivered" for d in tick_res["deliveries"])


def test_run_worker_tick_conn_none_and_loop_error_recovery() -> None:
    """Verify managed connections and loop error / KeyboardInterrupt handling."""
    blocked_run = {"id": str(uuid.uuid4()), "workspace_id": str(uuid.uuid4()), "status": "blocked"}
    with patch("engine.__main__.db.get_connection") as mock_get_conn:
        mock_conn = mock_get_conn.return_value.__enter__.return_value
        with (
            patch("engine.__main__.pipeline.process_next_queued_run", return_value=blocked_run),
            patch("engine.__main__.services.report") as mock_report,
            patch("engine.__main__.webhooks.deliver_due", return_value=[]),
        ):
            res = run_worker_tick()
        assert res == {"run": blocked_run, "deliveries": []}
        mock_report.assert_not_called()
        assert mock_conn is not None

    with (
        patch(
            "engine.__main__.run_worker_tick",
            side_effect=[{"run": None, "deliveries": []}, RuntimeError("transient db error")],
        ),
        patch("engine.__main__.observability.capture_exception") as mock_cap,
        patch("engine.__main__.time.sleep") as mock_sleep,
    ):
        run_worker_loop(poll_interval_seconds=0.0, max_iterations=2)
    mock_cap.assert_called_once()
    mock_sleep.assert_called_once()

    with (
        patch("engine.__main__.run_worker_tick", return_value={"run": None, "deliveries": []}),
        patch("engine.__main__.time.sleep", side_effect=KeyboardInterrupt),
        contextlib.suppress(KeyboardInterrupt),
    ):
        run_worker_loop()


def _assert_archive_clean(members: list[str], kind: str) -> None:
    forbidden_tokens = (
        "foun" + "der/",
        "seed_" + "ke" + "ro",
        "seed_" + "pupi" + "lens",
        "seed_" + "yun" + "gu",
        "optical-" + "retail",
    )
    for member in members:
        normalized = member.replace("\\", "/")
        parts = normalized.split("/", 1)
        rel = parts[1] if kind == "sdist" and len(parts) > 1 else normalized
        if rel.startswith("context/") and rel != "context/README.md":
            raise AssertionError(f"Private context leaked into {kind}: {rel}")
        name = Path(rel).name
        if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
            raise AssertionError(f"Secret env file leaked into {kind}: {rel}")
        for tok in forbidden_tokens:
            assert tok not in rel.lower(), f"Forbidden token '{tok}' in {kind}: {rel}"


def test_wheel_and_sdist_exclude_private_context(tmp_path: Path) -> None:
    """M8-04 & M9-01: Built wheel/sdist use macy package identity and exclude private files."""
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = tmp_path / "dist"
    uv_exe = shutil.which("uv") or "uv"
    proc = subprocess.run(  # noqa: S603
        [uv_exe, "build", "--out-dir", str(out_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0

    wheels = list(out_dir.glob("*.whl"))
    sdists = list(out_dir.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    assert wheels[0].name.startswith("macy-")
    assert sdists[0].name.startswith("macy-")

    with zipfile.ZipFile(wheels[0]) as zf:
        wheel_members = zf.namelist()
        ep_files = [m for m in wheel_members if m.endswith(".dist-info/entry_points.txt")]
        assert len(ep_files) == 1
        ep_text = zf.read(ep_files[0]).decode("utf-8")
    assert any(m.startswith("engine/") for m in wheel_members)
    _assert_archive_clean(wheel_members, "wheel")

    ep_parser = configparser.ConfigParser()
    ep_parser.read_string(ep_text)
    assert ep_parser["console_scripts"]["macy"] == "engine.cli:main"
    assert ("content" + "-engine") not in ep_parser["console_scripts"]
    assert "macy.capabilities" in ep_parser
    assert "macy.llm" in ep_parser
    legacy_ep_prefix = "content" + "_engine."
    assert not any(sec.startswith(legacy_ep_prefix) for sec in ep_parser.sections())

    with tarfile.open(sdists[0], "r:gz") as tf:
        sdist_members = tf.getnames()
    _assert_archive_clean(sdist_members, "sdist")


def _iter_macy_tracked_paths(repo_root: Path) -> list[Path]:
    git_exe = shutil.which("git") or "git"
    proc = subprocess.run(  # noqa: S603
        [git_exe, "-c", "core.quotePath=false", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    excluded_exact = {
        "docs/adr/ADR-033-macy-package-cli-and-tooling-identity.md",
        "docs/plans/M9.md",
        "uv.lock",
    }
    scopes = (
        "pyproject.toml",
        "README.md",
        "CONTRIBUTING.md",
        "engine/",
        "scripts/",
        "infra/",
        "docs/",
    )
    openspec_prefix = "openspec/"
    archive_prefix = "openspec/changes/archive/"
    selected: list[Path] = []
    for rel in proc.stdout.splitlines():
        norm = rel.strip().replace("\\", "/")
        if not norm or norm in excluded_exact:
            continue
        in_scope = (
            norm in scopes
            or norm.startswith(scopes)
            or (norm.startswith(openspec_prefix) and not norm.startswith(archive_prefix))
        )
        if in_scope and (repo_root / norm).is_file():
            selected.append(repo_root / norm)
    return selected


def test_repo_uses_macy_instead_of_legacy_cli_name() -> None:
    """M9-03 D2: Zero legacy CLI or entry-point group names remain outside M9/archive docs."""
    repo_root = Path(__file__).resolve().parent.parent
    legacy_cli = "content" + "-engine"
    legacy_mod = "content" + "_engine"
    hits: list[str] = []
    for path in _iter_macy_tracked_paths(repo_root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if legacy_cli in text or legacy_mod in text:
            hits.append(str(path.relative_to(repo_root)))
    assert hits == [], f"Legacy naming references remain: {hits}"
