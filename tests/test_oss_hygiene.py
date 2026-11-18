"""Tests for M8-03 & M8-04 OSS hygiene gate and git history sanitization checker."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    git_exe = shutil.which("git") or "git"
    proc = subprocess.run(  # noqa: S603
        [git_exe, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _init_clean_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "CI Bot")
    (repo / "LICENSE").write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    (repo / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
    (repo / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
    (repo / "README.md").write_text("# Macy\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial clean oss commit")


def test_oss_hygiene_detects_leaks_and_missing_governance(tmp_path: Path) -> None:
    """D2 & D3: Hygiene gate flags forbidden tokens, local paths, and missing governance."""
    from scripts import check_oss_hygiene

    repo = tmp_path / "dirty_repo"
    _init_clean_git_repo(repo)
    assert check_oss_hygiene.audit_oss_hygiene(repo) == []

    # 1. Missing governance file fails
    (repo / "LICENSE").unlink()
    missing_issues = check_oss_hygiene.audit_oss_hygiene(repo)
    assert any("LICENSE" in issue for issue in missing_issues)
    (repo / "LICENSE").write_text("Apache License\nVersion 2.0\n", encoding="utf-8")

    # 2. Forbidden directory & proprietary tokens fail
    leak_dir = repo / ("foun" + "der")
    leak_dir.mkdir()
    (leak_dir / "notes.md").write_text("secret strategy\n", encoding="utf-8")
    bad_doc = repo / "docs.md"
    bad_tok = "ke" + "ro"
    local_uri = "file:///c" + ":/Users/demo/notes.md"
    bad_doc.write_text(f"Sample {bad_tok} link {local_uri}\n", encoding="utf-8")
    _git(repo, "add", ".")

    issues = check_oss_hygiene.audit_oss_hygiene(repo)
    assert any(("foun" + "der") in item for item in issues)
    assert any("docs.md" in item for item in issues)
    assert len(issues) >= 3

    assert check_oss_hygiene.main(["--repo-root", str(repo)]) == 1
    assert check_oss_hygiene.main(["--repo-root", str(repo), "--check-governance"]) == 0


def test_oss_hygiene_git_history_audit(tmp_path: Path) -> None:
    """M8-04 D3: --check-git-history fails when deleted secrets linger in commit history."""
    from scripts import check_oss_hygiene

    repo = tmp_path / "hist_repo"
    _init_clean_git_repo(repo)

    # Clean history passes
    assert check_oss_hygiene.audit_oss_hygiene(repo, check_git_history=True) == []
    assert check_oss_hygiene.main(["--repo-root", str(repo), "--check-git-history"]) == 0

    # Commit a forbidden fixture then delete it in a later commit — history audit must fail
    secret_fix = repo / "fixtures" / ("seed_" + "ke" + "ro" + ".yaml")
    secret_fix.parent.mkdir(parents=True, exist_ok=True)
    secret_fix.write_text("positioning: private\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add private fixture")
    _git(repo, "rm", str(secret_fix.relative_to(repo)))
    _git(repo, "commit", "-m", "remove private fixture from tip")

    # Working tree looks clean, but git history check catches the old commit
    assert check_oss_hygiene.audit_oss_hygiene(repo, check_git_history=False) == []
    hist_issues = check_oss_hygiene.audit_oss_hygiene(repo, check_git_history=True)
    assert any("history" in item.lower() for item in hist_issues)
    assert check_oss_hygiene.main(["--repo-root", str(repo), "--check-git-history"]) == 1


@pytest.mark.parametrize("flag", ["--check-governance"])
def test_oss_hygiene_governance_cli_exit(tmp_path: Path, flag: str) -> None:
    """D3: Governance-only CLI returns 1 when governance docs are missing or wrong license."""
    from scripts import check_oss_hygiene

    repo = tmp_path / "gov_repo"
    _init_clean_git_repo(repo)
    (repo / "LICENSE").write_text("Proprietary License\n", encoding="utf-8")
    assert check_oss_hygiene.main(["--repo-root", str(repo), flag]) == 1
