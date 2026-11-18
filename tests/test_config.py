from pathlib import Path

import pytest

from engine.config import (
    PackValidationError,
    compute_pack_version,
    load_format_pack,
    load_industry_pack,
    load_prompt,
)


def test_config_importable() -> None:
    from engine import config

    assert hasattr(config, "load_industry_pack")
    assert hasattr(config, "load_format_pack")
    assert hasattr(config, "load_prompt")
    assert hasattr(config, "compute_pack_version")


def test_load_starter_industry_packs() -> None:
    general = load_industry_pack("general")
    assert general.slug == "general"
    assert general.name == "General Content & Digital Operations"
    assert "audience" in general.markdown_docs
    assert len(general.sources) > 0

    saas = load_industry_pack("b2b-saas")
    assert saas.slug == "b2b-saas"
    assert "audience" in saas.markdown_docs


def test_load_blog_format_pack() -> None:
    format_pack = load_format_pack("blog")
    assert format_pack.slug == "blog"
    assert format_pack.name == "Blog Post"
    assert len(format_pack.rubric) > 0


def test_load_prompts() -> None:
    score_prompt = load_prompt("score")
    assert len(score_prompt) > 0

    brief_prompt = load_prompt("brief.md")
    assert len(brief_prompt) > 0

    write_prompt = load_prompt("write_blog")
    assert len(write_prompt) > 0

    eval_prompt = load_prompt("evaluate")
    assert len(eval_prompt) > 0


def test_compute_pack_version_deterministic_hash() -> None:
    v1 = compute_pack_version(industry_slug="general", format_slug="blog")
    v2 = compute_pack_version(industry_slug="general", format_slug="blog")
    assert v1 == v2
    assert len(v1) == 64


def test_broken_pack_fails_with_file_and_field(tmp_path: Path) -> None:
    bad_industry_dir = tmp_path / "broken-pack"
    bad_industry_dir.mkdir(parents=True)
    profile_file = bad_industry_dir / "profile.yaml"
    profile_file.write_text("slug: broken-pack\n", encoding="utf-8")

    with pytest.raises(PackValidationError) as exc_info:
        load_industry_pack("broken-pack", base_dir=tmp_path)

    err_msg = str(exc_info.value)
    assert "profile.yaml" in err_msg
    assert "name" in err_msg


def test_config_missing_directories_and_files(tmp_path: Path) -> None:
    # Non-existent pack dir
    with pytest.raises(PackValidationError, match="not found"):
        load_industry_pack("does-not-exist", base_dir=tmp_path)

    with pytest.raises(PackValidationError, match="not found"):
        load_format_pack("does-not-exist", base_dir=tmp_path)

    # Missing prompt
    with pytest.raises(PackValidationError, match="Prompt template 'missing.md' not found"):
        load_prompt("missing", base_dir=tmp_path)

    # Missing profile.yaml
    empty_pack = tmp_path / "empty-pack"
    empty_pack.mkdir()
    with pytest.raises(PackValidationError, match="File does not exist"):
        load_industry_pack("empty-pack", base_dir=tmp_path)


def test_config_invalid_yaml_types(tmp_path: Path) -> None:
    pack_dir = tmp_path / "bad-yaml-pack"
    pack_dir.mkdir()

    # Not a mapping (list)
    (pack_dir / "profile.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(PackValidationError, match="Expected YAML mapping"):
        load_industry_pack("bad-yaml-pack", base_dir=tmp_path)

    # Syntax error
    (pack_dir / "profile.yaml").write_text(":\n", encoding="utf-8")
    with pytest.raises(PackValidationError):
        load_industry_pack("bad-yaml-pack", base_dir=tmp_path)


def test_format_pack_validation_and_optional_rubric(tmp_path: Path) -> None:
    format_dir = tmp_path / "test-format"
    format_dir.mkdir()

    # Missing field in format.yaml
    (format_dir / "format.yaml").write_text("name: Test\n", encoding="utf-8")
    with pytest.raises(PackValidationError) as exc_info:
        load_format_pack("test-format", base_dir=tmp_path)
    assert "format.yaml" in str(exc_info.value)
    assert "slug" in str(exc_info.value)

    # Valid format without rubric
    (format_dir / "format.yaml").write_text(
        "name: Test\nslug: test-format\nmin_words: 100\nmax_words: 500\nstructure: [title]\n",
        encoding="utf-8",
    )
    loaded = load_format_pack("test-format", base_dir=tmp_path)
    assert loaded.rubric == []


def test_industry_pack_without_sources(tmp_path: Path) -> None:
    industry_dir = tmp_path / "no-sources"
    industry_dir.mkdir()
    (industry_dir / "profile.yaml").write_text(
        "name: No Sources\nslug: no-sources\nmarket: US\nlocales: [en-US]\ndescription: Test\n",
        encoding="utf-8",
    )
    loaded = load_industry_pack("no-sources", base_dir=tmp_path)
    assert loaded.sources == []


def test_compute_pack_version_missing_directories(tmp_path: Path) -> None:
    # All directories missing still computes a deterministic hash
    h = compute_pack_version(
        industry_slug="none",
        format_slug="none",
        packs_dir=tmp_path / "packs",
        prompts_dir=tmp_path / "prompts",
    )
    assert len(h) == 64
