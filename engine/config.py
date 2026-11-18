"""Git packs, overlay context dirs, and prompt loader (ADR-009, ADR-032)."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDUSTRY_PACK = "general"
DEFAULT_BRAND_FIXTURE_NAME = "seed_example.yaml"
DEFAULT_USER_AGENT = "MacyContentEngine/1.0"


class PackValidationError(Exception):
    """Raised when a pack file is corrupted, missing, or has invalid fields."""

    def __init__(self, file: str, field: str, message: str) -> None:
        self.file = file
        self.field = field
        self.message = message
        super().__init__(f"Pack validation error in '{file}' on field '{field}': {message}")


class IndustryPack(BaseModel):
    """Industry configuration loaded from git pack directory or workspace snapshot."""

    name: str
    slug: str
    market: str
    locales: list[str] = Field(default_factory=list)
    description: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    markdown_docs: dict[str, str] = Field(default_factory=dict)


class RubricCriterion(BaseModel):
    """Format evaluation rubric criterion."""

    criterion: str
    weight: int
    critical: bool = False


class FormatPack(BaseModel):
    """Format configuration and rubric loaded from git pack directory."""

    name: str
    slug: str
    min_words: int
    max_words: int
    structure: list[str] = Field(default_factory=list)
    rubric: list[RubricCriterion] = Field(default_factory=list)


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw) if raw else None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def get_default_industry_pack() -> str:
    """Return configured default industry pack slug, falling back to neutral 'general'."""
    return os.getenv("MACY_DEFAULT_PACK", "").strip() or DEFAULT_INDUSTRY_PACK


def get_user_agent() -> str:
    """Return configured HTTP User-Agent string with neutral OSS default."""
    return os.getenv("MACY_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


def get_packs_roots(explicit_base: Path | str | None = None) -> list[Path]:
    """Return ordered pack root search paths (env overlay > Pocket /data/context > OSS)."""
    if explicit_base is not None:
        return [Path(explicit_base)]
    roots: list[Path] = []
    if explicit_packs := _env_path("MACY_PACKS_DIR"):
        roots.append(explicit_packs)
    if ctx_dir := _env_path("MACY_CONTEXT_DIR"):
        roots.append(ctx_dir / "packs")
    data_dir = _env_path("MACY_DATA_DIR") or Path("/data")
    roots.append(data_dir / "context" / "packs")
    roots.append(REPO_ROOT / "packs")
    return _dedupe_paths(roots)


def get_fixtures_roots(explicit_base: Path | str | None = None) -> list[Path]:
    """Return ordered brand fixture search paths (env overlay > Pocket /data/context > OSS)."""
    if explicit_base is not None:
        return [Path(explicit_base)]
    roots: list[Path] = []
    if explicit_fixtures := _env_path("MACY_FIXTURES_DIR"):
        roots.append(explicit_fixtures)
    if ctx_dir := _env_path("MACY_CONTEXT_DIR"):
        roots.append(ctx_dir / "fixtures")
    data_dir = _env_path("MACY_DATA_DIR") or Path("/data")
    roots.append(data_dir / "context" / "fixtures")
    roots.append(REPO_ROOT / "fixtures")
    return _dedupe_paths(roots)


def list_available_brand_fixtures(base_dir: Path | str | None = None) -> dict[str, Path]:
    """Discover brand fixture YAML files merged across search layers (higher priority wins)."""
    merged: dict[str, Path] = {}
    for root in reversed(get_fixtures_roots(base_dir)):
        if not root.is_dir():
            continue
        for file_path in sorted(root.glob("*.y*ml")):
            if file_path.suffix.lower() in {".yaml", ".yml"} and file_path.is_file():
                merged[file_path.stem] = file_path
    return dict(sorted(merged.items()))


def resolve_brand_fixture_path(fixture_path: str | Path | None = None) -> Path:
    """Resolve a brand fixture path or filename across layered fixture roots."""
    raw = str(fixture_path).strip() if fixture_path is not None else ""
    if not raw:
        raw = os.getenv("MACY_DEFAULT_BRAND_FIXTURE", "").strip() or DEFAULT_BRAND_FIXTURE_NAME
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    if not candidate.is_absolute() and len(candidate.parts) == 1:
        for root in get_fixtures_roots():
            layered = root / candidate.name
            if layered.is_file():
                return layered
    return candidate


def get_default_brand_fixture() -> Path:
    """Return resolved default brand fixture path."""
    return resolve_brand_fixture_path(None)


def _industry_search_dirs(base_dir: Path | str | None) -> list[Path]:
    if base_dir is not None:
        return [Path(base_dir)]
    return [root / "industries" for root in get_packs_roots()]


def _format_search_dirs(base_dir: Path | str | None) -> list[Path]:
    if base_dir is not None:
        return [Path(base_dir)]
    return [root / "formats" for root in get_packs_roots()]


def _resolve_pack_dir(search_dirs: list[Path], slug: str) -> Path:
    for parent in search_dirs:
        candidate = parent / slug
        if candidate.is_dir():
            return candidate
    fallback_dir = search_dirs[0] / slug
    raise PackValidationError("pack_dir", "directory", f"Directory {fallback_dir} not found")


def list_available_industry_packs(base_dir: Path | str | None = None) -> list[str]:
    """Discover industry pack slugs across search layers."""
    slugs: set[str] = set()
    for parent in _industry_search_dirs(base_dir):
        if not parent.is_dir():
            continue
        for entry in parent.iterdir():
            if entry.is_dir() and (entry / "profile.yaml").is_file():
                slugs.add(entry.name)
    return sorted(slugs)


def _safe_load_yaml(file_path: Path, filename_for_error: str) -> dict[str, Any]:
    if not file_path.exists():
        raise PackValidationError(filename_for_error, "file", "File does not exist")
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PackValidationError(filename_for_error, "root", "Expected YAML mapping")
        return data
    except yaml.YAMLError as e:
        raise PackValidationError(filename_for_error, "syntax", str(e)) from e


def load_industry_pack(pack_slug: str, base_dir: Path | str | None = None) -> IndustryPack:
    """Load and validate an industry pack across layered search paths."""
    pack_dir = _resolve_pack_dir(_industry_search_dirs(base_dir), pack_slug)

    profile_path = pack_dir / "profile.yaml"
    profile_data = _safe_load_yaml(profile_path, "profile.yaml")

    for field in ("name", "slug", "market", "locales", "description"):
        if field not in profile_data:
            raise PackValidationError("profile.yaml", field, f"Missing required field '{field}'")

    sources_path = pack_dir / "sources.yaml"
    sources: list[dict[str, Any]] = []
    if sources_path.exists():
        sources_data = _safe_load_yaml(sources_path, "sources.yaml")
        sources = sources_data.get("sources", [])

    markdown_docs: dict[str, str] = {}
    for md_file in pack_dir.glob("*.md"):
        markdown_docs[md_file.stem] = md_file.read_text(encoding="utf-8")

    return IndustryPack(
        name=profile_data["name"],
        slug=profile_data["slug"],
        market=profile_data["market"],
        locales=profile_data["locales"],
        description=profile_data["description"],
        sources=sources,
        markdown_docs=markdown_docs,
    )


def resolve_industry_pack_dict(
    pack_slug: str | None,
    workspace_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load industry pack dict from layered dirs, falling back to workspace DB snapshot."""
    if pack_slug:
        try:
            return load_industry_pack(pack_slug).model_dump(mode="json")
        except PackValidationError:
            pass
    if isinstance(workspace_config, Mapping):
        snap = workspace_config.get("industry_pack_snapshot")
        if isinstance(snap, Mapping) and (not pack_slug or snap.get("slug") == pack_slug):
            return dict(snap)
    return {"slug": pack_slug} if pack_slug else {}


def load_format_pack(format_slug: str, base_dir: Path | str | None = None) -> FormatPack:
    """Load and validate a content format pack and rubric across layered search paths."""
    pack_dir = _resolve_pack_dir(_format_search_dirs(base_dir), format_slug)

    format_path = pack_dir / "format.yaml"
    format_data = _safe_load_yaml(format_path, "format.yaml")

    for field in ("name", "slug", "min_words", "max_words", "structure"):
        if field not in format_data:
            raise PackValidationError("format.yaml", field, f"Missing required field '{field}'")

    rubric_path = pack_dir / "rubric.yaml"
    rubric_items: list[RubricCriterion] = []
    if rubric_path.exists():
        rubric_data = _safe_load_yaml(rubric_path, "rubric.yaml")
        raw_rubric = rubric_data.get("rubric", [])
        rubric_items = [RubricCriterion(**item) for item in raw_rubric]

    return FormatPack(
        name=format_data["name"],
        slug=format_data["slug"],
        min_words=format_data["min_words"],
        max_words=format_data["max_words"],
        structure=format_data["structure"],
        rubric=rubric_items,
    )


def load_prompt(prompt_name: str, base_dir: Path | str = "prompts") -> str:
    """Load a markdown prompt template from the prompts directory."""
    name = prompt_name if prompt_name.endswith(".md") else f"{prompt_name}.md"
    prompt_path = Path(base_dir) / name
    if not prompt_path.exists():
        raise PackValidationError(name, "prompt", f"Prompt template '{name}' not found")
    return prompt_path.read_text(encoding="utf-8")


def _optional_pack_subdir(
    kind: str,
    slug: str,
    packs_dir: Path | str | None,
) -> Path | None:
    search_dirs = (
        [Path(packs_dir) / kind]
        if packs_dir is not None
        else [root / kind for root in get_packs_roots()]
    )
    for parent in search_dirs:
        candidate = parent / slug
        if candidate.is_dir():
            return candidate
    return None


def compute_pack_version(
    industry_slug: str,
    format_slug: str,
    packs_dir: Path | str | None = None,
    prompts_dir: Path | str = "prompts",
) -> str:
    """Compute a deterministic SHA-256 hash across resolved pack and prompt files."""
    hasher = hashlib.sha256()

    industry_path = _optional_pack_subdir("industries", industry_slug, packs_dir)
    format_path = _optional_pack_subdir("formats", format_slug, packs_dir)
    prompts_path = Path(prompts_dir)

    all_files: list[Path] = []
    if industry_path is not None:
        all_files.extend(f for f in industry_path.rglob("*") if f.is_file())
    if format_path is not None:
        all_files.extend(f for f in format_path.rglob("*") if f.is_file())
    if prompts_path.exists():
        all_files.extend(f for f in prompts_path.glob("*.md") if f.is_file())

    for file_path in sorted(all_files, key=lambda p: p.as_posix()):
        hasher.update(file_path.as_posix().encode("utf-8"))
        hasher.update(file_path.read_bytes())

    return hasher.hexdigest()
