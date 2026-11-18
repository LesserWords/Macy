"""OSS public-readiness hygiene checker for tracked files, governance, and git history."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

_TEXT_SKIP_SUFFIXES = frozenset(
    {
        ".mo",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".pdf",
        ".woff",
        ".woff2",
        ".pyc",
        ".pyo",
        ".db",
        ".sqlite",
        ".zip",
        ".gz",
        ".whl",
    }
)


def _build_forbidden_path_prefixes() -> tuple[str, ...]:
    """Build forbidden path prefixes dynamically so this file does not self-match."""
    brand1 = "ke" + "ro"
    brand2 = "pupi" + "lens"
    brand3 = "yun" + "gu"
    vertical = "optical-" + "retail"
    return (
        "foun" + "der/",
        f"fixtures/seed_{brand1}",
        f"fixtures/seed_{brand2}",
        f"fixtures/seed_{brand3}",
        f"packs/industries/{vertical}/",
    )


def _build_forbidden_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Build forbidden content regexes dynamically so this file does not self-match."""
    brand1 = "ke" + "ro"
    brand2 = "pupi" + "lens"
    brand3 = "yun" + "gu"
    org = "log" + r"[\s_-]*" + "sistemas"
    owner = "tia" + "go"
    vertical = "optical-" + "retail"
    work_dir = "Organiza" + "ção docs"
    return (
        ("brand1_token", re.compile(brand1, re.IGNORECASE)),
        ("brand1_compound", re.compile(rf"{brand1}(?:otica|-optical)", re.IGNORECASE)),
        ("brand2_token", re.compile(brand2, re.IGNORECASE)),
        ("brand3_token", re.compile(brand3, re.IGNORECASE)),
        ("org_token", re.compile(org, re.IGNORECASE)),
        ("personal_handle", re.compile(rf"{owner}@|\b{owner}\b", re.IGNORECASE)),
        ("vertical_slug", re.compile(vertical, re.IGNORECASE)),
        ("local_file_uri", re.compile(r"file:///[A-Za-z]:/")),
        ("local_work_path", re.compile(re.escape(work_dir), re.IGNORECASE)),
    )


def _git_exe() -> str:
    return shutil.which("git") or "git"


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [_git_exe(), "-c", "core.quotePath=false", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _is_forbidden_path(rel_path: str) -> str | None:
    normalized = rel_path.replace("\\", "/")
    for prefix in _build_forbidden_path_prefixes():
        if normalized.startswith(prefix):
            return f"forbidden tracked path '{normalized}'"
    name = Path(normalized).name
    is_env_secret = name == ".env" or (name.startswith(".env.") and not name.endswith(".example"))
    is_secrets_toml = name == "secrets.toml"
    if is_env_secret or is_secrets_toml:
        return f"forbidden secret filename '{normalized}'"
    return None


def _check_governance_files(repo_root: Path) -> list[str]:
    issues: list[str] = []
    license_path = repo_root / "LICENSE"
    if not license_path.is_file():
        issues.append("missing governance file: LICENSE")
    else:
        license_text = license_path.read_text(encoding="utf-8", errors="ignore")
        if "Apache License" not in license_text:
            issues.append("LICENSE must contain Apache License 2.0 text")

    for filename in ("CONTRIBUTING.md", "SECURITY.md"):
        doc_path = repo_root / filename
        is_missing = (
            not doc_path.is_file()
            or not doc_path.read_text(encoding="utf-8", errors="ignore").strip()
        )
        if is_missing:
            issues.append(f"missing or empty governance file: {filename}")
    return issues


def _get_tracked_paths(repo_root: Path) -> list[str]:
    proc = _run_git(repo_root, "ls-files", "-z")
    if proc.returncode != 0:
        return []
    return [entry for entry in proc.stdout.split("\x00") if entry]


def _scan_text_file(
    repo_root: Path,
    rel_path: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
) -> list[str]:
    file_path = repo_root / rel_path
    if not file_path.is_file() or file_path.suffix.lower() in _TEXT_SKIP_SUFFIXES:
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    issues: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        for label, pattern in patterns:
            if pattern.search(line):
                issues.append(f"{rel_path}:{line_no}: forbidden content match ({label})")
    return issues


def _collect_commit_shas(repo_root: Path, target_ref: str) -> list[str]:
    proc = _run_git(repo_root, "rev-list", target_ref)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _scan_git_history_paths(repo_root: Path, target_ref: str) -> list[str]:
    proc = _run_git(repo_root, "log", "--format=", "--name-only", target_ref)
    if proc.returncode != 0:
        return [f"git history check failed for ref '{target_ref}': {proc.stderr.strip()}"]
    issues: list[str] = []
    seen: set[str] = set()
    for raw_line in proc.stdout.splitlines():
        rel_path = raw_line.strip()
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        violation = _is_forbidden_path(rel_path)
        if violation:
            issues.append(f"git history contains {violation}")
    return issues


def _scan_git_history_contents(repo_root: Path, target_ref: str) -> list[str]:
    shas = _collect_commit_shas(repo_root, target_ref)
    if not shas:
        return []
    patterns = _build_forbidden_patterns()
    combined = "|".join(f"(?:{regex.pattern})" for _, regex in patterns)
    issues: list[str] = []
    chunk_size = 50
    for idx in range(0, len(shas), chunk_size):
        batch = shas[idx : idx + chunk_size]
        proc = _run_git(repo_root, "grep", "-I", "-i", "-n", "-E", combined, *batch)
        if proc.returncode == 0 and proc.stdout.strip():
            lines = proc.stdout.strip().splitlines()
            sample = lines[0]
            msg = (
                f"git history content leak on '{target_ref}' ({len(lines)} matches, e.g. {sample})"
            )
            issues.append(msg)
            break
    return issues


def audit_oss_hygiene(
    repo_root: Path,
    *,
    check_governance: bool = True,
    check_git_history: bool = False,
    branch: str | None = None,
) -> list[str]:
    """Audit repository tracked files, governance docs, and optionally git commit history."""
    issues: list[str] = []
    if check_governance:
        issues.extend(_check_governance_files(repo_root))

    patterns = _build_forbidden_patterns()
    for rel_path in _get_tracked_paths(repo_root):
        path_issue = _is_forbidden_path(rel_path)
        if path_issue:
            issues.append(path_issue)
        issues.extend(_scan_text_file(repo_root, rel_path, patterns))

    if check_git_history:
        target_ref = branch or "HEAD"
        issues.extend(_scan_git_history_paths(repo_root, target_ref))
        issues.extend(_scan_git_history_contents(repo_root, target_ref))

    return issues


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for OSS hygiene checks."""
    parser = argparse.ArgumentParser(description="Check OSS repository hygiene.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Path to git repository root.",
    )
    parser.add_argument(
        "--check-governance",
        action="store_true",
        help="Verify only LICENSE, CONTRIBUTING.md, and SECURITY.md governance files.",
    )
    parser.add_argument(
        "--check-git-history",
        action="store_true",
        help="Also verify commit history for leaked paths or tokens.",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Git ref/branch to inspect when --check-git-history is set (default: HEAD).",
    )
    args = parser.parse_args(argv)

    if args.check_governance:
        issues = _check_governance_files(args.repo_root)
    else:
        issues = audit_oss_hygiene(
            args.repo_root,
            check_governance=True,
            check_git_history=args.check_git_history,
            branch=args.branch,
        )

    for issue in issues:
        print(f"OSS-HYGIENE ERROR: {issue}", file=sys.stderr)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
