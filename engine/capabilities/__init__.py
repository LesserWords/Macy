"""Capability plugin package exports (ADR-029)."""

from engine.capabilities.ports import CapabilityManifest, CapabilityPlugin
from engine.capabilities.registry import (
    CORE_CAPABILITY_SLUG,
    allowed_mcp_tools,
    allowed_ui_screens,
    capability_for_mcp_tool,
    discover_capabilities,
    get_capability,
    normalize_enabled_capabilities,
    required_ports_for_capabilities,
)

__all__ = [
    "CORE_CAPABILITY_SLUG",
    "CapabilityManifest",
    "CapabilityPlugin",
    "allowed_mcp_tools",
    "allowed_ui_screens",
    "capability_for_mcp_tool",
    "discover_capabilities",
    "get_capability",
    "normalize_enabled_capabilities",
    "required_ports_for_capabilities",
]
