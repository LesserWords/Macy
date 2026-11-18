"""Capability plugin discovery and surface filtering helpers (ADR-029)."""

from __future__ import annotations

import importlib
import importlib.metadata
import tomllib
from collections.abc import Iterable
from pathlib import Path

from engine.capabilities.ports import CapabilityManifest

CAPABILITY_GROUP = "content_engine.capabilities"
CORE_CAPABILITY_SLUG = "core-compliance"


def _load_pyproject_capabilities(existing: dict[str, CapabilityManifest]) -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():  # pragma: no cover
        return
    parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    ep_table = parsed.get("project", {}).get("entry-points", {}).get(CAPABILITY_GROUP, {})
    for slug, import_spec in ep_table.items():
        if slug not in existing:
            module_name, symbol_name = str(import_spec).split(":", 1)
            cls_obj = getattr(importlib.import_module(module_name), symbol_name)
            existing[slug] = cls_obj().manifest()


def discover_capabilities() -> dict[str, CapabilityManifest]:
    """Discover all registered capability plugins via entry points and pyproject fallback."""
    manifests: dict[str, CapabilityManifest] = {}
    for ep in importlib.metadata.entry_points(group=CAPABILITY_GROUP):
        try:
            plugin_cls = ep.load()
            manifests[ep.name] = plugin_cls().manifest()
        except Exception:  # noqa: S112
            continue
    _load_pyproject_capabilities(manifests)
    return manifests


def get_capability(slug: str) -> CapabilityManifest:
    """Return a single capability manifest by slug or raise KeyError."""
    caps = discover_capabilities()
    if slug not in caps:
        msg = f"Unknown capability plugin: '{slug}'"
        raise KeyError(msg)
    return caps[slug]


def normalize_enabled_capabilities(
    requested: Iterable[str] | None,
) -> list[str]:
    """Validate and normalize capability slugs, always retaining always-enabled core plugins."""
    available = discover_capabilities()
    if requested is None:
        return list(available.keys())

    ordered: list[str] = [slug for slug, manifest in available.items() if manifest.always_enabled]
    for raw in requested:
        slug = str(raw).strip()
        if not slug:
            continue
        if slug not in available:
            msg = f"Unknown capability plugin: '{slug}'"
            raise ValueError(msg)
        if slug not in ordered:
            ordered.append(slug)
    return ordered


def _collect_surface_items(
    enabled_slugs: Iterable[str] | None,
    attr: str,
) -> set[str]:
    available = discover_capabilities()
    active = normalize_enabled_capabilities(enabled_slugs)
    items: set[str] = set()
    for slug in active:
        items.update(getattr(available[slug], attr))
    return items


def allowed_ui_screens(enabled_slugs: Iterable[str] | None) -> set[str]:
    """Return the union of UI screen keys allowed for the enabled capabilities."""
    return _collect_surface_items(enabled_slugs, "ui_screens")


def allowed_mcp_tools(enabled_slugs: Iterable[str] | None) -> set[str]:
    """Return the union of MCP tool names allowed for the enabled capabilities."""
    return _collect_surface_items(enabled_slugs, "mcp_tools")


def required_ports_for_capabilities(enabled_slugs: Iterable[str] | None) -> set[str]:
    """Return the union of required integration port names for enabled capabilities."""
    return _collect_surface_items(enabled_slugs, "required_ports")


def capability_for_mcp_tool(tool_name: str) -> str | None:
    """Return the owning capability slug for a given MCP tool name, if known."""
    for slug, manifest in discover_capabilities().items():
        if tool_name in manifest.mcp_tools:
            return slug
    return None
