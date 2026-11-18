import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_static_analysis_gate_configs_exist() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] == 10
    assert pyproject["tool"]["ruff"]["lint"]["pylint"] == {
        "max-branches": 12,
        "max-statements": 50,
        "max-args": 5,
    }
    assert pyproject["tool"]["complexipy"]["max-complexity-allowed"] == 15
    assert "contracts" in pyproject["tool"]["importlinter"]
    assert (ROOT / "vulture_whitelist.py").is_file()
    assert (ROOT / ".jscpd.json").is_file()


def test_check_script_invokes_all_static_gates() -> None:
    check_sh = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    for required_cmd in ("complexipy", "vulture", "jscpd", "lint-imports"):
        assert required_cmd in check_sh


def test_coverage_and_hypothesis_ci_profile_configured() -> None:
    from hypothesis import settings

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    cov_run = pyproject["tool"]["coverage"]["run"]
    cov_report = pyproject["tool"]["coverage"]["report"]

    assert cov_run["branch"] is True
    assert "engine" in cov_run["source"]
    assert cov_report["fail_under"] == 90

    ci_profile = settings.get_profile("ci")
    assert ci_profile.derandomize is True

    check_sh = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "diff-cover" in check_sh
